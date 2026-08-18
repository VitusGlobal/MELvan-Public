"""WSGI entry point for hosted MELvan deployments."""

from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .auth import SessionStore, build_email_sender
from .config import Settings
from .identity import UserDirectory
from .media import (
    build_attachment_store,
    build_photo_text_extractor,
    build_speech_transcriber,
)
from .model import build_model_adapter
from .relational import build_database
from .service import MelvanService
from .web import APIApplication, STATIC_ROOT


StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class ProductionApplication:
    def __init__(self, api: APIApplication):
        self.api = api

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = urlparse(str(environ.get("PATH_INFO", "/"))).path
        if method == "POST" and not self._origin_allowed(environ):
            return self._json(start_response, HTTPStatus.FORBIDDEN, {"error": "Cross-origin request denied."})
        if path.startswith("/api/"):
            return self._api(environ, start_response, method, path)
        return self._static(start_response, path)

    def _api(
        self,
        environ: dict[str, Any],
        start_response: StartResponse,
        method: str,
        path: str,
    ) -> Iterable[bytes]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return self._json(start_response, HTTPStatus.BAD_REQUEST, {"error": "Invalid request length."})
        if length > 17_000_000:
            return self._json(start_response, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request is too large."})
        payload: dict[str, Any] = {}
        if length:
            try:
                parsed = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(start_response, HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON."})
            if not isinstance(parsed, dict):
                return self._json(start_response, HTTPStatus.BAD_REQUEST, {"error": "Request body must be a JSON object."})
            payload = parsed
        if path == "/api/auth/recovery":
            payload["request_source"] = str(
                environ.get("HTTP_X_FORWARDED_FOR") or environ.get("REMOTE_ADDR") or ""
            ).split(",", 1)[0].strip()
        status, response = self.api.dispatch(
            method,
            path,
            payload,
            token=self._session_token(str(environ.get("HTTP_COOKIE", ""))),
        )
        cookie = None
        if (
            method == "POST"
            and path in {"/api/session", "/api/session/password"}
            and status == HTTPStatus.OK
        ):
            cookie = self._session_cookie(
                str(response.pop("token")),
                max_age=self._cookie_max_age(response.get("expires_at")),
            )
        elif method == "POST" and path == "/api/session/logout" and status == HTTPStatus.OK:
            cookie = self._session_cookie("", max_age=0)
        return self._json(start_response, status, response, cookie=cookie)

    def _static(self, start_response: StartResponse, request_path: str) -> Iterable[bytes]:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        root = STATIC_ROOT.resolve()
        if root not in candidate.parents and candidate != root:
            return self._json(start_response, HTTPStatus.NOT_FOUND, {"error": "Not found."})
        if not candidate.is_file():
            candidate = STATIC_ROOT / "index.html"
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        headers = [
            ("Content-Type", f"{content_type}; charset=utf-8"),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "no-store"),
        ]
        headers.extend(self._security_headers())
        start_response("200 OK", headers)
        return [content]

    def _json(
        self,
        start_response: StartResponse,
        status: int,
        payload: dict[str, Any],
        *,
        cookie: str | None = None,
    ) -> Iterable[bytes]:
        content = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(content))),
        ]
        if cookie:
            headers.append(("Set-Cookie", cookie))
        headers.extend(self._security_headers())
        start_response(f"{int(status)} {HTTPStatus(int(status)).phrase}", headers)
        return [content]

    @staticmethod
    def _session_token(raw_cookie: str) -> str | None:
        cookies = SimpleCookie()
        try:
            cookies.load(raw_cookie)
        except Exception:
            return None
        return cookies["melvan_session"].value if "melvan_session" in cookies else None

    @staticmethod
    def _session_cookie(token: str, *, max_age: int = 43_200) -> str:
        return (
            f"melvan_session={token}; Path=/; HttpOnly; Secure; SameSite=Strict; "
            f"Max-Age={max_age}"
        )

    @staticmethod
    def _cookie_max_age(expires_at: Any) -> int:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _origin_allowed(environ: dict[str, Any]) -> bool:
        origin = str(environ.get("HTTP_ORIGIN") or "")
        if not origin:
            return True
        host = str(environ.get("HTTP_HOST") or "")
        forwarded = str(environ.get("HTTP_X_FORWARDED_PROTO") or "https").split(",", 1)[0].strip()
        return origin.rstrip("/") == f"{forwarded}://{host}".rstrip("/")

    @staticmethod
    def _security_headers() -> list[tuple[str, str]]:
        return [
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            (
                "Permissions-Policy",
                "camera=(self), microphone=(self), geolocation=()",
            ),
            (
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                "form-action 'self'; object-src 'none'; img-src 'self' data: blob:; "
                "script-src 'self'; style-src 'self'; connect-src 'self'",
            ),
            ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
        ]


def create_application(settings: Settings | None = None) -> ProductionApplication:
    selected = settings or Settings.from_env()
    if selected.environment != "hosted":
        raise ValueError("The production server requires MELVAN_ENVIRONMENT=hosted.")
    selected.validate_hosted()
    database = build_database(selected)
    fixture = Path(__file__).resolve().parent / "fixtures" / "logic_model.json"
    users = UserDirectory(
        database,
        legacy_path=(
            selected.users_path or selected.database_path.parent / "users.json"
            if selected.deployment_profile != "production"
            else None
        ),
        initial_owner_email=selected.initial_admin_email,
    )
    model = build_model_adapter(selected)
    service = MelvanService(
        database,
        model,
        fixture,
        user_directory=users,
        initial_admin_email=selected.initial_admin_email,
        attachment_store=build_attachment_store(selected),
        speech_transcriber=build_speech_transcriber(selected),
        photo_text_extractor=build_photo_text_extractor(selected, model),
    )
    service.initialize()
    sessions = SessionStore(
        service.users,
        database,
        pepper=str(selected.auth_pepper),
        email_sender=build_email_sender(selected),
    )
    return ProductionApplication(APIApplication(service, sessions=sessions))
