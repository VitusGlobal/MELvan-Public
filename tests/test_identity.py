from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from melvan.auth import (
    AzureCommunicationEmailSender,
    DevelopmentEmailSender,
    GENERIC_RECOVERY_MESSAGE,
    SessionStore,
    UNSUPERVISED_MAILBOX_NOTICE,
)
from melvan.db import Database, OWNER_WORKSPACE_ID
from melvan.identity import IdentityError, UserDirectory


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class IdentityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.database = Database(root / "melvan.sqlite3")
        self.database.initialize(FIXTURE)
        self.users = UserDirectory(
            self.database,
            legacy_path=root / "users.json",
            initial_owner_email="owner@example.org",
        )
        self.users.initialize()
        self.sender = DevelopmentEmailSender()
        self.sessions = SessionStore(
            self.users,
            self.database,
            pepper="test-authentication-pepper-value",
            email_sender=self.sender,
        )
        owner_temporary = self.sessions.bootstrap_owner()["temporary_password"]
        token, _, _, meta = self.sessions.login(
            "owner@example.org", owner_temporary, duration_choice="7_days"
        )
        self.assertTrue(meta["requires_password_change"])
        self.owner_token, self.owner, _, _ = self.sessions.change_password(
            token,
            "OwnerStrong1!",
            "OwnerStrong1!",
            user_guide_acknowledged=True,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_user(
        self,
        email: str = "rita@example.org",
        roles: list[str] | None = None,
        *,
        allow_administrator: bool = False,
        workspace_nickname: str | None = None,
    ) -> tuple[dict, dict]:
        payload = {
            "first_name": "Rita",
            "last_name": "Reporter",
            "email": email,
            "roles": roles or ["reporter"],
        }
        if allow_administrator:
            payload["workspace_nickname"] = workspace_nickname or "Rita workspace"
        user = self.sessions.create_user(
            payload,
            actor_id=self.owner["id"],
            workspace_id=OWNER_WORKSPACE_ID,
            allow_administrator=allow_administrator,
        )
        self.sessions.request_recovery(user["email"], request_source=f"test-{user['id']}")
        return user, self.sender.outbox[-1]

    def activate(self, user: dict, temporary: dict, password: str = "ValidPass1!"):
        token, _, _, meta = self.sessions.login(user["email"], temporary["temporary_password"])
        self.assertTrue(meta["requires_password_change"])
        return self.sessions.change_password(
            token, password, password, user_guide_acknowledged=True
        )

    def test_owner_is_unique_and_can_hold_every_role(self) -> None:
        self.assertCountEqual(["owner", "administrator"], self.owner["roles"])
        updated = self.users.update_user(
            self.owner["id"],
            {"roles": ["owner", "administrator", "reporter", "reviewer"]},
            actor_id=self.owner["id"],
            authorized_workspace_id=OWNER_WORKSPACE_ID,
            actor_is_owner=True,
        )
        self.assertCountEqual(
            ["owner", "administrator", "reporter", "reviewer"], updated["roles"]
        )
        with self.assertRaisesRegex(IdentityError, "Owner"):
            self.users.set_status(
                self.owner["id"],
                "suspended",
                authorized_workspace_id=OWNER_WORKSPACE_ID,
                actor_is_owner=True,
            )

    def test_fresh_install_requires_configured_owner_email(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "melvan.sqlite3")
            database.initialize(FIXTURE)
            with self.assertRaisesRegex(
                IdentityError, "MELVAN_INITIAL_ADMIN_EMAIL"
            ):
                UserDirectory(database).initialize()

    def test_existing_owner_does_not_depend_on_bootstrap_configuration(self) -> None:
        UserDirectory(self.database).initialize()
        self.assertEqual("owner@example.org", self.users.owner()["email"])

    def test_password_email_warns_that_the_mailbox_is_unsupervised(self) -> None:
        message = AzureCommunicationEmailSender._plain_text("Temporary1!", 7)
        self.assertTrue(message.endswith(UNSUPERVISED_MAILBOX_NOTICE))

    def test_legacy_json_is_imported_once_into_owner_workspace_without_pin_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "users.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "id": "legacy-admin",
                                "first_name": "Legacy",
                                "last_name": "Administrator",
                                "email": "legacy-owner@example.org",
                                "roles": ["administrator", "reporter"],
                                "access_code": "0000",
                                "status": "active",
                                "total_logins": 4,
                            },
                            {
                                "id": "legacy-reviewer",
                                "first_name": "Legacy",
                                "last_name": "Reviewer",
                                "email": "reviewer@example.org",
                                "roles": ["reviewer"],
                                "access_code": "1234",
                                "status": "active",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            database = Database(root / "melvan.sqlite3")
            database.initialize(FIXTURE)
            users = UserDirectory(
                database,
                legacy_path=legacy_path,
                initial_owner_email="legacy-owner@example.org",
            )
            users.initialize()

            owner = users.get_user("legacy-admin")
            reviewer = users.get_user("legacy-reviewer")
            self.assertEqual(OWNER_WORKSPACE_ID, owner["workspace_id"])
            self.assertEqual(OWNER_WORKSPACE_ID, reviewer["workspace_id"])
            self.assertCountEqual(
                ["owner", "administrator", "reporter"], owner["roles"]
            )
            self.assertEqual(["reviewer"], reviewer["roles"])
            self.assertEqual("bootstrap_required", owner["password_state"])
            self.assertEqual("bootstrap_required", reviewer["password_state"])
            with database.connection() as connection:
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(users)")
                }
            self.assertNotIn("access_code", columns)

            legacy_path.write_text(
                json.dumps({"users": []}),
                encoding="utf-8",
            )
            users.initialize()
            self.assertEqual("reviewer@example.org", users.get_user("legacy-reviewer")["email"])

    def test_owner_creates_administrator_with_isolated_workspace(self) -> None:
        admin, temporary = self.create_user(
            "admin@example.org",
            ["administrator", "reviewer"],
            allow_administrator=True,
            workspace_nickname="Northern pilot",
        )
        self.assertNotEqual(OWNER_WORKSPACE_ID, admin["workspace_id"])
        self.assertEqual("bootstrap_required", admin["password_state"])
        self.assertEqual(12, len(temporary["temporary_password"]))
        workspaces = {item["id"]: item for item in self.users.list_workspaces()}
        self.assertEqual(admin["id"], workspaces[admin["workspace_id"]]["owner_user_id"])
        self.assertEqual("Northern pilot", workspaces[admin["workspace_id"]]["nickname"])
        self.assertCountEqual(["administrator", "reviewer"], admin["roles"])

    def test_administrator_workspace_nickname_is_required_unique_and_owner_editable(
        self,
    ) -> None:
        with self.assertRaisesRegex(IdentityError, "workspace nickname"):
            self.sessions.create_user(
                {
                    "first_name": "Missing",
                    "last_name": "Nickname",
                    "email": "missing-nickname@example.org",
                    "roles": ["administrator"],
                },
                actor_id=self.owner["id"],
                workspace_id=OWNER_WORKSPACE_ID,
                allow_administrator=True,
            )
        with self.assertRaisesRegex(IdentityError, "unsupported"):
            self.create_user(
                "control-nickname@example.org",
                ["administrator"],
                allow_administrator=True,
                workspace_nickname="Control\nname",
            )
        admin, _ = self.create_user(
            "admin-nickname@example.org",
            ["administrator"],
            allow_administrator=True,
            workspace_nickname="Community Health",
        )
        with self.assertRaisesRegex(IdentityError, "already assigned"):
            self.create_user(
                "duplicate-nickname@example.org",
                ["administrator"],
                allow_administrator=True,
                workspace_nickname="  community health  ",
            )
        updated = self.users.update_user(
            admin["id"],
            {"workspace_nickname": "Youth Services"},
            actor_id=self.owner["id"],
            authorized_workspace_id=OWNER_WORKSPACE_ID,
            actor_is_owner=True,
        )
        self.assertEqual("Youth Services", updated["workspace_nickname"])
        self.assertEqual(admin["workspace_id"], updated["workspace_id"])
        with self.assertRaisesRegex(IdentityError, "Only the Owner"):
            self.users.update_user(
                admin["id"],
                {"workspace_nickname": "Unauthorized rename"},
                actor_id=admin["id"],
                authorized_workspace_id=admin["workspace_id"],
                actor_is_owner=False,
            )

    def test_suspending_administrator_blocks_workspace_and_preserves_user_statuses(
        self,
    ) -> None:
        admin, temporary = self.create_user(
            "workspace-admin@example.org",
            ["administrator"],
            allow_administrator=True,
            workspace_nickname="Workspace lock test",
        )
        admin_token, _, _, _ = self.activate(admin, temporary, "AdminStrong1!")
        reporter = self.sessions.create_user(
            {
                "first_name": "Workspace",
                "last_name": "Reporter",
                "email": "workspace-reporter@example.org",
                "roles": ["reporter"],
            },
            actor_id=admin["id"],
            workspace_id=admin["workspace_id"],
            allow_administrator=False,
        )
        self.sessions.request_recovery(
            reporter["email"], request_source="workspace-reporter"
        )
        reporter_temporary = self.sender.outbox[-1]
        reporter_token, _, _, _ = self.activate(
            reporter, reporter_temporary, "ReporterStrong1!"
        )

        result = self.users.set_status(
            admin["id"],
            "suspended",
            authorized_workspace_id=OWNER_WORKSPACE_ID,
            actor_is_owner=True,
            actor_id=self.owner["id"],
        )
        self.assertEqual(2, result["affected_account_count"])
        for token in (admin_token, reporter_token):
            with self.assertRaises(IdentityError):
                self.sessions.resolve(token)
        self.assertIsNone(self.users.find_active_by_email(reporter["email"]))
        before = len(self.sender.outbox)
        self.sessions.request_recovery(
            reporter["email"], request_source="suspended-workspace"
        )
        self.assertEqual(before, len(self.sender.outbox))
        inherited = self.users.get_user(reporter["id"])
        self.assertEqual("active", inherited["status"])
        self.assertTrue(inherited["inherited_suspension"])

        self.users.set_status(
            reporter["id"],
            "suspended",
            authorized_workspace_id=admin["workspace_id"],
            actor_is_owner=True,
            actor_id=self.owner["id"],
        )
        self.users.set_status(
            admin["id"],
            "active",
            authorized_workspace_id=OWNER_WORKSPACE_ID,
            actor_is_owner=True,
            actor_id=self.owner["id"],
        )
        self.assertIsNone(self.users.find_active_by_email(reporter["email"]))
        self.assertEqual(
            "suspended", self.users.get_user(reporter["id"])["effective_status"]
        )
        with self.database.connection() as connection:
            events = connection.execute(
                """
                SELECT event_type, details_json FROM audit_events
                WHERE entity_id = ? ORDER BY created_at
                """,
                (admin["workspace_id"],),
            ).fetchall()
        self.assertEqual(
            [
                "administrator_workspace_suspended",
                "administrator_workspace_reactivated",
            ],
            [event["event_type"] for event in events],
        )
        self.assertEqual(
            2, json.loads(events[0]["details_json"])["affected_account_count"]
        )

    def test_temporary_password_requires_replacement_and_is_hashed(self) -> None:
        user, temporary = self.create_user()
        token, _, _, meta = self.sessions.login(
            user["email"], temporary["temporary_password"], duration_choice="28_days"
        )
        self.assertTrue(meta["requires_password_change"])
        self.assertEqual("initial_setup", meta["password_setup_purpose"])
        self.assertTrue(meta["requires_user_guide_acknowledgement"])
        with self.assertRaisesRegex(IdentityError, "uppercase"):
            self.sessions.change_password(token, "weakpass1!", "weakpass1!")
        new_token, _, role, meta = self.sessions.change_password(
            token,
            "StrongPass1!",
            "StrongPass1!",
            user_guide_acknowledged=True,
        )
        self.assertEqual("reporter", role)
        self.assertFalse(meta["requires_password_change"])
        self.assertEqual(user["id"], self.sessions.resolve(new_token)["id"])
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
            ).fetchone()
            sessions = connection.execute(
                "SELECT token_hash FROM authenticated_sessions"
            ).fetchall()
        self.assertNotIn("StrongPass1!", row["password_hash"])
        self.assertTrue(all(new_token not in item["token_hash"] for item in sessions))

    def test_multi_role_session_requires_one_role_and_cannot_switch(self) -> None:
        user, temporary = self.create_user(roles=["reporter", "reviewer"])
        token, _, active_role, _ = self.activate(user, temporary)
        self.assertIsNone(active_role)
        with self.assertRaisesRegex(IdentityError, "Choose what"):
            self.sessions.resolve(token)
        self.assertEqual("reporter", self.sessions.assume_role(token, "reporter")["active_role"])
        with self.assertRaisesRegex(IdentityError, "Sign out"):
            self.sessions.assume_role(token, "reviewer")

    def test_three_failures_lock_for_24_hours_and_recovery_clears_lock(self) -> None:
        user, temporary = self.create_user()
        self.activate(user, temporary)
        for _ in range(3):
            with self.assertRaises(IdentityError):
                self.sessions.login(user["email"], "WrongPassword1!")
        self.assertEqual(
            "temporary_lockout",
            self.sessions.user_lock_statuses()[user["id"]]["status"],
        )
        result = self.sessions.request_recovery(user["email"], request_source="127.0.0.1")
        self.assertEqual(GENERIC_RECOVERY_MESSAGE, result["message"])
        recovery_password = self.sender.outbox[-1]["temporary_password"]
        token, _, _, meta = self.sessions.login(user["email"], recovery_password)
        self.assertTrue(meta["requires_password_change"])
        self.assertEqual("recovery", meta["password_setup_purpose"])
        self.assertFalse(meta["requires_user_guide_acknowledgement"])
        self.assertNotIn(user["id"], self.sessions.user_lock_statuses())
        self.sessions.change_password(token, "Recovered1!", "Recovered1!")

    def test_known_and_unknown_recovery_responses_are_identical(self) -> None:
        user, _ = self.create_user()
        known = self.sessions.request_recovery(user["email"], request_source="one")
        unknown = self.sessions.request_recovery("unknown@example.org", request_source="two")
        self.assertEqual(known, unknown)
        self.assertEqual(GENERIC_RECOVERY_MESSAGE, known["message"])

    def test_failed_recovery_delivery_is_safe_auditable_and_not_usable(self) -> None:
        class ProviderError:
            code = "DomainNotLinked"

        class DeliveryError(Exception):
            status_code = 404
            error = ProviderError()

        class FailingSender:
            def send_temporary_password(self, *args, **kwargs) -> None:
                raise DeliveryError("message containing recipient and credential")

        user = self.sessions.create_user(
            {
                "first_name": "Failed",
                "last_name": "Delivery",
                "email": "failed@example.org",
                "roles": ["reporter"],
            },
            actor_id=self.owner["id"],
            workspace_id=OWNER_WORKSPACE_ID,
            allow_administrator=False,
        )
        self.sessions.email_sender = FailingSender()
        result = self.sessions.request_recovery(
            user["email"], request_source="failed-delivery-test"
        )
        self.assertEqual(GENERIC_RECOVERY_MESSAGE, result["message"])
        with self.database.connection() as connection:
            credential = connection.execute(
                """
                SELECT recovery_salt, recovery_hash, recovery_expires_at
                FROM users WHERE id = ?
                """,
                (user["id"],),
            ).fetchone()
            event = connection.execute(
                """
                SELECT outcome, details_json FROM authentication_events
                WHERE event_type = 'password_recovery_delivery' AND user_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
        self.assertIsNone(credential["recovery_salt"])
        self.assertIsNone(credential["recovery_hash"])
        self.assertIsNone(credential["recovery_expires_at"])
        self.assertEqual("delivery_failed", event["outcome"])
        self.assertEqual(
            {
                "provider": "azure",
                "error_type": "DeliveryError",
                "status_code": 404,
                "error_code": "DomainNotLinked",
            },
            json.loads(event["details_json"]),
        )
        self.assertNotIn(user["email"], event["details_json"])
        self.assertNotIn("credential", event["details_json"])

    def test_user_creation_does_not_issue_an_administrative_password(self) -> None:
        before = len(self.sender.outbox)
        user = self.sessions.create_user(
            {
                "first_name": "New",
                "last_name": "User",
                "email": "new@example.org",
                "roles": ["reporter"],
            },
            actor_id=self.owner["id"],
            workspace_id=OWNER_WORKSPACE_ID,
            allow_administrator=False,
        )
        self.assertEqual(before, len(self.sender.outbox))
        self.assertEqual("bootstrap_required", user["password_state"])
        with self.database.connection() as connection:
            credential = connection.execute(
                "SELECT password_hash, password_salt FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()
        self.assertIsNone(credential["password_hash"])
        self.assertIsNone(credential["password_salt"])

    def test_initial_password_requires_versioned_user_guide_acknowledgement(self) -> None:
        user, temporary = self.create_user("guide-reader@example.org")
        token, _, _, meta = self.sessions.login(
            user["email"], temporary["temporary_password"]
        )
        self.assertEqual("initial_setup", meta["password_setup_purpose"])
        self.assertTrue(meta["requires_user_guide_acknowledgement"])

        with self.assertRaisesRegex(IdentityError, "User Guide"):
            self.sessions.change_password(token, "GuideStrong1!", "GuideStrong1!")

        self.sessions.change_password(
            token,
            "GuideStrong1!",
            "GuideStrong1!",
            user_guide_acknowledged=True,
        )
        with self.database.connection() as connection:
            event = connection.execute(
                """
                SELECT details_json FROM authentication_events
                WHERE event_type = 'password_changed' AND user_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
        self.assertEqual(
            {
                "password_setup_purpose": "initial_setup",
                "user_guide_acknowledged": True,
                "user_guide_version": "2026-08-18",
            },
            json.loads(event["details_json"]),
        )

    def test_self_service_reset_revokes_sessions_after_recovery_use(self) -> None:
        user, temporary = self.create_user()
        token, _, _, _ = self.activate(user, temporary)
        self.sessions.request_recovery(user["email"], request_source="self-service-reset")
        reset = self.sender.outbox[-1]["temporary_password"]
        self.assertEqual(user["id"], self.sessions.resolve(token)["id"])
        replacement, _, _, meta = self.sessions.login(user["email"], reset)
        self.assertTrue(meta["requires_password_change"])
        self.assertEqual("recovery", meta["password_setup_purpose"])
        self.assertFalse(meta["requires_user_guide_acknowledgement"])
        with self.assertRaises(IdentityError):
            self.sessions.resolve(token)
        self.sessions.change_password(replacement, "ResetPass1!", "ResetPass1!")


if __name__ == "__main__":
    unittest.main()
