#!/usr/bin/env python3
"""Generate the frozen PostgreSQL schema for Alembic revision 0001."""

from __future__ import annotations

import re
from pathlib import Path

from melvan.db import OWNER_WORKSPACE_ID, PROGRAM_TABLES, SCHEMA


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "migrations/postgresql_initial.sql"


def statements(script: str) -> list[str]:
    return [part.strip() for part in script.split(";") if part.strip()]


def add_workspace_column(statement: str, table: str) -> str:
    if table == "logic_model_drafts":
        return """
CREATE TABLE IF NOT EXISTS logic_model_drafts (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    base_logic_model_id TEXT REFERENCES logic_models(id),
    draft_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""".strip()
    closing = statement.rfind(")")
    if closing < 0:
        raise ValueError(f"Could not add workspace ownership to {table}.")
    before = statement[:closing].rstrip()
    if before.endswith(","):
        separator = "\n"
    else:
        separator = ",\n"
    return (
        f"{before}{separator}"
        "    workspace_id TEXT NOT NULL DEFAULT melvan_workspace_id() "
        "REFERENCES workspaces(id)\n"
        f"{statement[closing:]}"
    )


def build() -> str:
    output = [
        "-- Generated once for Alembic revision 0001. Do not regenerate after release.",
        "-- statement",
        (
            "CREATE FUNCTION melvan_workspace_id() RETURNS TEXT "
            "LANGUAGE SQL STABLE AS $$ "
            "SELECT COALESCE(NULLIF(current_setting('melvan.workspace_id', true), ''), "
            f"'{OWNER_WORKSPACE_ID}') $$"
        ),
    ]
    for raw in statements(SCHEMA):
        if raw.upper().startswith("PRAGMA "):
            continue
        statement = raw.replace(" COLLATE NOCASE", "")
        match = re.match(
            r"(?is)^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z0-9_]+)",
            statement,
        )
        table = match.group(1) if match else None
        if table in PROGRAM_TABLES:
            statement = add_workspace_column(statement, table)
        output.extend(["-- statement", statement])

    output.extend(
        [
            "-- statement",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_user_email "
                "ON users(lower(email))"
            ),
            "-- statement",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_workspace_nickname "
                "ON workspaces(lower(nickname))"
            ),
            "-- statement",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "one_current_logic_model_per_workspace "
                "ON logic_models(workspace_id, is_current) WHERE is_current = 1"
            ),
            "-- statement",
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "one_active_learning_policy_per_workspace "
                "ON learning_policies(workspace_id, is_active) WHERE is_active = 1"
            ),
        ]
    )
    for table in PROGRAM_TABLES:
        output.extend(
            [
                "-- statement",
                f"CREATE INDEX IF NOT EXISTS {table}_workspace ON {table}(workspace_id)",
            ]
        )
    output.extend(
        [
            "-- statement",
            (
                "INSERT INTO workspaces "
                "(id, name, nickname, workspace_type, owner_user_id, created_by, "
                "status, created_at, updated_at) VALUES "
                f"('{OWNER_WORKSPACE_ID}', 'MELvan', 'MELvan', 'owner', "
                "'user-admin', 'system-migration', 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING"
            ),
            "-- statement",
            (
                "INSERT INTO learning_policies "
                "(version, enabled, is_active, config_json, created_at, workspace_id) "
                "VALUES ('reviewer-memory-v1', 1, 1, "
                "'{\"limit\": 8, \"rolling_window\": 3, \"minimum_events\": 20}', "
                f"CURRENT_TIMESTAMP, '{OWNER_WORKSPACE_ID}') "
                "ON CONFLICT DO NOTHING"
            ),
            "",
        ]
    )
    return "\n".join(output)


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(build(), encoding="utf-8")
    print(TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
