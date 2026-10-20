"""Create the ingestion log.

Revision ID: 0001
Revises:
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ingestion_log table and its indexes."""
    op.create_table(
        "ingestion_log",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('stored', 'unchanged', 'no_data', 'failed')",
            name=op.f("ck_ingestion_log_outcome_is_known"),
        ),
        sa.CheckConstraint(
            "outcome NOT IN ('stored', 'unchanged') OR "
            "(object_key IS NOT NULL AND sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name=op.f("ck_ingestion_log_stored_files_are_described"),
        ),
        sa.CheckConstraint(
            "outcome <> 'failed' OR error IS NOT NULL",
            name=op.f("ck_ingestion_log_failures_say_why"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_log")),
    )
    op.create_index("ix_ingestion_log_lookup", "ingestion_log", ["source", "dataset", "trade_date"])
    op.create_index("ix_ingestion_log_object_key", "ingestion_log", ["object_key"])


def downgrade() -> None:
    """Drop the ingestion_log table."""
    op.drop_index("ix_ingestion_log_object_key", table_name="ingestion_log")
    op.drop_index("ix_ingestion_log_lookup", table_name="ingestion_log")
    op.drop_table("ingestion_log")
