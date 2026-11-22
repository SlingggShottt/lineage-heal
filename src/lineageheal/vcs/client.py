"""A small GitHub client for the healing workflow, over httpx (IF-SW-06, FR-FIX-04, FR-REV-01).

Two identities are used and never mixed. Everything the agents do (branches, commits, pull
requests, issues, closing a rejected pull request) is done as the LineageHeal GitHub App with
its installation token. Approving and merging is done with an approver's own user token, passed
in per call, so a merge is always attributable to a person and the bot cannot merge by itself.

What a request may touch is narrowed here as well as by the caller: branches must be named
`heal/<incident id>`, pull requests target one configured base branch, and committed paths go
through the same allowlist as the Fixer's patches.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from pydantic import SecretStr
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lineageheal.agents.fixer.patch import PatchError, check_path
from lineageheal.core.errors import VCSError
from lineageheal.vcs.auth import API_URL, AppAuth

log = structlog.get_logger()

API_VERSION = "2022-11-28"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
BRANCH_PREFIX = "heal/"
_BRANCH = re.compile(rf"^{BRANCH_PREFIX}\d{{1,12}}$")
# A request that never reached GitHub can be sent again safely, whatever its method.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)


@dataclass(frozen=True)
class PullRequest:
    """A pull request as GitHub reports it."""

    number: int
    url: str
    state: str  # "open" or "closed"
    merged: bool
    head: str
    base: str


@dataclass(frozen=True)
class Issue:
    """An issue the bot opened."""

    number: int
    url: str


def branch_name(incident_id: int) -> str:
    """The branch a fix for an incident goes on."""
    return f"{BRANCH_PREFIX}{incident_id}"


class GitHubClient:
    """Branches, commits, pull requests and issues in one repository."""

    def __init__(
        self,
        auth: AppAuth,
        repository: str,
        *,
        base_branch: str = "main",
        http: httpx.Client | None = None,
    ) -> None:
        """Create a client.

        Args:
            auth: Source of the App's installation token.
            repository: The repository as `owner/name`.
            base_branch: The branch fixes are proposed against.
            http: The HTTP client; one with GitHub's URL and sensible timeouts when omitted.
        """
        self._auth = auth
        self._repo = repository
        self._base = base_branch
        self._http = http or make_http_client()

    # -- reading ---------------------------------------------------------------------------

    def base_branch_sha(self) -> str:
        """The commit the base branch points at now."""
        ref = self._call("GET", f"/repos/{self._repo}/git/ref/heads/{self._base}")
        return str(ref["object"]["sha"])

    def pull_request(self, number: int) -> PullRequest:
        """A pull request's current state."""
        return _pull_request(self._call("GET", f"/repos/{self._repo}/pulls/{number}"))

    # -- as the App ------------------------------------------------------------------------

    def create_branch(self, incident_id: int, sha: str) -> str:
        """Create `heal/<incident id>` at `sha` and return its name.

        Raises:
            VCSError: The branch exists already, or GitHub refused.
        """
        name = branch_name(incident_id)
        self._call(
            "POST", f"/repos/{self._repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha}
        )
        log.info("github_branch_created", branch=name)
        return name

    def commit_files(
        self, branch: str, parent_sha: str, files: Mapping[str, str], message: str
    ) -> str:
        """Commit new content for some files to a heal branch, as one commit.

        Args:
            branch: A `heal/<id>` branch.
            parent_sha: The commit the branch is at, which becomes the commit's parent.
            files: Path to complete new text. Each path must be one a fix may change.
            message: The commit message.

        Returns:
            The new commit's SHA.

        Raises:
            VCSError: The branch name or a path is not allowed, or GitHub refused.
        """
        _require_heal_branch(branch)
        if not files:
            raise VCSError("There is nothing to commit")
        for path in files:
            try:
                check_path(path)
            except PatchError as exc:
                raise VCSError(f"Refusing to commit {path!r}: {exc}") from exc
        repo = f"/repos/{self._repo}/git"
        parent = self._call("GET", f"{repo}/commits/{parent_sha}")
        tree = self._call(
            "POST",
            f"{repo}/trees",
            json={
                "base_tree": parent["tree"]["sha"],
                "tree": [
                    {"path": path, "mode": "100644", "type": "blob", "content": text}
                    for path, text in files.items()
                ],
            },
        )
        commit = self._call(
            "POST",
            f"{repo}/commits",
            json={"message": message, "tree": tree["sha"], "parents": [parent_sha]},
        )
        self._call("PATCH", f"{repo}/refs/heads/{branch}", json={"sha": commit["sha"]})
        return str(commit["sha"])

    def open_pull_request(self, branch: str, title: str, body: str) -> PullRequest:
        """Open a pull request from a heal branch into the base branch."""
        _require_heal_branch(branch)
        data = self._call(
            "POST",
            f"/repos/{self._repo}/pulls",
            json={"title": title, "body": body, "head": branch, "base": self._base},
        )
        log.info("github_pull_request_opened", number=data["number"])
        return _pull_request(data)

    def open_issue(self, title: str, body: str, labels: tuple[str, ...] = ()) -> Issue:
        """Open an issue, for a root cause outside the dbt project."""
        data = self._call(
            "POST",
            f"/repos/{self._repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
        )
        log.info("github_issue_opened", number=data["number"])
        return Issue(int(data["number"]), str(data["html_url"]))

    def close_pull_request(self, number: int, comment: str | None = None) -> None:
        """Close a pull request unmerged, with an optional comment saying why."""
        if comment:
            self._call(
                "POST", f"/repos/{self._repo}/issues/{number}/comments", json={"body": comment}
            )
        self._call("PATCH", f"/repos/{self._repo}/pulls/{number}", json={"state": "closed"})

    # -- as an approver --------------------------------------------------------------------

    def approve(self, number: int, user_token: SecretStr, body: str = "") -> None:
        """Submit an approving review as the approver, with their own token."""
        self._call(
            "POST",
            f"/repos/{self._repo}/pulls/{number}/reviews",
            json={"event": "APPROVE", "body": body},
            user_token=user_token,
        )

    def squash_merge(self, number: int, user_token: SecretStr) -> str:
        """Squash-merge a pull request as the approver and return the merge commit's SHA.

        Raises:
            VCSError: GitHub refused, for example because required checks have not passed.
        """
        data = self._call(
            "PUT",
            f"/repos/{self._repo}/pulls/{number}/merge",
            json={"merge_method": "squash"},
            user_token=user_token,
        )
        log.info("github_pull_request_merged", number=number)
        return str(data["sha"])

    # -- plumbing --------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        user_token: SecretStr | None = None,
    ) -> dict[str, Any]:
        token = user_token.get_secret_value() if user_token else self._auth.token()
        try:
            response = self._send(method, path, json, token)
        except httpx.HTTPError as exc:
            raise VCSError(f"GitHub could not be reached: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise VCSError(_describe(method, path, response))
        result: dict[str, Any] = response.json() if response.content else {}
        return result

    @retry(
        retry=retry_if_exception_type(_NOT_SENT),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    def _send(
        self, method: str, path: str, json: dict[str, Any] | None, token: str
    ) -> httpx.Response:
        return self._http.request(
            method, path, json=json, headers={"Authorization": f"Bearer {token}"}
        )


def make_http_client() -> httpx.Client:
    """An HTTP client for GitHub's API with the headers it asks for."""
    return httpx.Client(
        base_url=API_URL,
        timeout=TIMEOUT,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION},
    )


def _require_heal_branch(branch: str) -> None:
    if not _BRANCH.fullmatch(branch):
        raise VCSError(f"{branch!r} is not a heal branch (expected {BRANCH_PREFIX}<incident id>)")


def _pull_request(data: Mapping[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(data["number"]),
        url=str(data["html_url"]),
        state=str(data["state"]),
        merged=bool(data.get("merged", False)),
        head=str(data["head"]["ref"]),
        base=str(data["base"]["ref"]),
    )


def _describe(method: str, path: str, response: httpx.Response) -> str:
    """A failure message built from GitHub's own words, never from the request or its token."""
    try:
        reason = str(response.json().get("message", ""))[:300]
    except ValueError:
        reason = ""
    limited = response.headers.get("x-ratelimit-remaining") == "0" or response.status_code == 429
    hint = " (rate limited)" if limited else ""
    return f"GitHub {method} {path} failed with HTTP {response.status_code}{hint}: {reason}"
