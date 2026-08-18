"""Create the initial MELvan PostgreSQL schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-29
"""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _statements() -> list[str]:
    path = Path(__file__).resolve().parents[1] / "postgresql_initial.sql"
    return [
        part.strip()
        for part in path.read_text(encoding="utf-8").split("-- statement")
        if part.strip() and not part.lstrip().startswith("-- Generated once")
    ]


def upgrade() -> None:
    for statement in _statements():
        op.execute(statement)


def downgrade() -> None:
    tables = (
        "password_recovery_requests",
        "authentication_locks",
        "authentication_events",
        "authenticated_sessions",
        "audit_events",
        "review_session_score_snapshots",
        "analysis_learning_examples",
        "learning_snapshots",
        "language_feedback",
        "learning_examples",
        "learning_policies",
        "scoring_events",
        "review_events",
        "review_sessions",
        "evidence_assessment_mappings",
        "evidence_assessments",
        "logic_model_activations",
        "evidence",
        "review_feedback",
        "analysis_mappings",
        "analyses",
        "statement_attachments",
        "media_staging",
        "statement_context_revisions",
        "statements",
        "element_relationships",
        "logic_model_drafts",
        "elements",
        "logic_models",
        "user_roles",
        "workspace_memberships",
        "users",
        "workspaces",
    )
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS melvan_workspace_id()")
