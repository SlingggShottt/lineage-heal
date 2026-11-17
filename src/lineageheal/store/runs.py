"""Pipeline runs and their signals in the application database (FR-WH-07, FR-DET-01).

LH-034 writes each dbt run's summary to a file; this module imports that summary, so the
Detector and the dashboard can query runs and signals without reading object storage.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import PipelineRun
from lineageheal.store.models import Signal as SignalRow


class RunRepository:
    """Imports run summaries and reads their signals back."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def record(
        self,
        summary: RunSummary,
        *,
        artifacts_prefix: str | None = None,
        recorded_at: datetime | None = None,
    ) -> bool:
        """Import one run and its signals.

        Importing is idempotent: a run that is already recorded is left exactly as it was,
        because a run's summary never changes once written.

        Args:
            summary: The run's summary, as read from its signals file.
            artifacts_prefix: Where the run's manifest and results are kept, if known.
            recorded_at: When the import happens; defaults to now.

        Returns:
            True if the run was new, False if it was already recorded.

        Raises:
            StorageError: The database could not be reached or rejected the run.
        """
        try:
            with Session(self._engine) as session, session.begin():
                if session.get(PipelineRun, summary.run_id) is not None:
                    return False
                session.add(
                    PipelineRun(
                        run_id=summary.run_id,
                        dbt_invocation_id=summary.dbt_invocation_id,
                        status="failed" if summary.has_failures else "succeeded",
                        generated_at=summary.generated_at,
                        counts=dict(summary.counts),
                        skipped_models=list(summary.skipped_models),
                        artifacts_prefix=artifacts_prefix,
                        recorded_at=recorded_at or datetime.now(UTC),
                    )
                )
                # The run must exist before its signals, which point at it.
                session.flush()
                session.add_all(_to_row(signal) for signal in summary.signals)
                return True
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not record the run: {explain_database_error(exc)}") from exc

    def add_signals(self, run_id: str, signals: Sequence[Signal]) -> int:
        """Attach more signals to a recorded run, skipping any it already has.

        The Detector uses this for signals that do not come from the dbt run itself, such as
        failed downloads. A signal is the same one if its kind and name match, so adding the same
        signals twice stores them once.

        Args:
            run_id: A run that has been recorded.
            signals: Signals whose `run_id` is that run's.

        Returns:
            How many signals were new.

        Raises:
            StorageError: A signal belongs to another run, the run is not recorded, or the
                database could not be written.
        """
        if any(signal.run_id != run_id for signal in signals):
            raise StorageError(f"Cannot add signals of another run to run {run_id}")
        try:
            with Session(self._engine) as session, session.begin():
                if session.get(PipelineRun, run_id) is None:
                    raise StorageError(f"Run {run_id} is not recorded, so it cannot take signals")
                known = {
                    (kind, name)
                    for kind, name in session.execute(
                        select(SignalRow.kind, SignalRow.name).where(SignalRow.run_id == run_id)
                    )
                }
                fresh = [s for s in signals if (s.kind.value, s.name) not in known]
                session.add_all(_to_row(signal) for signal in fresh)
                return len(fresh)
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not add signals: {explain_database_error(exc)}") from exc

    def signals_for(self, run_id: str) -> list[Signal]:
        """Return a run's signals in the order they were recorded.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                rows = session.scalars(
                    select(SignalRow).where(SignalRow.run_id == run_id).order_by(SignalRow.id)
                )
                return [to_signal(row) for row in rows]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read signals: {explain_database_error(exc)}") from exc

    def status_of(self, run_id: str) -> str | None:
        """Return `succeeded` or `failed` for a recorded run, or None if it is unknown.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                run = session.get(PipelineRun, run_id)
                return None if run is None else run.status
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the run: {explain_database_error(exc)}") from exc


def _to_row(signal: Signal) -> SignalRow:
    return SignalRow(
        run_id=signal.run_id,
        kind=signal.kind.value,
        severity=signal.severity.value,
        detected_at=signal.detected_at,
        name=signal.name,
        unique_id=signal.unique_id,
        check_type=signal.check_type,
        models=list(signal.models),
        model=signal.model,
        column_name=signal.column,
        failures=signal.failures,
        message=signal.message,
    )


def to_signal(row: SignalRow) -> Signal:
    """Rebuild a signal from its stored row."""
    return Signal(
        kind=SignalKind(row.kind),
        severity=Severity(row.severity),
        run_id=row.run_id,
        detected_at=row.detected_at,
        name=row.name,
        unique_id=row.unique_id,
        check_type=row.check_type,
        models=tuple(row.models),
        model=row.model,
        column=row.column_name,
        failures=row.failures,
        message=row.message,
    )
