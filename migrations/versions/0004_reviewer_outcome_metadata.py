"""Persist authoritative Outcome metadata supplied by Reviewers.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_feedback",
        sa.Column("corrected_outcome_category", sa.Text(), nullable=True),
    )
    op.add_column(
        "review_feedback",
        sa.Column(
            "corrected_outcome_change_types",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_check_constraint(
        "review_feedback_corrected_outcome_category",
        "review_feedback",
        "corrected_outcome_category IN ('A', 'B', 'C')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "review_feedback_corrected_outcome_category",
        "review_feedback",
        type_="check",
    )
    op.drop_column("review_feedback", "corrected_outcome_change_types")
    op.drop_column("review_feedback", "corrected_outcome_category")
