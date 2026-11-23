"""FR-REV-01, FR-REV-02: a decision is recorded whole, once, and only for what can be decided."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lineageheal.agents.fixer import Patch
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.models import Fix, Incident
from lineageheal.store.reviews import NotDecidableError, ReviewRepository

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
PATCH = Patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", ("dbt/models/x.sql",), 2)


def open_incident(engine: Engine, *, publish: bool = True) -> int:
    incident = IncidentRepository(engine).create(
        IncidentDraft("s", "error", "fp", None, {}, None, "nulls", 0.5, "rules", NOW)
    )
    fixes = FixRepository(engine)
    [fix_id] = fixes.save_attempts(
        incident.id, [SandboxedPatch(PATCH, {"passed": True}, True)], NOW
    )
    if publish:
        fixes.mark_published(fix_id, f"heal/{incident.id}", 7, "https://github.com/o/r/pull/7", NOW)
    return incident.id


def test_what_can_be_decided_is_the_published_pull_request_of_a_fix_proposed_incident(
    engine: Engine,
) -> None:
    incident_id = open_incident(engine)

    pending = ReviewRepository(engine).pending_patch(incident_id)

    assert (pending.number, pending.branch, pending.url) == (
        7,
        f"heal/{incident_id}",
        "https://github.com/o/r/pull/7",
    )


@pytest.mark.parametrize(
    ("setup", "code"),
    [("missing", "not_found"), ("unpublished", "no_pull_request"), ("resolved", "wrong_state")],
)
def test_anything_else_is_refused_with_the_reason(engine: Engine, setup: str, code: str) -> None:
    incident_id = (
        999 if setup == "missing" else open_incident(engine, publish=setup != "unpublished")
    )
    if setup == "resolved":
        with Session(engine) as session, session.begin():
            incident = session.get(Incident, incident_id)
            assert incident is not None
            incident.status, incident.resolved_at = "resolved", NOW

    with pytest.raises(NotDecidableError) as raised:
        ReviewRepository(engine).pending_patch(incident_id)

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("decision", "reason", "fix_status", "incident_status"),
    [("approved", None, "merged", "approved"), ("rejected", "Wrong model", "closed", "rejected")],
)
def test_a_decision_writes_the_review_and_moves_the_fix_and_the_incident_together(
    engine: Engine, decision: str, reason: str | None, fix_status: str, incident_status: str
) -> None:
    incident_id = open_incident(engine)
    repository = ReviewRepository(engine)
    pending = repository.pending_patch(incident_id)

    stored = repository.record_decision(
        pending, decision=decision, reviewer_login="ada", reason=reason, at=NOW
    )

    assert (stored.decision, stored.reviewer_login, stored.reason) == (decision, "ada", reason)
    with Session(engine) as session:
        fix = session.get(Fix, pending.fix_id)
        incident = session.get(Incident, incident_id)
        assert fix is not None
        assert incident is not None
        assert (fix.status, incident.status) == (fix_status, incident_status)
        assert incident.updated_at == NOW
    assert repository.latest_for_incident(incident_id) == stored


def test_a_second_decision_on_the_same_fix_is_refused_and_changes_nothing(engine: Engine) -> None:
    incident_id = open_incident(engine)
    repository = ReviewRepository(engine)
    pending = repository.pending_patch(incident_id)
    repository.record_decision(
        pending, decision="approved", reviewer_login="ada", reason=None, at=NOW
    )

    with pytest.raises(NotDecidableError) as raised:
        repository.record_decision(
            pending, decision="rejected", reviewer_login="grace", reason="no", at=NOW
        )

    assert raised.value.code == "already_decided"
    assert repository.latest_for_incident(incident_id) is not None
    assert repository.latest_for_incident(incident_id).decision == "approved"  # type: ignore[union-attr]


def test_a_decision_is_not_recorded_if_the_states_changed_underneath(engine: Engine) -> None:
    incident_id = open_incident(engine)
    repository = ReviewRepository(engine)
    pending = repository.pending_patch(incident_id)
    with Session(engine) as session, session.begin():
        incident = session.get(Incident, incident_id)
        assert incident is not None
        incident.status = "unresolved"

    with pytest.raises(NotDecidableError):
        repository.record_decision(
            pending, decision="approved", reviewer_login="ada", reason=None, at=NOW
        )

    assert repository.latest_for_incident(incident_id) is None


def test_an_incident_with_no_decision_has_none(engine: Engine) -> None:
    assert ReviewRepository(engine).latest_for_incident(open_incident(engine)) is None
