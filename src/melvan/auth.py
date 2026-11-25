"""Password authentication and revocable server-side sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import DatabaseAdapter
from .identity import IdentityError, UserDirectory


TEMPORARY_PASSWORD_LENGTH = 12
TEMPORARY_PASSWORD_DAYS = 7
PASSWORD_CHANGE_SESSION_MINUTES = 15
MAX_PASSWORD_ATTEMPTS = 3
LOCK_HOURS = 24
GENERIC_LOGIN_ERROR = "Unable to complete sign in."
SESSION_CHOICES = {"midnight", "7_days", "28_days"}
USER_GUIDE_VERSION = "2026-08-18"
COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "qwerty123",
    "letmein",
    "welcome1",
    "admin123",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class SessionStore:
    """Password credentials and sessions persisted in MELvan's database."""

    def __init__(
        self,
        users: UserDirectory,
        database: DatabaseAdapter,
        *,
        pepper: str,
    ):
        if len(pepper) < 16:
            raise ValueError("Authentication pepper must contain at least 16 characters.")
        self.users = users
        self.database = database
        self.pepper = pepper.encode("utf-8")

    def create_user(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
        workspace_id: str,
        allow_administrator: bool,
    ) -> dict[str, Any]:
        return self.users.create_user(
            payload,
            actor_id=actor_id,
            workspace_id=workspace_id,
            allow_administrator=allow_administrator,
        )

    def issue_temporary_password(
        self,
        user_id: str,
        *,
        actor_id: str,
        action: str,
    ) -> dict[str, str]:
        if action not in {"create", "reset"}:
            raise IdentityError("Choose create or reset for the temporary password.")
        user = self.users.get_user(user_id)
        if user["status"] != "active" or user.get("effective_status") != "active":
            raise IdentityError(
                "Temporary passwords can be issued only for active accounts.", status=409
            )
        expected_action = (
            "create"
            if user["password_state"] == "bootstrap_required"
            or int(user.get("total_logins") or 0) == 0
            else "reset"
        )
        if action != expected_action:
            raise IdentityError(
                f"This account requires the {expected_action} password action.", status=409
            )
        temporary = self._temporary_password()
        salt, password_hash = self._password_material(temporary)
        expires_at = timestamp(utc_now() + timedelta(days=TEMPORARY_PASSWORD_DAYS))
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_salt = ?, password_hash = ?,
                    password_state = 'password_change_required',
                    temporary_password_expires_at = ?,
                    recovery_salt = NULL, recovery_hash = NULL,
                    recovery_expires_at = NULL, failed_login_attempts = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (salt, password_hash, expires_at, timestamp(utc_now()), user_id),
            )
            self._revoke_user_in_connection(connection, user_id)
            self._clear_lock(connection, user_id)
            self._event(
                connection,
                "temporary_password_issued",
                user_id,
                "revealed_once",
                {
                    "purpose": "initial_setup" if action == "create" else "recovery",
                    "action": action,
                    "actor_id": actor_id,
                    "expires_at": expires_at,
                },
            )
        return {
            "temporary_password": temporary,
            "expires_at": expires_at,
            "action": action,
        }

    def record_temporary_password_handling(
        self,
        user_id: str,
        *,
        actor_id: str,
        action: str,
        channel: str,
    ) -> None:
        if action not in {"create", "reset"}:
            raise IdentityError("Choose create or reset for the temporary password.")
        if channel not in {"copy", "gmail", "outlook", "default_mail_app"}:
            raise IdentityError("Choose a supported temporary-password sharing option.")
        self.users.get_user(user_id)
        with self.database.connection() as connection:
            self._event(
                connection,
                "temporary_password_handled",
                user_id,
                "succeeded",
                {
                    "action": action,
                    "actor_id": actor_id,
                    "channel": channel,
                },
            )

    def _issue_owner_bootstrap_password(self, user_id: str) -> dict[str, str]:
        user = self.users.get_user(user_id)
        if user["status"] != "active":
            raise IdentityError("Owner bootstrap is available only for an active user.", status=409)
        temporary = self._temporary_password()
        salt, password_hash = self._password_material(temporary)
        expires_at = timestamp(utc_now() + timedelta(days=TEMPORARY_PASSWORD_DAYS))
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_salt = ?, password_hash = ?,
                    password_state = 'password_change_required',
                    temporary_password_expires_at = ?,
                    recovery_salt = NULL, recovery_hash = NULL,
                    recovery_expires_at = NULL, failed_login_attempts = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (salt, password_hash, expires_at, timestamp(utc_now()), user_id),
            )
            self._revoke_user_in_connection(connection, user_id)
            self._clear_lock(connection, user_id)
            self._event(
                connection,
                "temporary_password_issued",
                user_id,
                "revealed_once",
                {
                    "purpose": "owner_bootstrap",
                    "actor_id": "deployment-operator",
                    "expires_at": expires_at,
                },
            )
        return {"temporary_password": temporary, "expires_at": expires_at}

    def bootstrap_owner(self) -> dict[str, str]:
        owner = self.users.owner()
        if owner["password_state"] != "bootstrap_required":
            raise IdentityError("The Owner credential has already been initialized.", status=409)
        return self._issue_owner_bootstrap_password(owner["id"])

    def login(
        self,
        email: str,
        password: str,
        *,
        duration_choice: str = "midnight",
        timezone_name: str = "UTC",
    ) -> tuple[str, dict[str, Any], str | None, dict[str, Any]]:
        choice, zone, requested_expiry = self._session_expiry(
            duration_choice, timezone_name
        )
        now = utc_now()
        user = self.users.find_active_by_email(email)
        if not user:
            self._authentication_event("password_login", None, "denied", {})
            raise IdentityError(GENERIC_LOGIN_ERROR, status=401)

        denied = False
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            locked = self._user_is_locked(connection, user["id"], now)
            password_match = self._stored_password_matches(row, password, now)
            if locked or not password_match:
                self._record_failed_login(connection, user["id"], now)
                self._event(connection, "password_login", user["id"], "denied", {})
                denied = True
            elif row["password_state"] != "active":
                restricted_expiry = min(
                    now + timedelta(minutes=PASSWORD_CHANGE_SESSION_MINUTES),
                    requested_expiry,
                )
                setup_purpose = self._password_setup_purpose(row)
                token = self._create_session(
                    connection,
                    user,
                    active_role=None,
                    workspace_id=user["workspace_id"],
                    session_kind="password_change",
                    password_setup_purpose=setup_purpose,
                    duration_choice=choice,
                    timezone_name=zone,
                    expires_at=restricted_expiry,
                    requested_expires_at=requested_expiry,
                )
                self._event(
                    connection,
                    "password_login",
                    user["id"],
                    "password_change_required",
                    {
                        "password_setup_purpose": setup_purpose,
                    },
                )
                session_meta = {
                    "expires_at": timestamp(restricted_expiry),
                    "requested_expires_at": timestamp(requested_expiry),
                    "requires_password_change": True,
                    "password_setup_purpose": setup_purpose,
                    "requires_user_guide_acknowledgement": (
                        setup_purpose == "initial_setup"
                    ),
                }
                return token, user, None, session_meta

            else:
                active_role = user["roles"][0] if len(user["roles"]) == 1 else None
                token = self._create_session(
                    connection,
                    user,
                    active_role=active_role,
                    workspace_id=user["workspace_id"],
                    session_kind="full",
                    password_setup_purpose=None,
                    duration_choice=choice,
                    timezone_name=zone,
                    expires_at=requested_expiry,
                    requested_expires_at=requested_expiry,
                )
                connection.execute(
                    "UPDATE users SET failed_login_attempts = 0 WHERE id = ?", (user["id"],)
                )
                self._clear_lock(connection, user["id"])
                self._event(connection, "password_login", user["id"], "succeeded", {})

        if denied:
            raise IdentityError(GENERIC_LOGIN_ERROR, status=401)
        user = self.users.record_login(user["id"])
        return token, user, active_role, {
            "expires_at": timestamp(requested_expiry),
            "requested_expires_at": timestamp(requested_expiry),
            "requires_password_change": False,
        }

    def change_password(
        self,
        token: str | None,
        password: str,
        confirmation: str,
        *,
        user_guide_acknowledged: bool = False,
    ) -> tuple[str, dict[str, Any], str | None, dict[str, Any]]:
        if password != confirmation:
            raise IdentityError("The passwords do not match.")
        self._validate_permanent_password(password)
        session = self._session_row(token, allow_password_change=True)
        if session["session_kind"] != "password_change":
            raise IdentityError("This session does not require a password change.", status=409)
        setup_purpose = str(session.get("password_setup_purpose") or "recovery")
        requires_guide = setup_purpose == "initial_setup"
        if requires_guide and user_guide_acknowledged is not True:
            raise IdentityError(
                "Acknowledge that you have read the MELVan User Guide to continue."
            )
        now = utc_now()
        requested_expiry = datetime.fromisoformat(session["requested_expires_at"])
        if requested_expiry <= now:
            raise IdentityError("Sign in again to continue.", status=401)
        salt, password_hash = self._password_material(password)
        user = self.users.get_user(session["user_id"])
        active_role = user["roles"][0] if len(user["roles"]) == 1 else None
        new_token = secrets.token_urlsafe(32)
        new_hash = self._token_hash(new_token)
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_salt = ?, password_hash = ?, password_state = 'active',
                    temporary_password_expires_at = NULL,
                    recovery_salt = NULL, recovery_hash = NULL,
                    recovery_expires_at = NULL, failed_login_attempts = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (salt, password_hash, timestamp(now), user["id"]),
            )
            self._revoke_user_in_connection(connection, user["id"])
            connection.execute(
                """
                INSERT INTO authenticated_sessions
                    (token_hash, user_id, active_role, workspace_id, session_kind,
                     duration_choice, timezone_name, requested_expires_at,
                     created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, 'full', ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_hash,
                    user["id"],
                    active_role,
                    user["workspace_id"],
                    session["duration_choice"],
                    session["timezone_name"],
                    timestamp(requested_expiry),
                    timestamp(now),
                    timestamp(requested_expiry),
                    timestamp(now),
                ),
            )
            self._clear_lock(connection, user["id"])
            self._event(
                connection,
                "password_changed",
                user["id"],
                "succeeded",
                {
                    "password_setup_purpose": setup_purpose,
                    "user_guide_acknowledged": requires_guide,
                    "user_guide_version": USER_GUIDE_VERSION if requires_guide else None,
                },
            )
        user = self.users.record_login(user["id"])
        return new_token, user, active_role, {
            "expires_at": timestamp(requested_expiry),
            "requires_password_change": False,
            "requires_user_guide_acknowledgement": False,
        }

    def resolve(
        self,
        token: str | None,
        *,
        require_active_role: bool = True,
        allow_password_change: bool = False,
    ) -> dict[str, Any]:
        session = self._session_row(token, allow_password_change=allow_password_change)
        if session["session_kind"] == "password_change":
            if not allow_password_change:
                raise IdentityError("Create your password to continue.", status=409)
            return {
                "id": session["user_id"],
                "active_role": None,
                "workspace_id": session["workspace_id"],
                "requires_password_change": True,
                "password_setup_purpose": session.get("password_setup_purpose"),
                "requires_user_guide_acknowledgement": (
                    session.get("password_setup_purpose") == "initial_setup"
                ),
            }
        user = self.users.get_user(session["user_id"])
        active_role = session["active_role"]
        if active_role and active_role not in user["roles"]:
            self.logout(token)
            raise IdentityError("Sign in to continue.", status=401)
        if require_active_role and not active_role:
            raise IdentityError("Choose what you are here to do.", status=409)
        result = dict(user)
        home_workspace_id = result["workspace_id"]
        result["active_role"] = active_role
        result["workspace_id"] = session["workspace_id"]
        result["home_workspace_id"] = home_workspace_id
        result["workspace_read_only"] = bool(
            active_role == "owner" and session["workspace_id"] != home_workspace_id
        )
        result["session_expires_at"] = session["expires_at"]
        return result

    def assume_role(self, token: str | None, role: str) -> dict[str, Any]:
        user = self.resolve(token, require_active_role=False)
        value = str(role or "").lower()
        if user["active_role"] is not None:
            raise IdentityError("Sign out and sign in again to change roles.", status=409)
        if value not in user["roles"]:
            raise IdentityError("That role is not assigned to this user.", status=403)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE authenticated_sessions SET active_role = ? WHERE token_hash = ?",
                (value, self._token_hash(str(token or ""))),
            )
        user["active_role"] = value
        return user

    def switch_workspace(self, token: str | None, workspace_id: str) -> dict[str, Any]:
        user = self.resolve(token, require_active_role=False)
        if "owner" not in user["roles"] or user["active_role"] != "owner":
            raise IdentityError("Your role does not permit this action.", status=403)
        allowed = {item["id"] for item in self.users.list_workspaces()}
        if workspace_id not in allowed:
            raise IdentityError("Workspace not found.", status=404)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE authenticated_sessions SET workspace_id = ? WHERE token_hash = ?",
                (workspace_id, self._token_hash(str(token or ""))),
            )
            self._event(
                connection,
                "owner_workspace_switched",
                user["id"],
                "succeeded",
                {"workspace_id": workspace_id},
            )
        user["workspace_id"] = workspace_id
        user["workspace_read_only"] = bool(
            user["active_role"] == "owner"
            and workspace_id != user.get("home_workspace_id")
        )
        return user

    def logout(self, token: str | None) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE authenticated_sessions SET revoked_at = ? WHERE token_hash = ?",
                (timestamp(utc_now()), self._token_hash(str(token or ""))),
            )

    def revoke_user(self, user_id: str) -> None:
        with self.database.connection() as connection:
            self._revoke_user_in_connection(connection, user_id)

    def user_lock_statuses(self) -> dict[str, dict[str, str]]:
        now = utc_now()
        statuses: dict[str, dict[str, str]] = {}
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT user_id, locked_until, cleared_at FROM authentication_locks"
            ).fetchall()
            for row in rows:
                if row["cleared_at"] is None and row["locked_until"] > timestamp(now):
                    statuses[row["user_id"]] = {
                        "status": "temporary_lockout",
                        "locked_until": row["locked_until"],
                    }
                elif row["cleared_at"] is None:
                    self._clear_lock(connection, row["user_id"])
        return statuses

    def _session_row(self, token: str | None, *, allow_password_change: bool) -> Any:
        now = utc_now()
        token_hash = self._token_hash(str(token or ""))
        with self.database.connection() as connection:
            session = connection.execute(
                "SELECT * FROM authenticated_sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not session or session["revoked_at"] or session["expires_at"] <= timestamp(now):
                raise IdentityError("Sign in to continue.", status=401)
            user = connection.execute(
                """
                SELECT u.status, w.status AS workspace_status
                FROM users u
                JOIN workspace_memberships wm ON wm.user_id = u.id
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE u.id = ?
                """,
                (session["user_id"],),
            ).fetchone()
            if (
                not user
                or user["status"] != "active"
                or user["workspace_status"] != "active"
            ):
                connection.execute(
                    "UPDATE authenticated_sessions SET revoked_at = ? WHERE token_hash = ?",
                    (timestamp(now), token_hash),
                )
                raise IdentityError("Sign in to continue.", status=401)
            if session["session_kind"] == "password_change" and not allow_password_change:
                raise IdentityError("Create your password to continue.", status=409)
            connection.execute(
                "UPDATE authenticated_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (timestamp(now), token_hash),
            )
            return dict(session)

    def _create_session(
        self,
        connection: Any,
        user: dict[str, Any],
        *,
        active_role: str | None,
        workspace_id: str,
        session_kind: str,
        password_setup_purpose: str | None,
        duration_choice: str,
        timezone_name: str,
        expires_at: datetime,
        requested_expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO authenticated_sessions
                (token_hash, user_id, active_role, workspace_id, session_kind,
                 password_setup_purpose, duration_choice, timezone_name,
                 requested_expires_at, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._token_hash(token),
                user["id"],
                active_role,
                workspace_id,
                session_kind,
                password_setup_purpose,
                duration_choice,
                timezone_name,
                timestamp(requested_expires_at),
                timestamp(now),
                timestamp(expires_at),
                timestamp(now),
            ),
        )
        return token

    @staticmethod
    def _password_setup_purpose(row: Any) -> str:
        if row["password_state"] == "bootstrap_required":
            return "initial_setup"
        if row["password_state"] == "password_change_required" and int(
            row["total_logins"] or 0
        ) == 0:
            return "initial_setup"
        return "recovery"

    def _record_failed_login(self, connection: Any, user_id: str, now: datetime) -> None:
        connection.execute(
            "UPDATE users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )
        attempts = connection.execute(
            "SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)
        ).fetchone()["failed_login_attempts"]
        if int(attempts) < MAX_PASSWORD_ATTEMPTS:
            return
        locked_until = timestamp(now + timedelta(hours=LOCK_HOURS))
        connection.execute(
            """
            INSERT INTO authentication_locks
                (user_id, locked_until, reason, created_at, cleared_at)
            VALUES (?, ?, 'three_failed_passwords', ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                locked_until = excluded.locked_until,
                reason = excluded.reason,
                created_at = excluded.created_at,
                cleared_at = NULL
            """,
            (user_id, locked_until, timestamp(now)),
        )
        self._event(
            connection,
            "account_login_locked",
            user_id,
            "locked",
            {"locked_until": locked_until},
        )

    @staticmethod
    def _user_is_locked(connection: Any, user_id: str, now: datetime) -> bool:
        row = connection.execute(
            "SELECT locked_until, cleared_at FROM authentication_locks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row or row["cleared_at"] is not None:
            return False
        if row["locked_until"] <= timestamp(now):
            SessionStore._clear_lock(connection, user_id)
            return False
        return True

    @staticmethod
    def _clear_lock(connection: Any, user_id: str) -> None:
        connection.execute(
            "UPDATE authentication_locks SET cleared_at = ? WHERE user_id = ? AND cleared_at IS NULL",
            (timestamp(utc_now()), user_id),
        )
        connection.execute(
            "UPDATE users SET failed_login_attempts = 0 WHERE id = ?", (user_id,)
        )

    def _stored_password_matches(self, row: Any, password: str, now: datetime) -> bool:
        if not row["password_hash"] or not row["password_salt"]:
            return False
        if (
            row["password_state"] == "password_change_required"
            and (
                not row["temporary_password_expires_at"]
                or row["temporary_password_expires_at"] <= timestamp(now)
            )
        ):
            return False
        return self._password_matches(row["password_salt"], row["password_hash"], password)

    def _password_material(self, password: str) -> tuple[str, str]:
        salt = secrets.token_bytes(16)
        return salt.hex(), self._password_hash(password, salt)

    def _password_hash(self, password: str, salt: bytes) -> str:
        material = password.encode("utf-8") + b"\x00" + self.pepper
        return hashlib.scrypt(material, salt=salt, n=2**14, r=8, p=1).hex()

    def _password_matches(self, salt_hex: str, expected: str, password: str) -> bool:
        actual = self._password_hash(str(password or ""), bytes.fromhex(salt_hex))
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def _temporary_password() -> str:
        groups = (
            string.ascii_uppercase,
            string.ascii_lowercase,
            string.digits,
            "!@#$%^&*()-_=+",
        )
        characters = [secrets.choice(group) for group in groups]
        alphabet = "".join(groups)
        characters.extend(
            secrets.choice(alphabet)
            for _ in range(TEMPORARY_PASSWORD_LENGTH - len(characters))
        )
        secrets.SystemRandom().shuffle(characters)
        return "".join(characters)

    @staticmethod
    def _validate_permanent_password(password: str) -> None:
        value = str(password or "")
        valid = (
            len(value) >= 8
            and any(character.isupper() for character in value)
            and any(character.islower() for character in value)
            and any(character.isdigit() for character in value)
            and any(not character.isalnum() for character in value)
        )
        if not valid:
            raise IdentityError(
                "Use at least 8 characters with an uppercase letter, lowercase "
                "letter, number, and symbol."
            )
        if value.lower() in COMMON_PASSWORDS:
            raise IdentityError("Choose a password that is not commonly used.")

    @staticmethod
    def _session_expiry(
        duration_choice: str, timezone_name: str
    ) -> tuple[str, str, datetime]:
        choice = str(duration_choice or "midnight")
        if choice not in SESSION_CHOICES:
            raise IdentityError("Choose midnight tonight, 7 days, or 28 days.")
        zone_name = str(timezone_name or "UTC")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError as exc:
            raise IdentityError("Choose a valid timezone.") from exc
        now = utc_now()
        if choice == "midnight":
            local_now = now.astimezone(zone)
            tomorrow = local_now.date() + timedelta(days=1)
            expires = datetime.combine(tomorrow, time.min, zone).astimezone(timezone.utc)
        elif choice == "7_days":
            expires = now + timedelta(days=7)
        else:
            expires = now + timedelta(days=28)
        return choice, zone_name, expires

    def _token_hash(self, token: str) -> str:
        return hmac.new(self.pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _revoke_user_in_connection(
        connection: Any, user_id: str, *, except_token_hash: str | None = None
    ) -> None:
        now = timestamp(utc_now())
        if except_token_hash:
            connection.execute(
                """
                UPDATE authenticated_sessions SET revoked_at = ?
                WHERE user_id = ? AND token_hash <> ? AND revoked_at IS NULL
                """,
                (now, user_id, except_token_hash),
            )
        else:
            connection.execute(
                """
                UPDATE authenticated_sessions SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (now, user_id),
            )

    def _authentication_event(
        self,
        event_type: str,
        user_id: str | None,
        outcome: str,
        details: dict[str, Any],
    ) -> None:
        with self.database.connection() as connection:
            self._event(connection, event_type, user_id, outcome, details)

    @staticmethod
    def _event(
        connection: Any,
        event_type: str,
        user_id: str | None,
        outcome: str,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO authentication_events
                (id, event_type, user_id, challenge_id, outcome, details_json, created_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                f"aevt-{uuid.uuid4().hex}",
                event_type,
                user_id,
                outcome,
                json.dumps(details, ensure_ascii=True),
                timestamp(utc_now()),
            ),
        )
