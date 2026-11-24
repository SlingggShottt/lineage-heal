"""FR-API-01, FR-UI-01: feed freshness and the latest run, from the application database."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lineageheal.api.app import create_app
from lineageheal.api.feed_health import KNOWN_FEEDS
from lineageheal.core.settings import Settings
from lineageheal.core.signals import RunSummary, Severity, Signal
from lineageheal.ingestion.runner import DATASETS
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def client(engine: Engine) -> TestClient:
    api = create_app(Settings(), clock=lambda: NOW)
    api.state.engine = engine
    return TestClient(api)


def log(
    engine: Engine,
    source: str,
    dataset: str,
    day: date,
    outcome: str = "stored",
    *,
    at: datetime = NOW,
    error: str | None = None,
) -> None:
    stored = outcome in ("stored", "unchanged")
    IngestionLogRepository(engine).record(
        IngestionAttempt(
            source=source,
            dataset=dataset,
            trade_date=day,
            outcome=outcome,
            started_at=at,
            finished_at=at,
            object_key=f"{source}/{day}" if stored else None,
            size_bytes=10 if stored else None,
            sha256="a" * 64 if stored else None,
            error=error,
        )
    )


def by_dataset(body: dict[str, object]) -> dict[str, dict[str, object]]:
    feeds = body["feeds"]
    assert isinstance(feeds, list)
    return {f["dataset"]: f for f in feeds}


def test_the_known_feeds_match_what_the_loaders_offer() -> None:
    offered = {(source, dataset) for source, names in DATASETS.items() for dataset in names}

    assert set(KNOWN_FEEDS) == offered


def test_an_empty_database_reports_every_feed_as_no_data(client: TestClient) -> None:
    body = client.get("/feeds/health").json()

    assert body["as_of"] is None
    assert body["latest_run"] is None
    assert {f["status"] for f in body["feeds"]} == {"no_data"}
    assert len(body["feeds"]) == len(KNOWN_FEEDS)


def test_a_feed_with_a_recent_file_is_ok_and_one_behind_the_others_is_late(
    client: TestClient, engine: Engine
) -> None:
    log(engine, "amfi", "navall", date(2026, 11, 23))
    log(engine, "nse", "cm_bhavcopy", date(2026, 11, 20))  # three days behind: a weekend
    log(engine, "nse", "security_master", date(2026, 11, 19))  # four days behind

    feeds = by_dataset(client.get("/feeds/health").json())

    assert feeds["navall"]["status"] == "ok"
    assert feeds["navall"]["lag_days"] == 0
    assert feeds["cm_bhavcopy"]["status"] == "ok"
    assert feeds["security_master"]["status"] == "late"
    assert feeds["security_master"]["lag_days"] == 4


def test_freshness_is_judged_against_the_newest_date_not_the_clock(
    client: TestClient, engine: Engine
) -> None:
    # Every file is months older than the test clock, and nothing is late.
    log(engine, "amfi", "navall", date(2026, 3, 2))
    log(engine, "nse", "cm_bhavcopy", date(2026, 3, 2))

    body = client.get("/feeds/health").json()

    assert body["as_of"] == "2026-03-02"
    assert by_dataset(body)["navall"]["status"] == "ok"


def test_a_failed_latest_attempt_marks_the_feed_failing_with_a_clipped_reason(
    client: TestClient, engine: Engine
) -> None:
    log(engine, "amfi", "navall", date(2026, 11, 20), at=NOW - timedelta(days=3))
    log(engine, "amfi", "navall", date(2026, 11, 23), "failed", error="x" * 500)

    feed = by_dataset(client.get("/feeds/health").json())["navall"]

    assert feed["status"] == "failing"
    assert feed["last_attempt_outcome"] == "failed"
    assert feed["latest_trade_date"] == "2026-11-20"
    assert len(feed["last_error"]) == 200


def test_a_later_good_attempt_clears_a_failure(client: TestClient, engine: Engine) -> None:
    log(engine, "amfi", "navall", date(2026, 11, 23), "failed", error="boom", at=NOW)
    log(engine, "amfi", "navall", date(2026, 11, 23), at=NOW + timedelta(minutes=5))

    feed = by_dataset(client.get("/feeds/health").json())["navall"]

    assert feed["status"] == "ok"
    assert feed["last_error"] is None


def test_the_latest_run_is_reported_with_its_signal_counts(
    client: TestClient, engine: Engine
) -> None:
    def signal(name: str, severity: Severity) -> Signal:
        return Signal(
            kind="test_failed" if severity == "error" else "test_warning",
            severity=severity,
            run_id="new",
            detected_at=NOW,
            name=name,
            check_type="not_null",
            model="brz_amfi__navall",
        )

    runs = RunRepository(engine)
    runs.record(RunSummary(run_id="old", generated_at=NOW - timedelta(days=1), counts={}))
    runs.record(
        RunSummary(
            run_id="new",
            generated_at=NOW,
            counts={},
            skipped_models=("m1",),
            signals=(signal("a", "error"), signal("b", "warn"), signal("c", "warn")),
        )
    )

    run = client.get("/feeds/health").json()["latest_run"]

    assert run["run_id"] == "new"
    assert run["status"] == "failed"
    assert (run["errors"], run["warnings"], run["skipped_models"]) == (1, 2, 1)


def test_without_a_database_the_endpoint_says_so() -> None:
    with TestClient(create_app(Settings()), raise_server_exceptions=False) as bare:
        response = bare.get("/feeds/health")

    assert response.status_code == 503
