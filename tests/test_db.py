from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from melvan.auth import SessionStore
from melvan.db import Database
from melvan.model import DemoModelAdapter
from melvan.service import MelvanService


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class DemoResetTestCase(unittest.TestCase):
    def test_existing_initial_password_session_receives_purpose_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "melvan.sqlite3")
            service = MelvanService(
                database,
                DemoModelAdapter(),
                FIXTURE,
                initial_admin_email="owner@example.org",
            )
            service.initialize()
            sessions = SessionStore(
                service.users,
                database,
                pepper="database-backfill-test-pepper",
            )
            temporary = sessions.bootstrap_owner()["temporary_password"]
            token, _, _, _ = sessions.login("owner@example.org", temporary)
            with database.connection() as connection:
                connection.execute(
                    """
                    UPDATE authenticated_sessions
                    SET password_setup_purpose = NULL
                    WHERE token_hash = ?
                    """,
                    (sessions._token_hash(token),),
                )

            database.initialize(FIXTURE)

            with database.connection() as connection:
                purpose = connection.execute(
                    """
                    SELECT password_setup_purpose FROM authenticated_sessions
                    WHERE token_hash = ?
                    """,
                    (sessions._token_hash(token),),
                ).fetchone()["password_setup_purpose"]
            self.assertEqual("initial_setup", purpose)

    def test_legacy_review_events_gain_self_review_audit_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "melvan.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE review_events (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        statement_id TEXT NOT NULL,
                        analysis_id TEXT NOT NULL,
                        resulting_analysis_id TEXT,
                        reviewer_id TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        comments TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )

            database = Database(path)
            database.initialize(FIXTURE)

            with database.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(review_events)"
                    ).fetchall()
                }
            self.assertIn("is_self_review", columns)

    def test_workspace_scoped_learning_policies_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "melvan.sqlite3")
            database.initialize(FIXTURE)
            with database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO workspaces
                        (id, name, nickname, workspace_type, owner_user_id, created_by, status,
                         created_at, updated_at)
                    VALUES (
                        'workspace-admin', 'Administrator workspace', 'Administrator',
                        'administrator',
                        'user-admin', 'user-admin', 'active', datetime('now'), datetime('now')
                    )
                    """
                )
            with database.workspace("workspace-admin"):
                with database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO learning_policies
                            (version, enabled, is_active, config_json, created_at)
                        VALUES ('workspace-admin-policy', 1, 1, '{}', datetime('now'))
                        """
                    )

            restarted = Database(database.path)
            restarted.initialize(FIXTURE)

            with restarted.connection() as connection:
                active_policies = connection.execute(
                    """
                    SELECT workspace_id, COUNT(*) AS policy_count
                    FROM learning_policies
                    WHERE is_active = 1
                    GROUP BY workspace_id
                    ORDER BY workspace_id
                    """
                ).fetchall()
                indexes = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA index_list(learning_policies)"
                    ).fetchall()
                }
            self.assertEqual(
                [
                    ("workspace-admin", 1),
                    ("workspace-owner", 1),
                ],
                [(row["workspace_id"], row["policy_count"]) for row in active_policies],
            )
            self.assertNotIn("one_active_learning_policy", indexes)
            self.assertIn("one_active_learning_policy_per_workspace", indexes)

    def test_existing_administrator_workspaces_receive_unique_nickname_backfills(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "melvan.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE workspaces (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        workspace_type TEXT NOT NULL,
                        owner_user_id TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                for workspace_id in ("workspace-one", "workspace-two"):
                    connection.execute(
                        """
                        INSERT INTO workspaces
                            (id, name, workspace_type, owner_user_id, created_by,
                             status, created_at, updated_at)
                        VALUES (?, 'Example Administrator', 'administrator', ?, 'user-admin',
                                'active', datetime('now'), datetime('now'))
                        """,
                        (workspace_id, f"user-{workspace_id}"),
                    )

            database = Database(path)
            database.initialize(FIXTURE)
            with database.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(workspaces)")
                }
                nicknames = [
                    row["nickname"]
                    for row in connection.execute(
                        "SELECT nickname FROM workspaces ORDER BY id"
                    ).fetchall()
                ]
            self.assertIn("nickname", columns)
            self.assertEqual(3, len(nicknames))
            self.assertEqual(3, len({nickname.casefold() for nickname in nicknames}))
            self.assertIn("MELvan", nicknames)

    def test_persistent_snapshot_restores_a_fresh_runtime_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "persistent" / "melvan.sqlite3"
            first = Database(root / "runtime-one.sqlite3", snapshot_path=snapshot)
            first.initialize(FIXTURE)
            with first.connection() as connection:
                connection.execute(
                    "INSERT INTO audit_events (id, event_type, entity_type, entity_id, details_json, created_at) "
                    "VALUES ('snapshot-test', 'snapshot_test', 'system', 'snapshot', '{}', datetime('now'))"
                )

            second = Database(root / "runtime-two.sqlite3", snapshot_path=snapshot)
            second.initialize(FIXTURE)
            with second.connection() as connection:
                restored = connection.execute(
                    "SELECT 1 FROM audit_events WHERE id = 'snapshot-test'"
                ).fetchone()
            self.assertIsNotNone(restored)

    def test_legacy_mapping_rows_inherit_analysis_confidence_and_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "melvan.sqlite3")
            service = MelvanService(
                database,
                DemoModelAdapter(),
                FIXTURE,
                initial_admin_email="owner@example.org",
            )
            service.initialize()
            statement = service.create_statement(
                "Community partners are using evidence-informed practices in service planning."
            )
            analysis_id = statement["analysis"]["id"]
            element_id = statement["mappings"][0]["id"]
            with database.connection() as connection:
                connection.execute("DROP TABLE analysis_mappings")
                connection.execute(
                    """
                    CREATE TABLE analysis_mappings (
                        analysis_id TEXT NOT NULL,
                        element_id TEXT NOT NULL,
                        rank INTEGER NOT NULL,
                        PRIMARY KEY (analysis_id, element_id)
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO analysis_mappings (analysis_id, element_id, rank) VALUES (?, ?, 1)",
                    (analysis_id, element_id),
                )
                connection.execute(
                    "UPDATE analyses SET mapping_confidence = 0.61, mapping_rationale = ? WHERE id = ?",
                    ("Legacy aggregate rationale.", analysis_id),
                )

            database.initialize(FIXTURE)
            with database.connection() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(analysis_mappings)"
                    ).fetchall()
                }
                mapping = connection.execute(
                    "SELECT confidence, rationale FROM analysis_mappings WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
            self.assertTrue({"confidence", "rationale"}.issubset(columns))
            self.assertEqual(0.61, mapping["confidence"])
            self.assertEqual("Legacy aggregate rationale.", mapping["rationale"])

    def test_reset_clears_workflow_and_preserves_administrative_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "melvan.sqlite3")
            service = MelvanService(
                database,
                DemoModelAdapter(),
                FIXTURE,
                initial_admin_email="owner@example.org",
            )
            service.initialize()
            reporter = service.create_user(
                {
                    "first_name": "Rita",
                    "last_name": "Reporter",
                    "email": "rita@example.org",
                    "roles": ["reporter"],
                }
            )

            draft = service.start_logic_model_draft("user-admin")
            draft["title"] = "Committed Logic Model"
            service.save_logic_model_draft(draft, "user-admin")
            committed = service.commit_logic_model_draft("user-admin", confirmed=True)
            retained = service.start_logic_model_draft("user-admin")
            retained["title"] = "Unfinished Logic Model"
            service.save_logic_model_draft(retained, "user-admin")

            statement = service.create_statement("We held a neighbourhood poetry gathering.")
            with database.connection() as connection:
                connection.execute(
                    "UPDATE analyses SET match_status = 'existing_match' WHERE id = ?",
                    (statement["analysis"]["id"],),
                )
                connection.execute(
                    """
                    INSERT INTO analysis_mappings
                        (analysis_id, element_id, rank, confidence, rationale)
                    VALUES (?, 'O1', 1, 0.9, 'Test-approved Logic Model mapping.')
                    """,
                    (statement["analysis"]["id"],),
                )
            statement = service.get_statement(statement["id"])
            service.accept_statement(statement["id"], statement["analysis"]["id"])

            database.reset()
            restarted = MelvanService(database, DemoModelAdapter(), FIXTURE)
            restarted.initialize()

            self.assertEqual(
                reporter["id"],
                restarted.users.find_active_by_email("rita@example.org")["id"],
            )
            self.assertEqual(committed["database_id"], restarted.logic_model()["database_id"])
            self.assertEqual(2, sum(not item["is_draft"] for item in restarted.logic_model_versions()))
            self.assertEqual("Unfinished Logic Model", restarted.logic_model_draft()["title"])
            self.assertEqual([], restarted.review_statements())
            self.assertEqual([], restarted.evidence())
            self.assertEqual([], restarted.evidence_review_items())

            with database.connection() as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM statement_context_revisions").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM elements WHERE logic_model_id IS NULL").fetchone()[0])
                self.assertGreater(
                    connection.execute(
                        "SELECT COUNT(*) FROM logic_model_activations"
                    ).fetchone()[0],
                    0,
                )
                self.assertGreater(
                    connection.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE entity_type = 'logic_model'"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
