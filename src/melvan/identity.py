"""Relational users, roles, and isolated MELvan workspaces."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import DatabaseAdapter, OWNER_WORKSPACE_ID


ROLES = {"owner", "administrator", "reporter", "reviewer"}
MAX_WORKSPACE_NICKNAME_LENGTH = 40


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IdentityError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


class UserDirectory:
    """Database-backed identity directory with a one-time JSON migration source."""

    def __init__(
        self,
        database: DatabaseAdapter,
        *,
        legacy_path: Path | None = None,
        initial_owner_email: str | None = None,
    ):
        self.database = database
        self.path = legacy_path
        self.initial_owner_email = (
            self._normalize_email(initial_owner_email)
            if initial_owner_email
            else None
        )

    def initialize(self) -> None:
        with self.database.connection() as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                self._validate_invariants(connection)
                return
            legacy_users = self._legacy_users()
            now = utc_now()
            owner_source = next(
                (
                    user
                    for user in legacy_users
                    if (
                        self.initial_owner_email
                        and self._normalize_email(user.get("email"))
                        == self.initial_owner_email
                    )
                    or "administrator" in set(user.get("roles") or [])
                ),
                None,
            )
            if owner_source is None and not self.initial_owner_email:
                raise IdentityError(
                    "MELVAN_INITIAL_ADMIN_EMAIL is required to initialize the Owner.",
                    status=503,
                )
            owner_email = (
                self.initial_owner_email
                or self._normalize_email((owner_source or {}).get("email"))
            )
            owner_id = str((owner_source or {}).get("id") or "user-admin")
            connection.execute(
                """
                UPDATE workspaces
                SET owner_user_id = ?, created_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (owner_id, owner_id, now, OWNER_WORKSPACE_ID),
            )
            if owner_source is None:
                owner_source = {
                    "id": owner_id,
                    "first_name": "MELvan",
                    "last_name": "Owner",
                    "email": owner_email,
                    "roles": ["administrator"],
                    "status": "active",
                    "last_login_at": None,
                    "total_logins": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                legacy_users.insert(0, owner_source)

            for source in legacy_users:
                is_owner = str(source.get("id")) == owner_id
                user_id = str(source.get("id") or f"user-{uuid.uuid4().hex[:12]}")
                email = (
                    owner_email
                    if is_owner
                    else self._normalize_email(source.get("email"))
                )
                first_name = " ".join(str(source.get("first_name") or "MELvan").split())
                last_name = " ".join(
                    str(source.get("last_name") or ("Owner" if is_owner else "User")).split()
                )
                created_at = str(source.get("created_at") or now)
                updated_at = str(source.get("updated_at") or now)
                connection.execute(
                    """
                    INSERT INTO users
                        (id, first_name, last_name, email, status, password_state,
                         last_login_at, total_logins, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'bootstrap_required', ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        first_name,
                        last_name,
                        email,
                        source.get("status") if source.get("status") in {"active", "suspended"} else "active",
                        source.get("last_login_at"),
                        max(0, int(source.get("total_logins") or 0)),
                        created_at,
                        updated_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO workspace_memberships
                        (user_id, workspace_id, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, OWNER_WORKSPACE_ID, owner_id, created_at),
                )
                roles = (
                    {"owner", "administrator"}
                    | (set(source.get("roles") or []) & {"reporter", "reviewer"})
                    if is_owner
                    else (set(source.get("roles") or []) & {"reporter", "reviewer"})
                )
                if not roles and not is_owner:
                    roles = {"reporter"}
                for role in sorted(roles):
                    connection.execute(
                        """
                        INSERT INTO user_roles
                            (user_id, workspace_id, role, created_by, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (user_id, OWNER_WORKSPACE_ID, role, owner_id, created_at),
                    )
            connection.execute(
                """
                UPDATE authenticated_sessions
                SET workspace_id = (
                    SELECT workspace_id FROM workspace_memberships
                    WHERE user_id = authenticated_sessions.user_id
                )
                WHERE workspace_id IS NULL
                """
            )
            self._validate_invariants(connection)

    def _legacy_users(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.is_file():
            return []
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IdentityError(
                f"The legacy user directory at {self.path} is unreadable: {exc}", status=503
            ) from exc
        users = document.get("users")
        if not isinstance(users, list):
            raise IdentityError(
                "The legacy user directory does not contain a valid users list.", status=503
            )
        return [dict(item) for item in users if isinstance(item, dict)]

    @staticmethod
    def _validate_invariants(connection: Any) -> None:
        owners = connection.execute(
            "SELECT user_id, workspace_id FROM user_roles WHERE role = 'owner'"
        ).fetchall()
        if len(owners) != 1:
            raise IdentityError("MELvan must contain exactly one Owner.", status=503)
        owner_workspace = connection.execute(
            "SELECT id, owner_user_id FROM workspaces WHERE workspace_type = 'owner'"
        ).fetchall()
        if len(owner_workspace) != 1 or owner_workspace[0]["owner_user_id"] != owners[0]["user_id"]:
            raise IdentityError("The Owner workspace is not configured correctly.", status=503)

    def find_active_by_email(self, email: str) -> dict[str, Any] | None:
        try:
            normalized = self._normalize_email(email)
        except IdentityError:
            return None
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM users u
                JOIN workspace_memberships wm ON wm.user_id = u.id
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE lower(u.email) = lower(?)
                  AND u.status = 'active'
                  AND w.status = 'active'
                """,
                (normalized,),
            ).fetchone()
            return self._hydrate(connection, row) if row else None

    def get_user(self, user_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                raise IdentityError("User not found.", status=404)
            return self._hydrate(connection, row)

    def list_users(
        self,
        workspace_id: str,
        *,
        include_administrators: bool = False,
        include_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            if include_administrators:
                rows = connection.execute(
                    """
                    SELECT DISTINCT u.*
                    FROM users u
                    JOIN workspace_memberships wm ON wm.user_id = u.id
                    LEFT JOIN user_roles ur ON ur.user_id = u.id
                    WHERE wm.workspace_id = ? OR ur.role = 'administrator' OR ur.role = 'owner'
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT u.*
                    FROM users u
                    JOIN workspace_memberships wm ON wm.user_id = u.id
                    WHERE wm.workspace_id = ?
                      AND (
                        u.id = ?
                        OR NOT EXISTS (
                          SELECT 1 FROM user_roles ur
                          WHERE ur.user_id = u.id
                            AND ur.role IN ('owner', 'administrator')
                        )
                      )
                    """,
                    (workspace_id, include_user_id or ""),
                ).fetchall()
            users = [self._hydrate(connection, row) for row in rows]
        return sorted(
            users,
            key=lambda item: (
                "owner" not in item["roles"],
                "administrator" not in item["roles"],
                item["last_name"].lower(),
                item["first_name"].lower(),
            ),
        )

    def list_administrators(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT u.*
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN workspaces w
                  ON w.owner_user_id = u.id
                 AND w.workspace_type = 'administrator'
                WHERE ur.role = 'administrator'
                """
            ).fetchall()
            users = [self._hydrate(connection, row) for row in rows]
        return sorted(
            users,
            key=lambda item: (
                item["workspace_nickname"].lower(),
                item["last_name"].lower(),
                item["first_name"].lower(),
            ),
        )

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT w.*, u.first_name, u.last_name, u.email
                FROM workspaces w
                JOIN users u ON u.id = w.owner_user_id
                ORDER BY CASE w.workspace_type WHEN 'owner' THEN 0 ELSE 1 END,
                         lower(w.nickname), w.created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_user(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
        workspace_id: str,
        allow_administrator: bool = False,
        credential: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        first_name, last_name, email, roles = self._profile(
            payload, allow_administrator=allow_administrator
        )
        now = utc_now()
        user_id = f"user-{uuid.uuid4().hex[:12]}"
        creates_workspace = "administrator" in roles
        workspace_nickname = (
            self._normalize_workspace_nickname(payload.get("workspace_nickname"))
            if creates_workspace
            else None
        )
        target_workspace_id = (
            f"workspace-{uuid.uuid4().hex[:12]}" if creates_workspace else workspace_id
        )
        with self.database.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM users WHERE lower(email) = lower(?)", (email,)
            ).fetchone():
                raise IdentityError("That email address is already assigned.", status=409)
            if workspace_nickname and connection.execute(
                "SELECT 1 FROM workspaces WHERE lower(nickname) = lower(?)",
                (workspace_nickname,),
            ).fetchone():
                raise IdentityError(
                    "That workspace nickname is already assigned.", status=409
                )
            connection.execute(
                """
                INSERT INTO users
                    (id, first_name, last_name, email, status, password_salt,
                     password_hash, password_state, temporary_password_expires_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    first_name,
                    last_name,
                    email,
                    (credential or {}).get("password_salt"),
                    (credential or {}).get("password_hash"),
                    "password_change_required" if credential else "bootstrap_required",
                    (credential or {}).get("temporary_password_expires_at"),
                    now,
                    now,
                ),
            )
            if creates_workspace:
                connection.execute(
                    """
                    INSERT INTO workspaces
                        (id, name, nickname, workspace_type, owner_user_id, created_by, status,
                         created_at, updated_at)
                    VALUES (?, ?, ?, 'administrator', ?, ?, 'active', ?, ?)
                    """,
                    (
                        target_workspace_id,
                        f"{first_name} {last_name}",
                        workspace_nickname,
                        user_id,
                        actor_id,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO workspace_memberships
                    (user_id, workspace_id, created_by, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, target_workspace_id, actor_id, now),
            )
            for role in roles:
                connection.execute(
                    """
                    INSERT INTO user_roles
                        (user_id, workspace_id, role, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, target_workspace_id, role, actor_id, now),
                )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            created = self._hydrate(connection, row)
        if creates_workspace:
            with self.database.workspace(target_workspace_id):
                with self.database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO learning_policies
                            (version, enabled, is_active, config_json, created_at)
                        VALUES (?, 1, 1, ?, ?)
                        """,
                        (
                            f"reviewer-memory-v1-{target_workspace_id.removeprefix('workspace-')}",
                            '{"limit": 8, "rolling_window": 3, "minimum_events": 20}',
                            now,
                        ),
                    )
        return created

    def update_user(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
        authorized_workspace_id: str,
        actor_is_owner: bool = False,
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                raise IdentityError("User not found.", status=404)
            current = self._hydrate(connection, row)
            if (
                not actor_is_owner
                and current["workspace_id"] != authorized_workspace_id
            ):
                raise IdentityError("User not found.", status=404)
            nickname_supplied = "workspace_nickname" in payload
            if nickname_supplied and (
                not actor_is_owner
                or "administrator" not in current["roles"]
                or "owner" in current["roles"]
            ):
                raise IdentityError(
                    "Only the Owner can change an Administrator workspace nickname.",
                    status=403,
                )
            profile = {
                "first_name": payload.get("first_name", current["first_name"]),
                "last_name": payload.get("last_name", current["last_name"]),
                "email": payload.get("email", current["email"]),
                "roles": payload.get("roles", current["roles"]),
            }
            first_name, last_name, email, roles = self._profile(
                profile,
                allow_administrator=actor_is_owner
                and bool({"owner", "administrator"} & set(current["roles"])),
                allow_owner="owner" in current["roles"],
            )
            if "owner" in current["roles"]:
                roles = sorted(set(roles) | {"owner"})
            if "administrator" in current["roles"]:
                roles = sorted(set(roles) | {"administrator"})
            workspace_nickname: str | None = None
            if nickname_supplied:
                workspace_nickname = self._normalize_workspace_nickname(
                    payload.get("workspace_nickname")
                )
                duplicate_nickname = connection.execute(
                    """
                    SELECT id FROM workspaces
                    WHERE lower(nickname) = lower(?) AND id <> ?
                    """,
                    (workspace_nickname, current["workspace_id"]),
                ).fetchone()
                if duplicate_nickname:
                    raise IdentityError(
                        "That workspace nickname is already assigned.", status=409
                    )
            duplicate = connection.execute(
                "SELECT id FROM users WHERE lower(email) = lower(?) AND id <> ?",
                (email, user_id),
            ).fetchone()
            if duplicate:
                raise IdentityError("That email address is already assigned.", status=409)
            connection.execute(
                """
                UPDATE users
                SET first_name = ?, last_name = ?, email = ?, updated_at = ?
                WHERE id = ?
                """,
                (first_name, last_name, email, utc_now(), user_id),
            )
            if workspace_nickname is not None:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE workspaces
                    SET nickname = ?, updated_at = ?
                    WHERE id = ? AND workspace_type = 'administrator'
                    """,
                    (workspace_nickname, now, current["workspace_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events
                        (id, event_type, entity_type, entity_id, details_json, created_at)
                    VALUES (?, 'workspace_nickname_changed', 'workspace', ?, ?, ?)
                    """,
                    (
                        f"audit-{uuid.uuid4().hex}",
                        current["workspace_id"],
                        json.dumps(
                            {
                                "actor_id": actor_id,
                                "workspace_nickname": workspace_nickname,
                            },
                            ensure_ascii=True,
                        ),
                        now,
                    ),
                )
            connection.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            for role in roles:
                connection.execute(
                    """
                    INSERT INTO user_roles
                        (user_id, workspace_id, role, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, current["workspace_id"], role, actor_id, utc_now()),
                )
            updated = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return self._hydrate(connection, updated)

    def set_status(
        self,
        user_id: str,
        status: str,
        *,
        authorized_workspace_id: str,
        actor_is_owner: bool = False,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"active", "suspended"}:
            raise IdentityError("Choose active or suspended status.")
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                raise IdentityError("User not found.", status=404)
            user = self._hydrate(connection, row)
            if not actor_is_owner and user["workspace_id"] != authorized_workspace_id:
                raise IdentityError("User not found.", status=404)
            if "owner" in user["roles"] and status == "suspended":
                raise IdentityError("The Owner cannot be suspended.")
            now = utc_now()
            connection.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, user_id),
            )
            affected_account_count = 1
            if "administrator" in user["roles"] and "owner" not in user["roles"]:
                connection.execute(
                    "UPDATE workspaces SET status = ?, updated_at = ? WHERE owner_user_id = ?",
                    (status, now, user_id),
                )
                workspace = connection.execute(
                    """
                    SELECT id FROM workspaces
                    WHERE owner_user_id = ? AND workspace_type = 'administrator'
                    """,
                    (user_id,),
                ).fetchone()
                if workspace:
                    affected_account_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) AS total
                            FROM workspace_memberships
                            WHERE workspace_id = ?
                            """,
                            (workspace["id"],),
                        ).fetchone()["total"]
                    )
                    if status == "suspended":
                        connection.execute(
                            """
                            UPDATE authenticated_sessions
                            SET revoked_at = ?
                            WHERE revoked_at IS NULL
                              AND user_id IN (
                                  SELECT user_id FROM workspace_memberships
                                  WHERE workspace_id = ?
                              )
                            """,
                            (now, workspace["id"]),
                        )
                    connection.execute(
                        """
                        INSERT INTO audit_events
                            (id, event_type, entity_type, entity_id, details_json, created_at)
                        VALUES (?, ?, 'workspace', ?, ?, ?)
                        """,
                        (
                            f"audit-{uuid.uuid4().hex}",
                            (
                                "administrator_workspace_suspended"
                                if status == "suspended"
                                else "administrator_workspace_reactivated"
                            ),
                            workspace["id"],
                            json.dumps(
                                {
                                    "actor_id": actor_id,
                                    "administrator_id": user_id,
                                    "affected_account_count": affected_account_count,
                                    "resulting_state": status,
                                },
                                ensure_ascii=True,
                            ),
                            now,
                        ),
                    )
            updated = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            result = self._hydrate(connection, updated)
            result["affected_account_count"] = affected_account_count
            return result

    def record_login(self, user_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT u.*, w.status AS workspace_status
                FROM users u
                JOIN workspace_memberships wm ON wm.user_id = u.id
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE u.id = ?
                """,
                (user_id,),
            ).fetchone()
            if (
                not row
                or row["status"] != "active"
                or row["workspace_status"] != "active"
            ):
                raise IdentityError("Unable to complete sign in.", status=401)
            now = utc_now()
            connection.execute(
                """
                UPDATE users
                SET last_login_at = ?, total_logins = total_logins + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, now, user_id),
            )
            updated = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return self._hydrate(connection, updated)

    def owner(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                WHERE ur.role = 'owner'
                """
            ).fetchone()
            if not row:
                raise IdentityError("MELvan does not have an Owner.", status=503)
            return self._hydrate(connection, row)

    @staticmethod
    def _hydrate(connection: Any, row: Any) -> dict[str, Any]:
        value = dict(row)
        membership = connection.execute(
            """
            SELECT wm.workspace_id, w.nickname, w.status AS workspace_status
            FROM workspace_memberships wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE wm.user_id = ?
            """,
            (row["id"],),
        ).fetchone()
        workspace_id = membership["workspace_id"] if membership else None
        roles = [
            item["role"]
            for item in connection.execute(
                """
                SELECT role FROM user_roles
                WHERE user_id = ? AND workspace_id = ?
                ORDER BY CASE role
                    WHEN 'owner' THEN 0 WHEN 'administrator' THEN 1
                    WHEN 'reporter' THEN 2 ELSE 3 END
                """,
                (row["id"], workspace_id),
            ).fetchall()
        ]
        value["workspace_id"] = workspace_id
        value["roles"] = roles
        value["workspace_nickname"] = membership["nickname"] if membership else None
        value["workspace_status"] = (
            membership["workspace_status"] if membership else "suspended"
        )
        value["inherited_suspension"] = bool(
            value["status"] == "active" and value["workspace_status"] == "suspended"
        )
        value["effective_status"] = (
            "suspended"
            if value["status"] == "suspended" or value["inherited_suspension"]
            else "active"
        )
        for secret in (
            "password_salt",
            "password_hash",
            "recovery_salt",
            "recovery_hash",
        ):
            value.pop(secret, None)
        return value

    @staticmethod
    def _normalize_email(value: Any) -> str:
        email = str(value or "").strip().lower()
        if (
            len(email) > 254
            or "@" not in email
            or email.startswith("@")
            or email.endswith("@")
            or any(character.isspace() for character in email)
        ):
            raise IdentityError("Enter a valid email address.")
        return email

    @staticmethod
    def _normalize_workspace_nickname(value: Any) -> str:
        raw = str(value or "")
        if any(ord(character) < 32 or ord(character) == 127 for character in raw):
            raise IdentityError("Workspace nickname contains unsupported characters.")
        nickname = " ".join(raw.split())
        if not nickname:
            raise IdentityError("Enter a workspace nickname.")
        if len(nickname) > MAX_WORKSPACE_NICKNAME_LENGTH:
            raise IdentityError(
                f"Workspace nickname must be {MAX_WORKSPACE_NICKNAME_LENGTH} characters or fewer."
            )
        return nickname

    @classmethod
    def _profile(
        cls,
        payload: dict[str, Any],
        *,
        allow_administrator: bool = False,
        allow_owner: bool = False,
    ) -> tuple[str, str, str, list[str]]:
        first_name = " ".join(str(payload.get("first_name") or "").split())
        last_name = " ".join(str(payload.get("last_name") or "").split())
        email = cls._normalize_email(payload.get("email"))
        roles = sorted(set(payload.get("roles") or []))
        if not first_name or not last_name:
            raise IdentityError("Enter the user's first and last name.")
        allowed = {"reporter", "reviewer"}
        if allow_administrator:
            allowed.add("administrator")
        if allow_owner:
            allowed.add("owner")
        if not roles or not set(roles).issubset(allowed):
            if allow_administrator:
                raise IdentityError(
                    "Assign Administrator, Reporter, Reviewer, or an allowed combination."
                )
            raise IdentityError("Assign the Reporter role, Reviewer role, or both.")
        return first_name, last_name, email, roles
