"""FR-DET-03, FR-DET-05, FR-LIN-04: incidents keep what they affect and the graph they used."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.lineage.graph import LineageGraph
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.lineage import LineageRepository
from lineageheal.store.models import Incident
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def draft(fingerprint: str = "fp1", version: str | None = None) -> IncidentDraft:
    return IncidentDraft(
        summary="not_null on brz_amfi__navall.nav_inr",
        severity="error",
        fingerprint=fingerprint,
        root_node="brz_amfi__navall.nav_inr",
        affected={"models": ["a"], "columns": ["a.b"], "gold_tables": ["gld_x"]},
        lineage_version=version,
        fault_class="nulls",
        confidence=0.6,
        classified_by="rules",
        opened_at=NOW,
    )


def signal(name: str, run_id: str = "run-1") -> Signal:
    return Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id=run_id,
        detected_at=NOW,
        name=name,
        check_type="not_null",
    )


@pytest.fixture
def repository(engine: Engine) -> IncidentRepository:
    return IncidentRepository(engine)


@pytest.fixture
def run(engine: Engine) -> None:
    summary = RunSummary(
        run_id="run-1", generated_at=NOW, counts={}, signals=(signal("a"), signal("b"))
    )
    RunRepository(engine).record(summary)


def test_an_incident_reads_back_as_it_was_opened(repository: IncidentRepository) -> None:
    created = repository.create(draft())

    assert repository.get(created.id) == created
    assert (created.status, created.fault_class, created.confidence) == ("detected", "nulls", 0.6)
    assert created.affected["gold_tables"] == ["gld_x"]
    assert created.opened_at == created.updated_at == NOW


def test_an_unknown_incident_is_none(repository: IncidentRepository) -> None:
    assert repository.get(99) is None


def test_the_newest_open_incident_with_a_fingerprint_is_found(
    repository: IncidentRepository, engine: Engine
) -> None:
    older = repository.create(draft())
    newer = repository.create(draft())
    repository.create(draft("other"))

    assert repository.find_open("fp1") == newer
    with Session(engine) as session, session.begin():
        session.execute(
            update(Incident)
            .where(Incident.id == newer.id)
            .values(status="resolved", resolved_at=NOW)
        )
    assert repository.find_open("fp1") == older
    assert repository.find_open("missing") is None


def test_signals_are_attached_once_and_the_incident_is_touched(
    repository: IncidentRepository, run: None
) -> None:
    incident = repository.create(draft())
    later = NOW + timedelta(hours=1)

    assert repository.attach(incident.id, "run-1", [signal("a"), signal("b")], at=later) == 2
    assert (
        repository.attach(incident.id, "run-1", [signal("a")], at=later + timedelta(hours=1)) == 0
    )

    assert [s.name for s in repository.signals_of(incident.id)] == ["a", "b"]
    touched = repository.get(incident.id)
    assert touched is not None
    assert touched.updated_at == later


def test_attaching_to_an_incident_that_does_not_exist_is_an_error(
    repository: IncidentRepository, run: None
) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        repository.attach(99, "run-1", [signal("a")], at=NOW)


def test_the_database_refuses_an_unknown_severity(
    repository: IncidentRepository,
) -> None:
    bad = draft()
    object.__setattr__(bad, "severity", "catastrophic")

    with pytest.raises(StorageError):
        repository.create(bad)


def test_an_incident_cannot_name_a_graph_version_that_is_not_stored(
    repository: IncidentRepository,
) -> None:
    with pytest.raises(StorageError):
        repository.create(draft(version="never-saved"))


def test_a_graph_version_an_incident_used_survives_newer_ones(
    repository: IncidentRepository, engine: Engine
) -> None:
    lineage = LineageRepository(engine)
    for hours, version in enumerate(["old", "mid", "new"]):
        lineage.save(LineageGraph(version, (), ()), activated_at=NOW + timedelta(hours=hours))
        if version == "old":
            repository.create(draft(version="old"))

    assert lineage.versions() == ["new", "mid", "old"]
    lineage.save(LineageGraph("newest", (), ()), activated_at=NOW + timedelta(hours=9))
    assert lineage.versions() == ["newest", "new", "old"]


def test_the_database_itself_refuses_to_delete_a_version_an_incident_uses(
    repository: IncidentRepository, engine: Engine
) -> None:
    LineageRepository(engine).save(LineageGraph("v1", (), ()), activated_at=NOW)
    repository.create(draft(version="v1"))

    from sqlalchemy import text

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(text("DELETE FROM lineage_version WHERE version = 'v1'"))


def open_incident(
    repository: IncidentRepository,
    minutes: int,
    *,
    root: str,
    fault_class: str = "nulls",
    severity: str = "error",
) -> int:
    fields = draft(fingerprint=f"fp-{minutes}").__dict__ | {
        "root_node": root,
        "fault_class": fault_class,
        "severity": severity,
        "opened_at": NOW + timedelta(minutes=minutes),
    }
    return repository.create(IncidentDraft(**fields)).id


def test_incidents_are_listed_newest_first_with_the_total(repository: IncidentRepository) -> None:
    ids = [open_incident(repository, m, root="brz_amfi__navall.nav_inr") for m in (0, 5, 5, 9)]

    page, total = repository.list_incidents(limit=3)
    rest, _ = repository.list_incidents(limit=3, offset=3)

    assert total == 4
    # the two opened at the same minute are ordered by id, newest first
    assert [i.id for i in page] == [ids[3], ids[2], ids[1]]
    assert [i.id for i in rest] == [ids[0]]


def test_filters_narrow_both_the_rows_and_the_total(repository: IncidentRepository) -> None:
    a = open_incident(repository, 0, root="brz_amfi__navall.nav_inr", fault_class="nulls")
    b = open_incident(repository, 1, root="brz_nse__bhavcopy.close", fault_class="unit_change")
    c = open_incident(
        repository, 2, root="raw.nse.bhavcopy.ClsPric", fault_class="schema_drift", severity="warn"
    )

    by_class, n_class = repository.list_incidents(limit=10, fault_class="unit_change")
    by_severity, _ = repository.list_incidents(limit=10, severity="warn")
    by_status, n_status = repository.list_incidents(limit=10, status="detected")
    nothing, n_nothing = repository.list_incidents(limit=10, status="approved")

    assert [i.id for i in by_class] == [b]
    assert n_class == 1
    assert [i.id for i in by_severity] == [c]
    assert n_status == 3
    assert {i.id for i in by_status} == {a, b, c}
    assert (nothing, n_nothing) == ([], 0)


def test_the_feed_filter_follows_the_naming_convention_for_models_and_raw_nodes(
    repository: IncidentRepository,
) -> None:
    amfi = open_incident(repository, 0, root="brz_amfi__navall.nav_inr")
    nse_model = open_incident(repository, 1, root="slv_nse__prices.close")
    nse_raw = open_incident(repository, 2, root="raw.nse.bhavcopy.ClsPric")
    open_incident(repository, 3, root="gld_market_daily_summary.rows")  # no feed in the name

    nse, n_nse = repository.list_incidents(limit=10, feed="nse")
    only_amfi, _ = repository.list_incidents(limit=10, feed="amfi")

    assert {i.id for i in nse} == {nse_model, nse_raw}
    assert n_nse == 2
    assert [i.id for i in only_amfi] == [amfi]


def test_a_feed_name_with_wildcard_characters_matches_nothing(
    repository: IncidentRepository,
) -> None:
    open_incident(repository, 0, root="brz_amfi__navall.nav_inr")

    for feed in ("%", "_", "a%", "am_i", "\\"):
        assert repository.list_incidents(limit=10, feed=feed) == ([], 0)
