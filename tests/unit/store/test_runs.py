"""FR-WH-07, FR-DET-01: a run's summary and its signals are imported once and read back intact."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.store.database import create_db_engine
from lineageheal.store.models import PipelineRun
from lineageheal.store.models import Signal as SignalRow
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


def signal(
    name: str = "not_null_brz_amfi__navall_nav_inr",
    *,
    run_id: str = "run-1",
    kind: SignalKind = SignalKind.TEST_FAILED,
    severity: Severity = Severity.ERROR,
) -> Signal:
    return Signal(
        kind=kind,
        severity=severity,
        run_id=run_id,
        detected_at=NOW,
        name=name,
        unique_id=f"test.lineageheal.{name}",
        check_type="not_null",
        models=("brz_amfi__navall",),
        model="brz_amfi__navall",
        column="nav_inr",
        failures=3,
        message="Got 3 results, configured to fail if != 0",
    )


def summary(*signals: Signal, run_id: str = "run-1") -> RunSummary:
    return RunSummary(
        run_id=run_id,
        dbt_invocation_id="abc-123",
        generated_at=NOW,
        counts={"success": 12, "pass": 190, "fail": len(signals)},
        skipped_models=("slv_amfi__nav", "gld_mf_nav_daily"),
        signals=tuple(signals),
    )


@pytest.fixture
def repository(engine: Engine) -> RunRepository:
    return RunRepository(engine)


def test_a_run_is_recorded_and_its_signals_read_back_unchanged(
    repository: RunRepository,
) -> None:
    original = signal()

    created = repository.record(summary(original), artifacts_prefix="runs/run-1")

    assert created
    assert repository.signals_for("run-1") == [original]


def test_the_run_keeps_what_the_summary_said(repository: RunRepository, engine: Engine) -> None:
    repository.record(summary(signal()), artifacts_prefix="runs/run-1", recorded_at=NOW)

    with Session(engine) as session:
        run = session.get(PipelineRun, "run-1")

    assert run is not None
    assert run.dbt_invocation_id == "abc-123"
    assert run.counts == {"success": 12, "pass": 190, "fail": 1}
    assert run.skipped_models == ["slv_amfi__nav", "gld_mf_nav_daily"]
    assert run.artifacts_prefix == "runs/run-1"
    assert run.recorded_at == NOW


def test_a_run_with_an_error_signal_is_failed(repository: RunRepository) -> None:
    repository.record(summary(signal()))

    assert repository.status_of("run-1") == "failed"


def test_a_run_with_only_warnings_succeeded(repository: RunRepository) -> None:
    warning = signal(kind=SignalKind.TEST_WARNING, severity=Severity.WARN)

    repository.record(summary(warning))

    assert repository.status_of("run-1") == "succeeded"
    assert repository.signals_for("run-1") == [warning]


def test_a_run_without_signals_is_recorded(repository: RunRepository) -> None:
    assert repository.record(summary())

    assert repository.status_of("run-1") == "succeeded"
    assert repository.signals_for("run-1") == []


def test_an_unknown_run_has_no_status(repository: RunRepository) -> None:
    assert repository.status_of("nope") is None


def test_importing_the_same_run_twice_changes_nothing(
    repository: RunRepository, engine: Engine
) -> None:
    first = repository.record(summary(signal()), recorded_at=NOW)
    second = repository.record(summary(signal(), signal("other_check")), recorded_at=NOW)

    assert (first, second) == (True, False)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SignalRow)) == 1
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 1


def test_the_same_check_in_two_runs_is_two_signals(repository: RunRepository) -> None:
    repository.record(summary(signal(run_id="run-1"), run_id="run-1"))
    repository.record(summary(signal(run_id="run-2"), run_id="run-2"))

    assert len(repository.signals_for("run-1")) == 1
    assert len(repository.signals_for("run-2")) == 1


def test_a_summary_that_repeats_a_signal_is_refused_whole(
    repository: RunRepository, engine: Engine
) -> None:
    """A half-imported run would read as a healthy one, so nothing of it is kept."""
    with pytest.raises(StorageError, match="Could not record the run"):
        repository.record(summary(signal(), signal()))

    assert repository.status_of("run-1") is None
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SignalRow)) == 0


def test_signals_come_back_in_the_order_they_were_recorded(repository: RunRepository) -> None:
    names = ["b_check", "a_check", "c_check"]

    repository.record(summary(*(signal(name) for name in names)))

    assert [s.name for s in repository.signals_for("run-1")] == names


def test_an_unreachable_database_is_reported_not_crashed_on() -> None:
    broken = RunRepository(create_db_engine("sqlite+pysqlite:////no/such/folder/x.db"))

    with pytest.raises(StorageError, match="Could not record the run"):
        broken.record(summary())
    with pytest.raises(StorageError, match="Could not read signals"):
        broken.signals_for("run-1")
    with pytest.raises(StorageError, match="Could not read the run"):
        broken.status_of("run-1")
