"""Alembic migration entry points kept separate from web replica startup."""

from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT


HEAD_REVISION = "0004"


def _config(database_url: str):
    try:
        from alembic.config import Config
    except ImportError as exc:
        raise RuntimeError(
            "Schema migration requires the 'production' dependency group."
        ) from exc
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(_config(database_url), revision)


def ensure_runtime_role(admin_url: str, runtime_url: str) -> str:
    """Create or rotate the constrained PostgreSQL application login."""

    try:
        from sqlalchemy import String, create_engine
        from sqlalchemy.engine import make_url
    except ImportError as exc:
        raise RuntimeError(
            "Runtime database provisioning requires the 'production' dependency group."
        ) from exc
    runtime = make_url(runtime_url)
    admin = make_url(admin_url)
    role = str(runtime.username or "")
    admin_role = str(admin.username or "")
    password = str(runtime.password or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("MELVAN_DATABASE_URL must contain a valid runtime role.")
    if role == admin_role:
        raise ValueError(
            "The PostgreSQL runtime role must be different from the migration role."
        )
    if not password:
        raise ValueError("MELVAN_DATABASE_URL must contain a runtime-role password.")
    engine = create_engine(admin_url, pool_pre_ping=True)
    quoted_role = engine.dialect.identifier_preparer.quote(role)
    literal_password = String().literal_processor(engine.dialect)(password)
    if not literal_password:
        raise ValueError("The PostgreSQL runtime-role password is invalid.")
    try:
        with engine.begin() as connection:
            exists = connection.exec_driver_sql(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            if exists is None:
                connection.exec_driver_sql(
                    f"CREATE ROLE {quoted_role} WITH LOGIN PASSWORD {literal_password} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
            else:
                connection.exec_driver_sql(
                    f"ALTER ROLE {quoted_role} WITH LOGIN PASSWORD {literal_password} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
    finally:
        engine.dispose()
    return role


def grant_runtime_access(admin_url: str, runtime_url: str) -> str:
    """Grant the non-owner application role only its required schema access."""

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
    except ImportError as exc:
        raise RuntimeError(
            "Runtime database grants require the 'production' dependency group."
        ) from exc
    role = str(make_url(runtime_url).username or "")
    admin_role = str(make_url(admin_url).username or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", role):
        raise ValueError("MELVAN_DATABASE_URL must contain a valid runtime role.")
    if role == admin_role:
        raise ValueError(
            "The PostgreSQL runtime role must be different from the migration role."
        )
    engine = create_engine(admin_url, pool_pre_ping=True)
    quoted_role = engine.dialect.identifier_preparer.quote(role)
    try:
        with engine.begin() as connection:
            attributes = connection.exec_driver_sql(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
                FROM pg_roles WHERE rolname = %s
                """,
                (role,),
            ).fetchone()
            if attributes is None:
                raise ValueError(
                    f"The PostgreSQL runtime role {role!r} does not exist."
                )
            if any(bool(value) for value in attributes):
                raise ValueError(
                    "The PostgreSQL runtime role must not be a superuser, "
                    "database creator, role creator, or RLS bypass role."
                )
            owned = connection.exec_driver_sql(
                """
                SELECT 1
                FROM pg_tables
                WHERE schemaname = 'public' AND tableowner = %s
                LIMIT 1
                """,
                (role,),
            ).fetchone()
            if owned is not None:
                raise ValueError(
                    "The PostgreSQL runtime role must not own application tables."
                )
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON ALL TABLES IN SCHEMA public TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"GRANT USAGE, SELECT, UPDATE "
                f"ON ALL SEQUENCES IN SCHEMA public TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {quoted_role}"
            )
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT EXECUTE ON FUNCTIONS TO {quoted_role}"
            )
    finally:
        engine.dispose()
    return role


def current_revision(database_url: str) -> str | None:
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "Schema inspection requires the 'production' dependency group."
        ) from exc
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            return str(row[0]) if row else None
    except Exception:
        return None
    finally:
        engine.dispose()


def assert_schema_current(database_url: str) -> None:
    current = current_revision(database_url)
    if current != HEAD_REVISION:
        raise RuntimeError(
            "PostgreSQL schema is not current. Run "
            "`python -m melvan.migrate upgrade` before starting web replicas."
        )
