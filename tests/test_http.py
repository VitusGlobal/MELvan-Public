from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from melvan import __version__
from melvan.db import Database
from melvan.model import DemoModelAdapter
from melvan.service import MelvanService
from melvan.web import APIApplication


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class HTTPTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        database = Database(Path(self.tempdir.name) / "melvan.sqlite3")
        service = MelvanService(
            database,
            DemoModelAdapter(),
            FIXTURE,
            initial_admin_email="owner@example.org",
        )
        service.initialize()
        self.api = APIApplication(service)
        self.passwords: dict[str, str] = {}
        owner_temporary = self.api.sessions.bootstrap_owner()["temporary_password"]
        status, admin_session = self.login(
            "owner@example.org",
            temporary_password=owner_temporary,
            new_password="OwnerStrong1!",
        )
        self.assertEqual(200, status)
        self.owner_token = admin_session["token"]
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "owner"}, self.owner_token
        )
        self.assertEqual(200, status)
        status, administrator_session = self.login("owner@example.org")
        self.assertEqual(200, status)
        self.assertTrue(administrator_session["requires_role_selection"])
        self.admin_token = administrator_session["token"]
        status, _ = self.api.dispatch(
            "POST",
            "/api/session/role",
            {"role": "administrator"},
            self.admin_token,
        )
        self.assertEqual(200, status)
        status, dual_created = self.api.dispatch(
            "POST",
            "/api/admin/users",
            {
                "first_name": "Dana",
                "last_name": "Dual",
                "email": "dana@example.org",
                "roles": ["reporter", "reviewer"],
            },
            self.admin_token,
        )
        self.assertEqual(201, status)
        dual_temporary = self.recovery_password("dana@example.org")
        status, reviewer_created = self.api.dispatch(
            "POST",
            "/api/admin/users",
            {
                "first_name": "Reva",
                "last_name": "Reviewer",
                "email": "reva@example.org",
                "roles": ["reviewer"],
            },
            self.admin_token,
        )
        self.assertEqual(201, status)
        reviewer_temporary = self.recovery_password("reva@example.org")
        status, session = self.login(
            "dana@example.org",
            temporary_password=dual_temporary,
            new_password="DanaStrong1!",
        )
        self.assertEqual(200, status)
        self.assertTrue(session["requires_role_selection"])
        self.token = session["token"]
        status, selected = self.api.dispatch(
            "POST", "/api/session/role", {"role": "reporter"}, self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("reporter", selected["active_role"])
        status, reviewer_session = self.login(
            "reva@example.org",
            temporary_password=reviewer_temporary,
            new_password="RevaStrong1!",
        )
        self.reviewer_token = reviewer_session["token"]
        self.assertEqual(200, status)
        self.assertEqual("reviewer", reviewer_session["active_role"])

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_acceptable(self, statement: dict, element_id: str = "O1") -> dict:
        analysis_id = statement["analysis"]["id"]
        with self.api.service.database.connection() as connection:
            connection.execute(
                "UPDATE analyses SET match_status = 'existing_match' WHERE id = ?",
                (analysis_id,),
            )
            connection.execute(
                "DELETE FROM analysis_mappings WHERE analysis_id = ?", (analysis_id,)
            )
            connection.execute(
                """
                INSERT INTO analysis_mappings
                    (analysis_id, element_id, rank, confidence, rationale)
                VALUES (?, ?, 1, 0.9, 'Test-approved Logic Model mapping.')
                """,
                (analysis_id, element_id),
            )
        return self.api.service.get_statement(statement["id"])

    def request(
        self, path: str, body: dict | None = None, token: str | None = None
    ) -> tuple[int, dict]:
        return self.api.dispatch(
            "POST" if body is not None else "GET",
            path,
            body,
            self.token if token is None else token,
        )

    def recovery_password(self, email: str) -> str:
        status, result = self.api.dispatch(
            "POST",
            "/api/auth/recovery",
            {"email": email, "request_source": f"http-test-{email}"},
        )
        self.assertEqual(202, status)
        self.assertIn("If an account exists", result["message"])
        return self.api.sessions.email_sender.outbox[-1]["temporary_password"]

    def login(
        self,
        email: str,
        *,
        temporary_password: str | None = None,
        new_password: str | None = None,
    ) -> tuple[int, dict]:
        password = temporary_password or self.passwords.get(email) or (
            "OwnerStrong1!" if email == "admin@melvan.local" else ""
        )
        status, result = self.api.dispatch(
            "POST",
            "/api/session",
            {
                "email": email,
                "password": password,
                "duration": "7_days",
                "timezone": "UTC",
            },
        )
        if status != 200 or not result.get("requires_password_change"):
            return status, result
        replacement = new_password or f"Valid{len(self.passwords) + 1}Pass!"
        status, result = self.api.dispatch(
            "POST",
            "/api/session/password",
            {
                "password": replacement,
                "confirmation": replacement,
                "user_guide_acknowledged": bool(
                    result.get("requires_user_guide_acknowledgement")
                ),
            },
            result["token"],
        )
        if status == 200:
            self.passwords[email] = replacement
        return status, result

    def test_learning_policy_governance_is_administrator_only(self) -> None:
        status, _ = self.api.dispatch(
            "GET", "/api/admin/learning-policy", token=self.reviewer_token
        )
        self.assertEqual(403, status)
        status, current = self.api.dispatch(
            "GET", "/api/admin/learning-policy", token=self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual("reviewer-memory-v1", current["policy"]["version"])
        status, published = self.api.dispatch(
            "POST",
            "/api/admin/learning-policy",
            {
                "version": "reviewer-memory-http-test",
                "enabled": False,
                "config": {"limit": 4, "rolling_window": 3, "minimum_events": 20},
            },
            self.admin_token,
        )
        self.assertEqual(201, status)
        self.assertFalse(published["policy"]["enabled"])
        status, restored = self.api.dispatch(
            "POST",
            "/api/admin/learning-policy/reviewer-memory-v1/activate",
            {},
            self.admin_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("reviewer-memory-v1", restored["policy"]["version"])

    def test_evidence_pdf_export_is_available_to_administrators_and_reviewers(self) -> None:
        status, reviewer_export = self.api.dispatch(
            "POST", "/api/evidence-export", {}, self.reviewer_token
        )
        self.assertEqual(200, status)
        self.assertEqual("application/pdf", reviewer_export["content_type"])
        status, _ = self.api.dispatch(
            "POST", "/api/evidence-export", {}, self.token
        )
        self.assertEqual(403, status)
        status, exported = self.api.dispatch(
            "POST", "/api/evidence-export", {}, self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual("application/pdf", exported["content_type"])
        self.assertEqual(0, exported["record_count"])
        self.assertTrue(base64.b64decode(exported["data_base64"]).startswith(b"%PDF-1.4"))

    def test_workspace_metrics_are_available_only_to_active_administrators(self) -> None:
        status, response = self.api.dispatch(
            "GET", "/api/admin/metrics", token=self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual(0, response["metrics"]["scored_reviews"])
        self.assertEqual(
            {
                "first_review_approval",
                "correct_level",
                "correct_direction",
                "correct_mapping",
                "overall_hygiene",
            },
            set(response["metrics"]["lifetime_scores"]),
        )

        for token in (self.owner_token, self.reviewer_token):
            with self.subTest(token=token):
                status, response = self.api.dispatch(
                    "GET", "/api/admin/metrics", token=token
                )
                self.assertEqual(403, status)
                self.assertIn("role does not permit", response["error"])

    def test_administrator_sees_temporary_lockout_separately_from_user_status(self) -> None:
        for _ in range(3):
            status, _ = self.api.dispatch(
                "POST",
                "/api/session",
                {"email": "dana@example.org", "password": "WrongPassword1!"},
            )
            self.assertEqual(401, status)

        status, payload = self.api.dispatch(
            "GET", "/api/admin/users", token=self.admin_token
        )
        self.assertEqual(200, status)
        dana = next(user for user in payload["users"] if user["email"] == "dana@example.org")
        self.assertEqual("active", dana["status"])
        self.assertEqual("temporary_lockout", dana["authentication_status"])
        self.assertIsNotNone(dana["locked_until"])

        status, replacement = self.api.dispatch(
            "POST", "/api/auth/recovery", {"email": "dana@example.org"}
        )
        self.assertEqual(202, status)
        self.assertEqual(
            "If an account exists for this email address, instructions to reset the password will be sent to it",
            replacement["message"],
        )

    def test_email_or_role_change_revokes_existing_session(self) -> None:
        dana = self.api.sessions.resolve(self.token)
        status, payload = self.api.dispatch(
            "POST",
            f"/api/admin/users/{dana['id']}",
            {
                "first_name": dana["first_name"],
                "last_name": dana["last_name"],
                "email": "dana.updated@example.org",
                "roles": ["reporter", "reviewer"],
            },
            self.admin_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("dana.updated@example.org", payload["user"]["email"])
        status, _ = self.api.dispatch("GET", "/api/bootstrap", token=self.token)
        self.assertEqual(401, status)

    def test_complete_api_journey(self) -> None:
        status, initial = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        self.assertEqual([], initial["review"])
        self.assertEqual([], initial["evidence"])
        self.assertEqual(0, initial["counts"]["evidence"])
        self.assertEqual(__version__, initial["app_version"])
        self.assertEqual("logic-model-demo-v1", initial["logic_model"]["id"])

        status, logic_model = self.request("/api/logic-model")
        self.assertEqual(200, status)
        self.assertEqual(13, len(logic_model["logic_model"]["elements"]))

        status, created = self.request(
            "/api/statements",
            {"text": "Community partners are using evidence-informed practices in service planning."},
        )
        self.assertEqual(201, status)
        statement = self.make_acceptable(created["statement"])

        status, accepted = self.request(
            f"/api/statements/{statement['id']}/accept",
            {"analysis_id": statement["analysis"]["id"]},
            self.reviewer_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("evidence", accepted["statement"]["status"])
        _, final = self.request("/api/bootstrap", token=self.reviewer_token)
        self.assertEqual([], final["review"])
        self.assertEqual(1, len(final["evidence"]))
        self.assertEqual(100.0, final["quality"]["percentage"])

        status, admin_users = self.request("/api/admin/users", token=self.admin_token)
        self.assertEqual(200, status)
        dana = next(item for item in admin_users["users"] if item["email"] == "dana@example.org")
        reva = next(item for item in admin_users["users"] if item["email"] == "reva@example.org")
        self.assertEqual(1, dana["total_logins"])
        self.assertIsNotNone(dana["last_login_at"])
        self.assertEqual(1, dana["statement_count"])
        self.assertEqual(0, dana["reviewed_statement_count"])
        self.assertEqual(1, reva["reviewed_statement_count"])
        _, admin_bootstrap = self.request("/api/bootstrap", token=self.admin_token)
        self.assertEqual([], admin_bootstrap["evidence"])
        self.assertEqual(1, admin_bootstrap["counts"]["evidence"])

    def test_photo_statement_persists_and_attachment_access_is_authorized(self) -> None:
        image = Image.new("RGB", (80, 40), "white")
        content = io.BytesIO()
        image.save(content, format="PNG")
        status, staged = self.request(
            "/api/media/photo/stage",
            {
                "data_base64": base64.b64encode(content.getvalue()).decode("ascii"),
                "source": "library",
            },
        )
        self.assertEqual(201, status)
        photo = staged["photo"]
        self.assertEqual("no_text", photo["outcome"])
        status, created = self.request(
            "/api/statements",
            {
                "text": "This photo records a completed program planning workshop.",
                "context": "Photographed after the workshop.",
                "capture": {
                    "method": "photo",
                    "stage_id": photo["stage_id"],
                    "candidate_text": "",
                    "edited": True,
                    "provider": photo["provider"],
                    "provider_version": photo["provider_version"],
                    "processing_ms": photo["processing_ms"],
                    "outcome": photo["outcome"],
                },
            },
        )
        self.assertEqual(201, status)
        statement = created["statement"]
        self.assertEqual("photo", statement["capture_method"])
        self.assertEqual(80, statement["attachment"]["width"])
        status, attachment = self.api.dispatch(
            "GET", statement["attachment"]["content_url"], token=self.token
        )
        self.assertEqual(200, status)
        self.assertTrue(
            base64.b64decode(attachment["data_base64"]).startswith(b"\xff\xd8")
        )
        status, _ = self.api.dispatch(
            "GET", statement["attachment"]["content_url"], token=self.owner_token
        )
        self.assertEqual(404, status)
        restarted = MelvanService(
            self.api.service.database,
            DemoModelAdapter(),
            FIXTURE,
            initial_admin_email="owner@example.org",
        )
        restarted.initialize()
        persisted = restarted.get_statement(statement["id"])
        self.assertEqual(statement["attachment"]["checksum"], persisted["attachment"]["checksum"])
        content = restarted.attachment_content(
            persisted["attachment"]["id"],
            statement["reporter_id"],
            {"reporter"},
        )
        self.assertTrue(content["data"].startswith(b"\xff\xd8"))

    def test_voice_transcription_retains_provenance_but_not_audio(self) -> None:
        status, payload = self.request(
            "/api/media/voice/transcribe",
            {
                "data_base64": base64.b64encode(b"transient-audio").decode("ascii"),
                "media_type": "audio/webm",
            },
        )
        self.assertEqual(200, status)
        transcription = payload["transcription"]
        status, created = self.request(
            "/api/statements",
            {
                "text": transcription["text"],
                "context": "",
                "capture": {
                    "method": "voice",
                    "candidate_text": transcription["text"],
                    "edited": False,
                    **transcription,
                },
            },
        )
        self.assertEqual(201, status)
        self.assertEqual("voice", created["statement"]["capture_method"])
        with self.api.service.database.connection() as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            for table in tables:
                columns = connection.execute(
                    f"PRAGMA table_info({table['name']})"
                ).fetchall()
                self.assertFalse(
                    any(
                        column["name"] in {"audio", "audio_bytes"}
                        for column in columns
                    )
                )

    def test_feedback_route_applies_changes_and_accepts_as_evidence(self) -> None:
        _, created = self.request(
            "/api/statements",
            {"text": "Community partners are using evidence-informed practices in service planning."},
        )
        statement = created["statement"]
        status, changed = self.request(
            f"/api/statements/{statement['id']}/feedback",
            {
                "analysis_id": statement["analysis"]["id"],
                "mapping_ids": ["LO5"],
            },
            self.reviewer_token,
        )

        self.assertEqual(200, status)
        self.assertEqual("evidence", changed["statement"]["status"])
        self.assertEqual(["LO5"], [item["id"] for item in changed["statement"]["mappings"]])
        _, workspace = self.request("/api/review", token=self.reviewer_token)
        self.assertEqual([], workspace["statements"])
        self.assertEqual(1, workspace["report"]["counts"]["sent_back"])
        _, evidence = self.request("/api/evidence", token=self.reviewer_token)
        self.assertEqual([statement["id"]], [item["id"] for item in evidence["evidence"]])

    def test_reviewer_session_report_and_not_relevant_routes(self) -> None:
        _, first = self.request(
            "/api/statements", {"text": "A planning meeting was held."}
        )
        _, second = self.request(
            "/api/statements", {"text": "A second planning meeting was held."}
        )
        status, workspace = self.request("/api/review", token=self.reviewer_token)
        self.assertEqual(200, status)
        self.assertIsNotNone(workspace["session"])
        self.assertEqual(2, len(workspace["statements"]))

        status, hidden = self.request(
            f"/api/statements/{first['statement']['id']}/not-relevant",
            {
                "analysis_id": first["statement"]["analysis"]["id"],
                "reason": "Outside the program.",
            },
            self.reviewer_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("not_relevant", hidden["statement"]["status"])
        status, ended = self.request(
            "/api/review/end", {}, self.reviewer_token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, ended["report"]["counts"]["not_relevant"])
        self.assertEqual(0, ended["report"]["session_scores"]["first_review_approval"]["total"])

        status, later = self.request("/api/review", token=self.reviewer_token)
        self.assertEqual(200, status)
        self.assertEqual([second["statement"]["id"]], [item["id"] for item in later["statements"]])

    def test_dual_user_can_review_their_own_statement(self) -> None:
        _, created = self.request(
            "/api/statements", {"text": "A planning meeting was held."}
        )
        _, pending = self.login("dana@example.org")
        dual_reviewer_token = pending["token"]
        status, _ = self.api.dispatch(
            "POST",
            "/api/session/role",
            {"role": "reviewer"},
            dual_reviewer_token,
        )
        self.assertEqual(200, status)
        status, workspace = self.api.dispatch(
            "GET", "/api/review", token=dual_reviewer_token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [created["statement"]["id"]],
            [item["id"] for item in workspace["statements"]],
        )
        created["statement"] = self.make_acceptable(created["statement"])
        status, accepted = self.api.dispatch(
            "POST",
            f"/api/statements/{created['statement']['id']}/accept",
            {"analysis_id": created["statement"]["analysis"]["id"]},
            dual_reviewer_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("evidence", accepted["statement"]["status"])
        with self.api.service.database.connection() as connection:
            event = connection.execute(
                """
                SELECT reviewer_id, is_self_review FROM review_events
                WHERE statement_id = ?
                """,
                (created["statement"]["id"],),
            ).fetchone()
        self.assertEqual(created["statement"]["reporter_id"], event["reviewer_id"])
        self.assertEqual(1, event["is_self_review"])

    def test_empty_statement_is_rejected(self) -> None:
        status, payload = self.request("/api/statements", {"text": "  "})
        self.assertEqual(400, status)
        self.assertIn("Enter a Statement", payload["error"])

    def test_health_exposes_the_authoritative_app_version(self) -> None:
        status, payload = self.api.dispatch("GET", "/api/health")
        self.assertEqual(200, status)
        self.assertEqual(__version__, payload["app_version"])

    def test_reporter_context_history_and_ownership_routes(self) -> None:
        status, created = self.request(
            "/api/statements",
            {"text": "A planning meeting was completed.", "context": "Regional team."},
        )
        self.assertEqual(201, status)
        statement = created["statement"]
        self.assertEqual("Regional team.", statement["context"]["text"])
        status, history = self.request("/api/my-statements")
        self.assertEqual(200, status)
        self.assertEqual([statement["id"]], [item["id"] for item in history["statements"]])
        status, revised = self.request(
            f"/api/statements/{statement['id']}/context",
            {"context": "Regional clinical team."},
        )
        self.assertEqual(200, status)
        self.assertEqual(2, revised["statement"]["context"]["version"])
        status, _ = self.request("/api/my-statements", token=self.reviewer_token)
        self.assertEqual(403, status)

    def test_identity_is_required_and_roles_are_enforced(self) -> None:
        status, _ = self.api.dispatch("GET", "/api/bootstrap")
        self.assertEqual(401, status)
        status, _ = self.request(
            "/api/statements", {"text": "Administrator cannot report."}, self.admin_token
        )
        self.assertEqual(403, status)
        status, admin = self.request("/api/bootstrap", token=self.admin_token)
        self.assertEqual(200, status)
        self.assertIn("users", admin)
        self.assertEqual([], admin["review"])
        dana = next(user for user in admin["users"] if user["email"] == "dana@example.org")
        status, _ = self.request(
            f"/api/admin/users/{dana['id']}/reset-password", {}, self.admin_token
        )
        self.assertEqual(404, status)
        status, _ = self.request(
            f"/api/admin/users/{dana['id']}/reset-password", {}, self.reviewer_token
        )
        self.assertEqual(404, status)
        status, assessments = self.request(
            "/api/evidence-assessments", token=self.reviewer_token
        )
        self.assertEqual(200, status)
        self.assertEqual([], assessments["assessments"])
        status, _ = self.api.dispatch("GET", "/api/evidence-assessments")
        self.assertEqual(401, status)

    def test_initial_password_requires_user_guide_acknowledgement_over_http(self) -> None:
        status, _ = self.api.dispatch(
            "POST",
            "/api/admin/users",
            {
                "first_name": "Guide",
                "last_name": "Reader",
                "email": "guide@example.org",
                "roles": ["reporter"],
            },
            self.admin_token,
        )
        self.assertEqual(201, status)
        temporary_password = self.recovery_password("guide@example.org")
        status, restricted = self.api.dispatch(
            "POST",
            "/api/session",
            {
                "email": "guide@example.org",
                "password": temporary_password,
                "duration": "7_days",
                "timezone": "UTC",
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("initial_setup", restricted["password_setup_purpose"])
        self.assertTrue(restricted["requires_user_guide_acknowledgement"])

        status, refreshed = self.api.dispatch(
            "GET", "/api/session", token=restricted["token"]
        )
        self.assertEqual(200, status)
        self.assertEqual("initial_setup", refreshed["password_setup_purpose"])
        self.assertTrue(refreshed["requires_user_guide_acknowledgement"])

        status, rejected = self.api.dispatch(
            "POST",
            "/api/session/password",
            {
                "password": "GuideStrong1!",
                "confirmation": "GuideStrong1!",
            },
            restricted["token"],
        )
        self.assertEqual(400, status)
        self.assertIn("User Guide", rejected["error"])

        status, accepted = self.api.dispatch(
            "POST",
            "/api/session/password",
            {
                "password": "GuideStrong1!",
                "confirmation": "GuideStrong1!",
                "user_guide_acknowledged": True,
            },
            restricted["token"],
        )
        self.assertEqual(200, status)
        self.assertFalse(accepted["requires_password_change"])

    def test_administrator_builds_and_commits_a_logic_model_draft(self) -> None:
        status, started = self.request(
            "/api/admin/logic-model-draft/start", {}, self.admin_token
        )
        self.assertEqual(200, status)
        draft = started["draft"]
        draft["title"] = "Renamed Logic Model"
        draft["recap"]["context"]["text"] = "Regional delivery context"
        status, saved = self.request(
            "/api/admin/logic-model-draft/save", {"draft": draft}, self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual("Renamed Logic Model", saved["draft"]["title"])

        status, summary = self.request(
            "/api/admin/logic-model-draft/commit-summary", token=self.admin_token
        )
        self.assertEqual(200, status)
        self.assertFalse(summary["summary"]["impact"]["requires_assessment"])
        status, committed = self.request(
            "/api/admin/logic-model-draft/commit", {"confirmed": True}, self.admin_token
        )
        self.assertEqual(200, status)
        self.assertEqual("2.0", committed["logic_model"]["version"])

        status, reporter_attempt = self.request(
            "/api/admin/logic-model-draft/start", {}, self.token
        )
        self.assertEqual(403, status)
        status, removed_upload = self.request(
            "/api/admin/logic-models", {"filename": "old.pdf"}, self.admin_token
        )
        self.assertEqual(404, status)

    def test_active_role_is_required_and_cannot_switch_in_session(self) -> None:
        status, pending = self.login("dana@example.org")
        token = pending["token"]
        status, session = self.api.dispatch("GET", "/api/session", token=token)
        self.assertEqual(200, status)
        self.assertTrue(session["requires_role_selection"])
        status, _ = self.api.dispatch("GET", "/api/bootstrap", token=token)
        self.assertEqual(409, status)
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "administrator"}, token
        )
        self.assertEqual(403, status)
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "reporter"}, token
        )
        self.assertEqual(200, status)
        status, _ = self.api.dispatch("GET", "/api/review", token=token)
        self.assertEqual(403, status)
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "reviewer"}, token
        )
        self.assertEqual(409, status)

    def test_administrator_combinations_use_the_same_role_preference(self) -> None:
        status, _ = self.request(
            "/api/admin/users/user-admin",
            {
                "first_name": "MELvan",
                "last_name": "Administrator",
                "email": "admin@melvan.local",
                "roles": ["reporter", "reviewer"],
            },
            self.admin_token,
        )
        self.assertEqual(200, status)
        status, session = self.login("admin@melvan.local")
        self.assertTrue(session["requires_role_selection"])
        self.assertCountEqual(
            ["owner", "administrator", "reporter", "reviewer"], session["user"]["roles"]
        )
        token = session["token"]
        status, _ = self.api.dispatch(
            "POST", "/api/session/role", {"role": "reporter"}, token
        )
        self.assertEqual(200, status)
        status, _ = self.api.dispatch("GET", "/api/admin/users", token=token)
        self.assertEqual(403, status)
        status, reporter = self.api.dispatch("GET", "/api/bootstrap", token=token)
        self.assertEqual(200, status)
        self.assertEqual("reporter", reporter["active_role"])
        self.assertEqual([], reporter["users"])
        status, created = self.api.dispatch(
            "POST",
            "/api/statements",
            {"text": "The Owner facilitated a program planning session."},
            token,
        )
        self.assertEqual(201, status)
        status, _ = self.api.dispatch("POST", "/api/session/logout", token=token)
        self.assertEqual(200, status)

        status, reviewer_session = self.login("admin@melvan.local")
        self.assertEqual(200, status)
        reviewer_token = reviewer_session["token"]
        status, _ = self.api.dispatch(
            "POST",
            "/api/session/role",
            {"role": "reviewer"},
            reviewer_token,
        )
        self.assertEqual(200, status)
        status, review = self.api.dispatch(
            "GET", "/api/review", token=reviewer_token
        )
        self.assertEqual(200, status)
        self.assertIn(
            created["statement"]["id"],
            [item["id"] for item in review["statements"]],
        )
        created["statement"] = self.make_acceptable(created["statement"])
        status, accepted = self.api.dispatch(
            "POST",
            f"/api/statements/{created['statement']['id']}/accept",
            {"analysis_id": created["statement"]["analysis"]["id"]},
            reviewer_token,
        )
        self.assertEqual(200, status)
        self.assertEqual("evidence", accepted["statement"]["status"])


if __name__ == "__main__":
    unittest.main()
