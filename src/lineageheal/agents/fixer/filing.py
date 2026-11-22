"""Files a suggestion as a GitHub issue and records it (FR-FIX-05).

Only an issue is ever opened on this path: there is no branch, commit or pull request here, and
the Fixer never opens a pull request for anything outside `dbt/`. The suggestion is stored
first, so the dashboard has it even if GitHub is unreachable; the incident only becomes
`suggestion_filed` once an issue exists. Filing twice for one incident opens one issue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import structlog

from lineageheal.agents.fixer.suggestion import Suggestion
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import StoredIncident
from lineageheal.vcs.client import Issue
from lineageheal.vcs.issue import LABELS, render_issue

log = structlog.get_logger(__name__)


class IssueOpener(Protocol):
    """What filing needs from the GitHub client."""

    def open_issue(self, title: str, body: str, labels: tuple[str, ...] = ()) -> Issue:
        """Open an issue."""


@dataclass(frozen=True)
class FiledSuggestion:
    """A suggestion and the issue it was filed as."""

    fix_id: int
    issue_number: int
    issue_url: str
    already_filed: bool


def file_suggestion(
    fixes: FixRepository,
    github: IssueOpener,
    incident: StoredIncident,
    suggestion: Suggestion,
    evidence: Sequence[str],
    *,
    at: datetime,
    dashboard_url: str | None = None,
) -> FiledSuggestion:
    """Store a suggestion, open its issue and link the two.

    Args:
        fixes: The fix repository.
        github: Opens the issue.
        incident: The incident the suggestion is for.
        suggestion: The drafted change.
        evidence: One line per piece of evidence, for the issue.
        at: The time to record.
        dashboard_url: A link to the incident in the dashboard, if there is a deployment.

    Returns:
        The fix and its issue. If the incident already had a filed suggestion, that one is
        returned and nothing is opened.

    Raises:
        StorageError: The suggestion or the link could not be stored.
        VCSError: GitHub refused or could not be reached. The draft stays stored for a retry.
    """
    for fix in fixes.for_incident(incident.id):
        if fix.kind == "suggestion" and fix.status == "published" and fix.github_url:
            return FiledSuggestion(fix.id, fix.github_number or 0, fix.github_url, True)
    fix_id = fixes.save_suggestion(incident.id, suggestion.model_dump(), at)
    cause = incident.root_cause or {}
    content = render_issue(
        incident_id=incident.id,
        summary=incident.summary,
        fault_class=incident.fault_class,
        confidence=incident.confidence,
        explanation=str(cause.get("explanation", "")),
        evidence=evidence,
        suggestion=suggestion,
        dashboard_url=dashboard_url,
    )
    issue = github.open_issue(content.title, content.body, LABELS)
    fixes.mark_filed(fix_id, issue.number, issue.url, at)
    log.info("suggestion_filed", incident_id=incident.id, issue=issue.number)
    return FiledSuggestion(fix_id, issue.number, issue.url, False)
