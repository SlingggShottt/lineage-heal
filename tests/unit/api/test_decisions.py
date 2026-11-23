"""FR-REV-01 to 03, IF-SW-06: approving and rejecting a fix, against a mocked GitHub."""

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
from lineageheal.api.problems import TYPE_PREFIX
from lineageheal.api.security import SESSION_COOKIE, TokenCipher, new_token
from lineageheal.core.settings import Settings
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.models import Fix, Incident, Review
from lineageheal.store.reviews import NotDecidableError, ReviewRepository
from lineageheal.store.sessions import SessionRepository, StoredSession
from lineageheal.vcs.auth import AppAuth
from lineageheal.vcs.client import GitHubClient, make_http_client

REPO = "acme/pipeline"
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
SECRET = "k" * 40
USER_TOKEN = "ghu_the_approvers_own_token"
APP_TOKEN = "ghs_the_apps_installation_token"
PR = 101
DIFF = "--- a/dbt/models/bronze/b.sql\n+++ b/dbt/models/bronze/b.sql\n@@ -1 +1 @@\n-a\n+b\n"
DASHBOARD = "https://dash.example.com"


class FakeAppAuth:
    """Stands in for the App's signed-in installation token, which is not what is tested here."""

    def token(self) -> str:
        return APP_TOKEN


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "session_secret": SECRET,
        "approver_github_logins": "ada",
        "dashboard_url": DASHBOARD,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        router.get(f"/repos/{REPO}/pulls/{PR}").mock(
            return_value=httpx.Response(200, json=pull_request())
        )
        router.post(f"/repos/{REPO}/pulls/{PR}/reviews").mock(
            return_value=httpx.Response(200, json={})
        )
        router.put(f"/repos/{REPO}/pulls/{PR}/merge").mock(
            return_value=httpx.Response(200, json={"sha": "merge123"})
        )
        router.post(f"/repos/{REPO}/issues/{PR}/comments").mock(
            return_value=httpx.Response(201, json={})
        )
        router.patch(f"/repos/{REPO}/pulls/{PR}").mock(return_value=httpx.Response(200, json={}))
        yield router


def pull_request(
    *, state: str = "open", merged: bool = False, head: str = "heal/1"
) -> dict[str, Any]:
    return {
        "number": PR,
        "html_url": f"https://github.com/{REPO}/pull/{PR}",
        "state": state,
        "merged": merged,
        "head": {"ref": head},
        "base": {"ref": "main"},
    }


@pytest.fixture
def client(engine: Engine, github: respx.MockRouter) -> Iterator[TestClient]:
    api = create_app(settings(), clock=lambda: NOW)
    api.state.engine = engine
    api.state.github = GitHubClient(
        cast(AppAuth, FakeAppAuth()), REPO, base_branch="main", http=make_http_client()
    )
    with TestClient(api, base_url="http://localhost:8000") as test_client:
        yield test_client


def open_incident(engine: Engine, *, publish: bool = True) -> int:
    incident = IncidentRepository(engine).create(
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
            opened_at=NOW,
        )
    )
    fixes = FixRepository(engine)
    [fix_id] = fixes.save_attempts(
        incident.id,
        [SandboxedPatch(Patch(DIFF, ("dbt/models/bronze/b.sql",), 2), {"passed": True}, True)],
        NOW,
    )
    if publish:
        fixes.mark_published(
            fix_id, f"heal/{incident.id}", PR, f"https://github.com/{REPO}/pull/{PR}", NOW
        )
    return incident.id


def sign_in(
    client: TestClient, engine: Engine, login: str = "ada", secret: str = SECRET
) -> dict[str, str]:
    """A signed-in session, and the headers a state-changing request needs."""
    session_id, csrf = new_token(), new_token()
    from lineageheal.api.security import hash_session_id

    SessionRepository(engine).create(
        StoredSession(
            id_hash=hash_session_id(session_id),
            github_id=1,
            login=login,
            token_encrypted=TokenCipher(secret).encrypt(USER_TOKEN),
            csrf_token=csrf,
            created_at=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(hours=8),
        )
    )
    client.cookies.set(SESSION_COOKIE, session_id)
    return {"X-CSRF-Token": csrf}


def auth_of(call: Any) -> str:
    return str(call.request.headers["authorization"])


def calls(github: respx.MockRouter) -> list[tuple[str, str, str]]:
    """(method, path, token) for every request GitHub received, in order."""
    return [
        (c.request.method, c.request.url.path.removeprefix(f"/repos/{REPO}"), auth_of(c))
        for c in github.calls
    ]


