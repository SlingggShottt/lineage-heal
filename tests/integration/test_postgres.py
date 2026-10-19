"""The ingestion log against a real Postgres (needs `make up`; skipped when it is not running).

SQLite stands in for Postgres in the unit tests. These check what only Postgres can show:
real timestamptz handling, the database's own constraint enforcement and the migration
applied to the server the project actually uses.
"""

import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from lineageheal.core.errors import StorageError
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.migrate import current_revision, downgrade_database, upgrade_database
from lineageheal.store.models import Base

# The development database from docker-compose.yml. Override to test another server.
ADMIN_URL = os.environ.get(
    "LINEAGEHEAL_TEST_POSTGRES_URL",
    "postgresql+psycopg://lineageheal:lineageheal@localhost:5433/postgres",
)
DAY = date(2026, 10, 16)
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A throwaway database on the development server, dropped afterwards."""
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"lh_test_{secrets.token_hex(4)}"
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except SQLAlchemyError:
        admin.dispose()
        pytest.skip("Postgres is not reachable; start it with `make up`")
    try:
        yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def repository(database_url: str) -> Iterator[IngestionLogRepository]:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE ingestion_log"))
    yield IngestionLogRepository(engine)
    engine.dispose()


def attempt(key: str, minutes: int, outcome: str = "stored") -> IngestionAttempt:
    when = T0 + timedelta(minutes=minutes)
    return IngestionAttempt(
        "amfi", "navall", DAY, outcome, when, when,
        object_key=key, size_bytes=10, sha256="ab" * 32, http_status=200,
    )  # fmt: skip


def test_the_migration_runs_on_postgres_and_matches_the_models(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)

    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    columns = {c["name"]: c for c in inspect(engine).get_columns("ingestion_log")}
    engine.dispose()

    assert current_revision(database_url) == "0001"
    assert differences == []
    assert str(columns["started_at"]["type"]) == "TIMESTAMP"
    assert columns["started_at"]["type"].timezone is True
    assert str(columns["id"]["type"]) == "BIGINT"


def test_timestamps_survive_a_round_trip_as_the_same_instant_in_utc(
    repository: IngestionLogRepository,
) -> None:
    repository.record(attempt("k", 0))

    (read_back,) = repository.recent()

    assert read_back.started_at == T0
    assert read_back.started_at.utcoffset() == timedelta(0)


def test_postgres_itself_refuses_inconsistent_rows(repository: IngestionLogRepository) -> None:
    bad = IngestionAttempt("amfi", "navall", DAY, "stored", T0, T0)

    with pytest.raises(StorageError, match="ck_ingestion_log_stored_files_are_described"):
        repository.record(bad)


def test_versions_are_ordered_by_arrival_on_postgres(repository: IngestionLogRepository) -> None:
    repository.record(attempt("republished", 30))
    repository.record(attempt("original", 5))
    repository.record(attempt("original", 90, outcome="unchanged"))

    versions = repository.stored_versions("amfi", "navall", DAY)

    assert [v.object_key for v in versions] == ["original", "republished"]
    assert versions[0].ingested_at == T0 + timedelta(minutes=5)


def test_the_migration_can_be_reverted_and_reapplied(database_url: str) -> None:
    upgrade_database(database_url)

    downgrade_database(database_url, "base")
    assert current_revision(database_url) is None

    upgrade_database(database_url)
    assert current_revision(database_url) == "0001"
