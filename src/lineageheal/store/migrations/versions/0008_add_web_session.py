"""Add the table of signed-in dashboard sessions.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Create the table. It touches no existing table."""
    op.create_table(
        "web_session",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_web_session_expires_after_creation")
        ),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_web_session")),
    )
    op.create_index("ix_web_session_expires_at", "web_session", ["expires_at"])


def downgrade() -> None:
    """Drop the table; the only loss is that everyone signs in again."""
    op.drop_table("web_session")
