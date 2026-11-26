"""Add the table of published benchmark runs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Create the table. It touches no existing table."""
    op.create_table(
        "benchmark_run",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("generated_at", TIMESTAMP, nullable=False),
        sa.Column("published_at", TIMESTAMP, nullable=False),
        sa.Column("test_cases", sa.Integer(), nullable=False),
        sa.Column("report", JSON_TYPE, nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_benchmark_run")),
    )
    op.create_index("ix_benchmark_run_generated_at", "benchmark_run", ["generated_at"])


def downgrade() -> None:
    """Drop the table; the reports can be published again from the runs' files."""
    op.drop_table("benchmark_run")
