"""Enforce PostgreSQL workspace isolation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


PROGRAM_TABLES = (
    "logic_models",
    "elements",
    "logic_model_drafts",
    "element_relationships",
    "statements",
    "statement_context_revisions",
    "media_staging",
    "statement_attachments",
    "analyses",
    "analysis_mappings",
    "review_feedback",
    "evidence",
    "logic_model_activations",
    "evidence_assessments",
    "evidence_assessment_mappings",
    "review_sessions",
    "review_events",
    "scoring_events",
    "learning_policies",
    "learning_examples",
    "language_feedback",
    "learning_snapshots",
    "analysis_learning_examples",
    "review_session_score_snapshots",
    "audit_events",
)


WORKSPACE_RELATIONSHIPS = (
    ("elements", "logic_model_id", "logic_models", "id"),
    ("logic_model_drafts", "base_logic_model_id", "logic_models", "id"),
    ("element_relationships", "to_element_id", "elements", "id"),
    ("element_relationships", "from_element_id", "elements", "id"),
    ("statement_context_revisions", "statement_id", "statements", "id"),
    ("statement_attachments", "statement_id", "statements", "id"),
    ("analyses", "context_revision_id", "statement_context_revisions", "id"),
    ("analyses", "proposed_element_id", "elements", "id"),
    ("analyses", "statement_id", "statements", "id"),
    ("analysis_mappings", "element_id", "elements", "id"),
    ("analysis_mappings", "analysis_id", "analyses", "id"),
    ("review_feedback", "analysis_id", "analyses", "id"),
    ("review_feedback", "statement_id", "statements", "id"),
    ("evidence", "analysis_id", "analyses", "id"),
    ("evidence", "statement_id", "statements", "id"),
    ("logic_model_activations", "previous_logic_model_id", "logic_models", "id"),
    ("logic_model_activations", "logic_model_id", "logic_models", "id"),
    ("evidence_assessments", "activation_id", "logic_model_activations", "id"),
    ("evidence_assessments", "source_assessment_id", "evidence_assessments", "id"),
    ("evidence_assessments", "source_logic_model_id", "logic_models", "id"),
    ("evidence_assessments", "accepted_analysis_id", "analyses", "id"),
    ("evidence_assessments", "logic_model_id", "logic_models", "id"),
    ("evidence_assessments", "evidence_id", "evidence", "id"),
    ("evidence_assessment_mappings", "element_id", "elements", "id"),
    (
        "evidence_assessment_mappings",
        "assessment_id",
        "evidence_assessments",
        "id",
    ),
    ("review_events", "evidence_assessment_id", "evidence_assessments", "id"),
    ("review_events", "resulting_analysis_id", "analyses", "id"),
    ("review_events", "analysis_id", "analyses", "id"),
    ("review_events", "statement_id", "statements", "id"),
    ("review_events", "session_id", "review_sessions", "id"),
    ("scoring_events", "first_review_event_id", "review_events", "id"),
    ("scoring_events", "evidence_assessment_id", "evidence_assessments", "id"),
    ("scoring_events", "statement_id", "statements", "id"),
    ("learning_examples", "policy_version", "learning_policies", "version"),
    (
        "learning_examples",
        "evidence_assessment_id",
        "evidence_assessments",
        "id",
    ),
    ("learning_examples", "resulting_analysis_id", "analyses", "id"),
    ("learning_examples", "source_analysis_id", "analyses", "id"),
    ("learning_examples", "statement_id", "statements", "id"),
    ("learning_examples", "review_event_id", "review_events", "id"),
    ("language_feedback", "statement_id", "statements", "id"),
    ("language_feedback", "review_event_id", "review_events", "id"),
    ("learning_snapshots", "policy_version", "learning_policies", "version"),
    ("learning_snapshots", "analysis_id", "analyses", "id"),
    ("learning_snapshots", "statement_id", "statements", "id"),
    (
        "analysis_learning_examples",
        "learning_example_id",
        "learning_examples",
        "id",
    ),
    ("analysis_learning_examples", "analysis_id", "analyses", "id"),
    (
        "review_session_score_snapshots",
        "learning_policy_version",
        "learning_policies",
        "version",
    ),
    (
        "review_session_score_snapshots",
        "session_id",
        "review_sessions",
        "id",
    ),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION melvan_workspace_id() RETURNS TEXT
        LANGUAGE SQL STABLE AS $$
            SELECT NULLIF(current_setting('melvan.workspace_id', true), '')
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION melvan_workspace_writable() RETURNS BOOLEAN
        LANGUAGE SQL STABLE AS $$
            SELECT COALESCE(
                current_setting('melvan.workspace_access', true) = 'read_write',
                FALSE
            )
        $$
        """
    )

    referenced_keys = sorted(
        {(parent, parent_key) for _, _, parent, parent_key in WORKSPACE_RELATIONSHIPS}
    )
    for index, (table, column) in enumerate(referenced_keys, start=1):
        op.execute(
            f"CREATE UNIQUE INDEX melvan_workspace_parent_{index:02d} "
            f"ON {table} ({column}, workspace_id)"
        )

    for index, (child, child_key, parent, parent_key) in enumerate(
        WORKSPACE_RELATIONSHIPS,
        start=1,
    ):
        op.execute(
            f"ALTER TABLE {child} "
            f"ADD CONSTRAINT melvan_workspace_fk_{index:02d} "
            f"FOREIGN KEY ({child_key}, workspace_id) "
            f"REFERENCES {parent} ({parent_key}, workspace_id)"
        )

    for table in PROGRAM_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY melvan_workspace_select ON {table}
            FOR SELECT
            USING (workspace_id = melvan_workspace_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY melvan_workspace_write ON {table}
            FOR ALL
            USING (
                workspace_id = melvan_workspace_id()
                AND melvan_workspace_writable()
            )
            WITH CHECK (
                workspace_id = melvan_workspace_id()
                AND melvan_workspace_writable()
            )
            """
        )


def downgrade() -> None:
    for table in reversed(PROGRAM_TABLES):
        op.execute(f"DROP POLICY IF EXISTS melvan_workspace_write ON {table}")
        op.execute(f"DROP POLICY IF EXISTS melvan_workspace_select ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for index in range(len(WORKSPACE_RELATIONSHIPS), 0, -1):
        child = WORKSPACE_RELATIONSHIPS[index - 1][0]
        op.execute(
            f"ALTER TABLE {child} "
            f"DROP CONSTRAINT IF EXISTS melvan_workspace_fk_{index:02d}"
        )

    referenced_keys = sorted(
        {(parent, parent_key) for _, _, parent, parent_key in WORKSPACE_RELATIONSHIPS}
    )
    for index in range(len(referenced_keys), 0, -1):
        op.execute(f"DROP INDEX IF EXISTS melvan_workspace_parent_{index:02d}")

    op.execute("DROP FUNCTION IF EXISTS melvan_workspace_writable()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION melvan_workspace_id() RETURNS TEXT
        LANGUAGE SQL STABLE AS $$
            SELECT COALESCE(
                NULLIF(current_setting('melvan.workspace_id', true), ''),
                'workspace-owner'
            )
        $$
        """
    )
