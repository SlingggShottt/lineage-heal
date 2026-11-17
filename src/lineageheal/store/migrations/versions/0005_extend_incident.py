"""Give incidents a severity, a root node, a fingerprint, what they affect and a graph version.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add the columns, their rule and a lookup index.

    Existing rows (there are none outside a developer's database) take the column defaults:
    an error, no root node, an empty fingerprint that never matches a new incident.
    """
    with op.batch_alter_table("incident") as batch:
        batch.add_column(
            sa.Column("severity", sa.String(length=8), server_default="error", nullable=False)
        )
        batch.add_column(sa.Column("root_node", sa.String(length=512), nullable=True))
        batch.add_column(
            sa.Column("fingerprint", sa.String(length=64), server_default="", nullable=False)
        )
        batch.add_column(
            sa.Column("affected", JSON, server_default=sa.text("'{}'"), nullable=False)
        )
        batch.add_column(sa.Column("lineage_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("classified_by", sa.String(length=128), nullable=True))
        batch.create_foreign_key(
            "fk_incident_lineage_version",
            "lineage_version",
            ["lineage_version"],
            ["version"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint("severity_is_known", "severity IN ('error', 'warn')")
        batch.create_index("ix_incident_fingerprint", ["fingerprint"])


def downgrade() -> None:
    """Remove the columns; the diagnosis they held is not kept elsewhere."""
    with op.batch_alter_table("incident") as batch:
        batch.drop_index("ix_incident_fingerprint")
        batch.drop_constraint("severity_is_known", type_="check")
        batch.drop_constraint("fk_incident_lineage_version", type_="foreignkey")
        for column in (
            "classified_by",
            "lineage_version",
            "affected",
            "fingerprint",
            "root_node",
            "severity",
        ):
            batch.drop_column(column)
