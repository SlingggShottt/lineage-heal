"""Failure signals: what a pipeline run reports when something is wrong (FR-DET-01).

A signal is one fact the Detector starts from: a test that failed, a model that errored. It
names the model and column involved but says nothing about the cause, which is the
Investigator's job. These types are plain data so that the same record can be written to a
file now and to the application database later (LH-036).
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SignalKind(StrEnum):
    """What went wrong."""

    TEST_FAILED = "test_failed"  # a dbt test configured to fail the build found rows
    TEST_WARNING = "test_warning"  # a test configured to warn found rows
    TEST_ERROR = "test_error"  # a test could not run (for example a SQL error in it)
    MODEL_ERROR = "model_error"  # a model could not be built
    RUN_ERROR = "run_error"  # dbt stopped without producing results (profile, parse, crash)


class Severity(StrEnum):
    """How the project treats the signal."""

    ERROR = "error"
    WARN = "warn"


class Signal(BaseModel):
    """One failure signal from a dbt run."""

    model_config = ConfigDict(frozen=True)

    kind: SignalKind
    severity: Severity
    run_id: str
    detected_at: datetime
    name: str  # the test or model that reported it
    unique_id: str | None = None  # dbt's identifier for it, when there is one
    check_type: str  # the generic test (not_null, volume_anomaly, ...), singular, model or run
    models: tuple[str, ...] = ()  # every model the test reads, or the model itself
    model: str | None = None  # the model the test is attached to, or the failed model
    column: str | None = None
    failures: int | None = None  # rows a test found, when dbt reports it
    message: str = ""


class RunSummary(BaseModel):
    """What one dbt run did, and the signals it raised."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    dbt_invocation_id: str | None = None
    generated_at: datetime
    counts: dict[str, int]  # nodes by result status: success, pass, fail, warn, error, skipped
    skipped_models: tuple[str, ...] = ()  # built nothing because something upstream failed
    signals: tuple[Signal, ...] = ()

    @property
    def has_failures(self) -> bool:
        """Whether any signal is an error, which is what should start the healing workflow."""
        return any(signal.severity is Severity.ERROR for signal in self.signals)
