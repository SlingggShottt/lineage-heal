"""The application store against a real Postgres (needs `make up`; skipped when it is not running).

SQLite stands in for Postgres in the unit tests. These check what only Postgres can show:
real timestamptz handling, JSONB columns, the database's own constraint enforcement and the
migrations applied to the server the project actually uses.
"""

import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from schema_drift import schema_differences
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.agents.investigator.models import (
    Conclusion,
    Evidence,
    Investigation,
    InvestigationStatus,
)
from lineageheal.core.errors import StorageError
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.migrate import (
    current_revision,
    downgrade_database,
    head_revision,
    upgrade_database,
)
from lineageheal.store.models import AgentRun, Fix, Incident
from lineageheal.store.runs import RunRepository

DAY = date(2026, 10, 16)
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


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
        differences = schema_differences(connection)
    columns = {c["name"]: c for c in inspect(engine).get_columns("ingestion_log")}
    engine.dispose()

    assert current_revision(database_url) == head_revision()
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
    assert current_revision(database_url) == head_revision()


def test_every_application_table_exists_with_jsonb_columns(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    jsonb = {c["name"]: str(c["type"]) for c in inspector.get_columns("incident")}
    engine.dispose()

    assert tables >= {
        "pipeline_run",
        "signal",
        "incident",
        "agent_run",
        "evidence",
        "fix",
        "review",
    }
    assert jsonb["root_cause"] == "JSONB"


def test_a_run_is_imported_on_postgres_and_read_back_unchanged(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    run_id = f"run-{secrets.token_hex(4)}"
    signal = Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id=run_id,
        detected_at=T0,
        name="not_null_brz_amfi__navall_nav_inr",
        check_type="not_null",
        models=("brz_amfi__navall",),
        model="brz_amfi__navall",
        column="nav_inr",
        failures=3,
        message="Got 3 results",
    )
    summary = RunSummary(run_id=run_id, generated_at=T0, counts={"fail": 1}, signals=(signal,))
    repository = RunRepository(engine)

    first = repository.record(summary)
    second = repository.record(summary)

    assert (first, second) == (True, False)
    assert repository.signals_for(run_id) == [signal]
    assert repository.status_of(run_id) == "failed"
    engine.dispose()


def test_postgres_refuses_what_the_lifecycle_forbids(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    opened = {"summary": "x", "opened_at": T0, "updated_at": T0}

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(Incident(status="resolved", **opened))  # resolved without a date
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(Incident(status="detected", confidence=1.5, **opened))
    engine.dispose()


def test_deleting_an_incident_removes_its_fixes_on_postgres(database_url: str) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    with Session(engine) as session, session.begin():
        incident = Incident(status="fix_proposed", summary="x", opened_at=T0, updated_at=T0)
        session.add(incident)
        session.flush()
        session.add(
            Fix(
                incident_id=incident.id,
                kind="suggestion",
                status="unverified",
                files=[],
                suggestion={"title": "t"},
                created_at=T0,
                updated_at=T0,
            )
        )
        session.flush()
        session.delete(incident)

    with Session(engine) as session:
        assert session.scalars(select(Fix)).all() == []
    engine.dispose()


def test_an_investigation_round_trips_through_jsonb_and_the_run_constraints_hold(
    database_url: str,
) -> None:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    with Session(engine) as session, session.begin():
        incident = Incident(status="detected", summary="x", opened_at=T0, updated_at=T0)
        session.add(incident)
        session.flush()
        incident_id = incident.id
    repository = InvestigationRepository(engine)
    found = Investigation(
        status=InvestigationStatus.CONCLUDED,
        conclusion=Conclusion(
            root_cause_node="raw.amfi.navall.nav",
            fault_class="schema_drift",
            explanation="Renamed.",
            evidence_ids=[1],
            confidence=0.9,
            fix_location="dbt",
            fix_reason="Bronze can read the new name.",
        ),
        partial=None,
        evidence=[
            Evidence(
                1, "get_raw_schema", {"source": "amfi"}, True, "c", {"columns": ["Nav", "Ünï"]}
            )
        ],
        llm_calls=3,
        elapsed_seconds=1.0,
        model_ids={"investigator.step.v1": "m"},
        prompt_hashes={"investigator.step.v1": "ab" * 32},
    )

    run_id = repository.start(incident_id, T0)
    repository.finish(run_id, found, T0 + timedelta(minutes=1))
    stored = repository.latest_for(incident_id)

    assert stored is not None
    assert stored.evidence[0]["output"] == {"columns": ["Nav", "Ünï"]}
    assert stored.finished_at == T0 + timedelta(minutes=1)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(
            AgentRun(
                incident_id=incident_id,
                status="failed",
                started_at=T0,
                finished_at=T0,
                model_ids={},
                prompt_versions={},
            )
        )  # no error text
    engine.dispose()


def test_the_api_reports_ready_against_a_migrated_postgres(database_url: str) -> None:
    from fastapi.testclient import TestClient

    from lineageheal.api.app import create_app
    from lineageheal.core.settings import Settings

    upgrade_database(database_url)

    with TestClient(create_app(Settings(database_url=database_url))) as client:  # type: ignore[arg-type]
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["revision"] == head_revision()
