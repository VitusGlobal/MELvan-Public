from __future__ import annotations

import multiprocessing
import os
import re
import sqlite3
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from melvan.config import Settings
from melvan.db import Database, DatabaseIntegrityError, PROGRAM_TABLES
from melvan.relational import (
    PostgresDatabase,
    SQLAlchemyConnection,
    _postgres_sql,
    build_database,
)


def _postgres_statement_worker(
    url: str,
    workspace_id: str,
    statement_id: str,
    start: object,
    results: object,
) -> None:
    database = PostgresDatabase(url)
    try:
        start.wait(10)
        with database.workspace(workspace_id):
            with database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO statements
                        (id, original_text, reporter_id, submitted_at, status)
                    VALUES (?, 'Concurrent statement', 'concurrency-reporter',
                            CURRENT_TIMESTAMP, 'awaiting_review')
                    """,
                    (statement_id,),
                )
        results.put(("ok", statement_id))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        database._engine.dispose()


def _postgres_review_session_worker(
    url: str,
    workspace_id: str,
    session_id: str,
    reviewer_id: str,
    start: object,
    results: object,
) -> None:
    database = PostgresDatabase(url)
    try:
        start.wait(10)
        with database.workspace(workspace_id):
            with database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO review_sessions
                        (id, reviewer_id, status, started_at,
                         scoring_policy_version)
                    VALUES (?, ?, 'open', CURRENT_TIMESTAMP, 'review-session-v1')
                    """,
                    (session_id, reviewer_id),
                )
        results.put(("ok", session_id))
    except DatabaseIntegrityError:
        results.put(("conflict", session_id))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        database._engine.dispose()


def _postgres_startup_worker(
    url: str,
    fixture_path: str,
    start: object,
    results: object,
) -> None:
    from melvan.identity import UserDirectory
    from melvan.media import AttachmentStore
    from melvan.model import DemoModelAdapter
    from melvan.service import MelvanService

    database = PostgresDatabase(url)
    try:
        users = UserDirectory(
            database,
            legacy_path=None,
            initial_owner_email="owner@example.org",
        )
        service = MelvanService(
            database,
            DemoModelAdapter(),
            Path(fixture_path),
            user_directory=users,
            attachment_store=AttachmentStore(
                Path("/tmp/melvan-postgres-startup-attachments")
            ),
        )
        start.wait(10)
        service.initialize()
        results.put(("ok", os.getpid()))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        database._engine.dispose()


class FakeMappings:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeResult:
    returns_rows = True
    rowcount = 1

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def mappings(self):
        return FakeMappings(self.rows)


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def exec_driver_sql(self, statement, parameters):
        self.calls.append((statement, parameters))
        return FakeResult([{"value": 1}])


