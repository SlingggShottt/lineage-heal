"""FR-INV-04, FR-DASH-03: an investigation is stored with its evidence and updates the incident."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from lineageheal.agents.investigator.models import (
    Conclusion,
    Evidence,
    Hypothesis,
    Investigation,
    InvestigationStatus,
)
from lineageheal.core.errors import StorageError
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.models import AgentRun, Incident
from lineageheal.store.models import Evidence as EvidenceRow

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=2)


def make_incident(engine: Engine) -> int:
    return (
        IncidentRepository(engine)
        .create(
            IncidentDraft(
                summary="not_null on slv_amfi__nav.nav_inr",
                severity="error",
                fingerprint="fp",
                root_node="slv_amfi__nav.nav_inr",
                affected={},
                lineage_version=None,
                fault_class="nulls",
                confidence=0.6,
                classified_by="rules",
                opened_at=NOW,
            )
        )
        .id
    )


def evidence() -> list[Evidence]:
    return [
        Evidence(1, "get_lineage", {"node": "a"}, True, "c", {"nodes": ["a"]}),
        Evidence(
            2,
            "get_raw_schema",
            {"source": "amfi"},
            True,
            "c",
            {"columns": ["x"]},
            node="raw.amfi.navall.nav",
            verdict="supports",
            collected_at=NOW + timedelta(seconds=30),
        ),
    ]


def concluded() -> Investigation:
    return Investigation(
        status=InvestigationStatus.CONCLUDED,
        conclusion=Conclusion(
            root_cause_node="raw.amfi.navall.nav",
            fault_class="schema_drift",
            explanation="The column was renamed.",
            evidence_ids=[2],
            confidence=0.9,
            alternatives=[
                Hypothesis(
                    node="brz_amfi__navall.nav_inr",
                    fault_class="nulls",
                    explanation="Blank values.",
                    confidence=0.2,
                )
            ],
            fix_location="dbt",
            fix_reason="Bronze can read the new name.",
        ),
        partial=None,
        evidence=evidence(),
        llm_calls=4,
        elapsed_seconds=3.0,
        prompt_ids=["investigator.step.v1"],
        model_ids={"investigator.step.v1": "gemini-standard"},
        prompt_hashes={"investigator.step.v1": "ab" * 32},
        input_tokens=900,
        output_tokens=120,
    )


def exhausted(with_guess: bool = True) -> Investigation:
    guess = Hypothesis(
        node="raw.amfi.navall.nav",
        fault_class="unknown",
        explanation="Leading node.",
        confidence=0.5,
    )
    return Investigation(
        status=InvestigationStatus.BUDGET_EXHAUSTED,
        conclusion=None,
        partial=guess if with_guess else None,
        evidence=evidence()[:1],
        llm_calls=11,
        elapsed_seconds=9.0,
        notes=["Stopped early: the model call limit was reached"],
    )


@pytest.fixture
def repository(engine: Engine) -> InvestigationRepository:
    return InvestigationRepository(engine)


def incident_row(engine: Engine, incident_id: int) -> Incident:
    with Session(engine) as session:
        row = session.get(Incident, incident_id)
        assert row is not None
        session.expunge(row)
        return row


def test_starting_opens_a_running_run_and_marks_the_incident(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)

    run_id = repository.start(incident_id, NOW)

    with Session(engine) as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        assert (run.status, run.finished_at) == ("running", None)
    assert incident_row(engine, incident_id).status == "investigating"


def test_a_concluded_investigation_updates_the_incident_and_stores_the_evidence(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)
    run_id = repository.start(incident_id, NOW)

    repository.finish(run_id, concluded(), LATER)

    row = incident_row(engine, incident_id)
    assert (row.status, row.fault_class, row.confidence) == ("investigating", "schema_drift", 0.9)
    assert (row.classified_by, row.root_node) == ("investigator", "raw.amfi.navall.nav")
    assert row.root_cause is not None
    assert row.root_cause["partial"] is False
    assert row.root_cause["fix_location"] == "dbt"
    assert row.root_cause["evidence_ids"] == [2]
    assert row.root_cause["alternatives"][0]["node"] == "brz_amfi__navall.nav_inr"
    stored = repository.latest_for(incident_id)
    assert stored is not None
    assert (stored.status, stored.llm_calls, stored.input_tokens) == ("succeeded", 4, 900)
    assert stored.prompt_versions == {"investigator.step.v1": "ab" * 32}
    assert stored.finished_at == LATER
    assert [e["tool"] for e in stored.evidence] == ["get_lineage", "get_raw_schema"]
    assert stored.evidence[0]["hypothesis"] is None
    assert stored.evidence[1]["hypothesis"] == "supports raw.amfi.navall.nav"
    assert stored.evidence[1]["collected_at"] == NOW + timedelta(seconds=30)
    assert stored.evidence[0]["collected_at"] == LATER  # no time recorded: the finish time


def test_an_exhausted_budget_leaves_the_incident_unresolved_with_a_partial_guess(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)
    run_id = repository.start(incident_id, NOW)

    repository.finish(run_id, exhausted(), LATER)

    row = incident_row(engine, incident_id)
    assert (row.status, row.fault_class, row.classified_by) == ("unresolved", "nulls", "rules")
    assert row.root_cause is not None
    assert row.root_cause["partial"] is True
    assert row.root_cause["node"] == "raw.amfi.navall.nav"
    assert row.root_cause["notes"] == ["Stopped early: the model call limit was reached"]
    stored = repository.latest_for(incident_id)
    assert stored is not None
    assert stored.status == "budget_exhausted"


def test_an_exhausted_budget_with_no_guess_stores_an_empty_partial(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)

    repository.finish(repository.start(incident_id, NOW), exhausted(with_guess=False), LATER)

    root_cause = incident_row(engine, incident_id).root_cause
    assert root_cause is not None
    assert (root_cause["node"], root_cause["partial"]) == (None, True)


def test_a_failed_run_keeps_its_error_and_returns_the_incident_to_detected(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)
    run_id = repository.start(incident_id, NOW)

    repository.fail(run_id, "the warehouse vanished", LATER)

    stored = repository.latest_for(incident_id)
    assert stored is not None
    assert (stored.status, stored.error) == ("failed", "the warehouse vanished")
    assert incident_row(engine, incident_id).status == "detected"


def test_an_unresolved_incident_can_be_investigated_again_and_the_newest_run_is_read(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)
    repository.finish(repository.start(incident_id, NOW), exhausted(), NOW)

    second = repository.start(incident_id, LATER)
    repository.finish(second, concluded(), LATER)

    stored = repository.latest_for(incident_id)
    assert stored is not None
    assert stored.agent_run_id == second
    assert incident_row(engine, incident_id).root_cause["partial"] is False  # type: ignore[index]


@pytest.mark.parametrize("status", ["fix_proposed", "approved", "rejected", "resolved"])
def test_an_incident_past_investigation_is_not_pulled_back(
    engine: Engine, repository: InvestigationRepository, status: str
) -> None:
    incident_id = make_incident(engine)
    with Session(engine) as session, session.begin():
        row = session.get(Incident, incident_id)
        assert row is not None
        row.status = status
        row.resolved_at = NOW if status == "resolved" else None

    with pytest.raises(StorageError, match="cannot be investigated again"):
        repository.start(incident_id, NOW)

    assert incident_row(engine, incident_id).status == status


def test_a_missing_incident_or_run_is_an_error(
    engine: Engine, repository: InvestigationRepository
) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        repository.start(999, NOW)
    with pytest.raises(StorageError, match="does not exist"):
        repository.finish(999, concluded(), NOW)
    with pytest.raises(StorageError, match="does not exist"):
        repository.fail(999, "x", NOW)


def test_a_finished_run_cannot_be_finished_or_failed_again(
    engine: Engine, repository: InvestigationRepository
) -> None:
    run_id = repository.start(make_incident(engine), NOW)
    repository.finish(run_id, concluded(), LATER)

    with pytest.raises(StorageError, match="already succeeded"):
        repository.finish(run_id, concluded(), LATER)
    with pytest.raises(StorageError, match="already succeeded"):
        repository.fail(run_id, "x", LATER)

    with Session(engine) as session:
        count = session.scalar(select(func.count()).select_from(EvidenceRow))
    assert count == 2


def test_a_save_that_fails_leaves_nothing_half_written(
    engine: Engine, repository: InvestigationRepository
) -> None:
    incident_id = make_incident(engine)
    run_id = repository.start(incident_id, NOW)
    broken = concluded()
    broken.evidence.append(Evidence(3, "t", {}, True, "c", {"bad": {1, 2}}))  # not JSON

    with pytest.raises(StorageError):
        repository.finish(run_id, broken, LATER)

    stored = repository.latest_for(incident_id)
    assert stored is not None
    assert (stored.status, stored.evidence) == ("running", [])
    row = incident_row(engine, incident_id)
    assert (row.status, row.fault_class, row.root_cause) == ("investigating", "nulls", None)


def test_an_incident_with_no_runs_has_no_investigation(
    engine: Engine, repository: InvestigationRepository
) -> None:
    assert repository.latest_for(make_incident(engine)) is None
