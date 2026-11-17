"""Create the lineage version, node and edge tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Create the tables in dependency order."""
    op.create_table(
        "lineage_version",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("activated_at", TIMESTAMP, nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "node_count >= 0 AND edge_count >= 0",
            name=op.f("ck_lineage_version_counts_are_not_negative"),
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_lineage_version")),
    )

    op.create_table(
        "lineage_node",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=512), nullable=False),
        sa.Column("table_name", sa.String(length=256), nullable=False),
        sa.Column("column_name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "kind IN ('raw', 'seed', 'model')", name=op.f("ck_lineage_node_kind_is_known")
        ),
        sa.ForeignKeyConstraint(
            ["version"],
            ["lineage_version.version"],
            name="fk_lineage_node_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version", "node_id", name=op.f("pk_lineage_node")),
    )

    op.create_table(
        "lineage_edge",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('direct', 'transform', 'aggregate', 'filter')",
            name=op.f("ck_lineage_edge_kind_is_known"),
        ),
        sa.CheckConstraint(
            "NOT inferred OR (source LIKE '%.*' AND target LIKE '%.*')",
            name=op.f("ck_lineage_edge_inferred_joins_row_sets"),
        ),
        sa.ForeignKeyConstraint(
            ["version", "source"],
            ["lineage_node.version", "lineage_node.node_id"],
            name="fk_lineage_edge_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version", "target"],
            ["lineage_node.version", "lineage_node.node_id"],
            name="fk_lineage_edge_target",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "version", "source", "target", "kind", name=op.f("pk_lineage_edge")
        ),
    )


def downgrade() -> None:
    """Drop the tables, edges first."""
    op.drop_table("lineage_edge")
    op.drop_table("lineage_node")
    op.drop_table("lineage_version")
