"""Allow one decision per fix.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the plain index on `fix_id` with a unique constraint, which indexes it too."""
    # Batch mode because SQLite cannot add a constraint to an existing table in place. No row is
    # changed; a database that somehow holds two reviews of one fix would refuse this, which is
    # the point of the rule.
    with op.batch_alter_table("review") as batch:
        batch.drop_index("ix_review_fix_id")
        batch.create_unique_constraint(op.f("uq_review_fix_id"), ["fix_id"])


def downgrade() -> None:
    """Go back to the plain index."""
    with op.batch_alter_table("review") as batch:
        batch.drop_constraint(op.f("uq_review_fix_id"), type_="unique")
        batch.create_index("ix_review_fix_id", ["fix_id"])
