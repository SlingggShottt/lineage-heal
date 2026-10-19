"""SQLAlchemy models for application state.

Alembic migrations in `migrations/versions` are written by hand to match these models; a
test fails if the two ever disagree.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, Index, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from lineageheal.store.types import UTCDateTime

# Fixed names for keys, indexes and checks keep migrations stable and readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}

# Outcomes of one ingestion attempt. "failed" is an attempt that raised an error; the other
# three mirror `IngestionOutcome` in the ingestion package.
OUTCOMES = ("stored", "unchanged", "no_data", "failed")


class Base(DeclarativeBase):
    """Base class for all models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IngestionLog(Base):
    """One row per ingestion attempt, successful or not (FR-ING-06).

    It records what was asked for, what came back and when, so that late or missing data
    can be detected and so that the latest stored version of a republished file can be
    chosen by when it arrived.
    """

    __tablename__ = "ingestion_log"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('stored', 'unchanged', 'no_data', 'failed')", name="outcome_is_known"
        ),
        # A file that exists must be fully described.
        CheckConstraint(
            "outcome NOT IN ('stored', 'unchanged') OR "
            "(object_key IS NOT NULL AND sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name="stored_files_are_described",
        ),
        CheckConstraint("outcome <> 'failed' OR error IS NOT NULL", name="failures_say_why"),
        Index("ix_ingestion_log_lookup", "source", "dataset", "trade_date"),
        Index("ix_ingestion_log_object_key", "object_key"),
    )

    # SQLite only auto-increments a plain INTEGER primary key.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime())
