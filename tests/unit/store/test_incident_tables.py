"""The incident tables refuse what the lifecycle forbids, in the database and not only in code."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.store.models import (
    AgentRun,
    Evidence,
    Fix,
    Incident,
    PipelineRun,
    Review,
    Signal,
)

T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 14, 10, 5, tzinfo=UTC)


def incident(**overrides: Any) -> Incident:
    fields: dict[str, Any] = {
        "status": "detected",
        "summary": "NAV column blank",
        "opened_at": T0,
        "updated_at": T0,
    }
    return Incident(**{**fields, **overrides})


def agent_run(incident_id: int, **overrides: Any) -> AgentRun:
    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "status": "running",
        "started_at": T0,
        "model_ids": {"investigator": "model-a"},
        "prompt_versions": {"investigator": "v1"},
    }
    return AgentRun(**{**fields, **overrides})


def fix(incident_id: int, **overrides: Any) -> Fix:
    fields: dict[str, Any] = {
        "incident_id": incident_id,
        "kind": "dbt_patch",
        "status": "unverified",
        "patch": "--- a/x.sql\n+++ b/x.sql\n",
        "files": ["dbt/models/silver/x.sql"],
        "created_at": T0,
        "updated_at": T0,
    }
    return Fix(**{**fields, **overrides})


def review(fix_id: int, **overrides: Any) -> Review:
    fields: dict[str, Any] = {
        "fix_id": fix_id,
        "reviewer_login": "jatinbansal2994",
        "decision": "approved",
        "decided_at": T1,
    }
    return Review(**{**fields, **overrides})


def refused(engine: Engine, *rows: Any) -> None:
    """Assert that the database rejects the rows, whatever the code allowed."""
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add_all(rows)


def stored(engine: Engine, *rows: Any) -> list[int]:
    with Session(engine) as session, session.begin():
        session.add_all(rows)
        session.flush()
        return [row.id for row in rows]


def saved(engine: Engine, *rows: Any) -> None:
    """Store rows whose keys the caller does not need, such as a run keyed by its text id."""
    with Session(engine) as session, session.begin():
        session.add_all(rows)


def count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


# --- incidents ---------------------------------------------------------------------------


def test_an_incident_keeps_its_diagnosis_as_structured_data(engine: Engine) -> None:
    root_cause = {"node": "raw.amfi.navall.Net Asset Value", "in_dbt": False, "why": "blank"}
    (incident_id,) = stored(
        engine, incident(fault_class="nulls", root_cause=root_cause, confidence=0.82)
    )

    with Session(engine) as session:
        row = session.get(Incident, incident_id)

    assert row is not None
    assert row.root_cause == root_cause
    assert (row.fault_class, row.confidence) == ("nulls", 0.82)


@pytest.mark.parametrize("status", ["detected", "investigating", "fix_proposed", "resolved"])
def test_every_lifecycle_status_is_accepted(engine: Engine, status: str) -> None:
    resolved_at = T1 if status == "resolved" else None

    stored(engine, incident(status=status, resolved_at=resolved_at))


def test_an_unknown_status_is_refused(engine: Engine) -> None:
    refused(engine, incident(status="exploded"))


def test_an_unknown_fault_class_is_refused(engine: Engine) -> None:
    refused(engine, incident(fault_class="gremlins"))


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_a_confidence_outside_zero_to_one_is_refused(engine: Engine, confidence: float) -> None:
    refused(engine, incident(confidence=confidence))


def test_a_resolved_incident_must_say_when(engine: Engine) -> None:
    refused(engine, incident(status="resolved", resolved_at=None))


def test_an_open_incident_cannot_have_a_resolution_time(engine: Engine) -> None:
    refused(engine, incident(status="investigating", resolved_at=T1))


# --- signals -----------------------------------------------------------------------------


def a_signal(run_id: str = "run-1", **overrides: Any) -> Signal:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "kind": "test_failed",
        "severity": "error",
        "detected_at": T0,
        "name": "not_null_x",
        "check_type": "not_null",
        "models": ["x"],
        "message": "Got 1 results",
    }
    return Signal(**{**fields, **overrides})


def a_run(run_id: str = "run-1", **overrides: Any) -> PipelineRun:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "status": "failed",
        "generated_at": T0,
        "counts": {"fail": 1},
        "skipped_models": [],
        "recorded_at": T0,
    }
    return PipelineRun(**{**fields, **overrides})


def test_a_signal_must_belong_to_a_known_run(engine: Engine) -> None:
    refused(engine, a_signal(run_id="no-such-run"))


def test_an_unknown_signal_kind_or_severity_is_refused(engine: Engine) -> None:
    saved(engine, a_run())

    refused(engine, a_signal(kind="gremlin"))
    refused(engine, a_signal(severity="panic"))


def test_an_unknown_run_status_is_refused(engine: Engine) -> None:
    refused(engine, a_run(status="dreaming"))


def test_deleting_an_incident_releases_its_signals_instead_of_deleting_them(
    engine: Engine,
) -> None:
    saved(engine, a_run())
    (incident_id,) = stored(engine, incident())
    saved(engine, a_signal(incident_id=incident_id))

    with Session(engine) as session, session.begin():
        session.delete(session.get(Incident, incident_id))

    with Session(engine) as session:
        remaining = session.scalars(select(Signal)).one()
    assert remaining.incident_id is None


# --- agent runs and evidence -------------------------------------------------------------


def test_a_running_agent_has_no_end_time_but_a_finished_one_must(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    stored(engine, agent_run(incident_id))
    stored(engine, agent_run(incident_id, status="succeeded", finished_at=T1))
    refused(engine, agent_run(incident_id, status="succeeded"))


def test_a_failed_agent_run_says_why(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    refused(engine, agent_run(incident_id, status="failed", finished_at=T1))
    stored(engine, agent_run(incident_id, status="failed", finished_at=T1, error="quota"))


def test_an_agent_run_must_belong_to_an_incident(engine: Engine) -> None:
    refused(engine, agent_run(999))


def test_evidence_keeps_the_tool_call_and_its_output(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())
    (run_id,) = stored(engine, agent_run(incident_id))
    (evidence_id,) = stored(
        engine,
        Evidence(
            agent_run_id=run_id,
            tool="get_lineage_path",
            arguments={"node": "gld_mf_nav_daily.nav_inr"},
            output=[{"node": "slv_amfi__nav.nav_inr"}, {"node": "brz_amfi__navall.nav_inr"}],
            collected_at=T0,
        ),
    )

    with Session(engine) as session:
        row = session.get(Evidence, evidence_id)

    assert row is not None
    assert row.arguments == {"node": "gld_mf_nav_daily.nav_inr"}
    assert [step["node"] for step in row.output] == [
        "slv_amfi__nav.nav_inr",
        "brz_amfi__navall.nav_inr",
    ]


# --- fixes and reviews -------------------------------------------------------------------


def test_a_patch_needs_a_diff(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    refused(engine, fix(incident_id, patch=None))


def test_a_suggestion_needs_no_diff(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    stored(engine, fix(incident_id, kind="suggestion", patch=None, status="unverified"))


@pytest.mark.parametrize("status", ["verified", "published", "merged"])
def test_no_patch_goes_further_without_a_sandbox_result(engine: Engine, status: str) -> None:
    (incident_id,) = stored(engine, incident())

    refused(engine, fix(incident_id, status=status, github_url="https://github.com/x/y/pull/1"))
    stored(
        engine,
        fix(
            incident_id,
            status=status,
            sandbox_result={"passed": True},
            github_url="https://github.com/x/y/pull/1",
        ),
    )


def test_a_published_fix_links_to_github(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    refused(engine, fix(incident_id, kind="suggestion", patch=None, status="published"))
    stored(
        engine,
        fix(
            incident_id,
            kind="suggestion",
            patch=None,
            status="published",
            github_url="https://github.com/x/y/issues/2",
            github_number=2,
        ),
    )


def test_an_unknown_fix_kind_or_status_is_refused(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())

    refused(engine, fix(incident_id, kind="magic"))
    refused(engine, fix(incident_id, status="yolo"))


def test_a_rejection_needs_a_reason_and_an_approval_does_not(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())
    (fix_id,) = stored(engine, fix(incident_id))

    stored(engine, review(fix_id))
    refused(engine, review(fix_id, decision="rejected"))
    refused(engine, review(fix_id, decision="rejected", reason="   "))
    stored(engine, review(fix_id, decision="rejected", reason="Wrong column"))


def test_an_unknown_decision_is_refused(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())
    (fix_id,) = stored(engine, fix(incident_id))

    refused(engine, review(fix_id, decision="shrug"))


def test_deleting_an_incident_deletes_what_hangs_from_it(engine: Engine) -> None:
    (incident_id,) = stored(engine, incident())
    (run_id,) = stored(engine, agent_run(incident_id))
    stored(
        engine,
        Evidence(agent_run_id=run_id, tool="t", arguments={}, output={}, collected_at=T0),
    )
    (fix_id,) = stored(engine, fix(incident_id))
    stored(engine, review(fix_id))

    with Session(engine) as session, session.begin():
        session.delete(session.get(Incident, incident_id))

    for model in (AgentRun, Evidence, Fix, Review):
        assert count(engine, model) == 0
