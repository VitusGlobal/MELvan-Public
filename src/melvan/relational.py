"""Profile-aware relational persistence adapters."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from .config import Settings
from .db import (
    Database,
    DatabaseConnection,
    DatabaseIntegrityError,
    OWNER_WORKSPACE_ID,
)

T = TypeVar("T")


_postgres_workspace: ContextVar[str] = ContextVar(
    "melvan_postgres_workspace_id", default=OWNER_WORKSPACE_ID
)
_postgres_read_only: ContextVar[bool] = ContextVar(
    "melvan_postgres_read_only", default=False
)


def _postgres_sql(statement: str) -> str:
    sql = statement.strip()
    sql = re.sub(r"\bdatetime\s*\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", sql)
    sql = sql.replace("?", "%s")
    if re.match(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO\b", sql):
        sql = re.sub(
            r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            sql,
            count=1,
        )
        sql = f"{sql.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
    return sql


class SQLAlchemyResult:
    def __init__(self, result: Any):
        self._result = result
        self.rowcount = int(result.rowcount or 0)
        self._mappings = result.mappings() if result.returns_rows else None

    def fetchone(self) -> Any | None:
        if self._mappings is None:
            return None
        row = self._mappings.fetchone()
        return CompatibilityRow(row) if row is not None else None

    def fetchall(self) -> list[Any]:
        if self._mappings is None:
            return []
        return [CompatibilityRow(row) for row in self._mappings.fetchall()]


class CompatibilityRow(Mapping[str, Any]):
    """Expose SQLAlchemy mappings with sqlite3.Row-compatible indexing."""

    def __init__(self, row: Mapping[str, Any]):
        self._mapping = dict(row)
        self._values = tuple(self._mapping.values())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)


class SQLAlchemyConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(
        self, statement: str, parameters: tuple[Any, ...] = ()
    ) -> SQLAlchemyResult:
        try:
            result = self._connection.exec_driver_sql(
                _postgres_sql(statement),
                tuple(parameters),
            )
        except Exception as exc:
            try:
                from sqlalchemy.exc import IntegrityError
            except ImportError:
                IntegrityError = ()  # type: ignore[assignment]
            if isinstance(exc, IntegrityError):
                raise DatabaseIntegrityError(str(exc)) from exc
            raise
        return SQLAlchemyResult(result)

    def executemany(
        self, statement: str, parameters: list[tuple[Any, ...]]
    ) -> SQLAlchemyResult:
        try:
            result = self._connection.exec_driver_sql(
                _postgres_sql(statement),
                [tuple(row) for row in parameters],
            )
        except Exception as exc:
            try:
                from sqlalchemy.exc import IntegrityError
            except ImportError:
                IntegrityError = ()  # type: ignore[assignment]
            if isinstance(exc, IntegrityError):
                raise DatabaseIntegrityError(str(exc)) from exc
            raise
        return SQLAlchemyResult(result)

    def executescript(self, script: str) -> SQLAlchemyResult:
        result: SQLAlchemyResult | None = None
        for statement in (part.strip() for part in script.split(";")):
            if statement:
                result = self.execute(statement)
        if result is None:
            raise ValueError("SQL script contained no statements.")
        return result


class PostgresDatabase:
    """SQLAlchemy Core transaction boundary for PostgreSQL."""

    dialect = "postgresql"

    def __init__(self, url: str):
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("PostgresDatabase requires a PostgreSQL URL.")
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise RuntimeError(
                "The production profile requires the 'production' dependency group."
            ) from exc
        self.url = url
        self.path = Path("/non-filesystem/postgresql")
        self.snapshot_path = None
        self._engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=10,
            pool_recycle=300,
            future=True,
        )

    def reset(self) -> None:
        raise RuntimeError(
            "Production PostgreSQL cannot be reset with the demo reset command."
        )

    def initialize(self, fixture_path: Path) -> None:
        from .schema_migrations import assert_schema_current

        assert_schema_current(self.url)
        self.run_transaction(
            lambda connection: Database._load_fixture(connection, fixture_path)
        )

    @contextmanager
    def startup_lock(self) -> Iterator[None]:
        with self._engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("melvan-startup",),
            )
            yield

    def run_transaction(
        self,
        operation: Callable[[DatabaseConnection], T],
        *,
        attempts: int = 3,
    ) -> T:
        if attempts < 1:
            raise ValueError("Transaction attempts must be at least one.")
        for attempt in range(attempts):
            try:
                with self.connection() as connection:
                    return operation(connection)
            except Exception as exc:
                sqlstate = str(
                    getattr(getattr(exc, "orig", None), "sqlstate", "")
                    or getattr(exc, "sqlstate", "")
                )
                if sqlstate not in {"40001", "40P01", "55P03"}:
                    raise
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.05 * (2**attempt))
        raise RuntimeError("Transaction retry loop exited unexpectedly.")

    @contextmanager
    def workspace(
        self, workspace_id: str, *, read_only: bool = False
    ) -> Iterator[None]:
        workspace_token = _postgres_workspace.set(
            str(workspace_id or OWNER_WORKSPACE_ID)
        )
        access_token = _postgres_read_only.set(bool(read_only))
        try:
            yield
        finally:
            _postgres_read_only.reset(access_token)
            _postgres_workspace.reset(workspace_token)

    @staticmethod
    def current_workspace_id() -> str:
        return _postgres_workspace.get()

    @contextmanager
    def connection(self) -> Iterator[DatabaseConnection]:
        with self._engine.begin() as connection:
            connection.exec_driver_sql(
                "SELECT set_config('melvan.workspace_id', %s, true)",
                (self.current_workspace_id(),),
            )
            connection.exec_driver_sql(
                "SELECT set_config('melvan.workspace_access', %s, true)",
                ("read_only" if _postgres_read_only.get() else "read_write",),
            )
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            yield SQLAlchemyConnection(connection)

    def readiness(self) -> dict[str, str]:
        try:
            with self._engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return {"status": "ready", "backend": "postgresql"}
        except Exception:
            return {"status": "unavailable", "backend": "postgresql"}


RelationalDatabase = Database | PostgresDatabase


def build_database(settings: Settings) -> RelationalDatabase:
    if settings.deployment_profile == "production":
        if not settings.database_url:
            raise ValueError(
                "The production profile requires MELVAN_DATABASE_URL."
            )
        return PostgresDatabase(settings.database_url)
    return Database(
        settings.database_path,
        snapshot_path=(
            settings.persistent_database_path
            if settings.deployment_profile == "hosted_demo"
            else None
        ),
    )