def state_of(engine: Engine, incident_id: int) -> tuple[str, list[str], int]:
    with Session(engine) as session:
        incident = session.get(Incident, incident_id)
        fixes = session.scalars(select(Fix).where(Fix.incident_id == incident_id)).all()
        reviews = session.scalars(select(Review)).all()
        assert incident is not None
        return incident.status, [f.status for f in fixes], len(reviews)


# --- approving ---------------------------------------------------------------------------------


def test_approving_reviews_and_merges_with_the_approvers_own_token_never_the_apps(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 200
    assert calls(github) == [
        ("GET", f"/pulls/{PR}", f"Bearer {APP_TOKEN}"),
        ("POST", f"/pulls/{PR}/reviews", f"Bearer {USER_TOKEN}"),  # the approver, first
        ("PUT", f"/pulls/{PR}/merge", f"Bearer {USER_TOKEN}"),
    ]
    review = next(c for c in github.calls if c.request.url.path.endswith("/reviews"))
    assert (
        review.request.read()
        == b'{"event":"APPROVE","body":"Approved from the LineageHeal dashboard by ada."}'
    )
    merge = next(c for c in github.calls if c.request.method == "PUT")
    assert merge.request.read() == b'{"merge_method":"squash"}'


def test_an_approval_is_recorded_after_github_did_it_and_moves_everything_together(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    body = client.post(f"/incidents/{incident_id}/approve", headers=headers).json()

    assert body == {
        "incident_id": incident_id,
        "fix_id": 1,
        "decision": "approved",
        "reviewer": "ada",
        "decided_at": "2026-11-24T10:00:00Z",
        "pull_request_number": PR,
        "pull_request_url": f"https://github.com/{REPO}/pull/{PR}",
        "merge_commit": "merge123",
    }
    assert state_of(engine, incident_id) == ("approved", ["merged"], 1)
    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["status"] == "approved"
    assert detail["decision"] == {
        "decision": "approved",
        "reviewer": "ada",
        "reason": None,
        "decided_at": "2026-11-24T10:00:00Z",
    }


def test_an_incident_awaiting_a_decision_shows_no_decision(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)

    assert client.get(f"/incidents/{incident_id}").json()["decision"] is None


def test_the_approvers_token_never_appears_in_a_response_or_the_logs(
    client: TestClient, engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    ok = client.post(f"/incidents/{incident_id}/approve", headers=headers)
    again = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    output = capsys.readouterr()
    for text in (ok.text, again.text, output.out, output.err):
        assert USER_TOKEN not in text
        assert APP_TOKEN not in text


# --- who may decide ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_stranger_is_told_to_sign_in_and_nothing_reaches_github(
    client: TestClient, engine: Engine, github: respx.MockRouter, action: str
) -> None:
    incident_id = open_incident(engine)

    response = client.post(f"/incidents/{incident_id}/{action}", json={"reason": "no"})

    assert response.status_code == 401
    assert response.json()["type"] == TYPE_PREFIX + "unauthenticated"
    assert calls(github) == []
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_signed_in_non_approver_is_refused_and_nothing_reaches_github(
    client: TestClient, engine: Engine, github: respx.MockRouter, action: str
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine, login="mallory")

    response = client.post(
        f"/incidents/{incident_id}/{action}", json={"reason": "no"}, headers=headers
    )

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "forbidden"
    assert calls(github) == []
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-CSRF-Token": "forged"}, {"Origin": "https://evil.example.com"}],
)
def test_an_approver_without_a_valid_csrf_token_and_origin_is_refused(
    client: TestClient,
    engine: Engine,
    github: respx.MockRouter,
    action: str,
    headers: dict[str, str],
) -> None:
    incident_id = open_incident(engine)
    good = sign_in(client, engine)
    sent = headers if "Origin" not in headers else {**good, **headers}

    response = client.post(
        f"/incidents/{incident_id}/{action}", json={"reason": "no"}, headers=sent
    )

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "csrf-failed"
    assert calls(github) == []


# --- what can be decided -----------------------------------------------------------------------


def test_an_incident_that_does_not_exist_is_a_404(client: TestClient, engine: Engine) -> None:
    headers = sign_in(client, engine)

    response = client.post("/incidents/999/approve", headers=headers)

    assert response.status_code == 404
    assert response.json()["type"] == TYPE_PREFIX + "not-found"


@pytest.mark.parametrize("incident_id", ["0", "-1", "abc"])
def test_an_id_that_cannot_be_an_incident_is_a_422(
    client: TestClient, engine: Engine, incident_id: str
) -> None:
    headers = sign_in(client, engine)

    assert client.post(f"/incidents/{incident_id}/approve", headers=headers).status_code == 422


def test_an_incident_with_no_pull_request_open_has_nothing_to_decide(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine, publish=False)  # verified, never opened as a pull request
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 409
    assert response.json()["type"] == TYPE_PREFIX + "not-decidable"
    assert "no pull request" in response.json()["detail"]
    assert calls(github) == []


def test_a_decided_incident_cannot_be_decided_again_and_github_is_not_asked(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)
    client.post(f"/incidents/{incident_id}/approve", headers=headers)
    before = len(github.calls)

    again = client.post(f"/incidents/{incident_id}/approve", headers=headers)
    reject = client.post(
        f"/incidents/{incident_id}/reject", json={"reason": "late"}, headers=headers
    )

    assert again.status_code == reject.status_code == 409
    assert "approved" in again.json()["detail"]
    assert len(github.calls) == before
    assert state_of(engine, incident_id) == ("approved", ["merged"], 1)


def test_a_pull_request_already_merged_on_github_is_not_approved_again(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(
        return_value=httpx.Response(200, json=pull_request(state="closed", merged=True))
    )
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 409
    assert "already merged on GitHub" in response.json()["detail"]
    assert [c[0] for c in calls(github)] == ["GET"]  # no review, no merge
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


def test_a_pull_request_that_is_not_the_one_opened_for_the_fix_is_never_touched(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(
        return_value=httpx.Response(200, json=pull_request(head="someone-elses-branch"))
    )
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 409
    assert "not the one opened for this fix" in response.json()["detail"]
    assert [c[0] for c in calls(github)] == ["GET"]


# --- GitHub says no ----------------------------------------------------------------------------


def test_a_refused_merge_is_a_409_with_githubs_reason_and_records_nothing(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.put(f"/repos/{REPO}/pulls/{PR}/merge").mock(
        return_value=httpx.Response(
            405, json={"message": 'Required status check "ci" is expected.'}
        )
    )
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 409
    assert response.json()["type"] == TYPE_PREFIX + "github-refused"
    assert 'Required status check "ci" is expected.' in response.json()["detail"]
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)
    # the approving review was sent before the merge was refused, so the approver can retry
    assert [c[1] for c in calls(github)][1:] == [f"/pulls/{PR}/reviews", f"/pulls/{PR}/merge"]


def test_a_token_github_no_longer_accepts_asks_the_approver_to_sign_in_again(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.post(f"/repos/{REPO}/pulls/{PR}/reviews").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 401
    assert response.json()["type"] == TYPE_PREFIX + "github-token-unusable"
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


def test_a_session_whose_stored_token_cannot_be_decrypted_asks_for_a_new_sign_in(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(
        client, engine, secret="rotated-" + "x" * 40
    )  # encrypted under another secret

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 401
    assert response.json()["type"] == TYPE_PREFIX + "github-token-unusable"
    assert calls(github) == []


def test_github_refusing_the_approver_permission_is_a_403(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.put(f"/repos/{REPO}/pulls/{PR}/merge").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible by integration"})
    )
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "github-forbidden"
    assert "Resource not accessible by integration" in response.json()["detail"]


def test_a_rate_limit_or_a_github_outage_is_a_503_to_try_again(
    client: TestClient, engine: Engine, github: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    github.put(f"/repos/{REPO}/pulls/{PR}/merge").mock(
        return_value=httpx.Response(403, json={"message": "API rate limit exceeded for user"})
    )
    limited = client.post(f"/incidents/{incident_id}/approve", headers=headers)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(side_effect=httpx.ConnectError("refused"))
    down = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    for response in (limited, down):
        assert response.status_code == 503
        assert response.json()["type"] == TYPE_PREFIX + "github-unavailable"
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


def test_without_the_github_app_configured_deciding_says_so_instead_of_failing(
    engine: Engine, github: respx.MockRouter
) -> None:
    api = create_app(settings(), clock=lambda: NOW)  # no GITHUB_APP_* settings, no client injected
    api.state.engine = engine
    incident_id = open_incident(engine)

    with TestClient(api, base_url="http://localhost:8000") as bare:
        headers = sign_in(bare, engine)
        response = bare.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "github-unavailable"
    assert calls(github) == []


# --- the gap between GitHub and the database ---------------------------------------------------


@pytest.mark.parametrize("failure", [NotDecidableError("already_decided", "raced"), RuntimeError])
def test_a_decision_github_carried_out_but_could_not_be_recorded_is_reported_as_such(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    from lineageheal.core.errors import StorageError

    incident_id = open_incident(engine)
    headers = sign_in(client, engine)
    error = failure if isinstance(failure, Exception) else StorageError("db down")

    def broken(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(ReviewRepository, "record_decision", broken)

    response = client.post(f"/incidents/{incident_id}/approve", headers=headers)

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "decision-not-recorded"
    assert f"pull request #{PR}" in response.json()["detail"]
    assert "db down" not in response.text


# --- rejecting ---------------------------------------------------------------------------------


def test_rejecting_closes_the_pull_request_as_the_app_with_the_reason_as_a_comment(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    response = client.post(
        f"/incidents/{incident_id}/reject",
        json={"reason": "  The rename is in the silver layer, not bronze. cc @alice  "},
        headers=headers,
    )

    assert response.status_code == 200
    assert calls(github) == [
        ("GET", f"/pulls/{PR}", f"Bearer {APP_TOKEN}"),
        ("POST", f"/issues/{PR}/comments", f"Bearer {APP_TOKEN}"),
        (
            "PATCH",
            f"/pulls/{PR}",
            f"Bearer {APP_TOKEN}",
        ),  # closing is the App's; no merge, no user token
    ]
    comment = next(c for c in github.calls if c.request.url.path.endswith("/comments"))
    text = comment.request.read().decode()
    assert "Rejected from the LineageHeal dashboard by ada." in text
    assert "The rename is in the silver layer" in text
    assert "@alice" not in text  # defused, so the quoted reason cannot notify anyone
    closing = next(c for c in github.calls if c.request.method == "PATCH")
    assert closing.request.read() == b'{"state":"closed"}'


def test_a_rejection_records_the_reason_and_closes_the_fix(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    body = client.post(
        f"/incidents/{incident_id}/reject", json={"reason": "  Wrong model.  "}, headers=headers
    ).json()

    assert (body["decision"], body["reviewer"], body["merge_commit"]) == ("rejected", "ada", None)
    assert state_of(engine, incident_id) == ("rejected", ["closed"], 1)
    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["decision"]["reason"] == "Wrong model."
    assert detail["status"] == "rejected"


@pytest.mark.parametrize(
    "body",
    [{}, {"reason": ""}, {"reason": "   \n\t "}, {"reason": "x" * 1001}, {"reason": 5}],
)
def test_a_rejection_needs_a_written_reason_and_nothing_reaches_github_without_one(
    client: TestClient, engine: Engine, github: respx.MockRouter, body: dict[str, Any]
) -> None:
    incident_id = open_incident(engine)
    headers = sign_in(client, engine)

    response = client.post(f"/incidents/{incident_id}/reject", json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body.reason"
    assert calls(github) == []
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


def test_a_pull_request_already_closed_on_github_cannot_be_rejected(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.get(f"/repos/{REPO}/pulls/{PR}").mock(
        return_value=httpx.Response(200, json=pull_request(state="closed"))
    )
    headers = sign_in(client, engine)

    response = client.post(
        f"/incidents/{incident_id}/reject", json={"reason": "no"}, headers=headers
    )

    assert response.status_code == 409
    assert "already closed on GitHub" in response.json()["detail"]
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


def test_if_github_will_not_close_the_pull_request_nothing_is_recorded(
    client: TestClient, engine: Engine, github: respx.MockRouter
) -> None:
    incident_id = open_incident(engine)
    github.patch(f"/repos/{REPO}/pulls/{PR}").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )
    headers = sign_in(client, engine)

    response = client.post(
        f"/incidents/{incident_id}/reject", json={"reason": "no"}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["type"] == TYPE_PREFIX + "github-refused"
    assert state_of(engine, incident_id) == ("fix_proposed", ["published"], 0)


# --- the contract ------------------------------------------------------------------------------


def test_the_schema_documents_both_endpoints_with_their_problem_responses(
    client: TestClient,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for action in ("approve", "reject"):
        responses = paths[f"/incidents/{{incident_id}}/{action}"]["post"]["responses"]
        assert {"200", "401", "403", "404", "409", "422", "503"} <= set(responses)
