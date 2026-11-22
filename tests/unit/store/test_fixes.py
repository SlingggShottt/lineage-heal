"""FR-FIX-01, FR-FIX-02: a drafted patch is stored unverified; verified needs a sandbox result."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.agents.fixer import Patch
from lineageheal.core.errors import StorageError
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.models import Fix, Incident

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
PATCH = Patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", ("dbt/models/x.sql",), 2)


@pytest.fixture
def incident_id(engine: Engine) -> int:
    return (
        IncidentRepository(engine)
        .create(IncidentDraft("s", "error", "fp", None, {}, None, "nulls", 0.5, "rules", NOW))
        .id
    )


def test_a_patch_is_stored_unverified_and_read_back(engine: Engine, incident_id: int) -> None:
    repository = FixRepository(engine)

    fix_id = repository.save_patch(incident_id, PATCH, NOW)

    [stored] = repository.for_incident(incident_id)
    assert stored.id == fix_id
    assert (stored.kind, stored.status) == ("dbt_patch", "unverified")
    assert stored.patch == PATCH.diff
    assert stored.files == ["dbt/models/x.sql"]
    assert stored.sandbox_result is None
    assert (stored.created_at, stored.updated_at) == (NOW, NOW)


def test_fixes_are_listed_oldest_first_and_only_for_their_incident(
    engine: Engine, incident_id: int
) -> None:
    repository = FixRepository(engine)
    other = (
        IncidentRepository(engine)
        .create(IncidentDraft("s", "error", "fp2", None, {}, None, "nulls", 0.5, "rules", NOW))
        .id
    )
    first = repository.save_patch(incident_id, PATCH, NOW)
    second = repository.save_patch(incident_id, PATCH, NOW)
    repository.save_patch(other, PATCH, NOW)

    assert [f.id for f in repository.for_incident(incident_id)] == [first, second]
    assert repository.for_incident(999) == []


def test_a_patch_for_an_incident_that_does_not_exist_is_a_storage_error(engine: Engine) -> None:
    with pytest.raises(StorageError):
        FixRepository(engine).save_patch(999, PATCH, NOW)


def test_the_database_refuses_to_call_a_patch_verified_without_a_sandbox_result(
    engine: Engine, incident_id: int
) -> None:
    fix_id = FixRepository(engine).save_patch(incident_id, PATCH, NOW)

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.execute(update(Fix).where(Fix.id == fix_id).values(status="verified"))


def sandboxed(passed: bool) -> SandboxedPatch:
    return SandboxedPatch(PATCH, {"passed": passed, "reason": "r"}, passed)


def status_of(engine: Engine, incident_id: int) -> str:
    incident = IncidentRepository(engine).get(incident_id)
    assert incident is not None
    return incident.status


def test_a_passing_attempt_is_stored_verified_and_proposes_the_fix(
    engine: Engine, incident_id: int
) -> None:
    repository = FixRepository(engine)

    ids = repository.save_attempts(incident_id, [sandboxed(False), sandboxed(True)], NOW)

    stored = repository.for_incident(incident_id)
    assert [f.id for f in stored] == ids
    assert [f.status for f in stored] == ["unverified", "verified"]
    assert all(f.sandbox_result is not None for f in stored)
    assert status_of(engine, incident_id) == "fix_proposed"


def test_when_no_attempt_passes_the_incident_is_marked_fix_unverified(
    engine: Engine, incident_id: int
) -> None:
    repository = FixRepository(engine)

    repository.save_attempts(incident_id, [sandboxed(False), sandboxed(False)], NOW)

    assert {f.status for f in repository.for_incident(incident_id)} == {"unverified"}
    assert status_of(engine, incident_id) == "fix_unverified"


def test_a_decided_incident_keeps_its_status(engine: Engine, incident_id: int) -> None:
    with Session(engine) as session, session.begin():
        session.execute(update(Incident).values(status="rejected"))

    FixRepository(engine).save_attempts(incident_id, [sandboxed(True)], NOW)

    assert status_of(engine, incident_id) == "rejected"


def test_attempts_for_an_unknown_incident_are_refused(engine: Engine) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        FixRepository(engine).save_attempts(999, [sandboxed(True)], NOW)
