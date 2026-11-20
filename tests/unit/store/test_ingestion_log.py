"""FR-ING-06: every ingestion attempt is recorded, and versions can be told apart by time."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine

from lineageheal.core.errors import StorageError
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository

DAY = date(2026, 10, 16)
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def stored(
    key: str, minutes: int, *, outcome: str = "stored", day: date = DAY, dataset: str = "navall"
) -> IngestionAttempt:
    return IngestionAttempt(
        source="amfi",
        dataset=dataset,
        trade_date=day,
        outcome=outcome,
        started_at=at(minutes),
        finished_at=at(minutes),
        object_key=key,
        size_bytes=100,
        sha256="ab" * 32,
        http_status=200,
    )


@pytest.fixture
def repository(engine: Engine) -> IngestionLogRepository:
    return IngestionLogRepository(engine)


def test_a_recorded_attempt_reads_back_exactly(repository: IngestionLogRepository) -> None:
    attempt = IngestionAttempt(
        source="nse",
        dataset="cm_bhavcopy",
        trade_date=DAY,
        outcome="stored",
        started_at=at(0),
        finished_at=at(1),
        object_key="raw/k",
        size_bytes=206122,
        sha256="cd" * 32,
        http_status=200,
    )

    new_id = repository.record(attempt)

    (read_back,) = repository.recent()
    assert read_back.id == new_id
    assert read_back == IngestionAttempt(**{**attempt.__dict__, "id": new_id})
    assert read_back.finished_at.tzinfo is UTC


def test_every_outcome_can_be_recorded(repository: IngestionLogRepository) -> None:
    repository.record(stored("k1", 0))
    repository.record(stored("k1", 1, outcome="unchanged"))
    repository.record(
        IngestionAttempt("amfi", "navall", DAY, "no_data", at(2), at(2), http_status=404)
    )
    repository.record(
        IngestionAttempt("amfi", "navall", DAY, "failed", at(3), at(3), error="HTTP 503")
    )

    outcomes = [attempt.outcome for attempt in repository.recent()]

    assert outcomes == ["failed", "no_data", "unchanged", "stored"]


def test_recent_returns_newest_first_and_honours_the_limit(
    repository: IngestionLogRepository,
) -> None:
    for minute in (5, 1, 3):  # recorded out of order on purpose
        repository.record(stored(f"k{minute}", minute))

    assert [a.object_key for a in repository.recent()] == ["k5", "k3", "k1"]
    assert [a.object_key for a in repository.recent(limit=2)] == ["k5", "k3"]


def test_recent_can_be_filtered(repository: IngestionLogRepository) -> None:
    repository.record(stored("a", 0, dataset="navall"))
    repository.record(stored("b", 1, dataset="other"))
    repository.record(
        IngestionAttempt("nse", "cm_bhavcopy", DAY, "failed", at(2), at(2), error="boom")
    )

    assert [a.object_key for a in repository.recent(dataset="other")] == ["b"]
    assert [a.source for a in repository.recent(source="nse")] == ["nse"]
    assert [a.outcome for a in repository.recent(outcome="failed")] == ["failed"]


@pytest.mark.parametrize(
    "bad_attempt",
    [
        pytest.param(
            IngestionAttempt("amfi", "navall", DAY, "stored", at(0), at(0)),
            id="a stored file without a description",
        ),
        pytest.param(
            IngestionAttempt("amfi", "navall", DAY, "failed", at(0), at(0)),
            id="a failure that does not say why",
        ),
        pytest.param(
            IngestionAttempt("amfi", "navall", DAY, "bogus", at(0), at(0)),
            id="an unknown outcome",
        ),
    ],
)
def test_the_database_refuses_inconsistent_attempts(
    repository: IngestionLogRepository, bad_attempt: IngestionAttempt
) -> None:
    with pytest.raises(StorageError, match="Could not write to the ingestion log"):
        repository.record(bad_attempt)

    assert repository.recent() == []


def test_a_naive_timestamp_is_refused_rather_than_guessed(
    repository: IngestionLogRepository,
) -> None:
    naive_time = datetime(2026, 10, 16)  # noqa: DTZ001 - deliberately naive
    naive = IngestionAttempt("amfi", "navall", DAY, "no_data", naive_time, at(0))

    with pytest.raises(StorageError, match="timezone-aware"):
        repository.record(naive)

    assert repository.recent() == []


# --- versions ----------------------------------------------------------------------------


def test_versions_come_back_oldest_first_whatever_order_they_were_recorded_in(
    repository: IngestionLogRepository,
) -> None:
    repository.record(stored("republished", 30))
    repository.record(stored("original", 5))

    versions = repository.stored_versions("amfi", "navall", DAY)

    assert [v.object_key for v in versions] == ["original", "republished"]
    assert versions[0].ingested_at == at(5)
    assert versions[0].size_bytes == 100


def test_finding_a_file_unchanged_does_not_create_or_move_a_version(
    repository: IngestionLogRepository,
) -> None:
    repository.record(stored("original", 5))
    repository.record(stored("original", 90, outcome="unchanged"))

    (version,) = repository.stored_versions("amfi", "navall", DAY)

    assert version.ingested_at == at(5)


def test_failed_and_empty_attempts_add_no_version(repository: IngestionLogRepository) -> None:
    repository.record(IngestionAttempt("amfi", "navall", DAY, "failed", at(0), at(0), error="x"))
    repository.record(IngestionAttempt("amfi", "navall", DAY, "no_data", at(1), at(1)))

    assert repository.stored_versions("amfi", "navall", DAY) == []


def test_a_version_stored_twice_counts_once_at_its_earliest_time(
    repository: IngestionLogRepository,
) -> None:
    """For example after a raw zone was rebuilt and the same file stored again."""
    repository.record(stored("k", 40))
    repository.record(stored("k", 10))

    (version,) = repository.stored_versions("amfi", "navall", DAY)

    assert version.ingested_at == at(10)


def test_versions_are_kept_apart_by_dataset_and_date(repository: IngestionLogRepository) -> None:
    repository.record(stored("mine", 0))
    repository.record(stored("other-day", 1, day=date(2026, 10, 15)))
    repository.record(stored("other-dataset", 2, dataset="other"))

    assert [v.object_key for v in repository.stored_versions("amfi", "navall", DAY)] == ["mine"]


# --- failures of the database itself ---------------------------------------------------------


@pytest.fixture
def unreachable_repository(tmp_path: object) -> IngestionLogRepository:
    return IngestionLogRepository(create_db_engine("sqlite+pysqlite:////no/such/folder/log.db"))


def test_an_unreachable_database_is_a_clear_error_on_write(
    unreachable_repository: IngestionLogRepository,
) -> None:
    with pytest.raises(StorageError, match="Could not write to the ingestion log"):
        unreachable_repository.record(stored("k", 0))


def test_an_unreachable_database_is_a_clear_error_on_read(
    unreachable_repository: IngestionLogRepository,
) -> None:
    with pytest.raises(StorageError, match="Could not read the ingestion log"):
        unreachable_repository.recent()
    with pytest.raises(StorageError, match="Could not read the ingestion log"):
        unreachable_repository.stored_versions("amfi", "navall", DAY)


def test_error_messages_do_not_echo_the_values_that_were_being_written(
    repository: IngestionLogRepository,
) -> None:
    secretish = IngestionAttempt(
        "amfi", "navall", DAY, "failed", at(0), at(0), error=None, object_key="do-not-echo-me"
    )

    with pytest.raises(StorageError) as raised:
        repository.record(secretish)

    assert "do-not-echo-me" not in str(raised.value)


# --- unresolved failures (FR-DET-01) ---------------------------------------------------------


def failed(minutes: int, *, day: date = DAY, dataset: str = "navall") -> IngestionAttempt:
    return IngestionAttempt(
        source="amfi",
        dataset=dataset,
        trade_date=day,
        outcome="failed",
        started_at=at(minutes),
        finished_at=at(minutes),
        http_status=403,
        error="blocked",
    )


def unresolved(
    repository: IngestionLogRepository, *, since: int = -60, until: int = 600
) -> list[IngestionAttempt]:
    return repository.unresolved_failures(since=at(since), until=at(until))


def test_a_failure_nothing_has_answered_is_unresolved(repository: IngestionLogRepository) -> None:
    repository.record(failed(0))

    [attempt] = unresolved(repository)

    assert (attempt.source, attempt.trade_date, attempt.error) == ("amfi", DAY, "blocked")


@pytest.mark.parametrize("outcome", ["stored", "unchanged", "no_data"])
def test_a_later_attempt_that_did_not_fail_resolves_it(
    repository: IngestionLogRepository, outcome: str
) -> None:
    repository.record(failed(0))
    repository.record(stored("raw/k", 5, outcome=outcome))

    assert unresolved(repository) == []


def test_a_later_failure_does_not_resolve_an_earlier_one(
    repository: IngestionLogRepository,
) -> None:
    repository.record(failed(0))
    repository.record(failed(5))

    assert [a.finished_at for a in unresolved(repository)] == [at(0), at(5)]


def test_only_attempts_for_the_same_feed_and_date_resolve_a_failure(
    repository: IngestionLogRepository,
) -> None:
    repository.record(failed(0))
    repository.record(stored("raw/other-day", 5, day=date(2026, 10, 15)))
    repository.record(stored("raw/other-dataset", 6, dataset="other"))

    assert len(unresolved(repository)) == 1


def test_an_earlier_success_does_not_hide_a_later_failure(
    repository: IngestionLogRepository,
) -> None:
    repository.record(stored("raw/k", 0))
    repository.record(failed(5))

    assert len(unresolved(repository)) == 1


def test_the_window_decides_what_is_seen(repository: IngestionLogRepository) -> None:
    repository.record(failed(0))
    repository.record(stored("raw/k", 30))

    assert unresolved(repository, until=10) != []  # asked as of before the success
    assert unresolved(repository, until=40) == []
    assert unresolved(repository, since=5) == []  # too old to matter


def test_attempts_at_the_same_instant_are_ordered_by_arrival(
    repository: IngestionLogRepository,
) -> None:
    repository.record(failed(0))
    repository.record(stored("raw/k", 0))

    assert unresolved(repository) == []


def test_for_day_returns_every_attempt_for_that_feed_and_day_oldest_first(
    repository: IngestionLogRepository,
) -> None:
    repository.record(stored("second", 10, outcome="unchanged"))
    repository.record(stored("first", 0))
    repository.record(stored("other-day", 5, day=date(2026, 10, 15)))
    repository.record(stored("other-dataset", 5, dataset="cm_bhavcopy"))

    attempts = repository.for_day("amfi", "navall", DAY)

    assert [a.object_key for a in attempts] == ["first", "second"]
    assert repository.for_day("amfi", "navall", date(2026, 1, 1)) == []
