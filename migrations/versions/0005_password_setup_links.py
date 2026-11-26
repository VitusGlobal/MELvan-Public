"""Replace temporary passwords with single-use password setup links.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_setup_links",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('initial_setup', 'recovery')",
            name="password_setup_links_purpose",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "password_setup_links_user",
        "password_setup_links",
        ["user_id", "expires_at"],
    )
    op.execute(
        """
        UPDATE users
        SET password_salt = NULL,
            password_hash = NULL,
            temporary_password_expires_at = NULL
        WHERE password_state = 'password_change_required'
          AND temporary_password_expires_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("password_setup_links_user", table_name="password_setup_links")
    op.drop_table("password_setup_links")
