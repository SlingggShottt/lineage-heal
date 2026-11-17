"""FR-DET-01: the Detector collects dbt's signals and unanswered download failures for a run."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lineageheal.agents.detector.collect import SignalCollector, ingestion_signals
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.models import PipelineRun
from lineageheal.store.runs import RunRepository

RUN_AT = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
DAY = date(2026, 10, 15)


def attempt(
    *,
    outcome: str = "failed",
    hours_before: float = 1,
    day: date = DAY,
    source: str = "amfi",
    dataset: str = "navall",
    status: int | None = 403,
    error: str | None = "blocked",
    row_id: int | None = None,
) -> IngestionAttempt:
    when = RUN_AT - timedelta(hours=hours_before)
    stored = outcome in ("stored", "unchanged")
    return IngestionAttempt(
        source=source,
        dataset=dataset,
        trade_date=day,
        outcome=outcome,
        started_at=when,
        finished_at=when,
        object_key="raw/k" if stored else None,
        size_bytes=10 if stored else None,
        sha256="ab" * 32 if stored else None,
        http_status=status,
        error=error if outcome == "failed" else None,
        id=row_id,
    )


def dbt_signal() -> Signal:
    return Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id="run-1",
        detected_at=RUN_AT,
        name="not_null_brz_amfi__navall_nav_inr",
        check_type="not_null",
        models=("brz_amfi__navall",),
        model="brz_amfi__navall",
        column="nav_inr",
        failures=1,
        message="Got 1 result",
    )


def summary(*signals: Signal) -> RunSummary:
    return RunSummary(
        run_id="run-1",
        generated_at=RUN_AT,
        counts={"fail": len(signals)},
        signals=tuple(signals),
    )


# --- turning attempts into signals ------------------------------------------------------------


def test_a_failed_download_becomes_one_error_signal_naming_the_feed_and_date() -> None:
    [signal] = ingestion_signals([attempt()], run_id="run-1", detected_at=RUN_AT)

    assert signal.kind is SignalKind.INGESTION_ERROR
    assert signal.severity is Severity.ERROR
    assert signal.name == "amfi/navall/2026-10-15"
    assert signal.check_type == "ingestion"
    assert signal.message == "download failed, HTTP 403: blocked"
    assert (signal.run_id, signal.detected_at) == ("run-1", RUN_AT)
    assert signal.model is None
    assert signal.models == ()


def test_a_failure_with_no_status_or_error_still_says_what_happened() -> None:
    [signal] = ingestion_signals(
        [attempt(status=None, error=None)], run_id="run-1", detected_at=RUN_AT
    )

    assert signal.message == "download failed, no error recorded"


def test_repeated_failures_of_one_feed_and_date_give_the_latest_error() -> None:
    older = attempt(hours_before=3, error="timed out", status=None, row_id=1)
    newer = attempt(hours_before=1, error="blocked", row_id=2)

    signals = ingestion_signals([newer, older], run_id="run-1", detected_at=RUN_AT)

    assert [s.message for s in signals] == ["download failed, HTTP 403: blocked"]


def test_different_feeds_and_dates_each_get_a_signal_in_a_stable_order() -> None:
    signals = ingestion_signals(
        [
            attempt(source="nse", dataset="cm_bhavcopy"),
            attempt(day=date(2026, 10, 14)),
            attempt(),
        ],
        run_id="run-1",
        detected_at=RUN_AT,
    )

    assert [s.name for s in signals] == [
        "amfi/navall/2026-10-14",
        "amfi/navall/2026-10-15",
        "nse/cm_bhavcopy/2026-10-15",
    ]


def test_an_overlong_error_is_cut() -> None:
    [signal] = ingestion_signals([attempt(error="x" * 5000)], run_id="run-1", detected_at=RUN_AT)

    assert len(signal.message) < 400


# --- collecting for a run ---------------------------------------------------------------------


@pytest.fixture
def log(engine: Engine) -> IngestionLogRepository:
    return IngestionLogRepository(engine)


@pytest.fixture
def runs(engine: Engine) -> RunRepository:
    return RunRepository(engine)


@pytest.fixture
def collector(runs: RunRepository, log: IngestionLogRepository) -> SignalCollector:
    return SignalCollector(runs, log)


def test_a_run_gets_its_dbt_signals_and_the_unanswered_download_failure(
    collector: SignalCollector, log: IngestionLogRepository
) -> None:
    log.record(attempt())

    signals = collector.collect(summary(dbt_signal()))

    assert [s.kind for s in signals] == [SignalKind.TEST_FAILED, SignalKind.INGESTION_ERROR]


def test_a_failure_that_a_later_download_answered_is_not_reported(
    collector: SignalCollector, log: IngestionLogRepository
) -> None:
    log.record(attempt(hours_before=3))
    log.record(attempt(outcome="stored", hours_before=2, status=200))

    assert collector.collect(summary()) == []


def test_collecting_twice_adds_nothing(
    collector: SignalCollector, log: IngestionLogRepository
) -> None:
    log.record(attempt())
    first = collector.collect(summary(dbt_signal()))

    assert collector.collect(summary(dbt_signal())) == first


def test_a_failure_after_the_run_or_long_before_it_is_not_this_runs_business(
    collector: SignalCollector, log: IngestionLogRepository
) -> None:
    log.record(attempt(hours_before=-2))  # finished after the run's summary
    log.record(attempt(hours_before=24 * 30, day=date(2026, 9, 15)))  # a month earlier

    assert collector.collect(summary()) == []


def test_a_failed_download_does_not_change_a_run_dbt_built_successfully(
    collector: SignalCollector, log: IngestionLogRepository, runs: RunRepository
) -> None:
    log.record(attempt())

    collector.collect(summary())

    assert runs.status_of("run-1") == "succeeded"


def test_the_artifacts_prefix_is_saved_with_the_run(
    collector: SignalCollector, engine: Engine
) -> None:
    collector.collect(summary(), artifacts_prefix="runs/run-1/")

    with Session(engine) as session:
        run = session.get(PipelineRun, "run-1")
        assert run is not None
        assert run.artifacts_prefix == "runs/run-1/"
