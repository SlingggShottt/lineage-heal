"""FR-FIX-04, FR-REV-01: branches, commits, pull requests, issues and the approver's merge."""

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from lineageheal.core.errors import VCSError
from lineageheal.vcs import GitHubClient

from .conftest import APP_TOKEN, REPO

PR = {
    "number": 7,
    "html_url": f"https://github.com/{REPO}/pull/7",
    "state": "open",
    "merged": False,
    "head": {"ref": "heal/12"},
    "base": {"ref": "main"},
}
FILE = "dbt/models/bronze/brz_amfi__navall.sql"


def body(route: respx.Route) -> dict[str, object]:
    return json.loads(route.calls.last.request.content)


def test_the_base_branch_sha_is_read(client: GitHubClient, github: respx.MockRouter) -> None:
    github.get(f"/repos/{REPO}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "abc123"}})
    )

    assert client.base_branch_sha() == "abc123"
    assert github.calls.last.request.headers["authorization"] == f"Bearer {APP_TOKEN}"


def test_a_branch_is_named_after_the_incident(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    route = github.post(f"/repos/{REPO}/git/refs").mock(return_value=httpx.Response(201, json={}))

    assert client.create_branch(12, "abc123") == "heal/12"
    assert body(route) == {"ref": "refs/heads/heal/12", "sha": "abc123"}


def test_an_existing_branch_is_an_error_with_githubs_reason(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    github.post(f"/repos/{REPO}/git/refs").mock(
        return_value=httpx.Response(422, json={"message": "Reference already exists"})
    )

    with pytest.raises(VCSError, match="Reference already exists"):
        client.create_branch(12, "abc123")


def test_files_are_committed_as_one_commit_on_the_branch(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    github.get(f"/repos/{REPO}/git/commits/parent1").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "tree0"}})
    )
    tree = github.post(f"/repos/{REPO}/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": "tree1"})
    )
    commit = github.post(f"/repos/{REPO}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "commit1"})
    )
    ref = github.patch(f"/repos/{REPO}/git/refs/heads/heal/12").mock(
        return_value=httpx.Response(200, json={})
    )

    sha = client.commit_files("heal/12", "parent1", {FILE: "select 1\n"}, "fix it")

    assert sha == "commit1"
    assert body(tree)["base_tree"] == "tree0"
    assert body(tree)["tree"] == [
        {"path": FILE, "mode": "100644", "type": "blob", "content": "select 1\n"}
    ]
    assert body(commit) == {"message": "fix it", "tree": "tree1", "parents": ["parent1"]}
    assert body(ref) == {"sha": "commit1"}


@pytest.mark.parametrize(
    "path", ["src/lineageheal/cli.py", ".github/workflows/ci.yml", "dbt/seeds/x.csv", "../x.sql"]
)
def test_a_path_outside_the_allowlist_is_never_committed(
    client: GitHubClient, github: respx.MockRouter, path: str
) -> None:
    with pytest.raises(VCSError, match="Refusing"):
        client.commit_files("heal/12", "parent1", {path: "x"}, "m")

    assert github.calls.call_count == 0


@pytest.mark.parametrize("branch", ["main", "heal/", "heal/abc", "feature/12", "heal/12/../main"])
def test_only_heal_branches_can_be_written(client: GitHubClient, branch: str) -> None:
    with pytest.raises(VCSError, match="not a heal branch"):
        client.commit_files(branch, "p", {FILE: "x"}, "m")
    with pytest.raises(VCSError, match="not a heal branch"):
        client.open_pull_request(branch, "t", "b")