class RelationalBoundaryTestCase(unittest.TestCase):
    def test_postgres_translation_handles_supported_legacy_syntax(self) -> None:
        self.assertEqual(
            "SELECT CURRENT_TIMESTAMP, value FROM sample WHERE id = %s",
            _postgres_sql(
                "SELECT datetime('now'), value FROM sample WHERE id = ?"
            ),
        )
        self.assertEqual(
            "INSERT INTO sample (id) VALUES (%s) ON CONFLICT DO NOTHING",
            _postgres_sql("INSERT OR IGNORE INTO sample (id) VALUES (?)"),
        )

    def test_sqlalchemy_connection_returns_mapping_rows(self) -> None:
        raw = FakeConnection()
        connection = SQLAlchemyConnection(raw)

        row = connection.execute("SELECT ? AS value", (1,)).fetchone()

        self.assertEqual(1, row["value"])
        self.assertEqual(1, row[0])
        self.assertEqual({"value": 1}, dict(row))
        self.assertEqual(("SELECT %s AS value", (1,)), raw.calls[0])

    def test_local_profile_builds_sqlite(self) -> None:
        settings = Settings.from_env(model_mode="demo")
        settings = replace(
            settings,
            deployment_profile="local",
            database_path=Path("/tmp/melvan-relational-test.sqlite3"),
        )
        self.assertIsInstance(build_database(settings), Database)

    def test_production_profile_selects_postgresql(self) -> None:
        settings = Settings.from_env(model_mode="demo")
        settings = replace(
            settings,
            deployment_profile="production",
            database_url="postgresql+psycopg://melvan:test@postgres/melvan",
        )
        sentinel = object()
        with patch("melvan.relational.PostgresDatabase", return_value=sentinel) as factory:
            self.assertIs(sentinel, build_database(settings))
        factory.assert_called_once_with(settings.database_url)

    def test_initial_postgresql_migration_is_portable_and_workspace_owned(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/postgresql_initial.sql"
        ).read_text(encoding="utf-8")

        for unsupported in (
            "PRAGMA ",
            "COLLATE NOCASE",
            "INSERT OR IGNORE",
            "INSERT OR REPLACE",
            "AUTOINCREMENT",
            "datetime('now')",
        ):
            self.assertNotIn(unsupported, migration)
        for table in PROGRAM_TABLES:
            match = re.search(
                rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} "
                r"\((.*?)\)\n-- statement",
                migration,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match, table)
            self.assertIn("workspace_id TEXT", match.group(1), table)
            if table != "logic_model_drafts":
                self.assertIn(
                    "DEFAULT melvan_workspace_id()",
                    match.group(1),
                    table,
                )

    def test_initial_postgresql_tables_match_current_sqlite_columns(self) -> None:
        root = Path(__file__).resolve().parents[1]
        migration = (root / "migrations/postgresql_initial.sql").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.sqlite3"
            Database(path).initialize(
                root / "src/melvan/fixtures/logic_model.json"
            )
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                tables = [
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
                for table in tables:
                    current = {
                        row["name"]
                        for row in connection.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    }
                    match = re.search(
                        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} "
                        r"\((.*?)\)\n-- statement",
                        migration,
                        flags=re.DOTALL,
                    )
                    self.assertIsNotNone(match, table)
                    migrated: set[str] = set()
                    for line in match.group(1).splitlines():
                        parts = line.strip().split(maxsplit=1)
                        if parts:
                            name = parts[0].rstrip(",")
                            if name in current:
                                migrated.add(name)
                    if table == "authenticated_sessions":
                        current.remove("password_setup_purpose")
                    self.assertEqual(current, migrated, table)
            finally:
                connection.close()

    def test_workspace_isolation_migration_forces_rls_and_composite_keys(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/0002_postgresql_workspace_isolation.py"
        ).read_text(encoding="utf-8")

        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("melvan_workspace_writable()", migration)
        self.assertIn("FOREIGN KEY ({child_key}, workspace_id)", migration)
        self.assertIn(
            "SELECT NULLIF(current_setting('melvan.workspace_id', true), '')",
            migration,
        )

    def test_password_setup_migration_retains_initial_or_recovery_purpose(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/0003_password_setup_purpose.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"password_setup_purpose"', migration)
        self.assertIn("'initial_setup', 'recovery'", migration)


@unittest.skipUnless(
    os.getenv("MELVAN_TEST_POSTGRES_URL")
    and os.getenv("MELVAN_TEST_POSTGRES_ADMIN_URL"),
    "PostgreSQL runtime and migration URLs are required for integration.",
)
class PostgreSQLIntegrationTestCase(unittest.TestCase):
    def test_initial_migration_fixture_and_identity(self) -> None:
        from melvan.identity import UserDirectory
        from melvan.media import AttachmentStore
        from melvan.model import DemoModelAdapter
        from melvan.schema_migrations import (
            HEAD_REVISION,
            current_revision,
            grant_runtime_access,
            upgrade,
        )
        from melvan.service import MelvanService

        url = str(os.environ["MELVAN_TEST_POSTGRES_URL"])
        admin_url = str(os.environ["MELVAN_TEST_POSTGRES_ADMIN_URL"])
        upgrade(admin_url)
        grant_runtime_access(admin_url, url)
        self.assertEqual(HEAD_REVISION, current_revision(url))

        database = PostgresDatabase(url)
        fixture = (
            Path(__file__).resolve().parents[1]
            / "src/melvan/fixtures/logic_model.json"
        )
        database.initialize(fixture)
        users = UserDirectory(
            database,
            legacy_path=None,
            initial_owner_email="owner@example.org",
        )
        users.initialize()

        self.assertEqual("owner@example.org", users.owner()["email"])
        test_token = uuid.uuid4().hex[:10]
        administrator = users.create_user(
            {
                "first_name": "Ada",
                "last_name": "Admin",
                "email": f"ada-{test_token}@example.org",
                "roles": ["administrator"],
                "workspace_nickname": f"Ada {test_token}",
            },
            actor_id=users.owner()["id"],
            workspace_id=users.owner()["workspace_id"],
            allow_administrator=True,
        )
        administrator_policy = (
            "reviewer-memory-v1-"
            f"{administrator['workspace_id'].removeprefix('workspace-')}"
        )
        with database.workspace(administrator["workspace_id"]):
            with database.connection() as connection:
                policy = connection.execute(
                    "SELECT workspace_id FROM learning_policies "
                    "WHERE version = ?",
                    (administrator_policy,),
                ).fetchone()
        self.assertEqual(administrator["workspace_id"], policy["workspace_id"])

        service = MelvanService(
            database,
            DemoModelAdapter(),
            fixture,
            user_directory=users,
            attachment_store=AttachmentStore(
                Path("/tmp/melvan-postgres-test-attachments")
            ),
        )
        service.initialize()
        statement = service.create_statement(
            "Researchers improved their capacity to collaborate.",
            users.owner()["id"],
        )
        service.accept_statement(
            statement["id"],
            statement["analysis"]["id"],
            users.owner()["id"],
        )
        self.assertEqual("evidence", service.get_statement(statement["id"])["status"])

        with database.workspace(administrator["workspace_id"]):
            with database.connection() as connection:
                self.assertIsNone(
                    connection.execute(
                        "SELECT id FROM statements WHERE id = ?",
                        (statement["id"],),
                    ).fetchone()
                )
            with self.assertRaises(DatabaseIntegrityError):
                with database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO statement_context_revisions
                            (id, statement_id, version, context_text,
                             created_by, created_at, workspace_id)
                        VALUES (?, ?, 1, '', ?, CURRENT_TIMESTAMP, ?)
                        """,
                        (
                            f"ctx-{uuid.uuid4().hex[:12]}",
                            statement["id"],
                            administrator["id"],
                            administrator["workspace_id"],
                        ),
                    )
            with self.assertRaises(Exception):
                with database.connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO statements
                            (id, original_text, reporter_id, submitted_at,
                             status, workspace_id)
                        VALUES (?, 'forged', ?, CURRENT_TIMESTAMP,
                                'awaiting_review', ?)
                        """,
                        (
                            f"stmt-{uuid.uuid4().hex[:12]}",
                            administrator["id"],
                            users.owner()["workspace_id"],
                        ),
                    )

        with database.workspace(
            administrator["workspace_id"], read_only=True
        ):
            with database.connection() as connection:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT version FROM learning_policies WHERE version = ?",
                        (administrator_policy,),
                    ).fetchone()
                )
                result = connection.execute(
                    "UPDATE learning_policies SET enabled = 0 WHERE version = ?",
                    (administrator_policy,),
                )
                self.assertEqual(0, result.rowcount)

        with database._engine.begin() as connection:
            self.assertEqual(
                0,
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM statements"
                ).scalar_one(),
            )

        process_context = multiprocessing.get_context("spawn")
        start = process_context.Event()
        results = process_context.Queue()
        concurrent_ids = [
            f"stmt-{uuid.uuid4().hex[:12]}" for _ in range(4)
        ]
        processes = [
            process_context.Process(
                target=_postgres_statement_worker,
                args=(
                    url,
                    users.owner()["workspace_id"],
                    statement_id,
                    start,
                    results,
                ),
            )
            for statement_id in concurrent_ids
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        outcomes = [results.get(timeout=2) for _ in processes]
        self.assertTrue(all(item[0] == "ok" for item in outcomes), outcomes)
        with database.connection() as connection:
            placeholders = ",".join("?" for _ in concurrent_ids)
            self.assertEqual(
                len(concurrent_ids),
                connection.execute(
                    f"SELECT COUNT(*) FROM statements WHERE id IN ({placeholders})",
                    tuple(concurrent_ids),
                ).fetchone()[0],
            )

        singleton_start = process_context.Event()
        singleton_results = process_context.Queue()
        reviewer_id = f"reviewer-{uuid.uuid4().hex[:10]}"
        singleton_processes = [
            process_context.Process(
                target=_postgres_review_session_worker,
                args=(
                    url,
                    users.owner()["workspace_id"],
                    f"review-session-{uuid.uuid4().hex[:10]}",
                    reviewer_id,
                    singleton_start,
                    singleton_results,
                ),
            )
            for _ in range(2)
        ]
        for process in singleton_processes:
            process.start()
        singleton_start.set()
        for process in singleton_processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        singleton_outcomes = [
            singleton_results.get(timeout=2) for _ in singleton_processes
        ]
        self.assertEqual(
            ["conflict", "ok"],
            sorted(item[0] for item in singleton_outcomes),
            singleton_outcomes,
        )

        startup_start = process_context.Event()
        startup_results = process_context.Queue()
        startup_processes = [
            process_context.Process(
                target=_postgres_startup_worker,
                args=(url, str(fixture), startup_start, startup_results),
            )
            for _ in range(2)
        ]
        for process in startup_processes:
            process.start()
        startup_start.set()
        for process in startup_processes:
            process.join(20)
            self.assertEqual(0, process.exitcode)
        startup_outcomes = [
            startup_results.get(timeout=2) for _ in startup_processes
        ]
        self.assertTrue(
            all(item[0] == "ok" for item in startup_outcomes),
            startup_outcomes,
        )

        with database.connection() as connection:
            model = connection.execute(
                "SELECT id, version FROM logic_models WHERE is_current = 1"
            ).fetchone()
            self.assertIsNotNone(model)
            self.assertGreater(
                connection.execute("SELECT COUNT(*) FROM elements").fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
