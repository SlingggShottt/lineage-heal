"""Reading and writing the ingestion log (FR-ING-06)."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.store.models import IngestionLog


@dataclass(frozen=True)
class IngestionAttempt:
    """One attempt to ingest a source's dataset for a trade date.

    `id` is set on attempts read back from the log and left empty when recording one.
    """

    source: str
    dataset: str
    trade_date: date
    outcome: str  # stored, unchanged, no_data or failed
    started_at: datetime
    finished_at: datetime
    object_key: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    http_status: int | None = None
    error: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class StoredVersion:
    """A raw object that was stored for a trade date, and when it first arrived."""

    object_key: str
    sha256: str
    size_bytes: int
    ingested_at: datetime


def _explain(exc: SQLAlchemyError) -> str:
    """Summarise a database error without echoing statements or parameters."""
    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    detail = lines[0] if lines else ""
    return f"{type(cause).__name__}: {detail}"


def _to_attempt(row: IngestionLog) -> IngestionAttempt:
    return IngestionAttempt(
        id=row.id,
        source=row.source,
        dataset=row.dataset,
        trade_date=row.trade_date,
        outcome=row.outcome,
        started_at=row.started_at,
        finished_at=row.finished_at,
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        http_status=row.http_status,
        error=row.error,
    )


class IngestionLogRepository:
    """Records ingestion attempts and answers questions about them."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def record(self, attempt: IngestionAttempt) -> int:
        """Store one attempt.

        Returns:
            The id of the new row.

        Raises:
            StorageError: The database could not be reached, or rejected the attempt
                (for example a stored file without an object key).
        """
        row = IngestionLog(
            source=attempt.source,
            dataset=attempt.dataset,
            trade_date=attempt.trade_date,
            outcome=attempt.outcome,
            object_key=attempt.object_key,
            size_bytes=attempt.size_bytes,
            sha256=attempt.sha256,
            http_status=attempt.http_status,
            error=attempt.error,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )
        try:
            with Session(self._engine) as session, session.begin():
                session.add(row)
                session.flush()
                return row.id
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not write to the ingestion log: {_explain(exc)}") from exc

    def recent(
        self,
        *,
        limit: int = 20,
        source: str | None = None,
        dataset: str | None = None,
        outcome: str | None = None,
    ) -> list[IngestionAttempt]:
        """Return the latest attempts, newest first, optionally filtered.

        Raises:
            StorageError: The database could not be read.
        """
        statement = select(IngestionLog).order_by(
            IngestionLog.finished_at.desc(), IngestionLog.id.desc()
        )
        if source is not None:
            statement = statement.where(IngestionLog.source == source)
        if dataset is not None:
            statement = statement.where(IngestionLog.dataset == dataset)
        if outcome is not None:
            statement = statement.where(IngestionLog.outcome == outcome)
        try:
            with Session(self._engine) as session:
                return [_to_attempt(row) for row in session.scalars(statement.limit(limit))]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the ingestion log: {_explain(exc)}") from exc

    def stored_versions(self, source: str, dataset: str, trade_date: date) -> list[StoredVersion]:
        """Return each raw object stored for a trade date, oldest first.

        A version's time is when it was first stored. Later attempts that found the same
        file unchanged do not move it, and failed or empty attempts add no version. This is
        what lets a republished file be told apart from the original by arrival time.

        Raises:
            StorageError: The database could not be read.
        """
        ingested_at = func.min(IngestionLog.finished_at).label("ingested_at")
        statement = (
            select(
                IngestionLog.object_key, IngestionLog.sha256, IngestionLog.size_bytes, ingested_at
            )
            .where(
                IngestionLog.source == source,
                IngestionLog.dataset == dataset,
                IngestionLog.trade_date == trade_date,
                IngestionLog.outcome == "stored",
            )
            .group_by(IngestionLog.object_key, IngestionLog.sha256, IngestionLog.size_bytes)
            .order_by(ingested_at, IngestionLog.object_key)
        )
        try:
            with Session(self._engine) as session:
                # The table's check constraint guarantees a stored file has all three values.
                return [
                    StoredVersion(
                        object_key=cast("str", key),
                        sha256=cast("str", sha),
                        size_bytes=cast("int", size),
                        ingested_at=when,
                    )
                    for key, sha, size, when in session.execute(statement)
                ]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the ingestion log: {_explain(exc)}") from exc
