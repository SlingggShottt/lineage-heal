"""Allow `ingestion_error` as a signal kind.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "kind IN ('test_failed', 'test_warning', 'test_error', 'model_error', 'run_error')"
AFTER = (
    "kind IN ('test_failed', 'test_warning', 'test_error', 'model_error', 'run_error', "
    "'ingestion_error')"
)
# The project's naming convention prefixes this to `ck_signal_kind_is_known`, so the short name
# is the one to pass; the full name would be prefixed twice.
CONSTRAINT = "kind_is_known"


def _replace_kind_check(condition: str) -> None:
    # Batch mode rebuilds the table on SQLite, which cannot alter a check constraint in place,
    # and issues plain ALTER statements on Postgres. Rows are copied across unchanged.
    with op.batch_alter_table("signal") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, condition)


def upgrade() -> None:
    """Add the ingestion-error kind to the signal table's check constraint."""
    _replace_kind_check(AFTER)


def downgrade() -> None:
    """Restore the old constraint.

    Ingestion-error signals would violate it, so they are deleted first. Nothing else is
    lost: they can be collected again from the ingestion log.
    """
    op.execute("DELETE FROM signal WHERE kind = 'ingestion_error'")
    _replace_kind_check(BEFORE)
