"""Opens the pull request for a patch that passed the sandbox (FR-FIX-04).

Only a patch with a passing sandbox result on record gets here: the database refuses to mark a
patch published otherwise, and this function checks the caller's verdict first. It creates the
`heal/<incident id>` branch from the base branch, commits the patch's files, opens the pull
request with the template's text and records the link. It never merges: merging is an
approver's act with their own token (FR-REV-01).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import structlog

from lineageheal.agents.fixer.patch import Patch
from lineageheal.core.errors import VCSError
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import StoredIncident
from lineageheal.vcs.client import PullRequest
from lineageheal.vcs.pull_request import render_pull_request

log = structlog.get_logger(__name__)

MAX_EVIDENCE_LINE = 240


class PullRequestOpener(Protocol):
    """What publishing needs from the GitHub client."""

    def base_branch_sha(self) -> str:
        """The commit the base branch points at."""

    def create_branch(self, incident_id: int, sha: str) -> str:
        """Create the incident's heal branch at `sha`."""

    def commit_files(
        self, branch: str, parent_sha: str, files: Mapping[str, str], message: str
    ) -> str:
        """Commit new file contents to the branch."""

    def open_pull_request(self, branch: str, title: str, body: str) -> PullRequest:
        """Open a pull request from the branch into the base branch."""


@dataclass(frozen=True)
class PublishedPatch:
    """A verified patch and the pull request it became."""

    branch: str
    number: int
    url: str


def publish_patch(
    fixes: FixRepository,
    github: PullRequestOpener,
    incident: StoredIncident,
    fix_id: int,
    patch: Patch,
    sandbox: Mapping[str, Any],
    *,
    lineage_path: Sequence[str],
    evidence: Sequence[str],
    change: str,
    at: datetime,
    dashboard_url: str | None = None,
) -> PublishedPatch:
    """Open a pull request for a verified patch and record it.

    Args:
        fixes: The fix repository.
        github: Creates the branch, commit and pull request.
        incident: The incident the patch fixes.
        fix_id: The stored fix holding the patch and its sandbox result.
        patch: The verified patch, with the new text of each file.
        sandbox: The sandbox result as stored, which must say the patch passed.
        lineage_path: Nodes from the root cause to a failing column.
        evidence: One line per piece of evidence.
        change: What the patch does and why.
        at: The time to record.
        dashboard_url: A link to the incident in the dashboard, if there is a deployment.

    Raises:
        VCSError: The patch did not pass the sandbox, carries no file text, or GitHub refused.
        StorageError: The link could not be stored.
    """
    if not sandbox.get("passed"):
        raise VCSError("Refusing to open a pull request for a patch that did not pass the sandbox")
    if not patch.new_contents:
        raise VCSError("The patch carries no file contents to commit")
    cause = incident.root_cause or {}
    content = render_pull_request(
        incident_id=incident.id,
        summary=incident.summary,
        fault_class=incident.fault_class,
        confidence=incident.confidence,
        explanation=str(cause.get("explanation", "")),
        lineage_path=lineage_path,
        evidence=evidence,
        change=change,
        sandbox=dict(sandbox),
        dashboard_url=dashboard_url,
    )
    base = github.base_branch_sha()
    branch = github.create_branch(incident.id, base)
    github.commit_files(branch, base, patch.new_contents, content.title)
    pull = github.open_pull_request(branch, content.title, content.body)
    fixes.mark_published(fix_id, branch, pull.number, pull.url, at)
    log.info("patch_published", incident_id=incident.id, pull_request=pull.number)
    return PublishedPatch(branch, pull.number, pull.url)


def evidence_lines(rows: Sequence[Mapping[str, Any]], limit: int = 8) -> list[str]:
    """One short line per evidence row: the tool, its arguments and what it returned."""
    lines = []
    for row in rows[:limit]:
        arguments = json.dumps(row.get("arguments", {}), default=str, sort_keys=True)
        output = json.dumps(row.get("output", {}), default=str, sort_keys=True)
        lines.append(f"{row.get('tool', 'tool')} {arguments} → {output}"[:MAX_EVIDENCE_LINE])
    return lines