def test_a_pull_request_targets_the_base_branch(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    route = github.post(f"/repos/{REPO}/pulls").mock(return_value=httpx.Response(201, json=PR))

    pull = client.open_pull_request("heal/12", "Fix", "Body")

    assert (pull.number, pull.state, pull.head, pull.base) == (7, "open", "heal/12", "main")
    assert body(route) == {"title": "Fix", "body": "Body", "head": "heal/12", "base": "main"}


def test_an_issue_is_opened_with_labels(client: GitHubClient, github: respx.MockRouter) -> None:
    route = github.post(f"/repos/{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 9, "html_url": "https://x/9"})
    )

    issue = client.open_issue("Suggest", "Body", ("lineageheal",))

    assert (issue.number, issue.url) == (9, "https://x/9")
    assert body(route)["labels"] == ["lineageheal"]


def test_a_rejected_pull_request_is_closed_with_the_reason(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    comment = github.post(f"/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    close = github.patch(f"/repos/{REPO}/pulls/7").mock(return_value=httpx.Response(200, json=PR))

    client.close_pull_request(7, "Wrong column")

    assert body(comment) == {"body": "Wrong column"}
    assert body(close) == {"state": "closed"}


def test_approval_and_merge_use_the_approvers_token_never_the_apps(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    review = github.post(f"/repos/{REPO}/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={})
    )
    merge = github.put(f"/repos/{REPO}/pulls/7/merge").mock(
        return_value=httpx.Response(200, json={"sha": "merged1"})
    )
    user = SecretStr("gho_user_token")

    client.approve(7, user)
    sha = client.squash_merge(7, user)

    assert sha == "merged1"
    assert body(review)["event"] == "APPROVE"
    assert body(merge) == {"merge_method": "squash"}
    for route in (review, merge):
        assert route.calls.last.request.headers["authorization"] == "Bearer gho_user_token"


def test_a_refused_merge_explains_why(client: GitHubClient, github: respx.MockRouter) -> None:
    github.put(f"/repos/{REPO}/pulls/7/merge").mock(
        return_value=httpx.Response(405, json={"message": "Required status check is expected"})
    )

    with pytest.raises(VCSError, match="Required status check"):
        client.squash_merge(7, SecretStr("gho_user_token"))


def test_a_rate_limit_is_named_as_one(client: GitHubClient, github: respx.MockRouter) -> None:
    github.get(f"/repos/{REPO}/pulls/7").mock(
        return_value=httpx.Response(
            403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate limit"}
        )
    )

    with pytest.raises(VCSError, match="rate limited"):
        client.pull_request(7)


def test_a_failure_message_never_contains_a_token(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    github.put(f"/repos/{REPO}/pulls/7/merge").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    with pytest.raises(VCSError) as error:
        client.squash_merge(7, SecretStr("gho_secret_value"))

    assert "gho_secret_value" not in str(error.value)


def test_a_request_that_never_connected_is_retried(
    client: GitHubClient, github: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    route = github.get(f"/repos/{REPO}/pulls/7").mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=PR)]
    )

    assert client.pull_request(7).number == 7
    assert route.call_count == 2


def test_a_request_that_may_have_been_received_is_not_repeated(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    route = github.post(f"/repos/{REPO}/issues").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(VCSError, match="could not be reached"):
        client.open_issue("t", "b")

    assert route.call_count == 1


def test_a_refusal_carries_githubs_status_and_reason_for_the_caller_to_act_on(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    github.put(f"/repos/{REPO}/pulls/7/merge").mock(
        return_value=httpx.Response(405, json={"message": "Required status check is expected."})
    )

    with pytest.raises(VCSError) as raised:
        client.squash_merge(7, SecretStr("ghu_user"))

    assert raised.value.status == 405
    assert raised.value.reason == "Required status check is expected."
    assert "ghu_user" not in str(raised.value)


def test_a_connection_failure_has_no_status(
    client: GitHubClient, github: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    github.get(f"/repos/{REPO}/pulls/7").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(VCSError) as raised:
        client.pull_request(7)

    assert raised.value.status is None


def test_a_merged_pull_request_names_who_merged_it(
    client: GitHubClient, github: respx.MockRouter
) -> None:
    body = {**PR, "state": "closed", "merged": True, "merged_by": {"login": "ada"}}
    github.get(f"/repos/{REPO}/pulls/7").mock(return_value=httpx.Response(200, json=body))

    assert client.pull_request(7).merged_by == "ada"
