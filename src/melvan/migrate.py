"""Operator-only schema migration command."""

from __future__ import annotations

import argparse
import os

from .config import Settings
from .schema_migrations import (
    current_revision,
    ensure_runtime_role,
    grant_runtime_access,
    upgrade,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Manage the MELvan database schema.")
    value.add_argument("command", choices=("current", "upgrade"))
    value.add_argument("--revision", default="head")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    if settings.deployment_profile != "production" or not settings.database_url:
        raise SystemExit(
            "MELVAN_DEPLOYMENT_PROFILE=production and MELVAN_DATABASE_URL are required."
        )
    if args.command == "current":
        print(current_revision(settings.database_url) or "unversioned")
        return 0
    migration_url = os.getenv("MELVAN_MIGRATION_DATABASE_URL")
    if not migration_url:
        raise SystemExit(
            "MELVAN_MIGRATION_DATABASE_URL is required for schema upgrades. "
            "The application runtime role must not own or migrate its schema."
        )
    upgrade(migration_url, args.revision)
    ensure_runtime_role(migration_url, settings.database_url)
    role = grant_runtime_access(migration_url, settings.database_url)
    print(f"MELvan schema upgraded to {args.revision}.")
    print(f"Runtime database access granted to {role}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
