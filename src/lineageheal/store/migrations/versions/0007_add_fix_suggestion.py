"""Add the drafted suggestion to a fix, for root causes outside the dbt project.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable column, so no existing row changes, and the rule that uses it."""
    # Batch mode because SQLite cannot add a constraint to an existing table in place.
    with op.batch_alter_table("fix") as batch:
        batch.add_column(
            sa.Column("suggestion", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_fix_suggestions_have_content"),
            "kind <> 'suggestion' OR suggestion IS NOT NULL",
        )


def downgrade() -> None:
    """Remove the column and its rule."""
    with op.batch_alter_table("fix") as batch:
        batch.drop_constraint(op.f("ck_fix_suggestions_have_content"), type_="check")
        batch.drop_column("suggestion")
