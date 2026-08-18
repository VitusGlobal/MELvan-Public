"""HTTP/JSON server for MELvan."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .auth import SessionStore
from .identity import IdentityError
from .service import DomainError, MelvanService


STATIC_ROOT = Path(__file__).resolve().parent / "static"


class MelvanHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        service: MelvanService,
        sessions: SessionStore | None = None,
        secure_cookies: bool = False,
    ):
        super().__init__(address, MelvanRequestHandler)
        self.service = service
        self.api = APIApplication(service, sessions=sessions)
        self.secure_cookies = secure_cookies


class APIApplication:
    """Pure API router so route behavior can be tested without opening a socket."""

    def __init__(self, service: MelvanService, sessions: SessionStore | None = None):
        self.service = service
        self.sessions = sessions or SessionStore(
            service.users,
            service.database,
            pepper="melvan-local-development-pepper",
        )

    def dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = payload or {}
        workspace_scope = None
        try:
            if method == "GET" and path == "/api/live":
                return HTTPStatus.OK, {"status": "alive"}
            if method == "GET" and path == "/api/ready":
                health = self.service.health()
                return (
                    HTTPStatus.OK
                    if health["status"] == "ready"
                    else HTTPStatus.SERVICE_UNAVAILABLE,
                    health,
                )
            if method == "GET" and path == "/api/health":
                return HTTPStatus.OK, self.service.health()
            if method == "POST" and path == "/api/auth/recovery":
                return HTTPStatus.ACCEPTED, self.sessions.request_recovery(
                    str(body.get("email") or ""),
                    request_source=str(body.get("request_source") or ""),
                )
            if method == "POST" and path == "/api/session":
                session_token, user, active_role, session_meta = self.sessions.login(
                    str(body.get("email") or ""),
                    str(body.get("password") or ""),
                    duration_choice=str(body.get("duration") or "midnight"),
                    timezone_name=str(body.get("timezone") or "UTC"),
                )
                return HTTPStatus.OK, {
                    "token": session_token,
                    "user": user,
                    "active_role": active_role,
                    "requires_role_selection": (
                        active_role is None
                        and not session_meta["requires_password_change"]
                    ),
                    **session_meta,
                }
            if method == "POST" and path == "/api/session/password":
                session_token, user, active_role, session_meta = self.sessions.change_password(
                    token,
                    str(body.get("password") or ""),
                    str(body.get("confirmation") or ""),
                    user_guide_acknowledged=(
                        body.get("user_guide_acknowledged") is True
                    ),
                )
                return HTTPStatus.OK, {
                    "token": session_token,
                    "user": user,
                    "active_role": active_role,
                    "requires_role_selection": active_role is None,
                    **session_meta,
                }

            if method == "GET" and path == "/api/session":
                user = self.sessions.resolve(
                    token, require_active_role=False, allow_password_change=True
                )
                if user.get("requires_password_change"):
                    return HTTPStatus.OK, {
                        "requires_password_change": True,
                        "requires_role_selection": False,
                        "active_role": None,
                        "password_setup_purpose": user.get(
                            "password_setup_purpose"
                        ),
                        "requires_user_guide_acknowledgement": user.get(
                            "requires_user_guide_acknowledgement", False
                        ),
                    }
                return HTTPStatus.OK, {
                    "user": {key: value for key, value in user.items() if key != "active_role"},
                    "active_role": user["active_role"],
                    "requires_role_selection": user["active_role"] is None,
                }
            if method == "POST" and path == "/api/session/logout":
                logout_user = self.sessions.resolve(token, require_active_role=False)
                with self.service.workspace(
                    logout_user["workspace_id"],
                    read_only=bool(logout_user.get("workspace_read_only")),
                ):
                    with self.service.database.connection() as connection:
                        rows = connection.execute(
                            """
                            SELECT id FROM media_staging
                            WHERE session_fingerprint = ?
                              AND workspace_id = melvan_workspace_id()
                            """,
                            (self._session_fingerprint(token),),
                        ).fetchall()
                    for row in rows:
                        self.service.discard_staged_photo(
                            row["id"],
                            logout_user["id"],
                            self._session_fingerprint(token),
                        )
                self.sessions.logout(token)
                return HTTPStatus.OK, {"status": "signed_out"}
            if method == "POST" and path == "/api/session/role":
                user = self.sessions.assume_role(token, str(body.get("role") or ""))
                self.service.record_role_action(
                    user["id"], user["active_role"], method, path
                )
                return HTTPStatus.OK, {
                    "user": {key: value for key, value in user.items() if key != "active_role"},
                    "active_role": user["active_role"],
                }

            user = self.sessions.resolve(token)
            workspace_scope = self.service.workspace(
                user["workspace_id"],
                read_only=bool(user.get("workspace_read_only")),
            )
            workspace_scope.__enter__()
            active_role = str(user["active_role"])
            roles = {active_role}
            observer_mode = bool(user.get("workspace_read_only"))
            if (
                observer_mode
                and method != "GET"
                and path
                not in {
                    "/api/owner/workspace",
                }
            ):
                raise DomainError(
                    "This Administrator workspace is read-only for the Owner.",
                    status=403,
                )
            if method == "GET" and path == "/api/bootstrap":
                data = self.service.bootstrap(
                    reviewer_id=user["id"] if "reviewer" in roles else None
                )
                review_access = "reviewer" in roles or observer_mode
                administrative_access = bool(
                    roles & {"owner", "administrator"}
                )
                selected_workspace = next(
                    (
                        item
                        for item in self.service.users.list_workspaces()
                        if item["id"] == user["workspace_id"]
                    ),
                    None,
                )
                result = {
                    "app_version": data["app_version"],
                    "user": user,
                    "logic_model": data["logic_model"],
                    "model": data["model"],
                    "quality": data["quality"] if review_access else {"matches": 0, "total": 0, "percentage": None},
                    "review": data["review"] if review_access else [],
                    "evidence": data["evidence"] if review_access else [],
                    "evidence_review": data["evidence_review"] if review_access else [],
                    "elements": data["elements"] if review_access else [],
                    "counts": {
                        "review": data["counts"]["review"] if review_access else 0,
                        "evidence": (
                            data["counts"]["evidence"]
                            if roles & {"owner", "administrator", "reviewer"}
                            else 0
                        ),
                        "evidence_review": data["counts"]["evidence_review"] if review_access else 0,
                    },
                    "logic_model_versions": data["logic_model_versions"] if administrative_access else [],
                    "logic_model_draft": data["logic_model_draft"] if administrative_access and not observer_mode else None,
                    "users": self._administrator_users(user) if administrative_access else [],
                    "workspaces": self.service.users.list_workspaces() if "owner" in roles else [],
                    "workspace_id": user["workspace_id"],
                    "workspace_nickname": (
                        selected_workspace["nickname"] if selected_workspace else None
                    ),
                    "workspace_read_only": observer_mode,
                    "my_statements": (
                        self.service.reporter_statements(user["id"])
                        if "reporter" in roles else []
                    ),
                    "active_role": active_role,
                    "review_session": data["review_session"] if "reviewer" in roles else None,
                    "review_report": data["review_report"] if "reviewer" in roles else None,
                }
                return HTTPStatus.OK, result
            if method == "GET" and path == "/api/logic-model":
                try:
                    logic_model = self.service.logic_model()
                except Exception:
                    logic_model = self.service.empty_logic_model_state()
                return HTTPStatus.OK, {"logic_model": logic_model}
            version_match = re.fullmatch(r"/api/logic-model/([^/]+)", path)
            if method == "GET" and version_match:
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "logic_model": self.service.logic_model(version_match.group(1))
                }
            if method == "GET" and path == "/api/review":
                if observer_mode:
                    return HTTPStatus.OK, {
                        "statements": self.service.review_statements(),
                        "session": None,
                        "report": None,
                    }
                self._require_role(roles, "reviewer")
                return HTTPStatus.OK, self.service.open_review_session(user["id"])
            if method == "POST" and path == "/api/review/end":
                self._require_role(roles, "reviewer")
                return HTTPStatus.OK, {
                    "report": self.service.end_review_session(user["id"])
                }
            if method == "GET" and path == "/api/evidence":
                if not observer_mode:
                    self._require_role(roles, "reviewer")
                return HTTPStatus.OK, {"evidence": self.service.evidence()}
            if method == "GET" and path == "/api/evidence-assessments":
                if not observer_mode:
                    self._require_role(roles, "reviewer")
                return HTTPStatus.OK, {
                    "assessments": self.service.evidence_review_items(
                        None if observer_mode else user["id"]
                    )
                }
            if method == "GET" and path == "/api/my-statements":
                self._require_role(roles, "reporter")
                return HTTPStatus.OK, {
                    "statements": self.service.reporter_statements(user["id"])
                }
            if method == "POST" and path == "/api/statements":
                self._require_role(roles, "reporter")
                try:
                    statement = self.service.create_statement(
                        body.get("text", ""),
                        user["id"],
                        body.get("context", ""),
                        capture=body.get("capture"),
                        session_fingerprint=self._session_fingerprint(token),
                    )
                except DomainError:
                    capture = body.get("capture") or {}
                    if capture.get("method") == "photo" and capture.get("stage_id"):
                        self.service.discard_staged_photo(
                            str(capture["stage_id"]),
                            user["id"],
                            self._session_fingerprint(token),
                        )
                    raise
                return HTTPStatus.CREATED, {"statement": statement}
            if method == "POST" and path == "/api/media/voice/transcribe":
                self._require_role(roles, "reporter")
                return HTTPStatus.OK, {
                    "transcription": self.service.transcribe_voice(
                        str(body.get("data_base64") or ""),
                        str(body.get("media_type") or ""),
                    )
                }
            if method == "POST" and path == "/api/media/photo/stage":
                self._require_role(roles, "reporter")
                return HTTPStatus.CREATED, {
                    "photo": self.service.stage_photo(
                        str(body.get("data_base64") or ""),
                        str(body.get("source") or ""),
                        user["id"],
                        self._session_fingerprint(token),
                    )
                }
            discard_match = re.fullmatch(r"/api/media/photo/([^/]+)/discard", path)
            if method == "POST" and discard_match:
                self._require_role(roles, "reporter")
                self.service.discard_staged_photo(
                    discard_match.group(1),
                    user["id"],
                    self._session_fingerprint(token),
                )
                return HTTPStatus.OK, {"status": "discarded"}
            attachment_match = re.fullmatch(r"/api/attachments/([^/]+)", path)
            if method == "GET" and attachment_match:
                content = self.service.attachment_content(
                    attachment_match.group(1),
                    user["id"],
                    roles,
                    observer_mode=observer_mode,
                )
                return HTTPStatus.OK, {
                    "media_type": content["media_type"],
                    "data_base64": base64.b64encode(content["data"]).decode("ascii"),
                }

            context_match = re.fullmatch(r"/api/statements/([^/]+)/context", path)
            if method == "POST" and context_match:
                self._require_role(roles, "reporter")
                statement = self.service.update_statement_context(
                    context_match.group(1), body.get("context", ""), user["id"]
                )
                return HTTPStatus.OK, {"statement": statement}

            if method == "GET" and path == "/api/admin/users":
                self._require_admin(roles)
                return HTTPStatus.OK, {"users": self._administrator_users(user)}
            if method == "POST" and path == "/api/admin/users":
                self._require_admin(roles)
                if "owner" in roles and set(body.get("roles") or []) != {
                    "administrator"
                }:
                    raise DomainError(
                        "The Owner can create Administrators only.", status=403
                    )
                created = self.sessions.create_user(
                    body,
                    actor_id=user["id"],
                    workspace_id=user["workspace_id"],
                    allow_administrator="owner" in roles,
                )
                return HTTPStatus.CREATED, {"user": created}
            if method == "GET" and path == "/api/owner/workspaces":
                self._require_role(roles, "owner")
                return HTTPStatus.OK, {
                    "workspaces": self.service.users.list_workspaces(),
                    "workspace_id": user["workspace_id"],
                }
            if method == "POST" and path == "/api/owner/workspace":
                self._require_role(roles, "owner")
                updated = self.sessions.switch_workspace(
                    token, str(body.get("workspace_id") or "")
                )
                return HTTPStatus.OK, {
                    "workspace_id": updated["workspace_id"],
                    "workspaces": self.service.users.list_workspaces(),
                }
            if method == "GET" and path == "/api/admin/learning-policy":
                self._require_admin(roles)
                return HTTPStatus.OK, {"policy": self.service.learning_policy()}
            if method == "POST" and path == "/api/admin/learning-policy":
                self._require_admin(roles)
                return HTTPStatus.CREATED, {
                    "policy": self.service.publish_learning_policy(body, user["id"])
                }
            policy_match = re.fullmatch(
                r"/api/admin/learning-policy/([^/]+)/activate", path
            )
            if method == "POST" and policy_match:
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "policy": self.service.activate_learning_policy(
                        policy_match.group(1), user["id"]
                    )
                }
            if method == "GET" and path == "/api/admin/learning-examples":
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "examples": self.service.learning_examples()
                }
            if method == "POST" and path in {
                "/api/evidence-export",
                "/api/admin/evidence-export",
            }:
                if not roles & {"administrator", "reviewer"}:
                    raise DomainError("Your role does not permit this action.", status=403)
                export = self.service.export_evidence_pdf(user["id"])
                return HTTPStatus.OK, {
                    "filename": export["filename"],
                    "content_type": export["content_type"],
                    "data_base64": base64.b64encode(export["content"]).decode("ascii"),
                    "record_count": export["record_count"],
                }
            example_match = re.fullmatch(
                r"/api/admin/learning-examples/([^/]+)/eligibility", path
            )
            if method == "POST" and example_match:
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "example": self.service.set_learning_example_eligibility(
                        example_match.group(1), body, user["id"]
                    )
                }
            user_match = re.fullmatch(r"/api/admin/users/([^/]+)", path)
            if method == "POST" and user_match:
                self._require_admin(roles)
                previous = self.service.users.get_user(user_match.group(1))
                if "owner" in roles:
                    self._require_owner_administrator_target(previous)
                    body = {**body, "roles": previous["roles"]}
                updated = self.service.update_user(
                    user_match.group(1),
                    body,
                    actor_id=user["id"],
                    actor_is_owner="owner" in roles,
                )
                if (
                    updated["email"] != previous["email"]
                    or updated["roles"] != previous["roles"]
                ):
                    self.sessions.revoke_user(updated["id"])
                return HTTPStatus.OK, {
                    "user": updated
                }
            status_match = re.fullmatch(r"/api/admin/users/([^/]+)/(suspend|reactivate)", path)
            if method == "POST" and status_match:
                self._require_admin(roles)
                if "owner" in roles:
                    self._require_owner_administrator_target(
                        self.service.users.get_user(status_match.group(1))
                    )
                status = "suspended" if status_match.group(2) == "suspend" else "active"
                updated_user = self.service.set_user_status(
                    status_match.group(1),
                    status,
                    actor_id=user["id"],
                    actor_is_owner="owner" in roles,
                )
                return HTTPStatus.OK, {"user": updated_user}
            if method == "POST" and path == "/api/admin/logic-model-draft/start":
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "draft": self.service.start_logic_model_draft(user["id"])
                }
            if method == "POST" and path == "/api/admin/logic-model/preload-demo":
                self._require_admin(roles)
                return HTTPStatus.CREATED, {
                    "logic_model": self.service.preload_demo_logic_model(
                        user["id"], confirmed=body.get("confirmed") is True
                    )
                }
            if method == "POST" and path == "/api/admin/logic-model-draft/save":
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "draft": self.service.save_logic_model_draft(body, user["id"])
                }
            if method == "POST" and path == "/api/admin/logic-model-draft/cancel":
                self._require_admin(roles)
                self.service.cancel_logic_model_draft(
                    user["id"], confirmed=body.get("confirmed") is True
                )
                return HTTPStatus.OK, {"status": "cancelled"}
            if method == "GET" and path == "/api/admin/logic-model-draft/commit-summary":
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "summary": self.service.logic_model_commit_summary()
                }
            if method == "POST" and path == "/api/admin/logic-model-draft/commit":
                self._require_admin(roles)
                return HTTPStatus.OK, {
                    "logic_model": self.service.commit_logic_model_draft(
                        user["id"], confirmed=body.get("confirmed") is True
                    )
                }
            activate_match = re.fullmatch(r"/api/admin/logic-models/([^/]+)/activate", path)
            if method == "POST" and activate_match:
                self._require_admin(roles)
                if body.get("confirmed") is not True:
                    raise DomainError("Confirm that you want to use this Logic Model version.")
                return HTTPStatus.OK, {
                    "logic_model": self.service.activate_logic_model(
                        activate_match.group(1), user["id"], reason="restore",
                        discard_draft=body.get("discard_draft") is True,
                        activation_id=body.get("activation_id"),
                    )
                }

            feedback_match = re.fullmatch(r"/api/statements/([^/]+)/feedback", path)
            if method == "POST" and feedback_match:
                self._require_role(roles, "reviewer")
                statement = self.service.submit_feedback(
                    feedback_match.group(1),
                    body,
                    user["id"],
                    accept_as_evidence=body.get("accept_as_evidence") is not False,
                )
                return HTTPStatus.OK, {"statement": statement}

            accept_match = re.fullmatch(r"/api/statements/([^/]+)/accept", path)
            if method == "POST" and accept_match:
                self._require_role(roles, "reviewer")
                statement = self.service.accept_statement(
                    accept_match.group(1),
                    str(body.get("analysis_id") or ""),
                    user["id"],
                )
                return HTTPStatus.OK, {"statement": statement}

            irrelevant_match = re.fullmatch(r"/api/statements/([^/]+)/not-relevant", path)
            if method == "POST" and irrelevant_match:
                self._require_role(roles, "reviewer")
                result = self.service.mark_statement_irrelevant(
                    irrelevant_match.group(1),
                    str(body.get("analysis_id") or ""),
                    user["id"],
                    body.get("reason"),
                )
                return HTTPStatus.OK, {"statement": result}

            assessment_match = re.fullmatch(r"/api/evidence-assessments/([^/]+)/decision", path)
            if method == "POST" and assessment_match:
                self._require_role(roles, "reviewer")
                result = self.service.decide_evidence_assessment(
                    assessment_match.group(1), body, user["id"]
                )
                return HTTPStatus.OK, {"assessment": result}
            return HTTPStatus.NOT_FOUND, {"error": "API route not found."}
        except (DomainError, IdentityError) as exc:
            return exc.status, {"error": str(exc)}
        finally:
            if workspace_scope is not None:
                workspace_scope.__exit__(None, None, None)

    @staticmethod
    def _require_role(roles: set[str], role: str) -> None:
        if role not in roles:
            raise DomainError("Your role does not permit this action.", status=403)

    @staticmethod
    def _session_fingerprint(token: str | None) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _require_admin(roles: set[str]) -> None:
        if not roles & {"owner", "administrator"}:
            raise DomainError("Your role does not permit this action.", status=403)

    def _administrator_users(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        locks = self.sessions.user_lock_statuses()
        observer_mode = bool(actor.get("workspace_read_only"))
        observed_administrator_id = None
        if observer_mode:
            observed = next(
                (
                    item
                    for item in self.service.users.list_workspaces()
                    if item["id"] == actor["workspace_id"]
                ),
                None,
            )
            observed_administrator_id = (
                observed["owner_user_id"] if observed else None
            )
        if actor["active_role"] == "owner" and not observer_mode:
            users = self.service.list_administrators()
        else:
            users = self.service.list_users(
                include_user_id=(
                    observed_administrator_id
                    if observer_mode
                    else actor["id"]
                    if actor["active_role"] == "administrator"
                    else None
                ),
            )
        for user in users:
            lock = locks.get(user["id"])
            user["authentication_status"] = lock["status"] if lock else "available"
            user["locked_until"] = lock["locked_until"] if lock else None
        return users

    @staticmethod
    def _require_owner_administrator_target(target: dict[str, Any]) -> None:
        if "administrator" not in target["roles"] or "owner" in target["roles"]:
            raise DomainError(
                "The Owner can manage Administrators only.", status=403
            )


class MelvanRequestHandler(BaseHTTPRequestHandler):
    server: MelvanHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                status, payload = self.server.api.dispatch(
                    "GET", path, token=self._session_token()
                )
                self._json(status, payload)
            else:
                self._static(path)
        except Exception as exc:  # Return a bounded error while preserving server logs.
            self.log_error("Unhandled GET failure: %s", exc)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if not self._origin_is_allowed():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin request denied."})
                return
            payload = self._request_json()
            if path == "/api/auth/recovery":
                payload["request_source"] = self.client_address[0]
            status, response = self.server.api.dispatch(
                "POST", path, payload, token=self._session_token()
            )
            cookie: str | None = None
            if path in {"/api/session", "/api/session/password"} and status == HTTPStatus.OK:
                token = str(response.pop("token"))
                cookie = self._session_cookie(
                    token, max_age=self._cookie_max_age(response.get("expires_at"))
                )
            elif path == "/api/session/logout" and status == HTTPStatus.OK:
                cookie = self._session_cookie("", max_age=0)
            self._json(status, response, cookie=cookie)
        except DomainError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON."})
        except Exception as exc:
            self.log_error("Unhandled POST failure: %s", exc)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unexpected server error."})

    def _request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DomainError("Invalid request length.") from exc
        if length > 17_000_000:
            raise DomainError("Request is too large.", status=413)
        body = self.rfile.read(length)
        if not body:
            return {}
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise DomainError("Request body must be a JSON object.")
        return value

    def _session_token(self) -> str | None:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            cookies = SimpleCookie()
        if "melvan_session" in cookies:
            return cookies["melvan_session"].value or None
        value = self.headers.get("Authorization", "")
        if value.startswith("Bearer "):
            return value[7:].strip() or None
        return None

    def _session_cookie(self, token: str, *, max_age: int = 43_200) -> str:
        parts = [
            f"melvan_session={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
            f"Max-Age={max_age}",
        ]
        if self.server.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def _cookie_max_age(expires_at: Any) -> int:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            return 0

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        scheme = forwarded or ("https" if self.server.secure_cookies else "http")
        return origin.rstrip("/") == f"{scheme}://{self.headers.get('Host', '')}".rstrip("/")

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in candidate.parents and candidate != STATIC_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            candidate = STATIC_ROOT / "index.html"
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: dict[str, Any], *, cookie: str | None = None) -> None:
        content = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(self), microphone=(self), geolocation=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; img-src 'self' data: blob:; "
            "script-src 'self'; style-src 'self'; connect-src 'self'",
        )
        if self.server.secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")
