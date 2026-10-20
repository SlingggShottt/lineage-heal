"""Recording ingestion attempts in the ingestion log (FR-ING-06).

The log is optional: when no database is configured, a recorder that does nothing is used,
so ingestion keeps working on a laptop with no Postgres. When a database is configured, a
failure to write the log is an error rather than something silently skipped, because the
log is what late-data detection and version selection depend on.
"""

from datetime import date, datetime
from typing import Protocol

import structlog
from sqlalchemy import Engine

from lineageheal.core.settings import Settings
from lineageheal.ingestion.raw_zone import StoredObject
from lineageheal.ingestion.types import IngestionResult
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository

log = structlog.get_logger(__name__)

# Enough to read a failure, short enough not to bloat the table with a stack trace.
MAX_ERROR_LENGTH = 2000


def attempt_from_result(
    result: IngestionResult, *, started_at: datetime, finished_at: datetime
) -> IngestionAttempt:
    """Describe a completed ingestion (stored, unchanged or no data) as a log entry."""
    stored = result.stored
    return IngestionAttempt(
        source=result.source,
        dataset=result.dataset,
        trade_date=result.trade_date,
        outcome=result.outcome.value,
        started_at=started_at,
        finished_at=finished_at,
        object_key=stored.key if stored else None,
        size_bytes=stored.size_bytes if stored else None,
        sha256=stored.sha256 if stored else None,
        http_status=result.http_status,
    )


def attempt_from_failure(
    *,
    source: str,
    dataset: str,
    trade_date: date,
    error: Exception,
    started_at: datetime,
    finished_at: datetime,
) -> IngestionAttempt:
    """Describe an ingestion that raised an error. The message is truncated."""
    return IngestionAttempt(
        source=source,
        dataset=dataset,
        trade_date=trade_date,
        outcome="failed",
        started_at=started_at,
        finished_at=finished_at,
        error=str(error)[:MAX_ERROR_LENGTH] or type(error).__name__,
    )


def attempt_from_injection(
    *,
    source: str,
    dataset: str,
    trade_date: date,
    stored: StoredObject,
    started_at: datetime,
    finished_at: datetime,
) -> IngestionAttempt:
    """Describe a file written to the raw zone by a fault injector rather than downloaded.

    It is logged like any other stored version, with no HTTP status, so the log matches what
    is actually in the raw zone.
    """
    return IngestionAttempt(
        source=source,
        dataset=dataset,
        trade_date=trade_date,
        outcome="stored" if stored.created else "unchanged",
        started_at=started_at,
        finished_at=finished_at,
        object_key=stored.key,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )


class AttemptRecorder(Protocol):
    """Where ingestion attempts are written."""

    def record(self, attempt: IngestionAttempt) -> None:
        """Write one attempt."""
        ...

    def close(self) -> None:
        """Release any connection."""
        ...


class NullRecorder:
    """Keeps no log. Used when no database is configured."""

    def record(self, attempt: IngestionAttempt) -> None:
        """Ignore the attempt."""

    def close(self) -> None:
        """Nothing to release."""


class DatabaseRecorder:
    """Writes attempts to the ingestion_log table."""

    def __init__(self, engine: Engine) -> None:
        """Create a recorder over a database engine."""
        self._engine = engine
        self._repository = IngestionLogRepository(engine)

    def record(self, attempt: IngestionAttempt) -> None:
        """Write one attempt.

        Raises:
            StorageError: The database rejected or could not receive the attempt.
        """
        self._repository.record(attempt)

    def close(self) -> None:
        """Release the database connections."""
        self._engine.dispose()


def open_recorder(settings: Settings) -> AttemptRecorder:
    """Return a database recorder if DATABASE_URL is set, otherwise one that keeps no log."""
    if settings.database_url is None:
        log.debug("ingestion_log_disabled", reason="DATABASE_URL is not set")
        return NullRecorder()
    return DatabaseRecorder(create_db_engine(settings.database_url.get_secret_value()))
