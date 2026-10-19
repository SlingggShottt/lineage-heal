"""FR-ING-06: how ingestion results become log entries, and when no log is kept."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from lineageheal.core.errors import StorageError
from lineageheal.core.settings import Settings
from lineageheal.ingestion.log import (
    MAX_ERROR_LENGTH,
    DatabaseRecorder,
    NullRecorder,
    attempt_from_failure,
    attempt_from_injection,
    attempt_from_result,
    open_recorder,
)
from lineageheal.ingestion.raw_zone import StoredObject
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionLogRepository
from lineageheal.store.migrate import upgrade_database

START = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
END = START + timedelta(seconds=2)
DAY = date(2026, 10, 16)
OBJECT = StoredObject(key="raw/k", sha256="ab" * 32, size_bytes=123, created=True)


def result(outcome: IngestionOutcome, stored: StoredObject | None, status: int) -> IngestionResult:
    return IngestionResult("amfi", "navall", DAY, outcome, status, stored)


def test_a_stored_result_records_where_the_file_went() -> None:
    attempt = attempt_from_result(
        result(IngestionOutcome.STORED, OBJECT, 200), started_at=START, finished_at=END
    )

    assert (attempt.source, attempt.dataset, attempt.trade_date) == ("amfi", "navall", DAY)
    assert attempt.outcome == "stored"
    assert (attempt.object_key, attempt.size_bytes, attempt.sha256) == ("raw/k", 123, "ab" * 32)
    assert attempt.http_status == 200
    assert (attempt.started_at, attempt.finished_at) == (START, END)
    assert attempt.error is None


def test_an_unchanged_result_points_at_the_existing_file() -> None:
    existing = StoredObject(key="raw/k", sha256="ab" * 32, size_bytes=123, created=False)

    attempt = attempt_from_result(
        result(IngestionOutcome.UNCHANGED, existing, 200), started_at=START, finished_at=END
    )

    assert attempt.outcome == "unchanged"
    assert attempt.object_key == "raw/k"


def test_a_day_with_no_data_records_the_status_and_no_file() -> None:
    attempt = attempt_from_result(
        result(IngestionOutcome.NO_DATA, None, 404), started_at=START, finished_at=END
    )

    assert attempt.outcome == "no_data"
    assert attempt.http_status == 404
    assert (attempt.object_key, attempt.size_bytes, attempt.sha256) == (None, None, None)


def test_a_failure_records_its_message() -> None:
    attempt = attempt_from_failure(
        source="nse",
        dataset="cm_bhavcopy",
        trade_date=DAY,
        error=RuntimeError("HTTP 503"),
        started_at=START,
        finished_at=END,
    )

    assert attempt.outcome == "failed"
    assert attempt.error == "HTTP 503"
    assert attempt.object_key is None


def test_a_long_failure_message_is_truncated_and_a_blank_one_names_the_error_type() -> None:
    kwargs = {
        "source": "amfi",
        "dataset": "navall",
        "trade_date": DAY,
        "started_at": START,
        "finished_at": END,
    }

    long = attempt_from_failure(error=RuntimeError("x" * 5000), **kwargs)  # type: ignore[arg-type]
    blank = attempt_from_failure(error=RuntimeError(""), **kwargs)  # type: ignore[arg-type]

    assert long.error is not None
    assert len(long.error) == MAX_ERROR_LENGTH
    assert blank.error == "RuntimeError"


def test_an_injected_file_is_logged_like_a_stored_version_without_a_status() -> None:
    attempt = attempt_from_injection(
        source="amfi",
        dataset="navall",
        trade_date=DAY,
        stored=OBJECT,
        started_at=START,
        finished_at=END,
    )

    assert attempt.outcome == "stored"
    assert attempt.http_status is None
    assert attempt.object_key == "raw/k"


def test_injecting_the_same_fault_again_is_logged_as_unchanged() -> None:
    again = StoredObject(key="raw/k", sha256="ab" * 32, size_bytes=123, created=False)

    attempt = attempt_from_injection(
        source="amfi", dataset="navall", trade_date=DAY, stored=again,
        started_at=START, finished_at=END,
    )  # fmt: skip

    assert attempt.outcome == "unchanged"


def test_without_a_database_no_log_is_kept_and_nothing_fails() -> None:
    recorder = open_recorder(Settings(_env_file=None))

    assert isinstance(recorder, NullRecorder)
    recorder.record(
        attempt_from_result(
            result(IngestionOutcome.NO_DATA, None, 404), started_at=START, finished_at=END
        )
    )
    recorder.close()


def test_with_a_database_attempts_are_written_to_it(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'log.db'}"
    upgrade_database(url)

    recorder = open_recorder(Settings(_env_file=None, database_url=SecretStr(url)))
    recorder.record(
        attempt_from_result(
            result(IngestionOutcome.STORED, OBJECT, 200), started_at=START, finished_at=END
        )
    )
    recorder.close()

    engine = create_db_engine(url)
    (written,) = IngestionLogRepository(engine).recent()
    engine.dispose()
    assert isinstance(recorder, DatabaseRecorder)
    assert (written.outcome, written.object_key) == ("stored", "raw/k")


def test_a_database_that_cannot_be_written_is_an_error_not_a_silent_gap(tmp_path: Path) -> None:
    recorder = DatabaseRecorder(create_db_engine("sqlite+pysqlite:////no/such/folder/log.db"))

    with pytest.raises(StorageError, match="ingestion log"):
        recorder.record(
            attempt_from_result(
                result(IngestionOutcome.STORED, OBJECT, 200), started_at=START, finished_at=END
            )
        )
