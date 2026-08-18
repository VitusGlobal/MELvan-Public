from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from melvan.db import Database
from melvan.model import DemoModelAdapter
from melvan.service import MelvanService
from melvan.web import APIApplication


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class WorkspaceIsolationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "melvan.sqlite3")
        self.service = MelvanService(
            self.database,
            DemoModelAdapter(),
            FIXTURE,
            initial_admin_email="owner@example.org",
        )
        self.service.initialize()
        self.api = APIApplication(self.service)
        temporary = self.api.sessions.bootstrap_owner()["temporary_password"]
        self.owner_token = self._activate(
            "owner@example.org", temporary, "OwnerStrong1!"
        )
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "owner"}, self.owner_token
        )
        self.assertEqual(200, status)
        self.admin_a, self.admin_a_token = self._create_administrator(
            "Admin", "Alpha", "alpha@example.org", "AlphaStrong1!"
        )
        self.admin_b, self.admin_b_token = self._create_administrator(
            "Admin", "Beta", "beta@example.org", "BetaStrong1!"
        )
        for token in (self.admin_a_token, self.admin_b_token):
            status, _ = self.api.dispatch(
                "POST",
                "/api/admin/logic-model/preload-demo",
                {"confirmed": True},
                token,
            )
            self.assertEqual(201, status)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _activate(self, email: str, temporary: str, replacement: str) -> str:
        status, session = self.api.dispatch(
            "POST",
            "/api/session",
            {
                "email": email,
                "password": temporary,
                "duration": "7_days",
                "timezone": "UTC",
            },
        )
        self.assertEqual(200, status)
        status, session = self.api.dispatch(
            "POST",
            "/api/session/password",
            {
                "password": replacement,
                "confirmation": replacement,
                "user_guide_acknowledged": bool(
                    session.get("requires_user_guide_acknowledgement")
                ),
            },
            session["token"],
        )
        self.assertEqual(200, status)
        return session["token"]

    def _create_administrator(
        self, first: str, last: str, email: str, password: str
    ) -> tuple[dict, str]:
        status, created = self.api.dispatch(
            "POST",
            "/api/admin/users",
            {
                "first_name": first,
                "last_name": last,
                "email": email,
                "roles": ["administrator"],
                "workspace_nickname": f"{first} {last} workspace",
            },
            self.owner_token,
        )
        self.assertEqual(201, status)
        token = self._activate(email, self._recovery_password(email), password)
        return created["user"], token

    def _create_workspace_user(
        self, admin_token: str, email: str, roles: list[str], password: str
    ) -> tuple[dict, str]:
        status, created = self.api.dispatch(
            "POST",
            "/api/admin/users",
            {
                "first_name": "Workspace",
                "last_name": "User",
                "email": email,
                "roles": roles,
            },
            admin_token,
        )
        self.assertEqual(201, status)
        return created["user"], self._activate(email, self._recovery_password(email), password)

    def _recovery_password(self, email: str) -> str:
        status, _ = self.api.dispatch(
            "POST",
            "/api/auth/recovery",
            {"email": email, "request_source": f"workspace-test-{email}"},
        )
        self.assertEqual(202, status)
        return self.api.sessions.email_sender.outbox[-1]["temporary_password"]

    def test_administrators_see_only_users_they_created(self) -> None:
        user_a, _ = self._create_workspace_user(
            self.admin_a_token, "reporter-a@example.org", ["reporter"], "ReporterA1!"
        )
        user_b, _ = self._create_workspace_user(
            self.admin_b_token, "reporter-b@example.org", ["reporter"], "ReporterB1!"
        )
        status, alpha = self.api.dispatch(
            "GET", "/api/admin/users", token=self.admin_a_token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            {self.admin_a["id"], user_a["id"]},
            {item["id"] for item in alpha["users"]},
        )
        status, beta = self.api.dispatch(
            "GET", "/api/admin/users", token=self.admin_b_token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            {self.admin_b["id"], user_b["id"]},
            {item["id"] for item in beta["users"]},
        )

        status, _ = self.api.dispatch(
            "POST",
            f"/api/admin/users/{user_b['id']}",
            {"first_name": "Cross-workspace change"},
            self.admin_a_token,
        )
        self.assertEqual(404, status)

    def test_owner_lists_and_manages_administrators_only(self) -> None:
        workspace_user, _ = self._create_workspace_user(
            self.admin_a_token,
            "owner-boundary-reporter@example.org",
            ["reporter"],
            "OwnerBoundary1!",
        )
        status, result = self.api.dispatch(
            "GET", "/api/admin/users", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            {self.admin_a["id"], self.admin_b["id"]},
            {item["id"] for item in result["users"]},
        )

        for roles in (["reporter"], ["reviewer"], ["administrator", "reporter"]):
            with self.subTest(roles=roles):
                status, response = self.api.dispatch(
                    "POST",
                    "/api/admin/users",
                    {
                        "first_name": "Invalid",
                        "last_name": "Owner creation",
                        "email": f"invalid-{roles[-1]}-{len(roles)}@example.org",
                        "roles": roles,
                        "workspace_nickname": f"Invalid {roles[-1]} {len(roles)}",
                    },
                    self.owner_token,
                )
                self.assertEqual(403, status)
                self.assertIn("Administrators only", response["error"])

        for suffix in ("", "/suspend", "/reactivate"):
            status, response = self.api.dispatch(
                "POST",
                f"/api/admin/users/{workspace_user['id']}{suffix}",
                {"first_name": "Blocked"},
                self.owner_token,
            )
            self.assertEqual(403, status)
            self.assertIn("Administrators only", response["error"])

    def test_owner_edit_preserves_administrator_secondary_roles(self) -> None:
        status, updated = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}",
            {
                "first_name": self.admin_a["first_name"],
                "last_name": self.admin_a["last_name"],
                "email": self.admin_a["email"],
                "roles": ["reporter", "reviewer"],
            },
            self.admin_a_token,
        )
        self.assertEqual(200, status)
        self.assertCountEqual(
            ["administrator", "reporter", "reviewer"], updated["user"]["roles"]
        )
        status, owner_updated = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}",
            {
                "first_name": "Renamed",
                "last_name": self.admin_a["last_name"],
                "email": self.admin_a["email"],
                "roles": ["administrator"],
                "workspace_nickname": "Renamed workspace",
            },
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertCountEqual(
            ["administrator", "reporter", "reviewer"],
            owner_updated["user"]["roles"],
        )

    def test_administrator_can_add_own_reporter_and_reviewer_roles(self) -> None:
        status, updated = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}",
            {
                "first_name": self.admin_a["first_name"],
                "last_name": self.admin_a["last_name"],
                "email": self.admin_a["email"],
                "roles": ["reporter", "reviewer"],
            },
            self.admin_a_token,
        )
        self.assertEqual(200, status)
        self.assertCountEqual(
            ["administrator", "reporter", "reviewer"],
            updated["user"]["roles"],
        )

    def test_owner_suspends_and_reactivates_entire_administrator_workspace(
        self,
    ) -> None:
        reporter, reporter_token = self._create_workspace_user(
            self.admin_a_token,
            "suspension-reporter@example.org",
            ["reporter"],
            "ReporterSuspend1!",
        )
        status, suspended = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}/suspend",
            {},
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, suspended["user"]["affected_account_count"])
        for token in (self.admin_a_token, reporter_token):
            status, _ = self.api.dispatch("GET", "/api/session", token=token)
            self.assertEqual(401, status)

        status, selected = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": self.admin_a["workspace_id"]},
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(self.admin_a["workspace_id"], selected["workspace_id"])
        status, users = self.api.dispatch(
            "GET", "/api/admin/users", token=self.owner_token
        )
        self.assertEqual(200, status)
        descendant = next(
            item for item in users["users"] if item["id"] == reporter["id"]
        )
        self.assertTrue(descendant["inherited_suspension"])

        status, _ = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}/reactivate",
            {},
            self.owner_token,
        )
        self.assertEqual(403, status)
        status, _ = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": "workspace-owner"},
            self.owner_token,
        )
        self.assertEqual(200, status)
        status, reactivated = self.api.dispatch(
            "POST",
            f"/api/admin/users/{self.admin_a['id']}/reactivate",
            {},
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, reactivated["user"]["affected_account_count"])
        status, _ = self.api.dispatch(
            "POST",
            "/api/session",
            {
                "email": self.admin_a["email"],
                "password": "AlphaStrong1!",
                "duration": "7_days",
                "timezone": "UTC",
            },
        )
        self.assertEqual(200, status)
        status, _ = self.api.dispatch(
            "POST",
            "/api/session",
            {
                "email": reporter["email"],
                "password": "ReporterSuspend1!",
                "duration": "7_days",
                "timezone": "UTC",
            },
        )
        self.assertEqual(200, status)

    def test_owner_switches_explicit_workspace_context_without_sharing_logic_model_records(
        self,
    ) -> None:
        status, owner_workspace = self.api.dispatch(
            "GET", "/api/bootstrap", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertEqual("workspace-owner", owner_workspace["workspace_id"])
        self.assertEqual(
            ["MELvan", "Admin Alpha workspace", "Admin Beta workspace"],
            [item["nickname"] for item in owner_workspace["workspaces"]],
        )
        owner_display_ids = {
            item["id"] for item in owner_workspace["logic_model"]["elements"]
        }
        owner_logic_model_id = owner_workspace["logic_model"]["database_id"]

        status, selected = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": self.admin_a["workspace_id"]},
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(self.admin_a["workspace_id"], selected["workspace_id"])
        status, administrator_workspace = self.api.dispatch(
            "GET", "/api/bootstrap", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            self.admin_a["workspace_id"], administrator_workspace["workspace_id"]
        )
        administrator_display_ids = {
            item["id"] for item in administrator_workspace["logic_model"]["elements"]
        }
        administrator_logic_model_id = administrator_workspace["logic_model"]["database_id"]
        self.assertEqual(owner_display_ids, administrator_display_ids)
        with self.database.connection() as connection:
            owner_records = {
                (row["id"], row["stable_id"])
                for row in connection.execute(
                    "SELECT id, stable_id FROM elements WHERE logic_model_id = ?",
                    (owner_logic_model_id,),
                )
            }
            administrator_records = {
                (row["id"], row["stable_id"])
                for row in connection.execute(
                    "SELECT id, stable_id FROM elements WHERE logic_model_id = ?",
                    (administrator_logic_model_id,),
                )
            }
        self.assertTrue(owner_records.isdisjoint(administrator_records))

        status, _ = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": "workspace-does-not-exist"},
            self.owner_token,
        )
        self.assertEqual(404, status)

    def test_owner_demo_logic_model_changes_do_not_propagate_to_existing_clones(
        self,
    ) -> None:
        with self.service.workspace(self.admin_a["workspace_id"]):
            administrator_before = self.service.logic_model()
        with self.service.workspace("workspace-owner"):
            owner_draft = self.service.start_logic_model_draft("user-admin")
            owner_draft["title"] = "Updated future-workspace demonstration model"
            self.service.save_logic_model_draft(owner_draft, "user-admin")
            owner_current = self.service.commit_logic_model_draft(
                "user-admin", confirmed=True
            )
        with self.service.workspace(self.admin_a["workspace_id"]):
            administrator_after = self.service.logic_model()
        _, admin_c_token = self._create_administrator(
            "Admin", "Current", "current@example.org", "CurrentStrong1!"
        )
        status, future_clone = self.api.dispatch(
            "POST",
            "/api/admin/logic-model/preload-demo",
            {"confirmed": True},
            admin_c_token,
        )

        self.assertEqual(
            "Updated future-workspace demonstration model", owner_current["title"]
        )
        self.assertEqual(administrator_before, administrator_after)
        self.assertNotEqual(owner_current["database_id"], administrator_after["database_id"])
        self.assertEqual(201, status)
        self.assertEqual(
            "Updated future-workspace demonstration model",
            future_clone["logic_model"]["title"],
        )
        self.assertEqual(
            "Owner demonstration Logic Model version 2.0",
            future_clone["logic_model"]["source"],
        )

    def test_owner_observes_administrator_workspace_without_mutation_authority(
        self,
    ) -> None:
        reporter, _ = self._create_workspace_user(
            self.admin_a_token,
            "observer-reporter@example.org",
            ["reporter"],
            "ObserverReporter1!",
        )
        status, selected = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": self.admin_a["workspace_id"]},
            self.owner_token,
        )
        self.assertEqual(200, status)
        self.assertEqual(self.admin_a["workspace_id"], selected["workspace_id"])

        status, session = self.api.dispatch(
            "GET", "/api/session", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertTrue(session["user"]["workspace_read_only"])
        self.assertEqual("workspace-owner", session["user"]["home_workspace_id"])

        status, observed = self.api.dispatch(
            "GET", "/api/bootstrap", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertTrue(observed["workspace_read_only"])
        self.assertEqual("Admin Alpha workspace", observed["workspace_nickname"])
        self.assertEqual(
            {self.admin_a["id"], reporter["id"]},
            {item["id"] for item in observed["users"]},
        )
        self.assertNotIn(self.admin_b["id"], {item["id"] for item in observed["users"]})
        self.assertEqual("1.0", observed["logic_model"]["version"])

        with self.database.connection() as connection:
            before = {
                "users": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "drafts": connection.execute(
                    "SELECT COUNT(*) FROM logic_model_drafts"
                ).fetchone()[0],
                "statements": connection.execute(
                    "SELECT COUNT(*) FROM statements"
                ).fetchone()[0],
            }
        blocked_requests = [
            ("/api/admin/users", {"first_name": "Blocked"}),
            (
                f"/api/admin/users/{reporter['id']}",
                {"first_name": "Blocked", "last_name": "Change"},
            ),
            (f"/api/admin/users/{reporter['id']}/suspend", {}),
            ("/api/statements", {"text": "Blocked Statement"}),
            ("/api/review/end", {}),
            ("/api/admin/learning-policy", {}),
            ("/api/admin/logic-model-draft/start", {}),
            ("/api/admin/logic-model/preload-demo", {"confirmed": True}),
            ("/api/admin/logic-model-draft/save", {"draft": {}}),
            ("/api/admin/logic-model-draft/cancel", {"confirmed": True}),
            ("/api/admin/logic-model-draft/commit", {"confirmed": True}),
        ]
        for path, payload in blocked_requests:
            with self.subTest(path=path):
                status, response = self.api.dispatch(
                    "POST", path, payload, self.owner_token
                )
                self.assertEqual(403, status)
                self.assertIn("read-only", response["error"])

        status, export = self.api.dispatch(
            "POST", "/api/evidence-export", {}, self.owner_token
        )
        self.assertEqual(403, status)
        self.assertIn("read-only", export["error"])

        with self.database.connection() as connection:
            after = {
                "users": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "drafts": connection.execute(
                    "SELECT COUNT(*) FROM logic_model_drafts"
                ).fetchone()[0],
                "statements": connection.execute(
                    "SELECT COUNT(*) FROM statements"
                ).fetchone()[0],
            }
        self.assertEqual(before, after)

        status, _ = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": "workspace-owner"},
            self.owner_token,
        )
        self.assertEqual(200, status)
        status, home = self.api.dispatch(
            "GET", "/api/bootstrap", token=self.owner_token
        )
        self.assertEqual(200, status)
        self.assertFalse(home["workspace_read_only"])
        status, export = self.api.dispatch(
            "POST", "/api/evidence-export", {}, self.owner_token
        )
        self.assertEqual(403, status)
        self.assertIn("does not permit", export["error"])

        status, _ = self.api.dispatch(
            "POST",
            "/api/owner/workspace",
            {"workspace_id": self.admin_b["workspace_id"]},
            self.admin_a_token,
        )
        self.assertEqual(403, status)

    def test_statements_and_review_queues_are_workspace_scoped(self) -> None:
        _, reporter_a = self._create_workspace_user(
            self.admin_a_token, "reporter-a@example.org", ["reporter"], "ReporterA1!"
        )
        _, reviewer_a = self._create_workspace_user(
            self.admin_a_token, "reviewer-a@example.org", ["reviewer"], "ReviewerA1!"
        )
        _, reporter_b = self._create_workspace_user(
            self.admin_b_token, "reporter-b@example.org", ["reporter"], "ReporterB1!"
        )
        _, reviewer_b = self._create_workspace_user(
            self.admin_b_token, "reviewer-b@example.org", ["reviewer"], "ReviewerB1!"
        )
        status, statement_a = self.api.dispatch(
            "POST",
            "/api/statements",
            {"text": "Alpha team completed its planning workshop."},
            reporter_a,
        )
        self.assertEqual(201, status)
        status, statement_b = self.api.dispatch(
            "POST",
            "/api/statements",
            {"text": "Beta team completed its planning workshop."},
            reporter_b,
        )
        self.assertEqual(201, status)

        status, queue_a = self.api.dispatch("GET", "/api/review", token=reviewer_a)
        self.assertEqual(200, status)
        self.assertEqual(
            [statement_a["statement"]["id"]],
            [item["id"] for item in queue_a["statements"]],
        )
        status, queue_b = self.api.dispatch("GET", "/api/review", token=reviewer_b)
        self.assertEqual(200, status)
        self.assertEqual(
            [statement_b["statement"]["id"]],
            [item["id"] for item in queue_b["statements"]],
        )

    def test_database_rejects_cross_workspace_relationships(self) -> None:
        _, reporter_b = self._create_workspace_user(
            self.admin_b_token, "reporter-b@example.org", ["reporter"], "ReporterB1!"
        )
        status, created = self.api.dispatch(
            "POST",
            "/api/statements",
            {"text": "Beta team completed its planning workshop."},
            reporter_b,
        )
        self.assertEqual(201, status)
        with self.database.workspace(self.admin_a["workspace_id"]):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cross-workspace"):
                with self.database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO statement_context_revisions
                            (id, statement_id, version, context_text, created_by, created_at)
                        VALUES ('cross-workspace-test', ?, 99, '', 'test', datetime('now'))
                        """,
                        (created["statement"]["id"],),
                    )


if __name__ == "__main__":
    unittest.main()
