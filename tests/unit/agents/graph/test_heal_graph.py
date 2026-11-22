"""FR-ORC-04: the graph detects a run's incidents and investigates each one, saving the result."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from detector_graphs import failing
from investigator_scripts import FAILING_NODE, build, conclusion, lineage_call
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lineageheal.agents.graph import HealDependencies, OutcomeKind, run_heal
from lineageheal.agents.graph.nodes import group_of
from lineageheal.agents.investigator import Investigator
from lineageheal.core.signals import RunSummary
from lineageheal.llm.types import LLMError
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.models import AgentRun, Incident
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
RUN = "run-1"


def good_script(times: int = 1) -> list[str | LLMError]:
    return [lineage_call(action="conclude"), conclusion()] * times


def open_incident(engine: Engine, signal_name: str, fingerprint: str) -> int:
    repository = IncidentRepository(engine)
    created = repository.create(
        IncidentDraft(
            summary="not_null on slv_amfi__nav.nav_inr",
            severity="error",
            fingerprint=fingerprint,
            root_node=FAILING_NODE,
            affected={
                "models": ["slv_amfi__nav", "gld_mf_nav_daily"],
                "columns": ["slv_amfi__nav.nav_inr"],
                "gold_tables": ["gld_mf_nav_daily"],
            },
            lineage_version=None,
            fault_class="nulls",
            confidence=0.6,
            classified_by="rules",
            opened_at=NOW,
        )
    )
    signal = failing("slv_amfi__nav", "nav_inr", name=signal_name)
    repository.attach(created.id, RUN, [signal], at=NOW)
    return created.id


@pytest.fixture
def ids(engine: Engine) -> list[int]:
    signals = (
        failing("slv_amfi__nav", "nav_inr", name="a"),
        failing("slv_amfi__nav", "nav_inr", name="b"),
    )
    RunRepository(engine).record(
        RunSummary(run_id=RUN, generated_at=NOW, counts={}, signals=signals)
    )
    return [open_incident(engine, "a", "fp-a"), open_incident(engine, "b", "fp-b")]


def deps(
    engine: Engine, ids: list[int], investigator: Investigator, makes: list[int] | None = None
) -> HealDependencies:
    def make() -> Investigator:
        if makes is not None:
            makes.append(1)
        return investigator

    return HealDependencies(
        detect=lambda: list(ids),
        incidents=IncidentRepository(engine),
        investigations=InvestigationRepository(engine),
        make_investigator=make,
        now=lambda: NOW,
    )


def status_of(engine: Engine, incident_id: int) -> str:
    with Session(engine) as session:
        row = session.get(Incident, incident_id)
        assert row is not None
        return row.status


def test_every_incident_of_the_run_is_investigated_and_saved(
    engine: Engine, ids: list[int]
) -> None:
    investigator, _ = build(good_script(2))

    outcomes = run_heal(deps(engine, ids, investigator), RUN)

    assert [o.incident_id for o in outcomes] == ids
    assert {o.kind for o in outcomes} == {OutcomeKind.CONCLUDED}
    assert outcomes[0].root_cause_node == "raw.amfi.navall.nav"
    assert outcomes[0].fix_location == "dbt"
    assert outcomes[0].llm_calls == 2
    for incident_id in ids:
        stored = InvestigationRepository(engine).latest_for(incident_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.evidence


def test_only_the_incident_asked_for_is_investigated(engine: Engine, ids: list[int]) -> None:
    investigator, _ = build(good_script())

    outcomes = run_heal(deps(engine, ids, investigator), RUN, only_incident=ids[1])

    assert [o.incident_id for o in outcomes] == [ids[1]]
    assert InvestigationRepository(engine).latest_for(ids[0]) is None


def test_an_incident_the_run_does_not_have_gives_no_outcomes(
    engine: Engine, ids: list[int]
) -> None:
    investigator, _ = build([])

    assert run_heal(deps(engine, ids, investigator), RUN, only_incident=999) == []


def test_a_run_with_no_incidents_gives_no_outcomes(engine: Engine) -> None:
    investigator, _ = build([])

    assert run_heal(deps(engine, [], investigator), RUN) == []


def test_the_investigator_is_built_once_and_only_when_needed(
    engine: Engine, ids: list[int]
) -> None:
    investigator, _ = build(good_script(2))
    makes: list[int] = []

    run_heal(deps(engine, [], investigator, makes), RUN)
    assert makes == []
    run_heal(deps(engine, ids, investigator, makes), RUN)
    assert makes == [1]


def test_an_incident_past_investigation_is_skipped_not_reopened(
    engine: Engine, ids: list[int]
) -> None:
    with Session(engine) as session, session.begin():
        row = session.get(Incident, ids[0])
        assert row is not None
        row.status = "fix_proposed"
    investigator, _ = build(good_script())

    outcomes = run_heal(deps(engine, ids, investigator), RUN)

    assert [o.kind for o in outcomes] == [OutcomeKind.SKIPPED, OutcomeKind.CONCLUDED]
    assert "fix_proposed" in outcomes[0].detail
    assert status_of(engine, ids[0]) == "fix_proposed"


def test_a_run_out_of_budget_leaves_the_incident_unresolved(engine: Engine, ids: list[int]) -> None:
    investigator, _ = build([LLMError("down")])

    outcomes = run_heal(deps(engine, ids[:1], investigator), RUN)

    assert outcomes[0].kind is OutcomeKind.BUDGET_EXHAUSTED
    assert "could not be reached" in outcomes[0].detail
    assert status_of(engine, ids[0]) == "unresolved"


def test_a_crash_is_recorded_and_does_not_stop_the_other_incidents(
    engine: Engine, ids: list[int]
) -> None:
    good, _ = build(good_script())

    class Crashing:
        calls = 0

        def investigate(self, *args: object) -> object:
            Crashing.calls += 1
            if Crashing.calls == 1:
                raise RuntimeError("warehouse vanished")
            return good.investigate(*args)  # type: ignore[arg-type]

    outcomes = run_heal(deps(engine, ids, Crashing()), RUN)  # type: ignore[arg-type]

    assert [o.kind for o in outcomes] == [OutcomeKind.FAILED, OutcomeKind.CONCLUDED]
    assert "RuntimeError: warehouse vanished" in outcomes[0].detail
    assert status_of(engine, ids[0]) == "detected"
    with Session(engine) as session:
        runs = {r.incident_id: r for r in session.query(AgentRun)}
    assert runs[ids[0]].status == "failed"
    assert runs[ids[0]].error == "RuntimeError: warehouse vanished"
    assert runs[ids[1]].status == "succeeded"


def test_more_incidents_than_the_librarys_default_step_limit_are_all_handled(
    engine: Engine, ids: list[int]
) -> None:
    investigator, _ = build([])
    many = [ids[0]] * 40  # the library's default limit is 25 steps
    with Session(engine) as session, session.begin():
        row = session.get(Incident, ids[0])
        assert row is not None
        row.status = "approved"

    outcomes = run_heal(deps(engine, many, investigator), RUN)

    assert len(outcomes) == 40
    assert {o.kind for o in outcomes} == {OutcomeKind.SKIPPED}


def test_the_group_is_rebuilt_from_what_the_detector_stored(engine: Engine, ids: list[int]) -> None:
    repository = IncidentRepository(engine)
    stored = repository.get(ids[0])
    assert stored is not None

    group = group_of(stored, repository.signals_of(ids[0]))

    assert group.root_node == FAILING_NODE
    assert group.gold_tables == ("gld_mf_nav_daily",)
    assert group.columns == ("slv_amfi__nav.nav_inr",)
    assert [s.name for s in group.signals] == ["a"]
