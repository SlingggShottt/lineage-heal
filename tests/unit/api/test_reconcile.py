"""FR-API-04: reading an incident brings its pull request or issue up to date with GitHub."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from lineageheal.agents.fixer import Patch
from lineageheal.api.app import create_app
from lineageheal.api.reconcile import CLOSED_ON_GITHUB
from lineageheal.core.settings import Settings
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.models import Fix, Review
from lineageheal.vcs.auth import AppAuth
from lineageheal.vcs.client import GitHubClient, make_http_client

REPO = "acme/pipeline"
START = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
PR = 101
DIFF = "--- a/dbt/models/bronze/b.sql\n+++ b/dbt/models/bronze/b.sql\n@@ -1 +1 @@\n-a\n+b\n"


class FakeAppAuth:
    def token(self) -> str:
        return "ghs_app"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        yield router


def pull_request(
    *,
    state: str = "open",
    merged: bool = False,
    head: str = "heal/1",
    merged_by: str | None = None,
) -> dict[str, Any]:
    return {
        "number": PR,
        "html_url": f"https://github.com/{REPO}/pull/{PR}",
        "state": state,
        "merged": merged,
        "merged_by": {"login": merged_by} if merged_by else None,
        "head": {"ref": head},
        "base": {"ref": "main"},
    }


def serve_pull_request(github: respx.MockRouter, **kwargs: Any) -> respx.Route:
    return github.get(f"/repos/{REPO}/pulls/{PR}").mock(
        return_value=httpx.Response(200, json=pull_request(**kwargs))
    )


@pytest.fixture
def client(engine: Engine, clock: Clock, github: respx.MockRouter) -> Iterator[TestClient]:
    api = create_app(Settings(), clock=clock)
    api.state.engine = engine
    api.state.github = GitHubClient(
        cast(AppAuth, FakeAppAuth()), REPO, base_branch="main", http=make_http_client()
    )
    with TestClient(api, base_url="http://localhost:8000") as test_client:
        yield test_client


def new_incident(engine: Engine) -> int:
    return (
        IncidentRepository(engine)
        .create(
            IncidentDraft(
                summary="not_null on brz_amfi__navall.nav_inr (8742 rows)",
                severity="error",
                fingerprint="fp",
                root_node=None,
                affected={},
                lineage_version=None,
                fault_class="schema_drift",
                confidence=0.9,
                classified_by="investigator",
                opened_at=START,
            )
        )
        .id
    )


def published_patch(engine: Engine) -> int:
    incident_id = new_incident(engine)
    fixes = FixRepository(engine)
    [fix_id] = fixes.save_attempts(
        incident_id,
        [SandboxedPatch(Patch(DIFF, ("dbt/models/bronze/b.sql",), 2), {"passed": True}, True)],
        START,
    )
    fixes.mark_published(
        fix_id, f"heal/{incident_id}", PR, f"https://github.com/{REPO}/pull/{PR}", START
    )
    return incident_id


def filed_suggestion(engine: Engine) -> int:
    incident_id = new_incident(engine)
    fixes = FixRepository(engine)
    fix_id = fixes.save_suggestion(incident_id, {"summary": "Change the loader"}, START)
    fixes.mark_filed(fix_id, 7, f"https://github.com/{REPO}/issues/7", START)
    return incident_id


def reviews(engine: Engine) -> list[Review]:
    with Session(engine) as session:
        return list(session.scalars(select(Review)).all())


def fix_status(engine: Engine, incident_id: int) -> str:
    with Session(engine) as session:
        return session.scalars(select(Fix).where(Fix.incident_id == incident_id)).one().status


def test_a_merge_made_on_github_is_recorded_when_the_incident_is_read(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github, state="closed", merged=True, merged_by="grace")

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["status"] == "approved"
    assert body["decision"]["decision"] == "approved"
    assert body["decision"]["reviewer"] == "grace"
    assert fix_status(engine, incident_id) == "merged"


def test_a_pull_request_closed_on_github_is_recorded_as_a_rejection_without_a_made_up_reason(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github, state="closed", merged=False)

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["status"] == "rejected"
    assert body["decision"]["reviewer"] == "github"
    assert body["decision"]["reason"] == CLOSED_ON_GITHUB
    assert fix_status(engine, incident_id) == "closed"


def test_the_fix_endpoint_reconciles_too(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github, state="closed", merged=True, merged_by="grace")

    assert client.get(f"/incidents/{incident_id}/fix").json()["status"] == "merged"


def test_a_pull_request_that_is_still_open_changes_nothing(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github)

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["status"] == "fix_proposed"
    assert body["decision"] is None
    assert reviews(engine) == []


def test_a_pull_request_with_another_head_is_never_trusted(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github, state="closed", merged=True, head="someone/else")

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["status"] == "fix_proposed"
    assert reviews(engine) == []


def test_github_is_asked_once_a_minute_not_on_every_read(
    client: TestClient, engine: Engine, github: respx.MockRouter, clock: Clock
) -> None:
    incident_id = published_patch(engine)
    route = serve_pull_request(github)

    client.get(f"/incidents/{incident_id}")
    client.get(f"/incidents/{incident_id}/fix")
    clock.now = START + timedelta(seconds=59)
    client.get(f"/incidents/{incident_id}")
    assert route.call_count == 1

    clock.now = START + timedelta(seconds=61)
    client.get(f"/incidents/{incident_id}")
    assert route.call_count == 2


def test_a_github_failure_serves_the_stored_state_and_writes_nothing(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(return_value=httpx.Response(500, json={}))

    response = client.get(f"/incidents/{incident_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "fix_proposed"
    assert reviews(engine) == []


def test_an_unreachable_github_does_not_fail_the_read(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = published_patch(engine)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(side_effect=httpx.ConnectError("down"))

    assert client.get(f"/incidents/{incident_id}").status_code == 200


def test_without_github_configured_the_stored_state_is_served(engine: Engine, clock: Clock) -> None:
    incident_id = published_patch(engine)
    api = create_app(Settings(), clock=clock)
    api.state.engine = engine

    with TestClient(api) as bare:
        response = bare.get(f"/incidents/{incident_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "fix_proposed"


def test_a_decision_already_recorded_is_not_recorded_twice(
    client: TestClient, engine: Engine, github: respx.MockRouter, clock: Clock
) -> None:
    incident_id = published_patch(engine)
    serve_pull_request(github, state="closed", merged=True, merged_by="grace")
    client.get(f"/incidents/{incident_id}")

    clock.now = START + timedelta(minutes=5)
    client.get(f"/incidents/{incident_id}")

    assert len(reviews(engine)) == 1


def test_listing_incidents_never_asks_github(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    published_patch(engine)
    route = serve_pull_request(github, state="closed", merged=True)

    assert client.get("/incidents").status_code == 200
    assert route.call_count == 0


def test_a_closed_issue_marks_the_suggestion_closed_but_is_no_decision(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = filed_suggestion(engine)
    github.get(f"/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(200, json={"number": 7, "state": "closed"})
    )

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["status"] == "suggestion_filed"
    assert body["decision"] is None
    assert fix_status(engine, incident_id) == "closed"
    assert reviews(engine) == []


def test_an_open_issue_changes_nothing(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = filed_suggestion(engine)
    github.get(f"/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(200, json={"number": 7, "state": "open"})
    )

    client.get(f"/incidents/{incident_id}")

    assert fix_status(engine, incident_id) == "published"
