"""Collecting the signals of a run: dbt's own, plus the downloads that failed (FR-DET-01).

The first step of the Detector is deterministic and reads no clock. dbt test failures, model
errors, anomaly monitors and missing expected files already arrive as dbt signals (LH-034,
D-37), because the monitors and the expected-file check are dbt tests. The one source dbt knows
nothing about is a download that failed before dbt ran, which only the ingestion log records.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.runs import RunRepository

# How far back an unanswered download failure is still worth raising. Without a limit a gap in a
# backfill from years ago would become a signal on every run for ever.
DEFAULT_LOOKBACK = timedelta(days=7)

# Longest error text kept in a signal; the log already truncates, this keeps messages readable.
_MAX_ERROR_CHARS = 300


def ingestion_signals(
    attempts: Sequence[IngestionAttempt], *, run_id: str, detected_at: datetime
) -> tuple[Signal, ...]:
    """Turn failed download attempts into signals, one for each feed and trade date.

    When the same feed and date failed more than once, only the latest failure is reported, since
    that is the error a person would act on.

    Args:
        attempts: Failed attempts, as `IngestionLogRepository.unresolved_failures` returns them.
        run_id: The run the signals are collected for.
        detected_at: Stamped on every signal. Callers pass the run's own time rather than the
            clock, so collecting for a past run gives the same signals every time.
    """
    latest: dict[tuple[str, str, str], IngestionAttempt] = {}
    for attempt in sorted(attempts, key=lambda a: (a.finished_at, a.id or 0)):
        latest[(attempt.source, attempt.dataset, attempt.trade_date.isoformat())] = attempt

    signals: list[Signal] = []
    for (source, dataset, trade_date), attempt in sorted(latest.items()):
        status = f"HTTP {attempt.http_status}: " if attempt.http_status else ""
        error = (attempt.error or "no error recorded")[:_MAX_ERROR_CHARS]
        signals.append(
            Signal(
                kind=SignalKind.INGESTION_ERROR,
                severity=Severity.ERROR,
                run_id=run_id,
                detected_at=detected_at,
                name=f"{source}/{dataset}/{trade_date}",
                check_type="ingestion",
                message=f"download failed, {status}{error}",
            )
        )
    return tuple(signals)


class SignalCollector:
    """Gathers every signal that bears on one pipeline run."""

    def __init__(
        self,
        runs: RunRepository,
        ingestion: IngestionLogRepository,
        *,
        lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> None:
        """Create a collector over the run store and the ingestion log."""
        self._runs = runs
        self._ingestion = ingestion
        self._lookback = lookback

    def collect(self, summary: RunSummary, *, artifacts_prefix: str | None = None) -> list[Signal]:
        """Record the run, add its ingestion signals and return all of its signals.

        Collecting is idempotent: running it again for the same run adds nothing. The run's own
        status is left as dbt's results set it, so a failed download raises a signal without
        changing a run that dbt built successfully.

        Args:
            summary: The run's summary, as written by the dbt asset.
            artifacts_prefix: Where the run's artifacts are kept, saved with the run if known.

        Raises:
            StorageError: The run store or the ingestion log could not be read or written.
        """
        self._runs.record(summary, artifacts_prefix=artifacts_prefix)
        attempts = self._ingestion.unresolved_failures(
            since=summary.generated_at - self._lookback, until=summary.generated_at
        )
        found = ingestion_signals(attempts, run_id=summary.run_id, detected_at=summary.generated_at)
        self._runs.add_signals(summary.run_id, found)
        return self._runs.signals_for(summary.run_id)
