"""Retain the purpose of restricted password-setup sessions.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "authenticated_sessions",
        sa.Column("password_setup_purpose", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "authenticated_sessions_password_setup_purpose_check",
        "authenticated_sessions",
        "password_setup_purpose IN ('initial_setup', 'recovery')",
    )
    op.execute(
        """
        UPDATE authenticated_sessions AS session
        SET password_setup_purpose = CASE
            WHEN users.total_logins = 0
             AND users.password_state IN (
                 'bootstrap_required', 'password_change_required'
             ) THEN 'initial_setup'
            ELSE 'recovery'
        END
        FROM users
        WHERE users.id = session.user_id
          AND session.session_kind = 'password_change'
          AND session.password_setup_purpose IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "authenticated_sessions_password_setup_purpose_check",
        "authenticated_sessions",
        type_="check",
    )
    op.drop_column("authenticated_sessions", "password_setup_purpose")
