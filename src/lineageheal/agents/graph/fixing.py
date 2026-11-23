"""The graph's fix step: verify and publish a dbt patch, or file a suggestion (FR-FIX-02/04/05).

Each incident is handled alone, so one that crashes is recorded and the rest go on. Nothing
leaves the machine unless `publish` is on: without it the Fixer and the sandbox still run and
their results are stored for the dashboard, but no branch, pull request or issue is opened.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import structlog

from lineageheal.agents.fixer.filing import IssueOpener, file_suggestion
from lineageheal.agents.fixer.publish import PullRequestOpener, evidence_lines, publish_patch
from lineageheal.agents.fixer.suggestion import SuggestionResult, SuggestionStatus
from lineageheal.agents.fixer.verify import (
    PatchProposer,
    PatchVerifier,
    VerificationStatus,
    verify_incident,
)
from lineageheal.agents.graph.state import FixKind, FixOutcome
from lineageheal.lineage.service import LineageService, UnknownNodeError
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentRepository, StoredIncident
from lineageheal.store.investigations import InvestigationRepository

log = structlog.get_logger(__name__)

# A fix is attempted for an incident the Investigator concluded on. Ones with a human decision, or
# a filed issue, are left alone; a verified patch that was never published may be run again.
FIXABLE = ("investigating", "fix_proposed", "fix_unverified")
_PUBLISHED = ("published", "merged", "closed")


class SuggestionDrafter(Protocol):
    """What the fix step needs from the Suggester."""

    def draft(
        self, incident: StoredIncident, evidence: list[dict[str, Any]] | None = None
    ) -> SuggestionResult:
        """A drafted suggestion for an incident whose cause is outside dbt."""


class GitHubPublisher(PullRequestOpener, IssueOpener, Protocol):
    """The GitHub client as the fix step uses it."""


@dataclass(frozen=True)
class FixDependencies:
    """Everything the fix step calls, passed in so tests can replace any of it."""

    fixer: PatchProposer
    sandbox: PatchVerifier
    suggester: SuggestionDrafter
    fixes: FixRepository
    lineage: LineageService
    now: Callable[[], datetime]
    max_attempts: int = 2
    github: GitHubPublisher | None = None  # None means nothing is opened
    dashboard_url: str | None = None


class FixNodes:
    """Fixes one incident at a time."""

    def __init__(
        self,
        deps: FixDependencies,
        incidents: IncidentRepository,
        investigations: InvestigationRepository,
    ) -> None:
        """Bind the step to its dependencies."""
        self._deps = deps
        self._incidents = incidents
        self._investigations = investigations

    def fix_one(self, incident_id: int) -> FixOutcome:
        """Fix an incident, never raising: a crash becomes a `FAILED` outcome."""
        try:
            return self._fix(incident_id)
        except Exception as exc:
            log.warning("heal_fix_failed", incident_id=incident_id, error=str(exc))
            return FixOutcome(incident_id, FixKind.FAILED, f"{type(exc).__name__}: {exc}")

    def _fix(self, incident_id: int) -> FixOutcome:
        deps = self._deps
        incident = self._incidents.get(incident_id)
        if incident is None:
            return FixOutcome(incident_id, FixKind.SKIPPED, "the incident is not stored")
        if incident.status not in FIXABLE:
            return FixOutcome(
                incident_id, FixKind.SKIPPED, f"already {incident.status}; left alone"
            )
        if any(f.status in _PUBLISHED for f in deps.fixes.for_incident(incident_id)):
            return FixOutcome(incident_id, FixKind.SKIPPED, "a fix is already published")
        cause = incident.root_cause or {}
        if cause.get("partial", True) or not cause.get("node"):
            return FixOutcome(incident_id, FixKind.SKIPPED, "no concluded root cause")
        if cause.get("fix_location") == "external":
            return self._suggest(incident)
        return self._patch(incident, str(cause["node"]))

    # -- a root cause outside dbt ----------------------------------------------------------

    def _suggest(self, incident: StoredIncident) -> FixOutcome:
        deps = self._deps
        stored = self._investigations.latest_for(incident.id)
        evidence = stored.evidence if stored else []
        drafted = deps.suggester.draft(incident, evidence)
        if drafted.status is not SuggestionStatus.DRAFTED or drafted.suggestion is None:
            return FixOutcome(
                incident.id, FixKind.NO_SUGGESTION, drafted.reason, llm_calls=drafted.llm_calls
            )
        if deps.github is None:
            deps.fixes.save_suggestion(incident.id, drafted.suggestion.model_dump(), deps.now())
            return FixOutcome(
                incident.id,
                FixKind.SUGGESTION_DRAFTED,
                "stored; run with --publish to open the issue",
                llm_calls=drafted.llm_calls,
            )
        filed = file_suggestion(
            deps.fixes,
            deps.github,
            incident,
            drafted.suggestion,
            evidence_lines(evidence),
            at=deps.now(),
            dashboard_url=deps.dashboard_url,
        )
        return FixOutcome(
            incident.id,
            FixKind.SUGGESTION_FILED,
            issue_url=filed.issue_url,
            llm_calls=drafted.llm_calls,
        )

    # -- a root cause inside dbt -----------------------------------------------------------

    def _patch(self, incident: StoredIncident, root_node: str) -> FixOutcome:
        deps = self._deps
        outcome = verify_incident(
            deps.fixer, deps.sandbox, incident, max_attempts=deps.max_attempts
        )
        ids = (
            deps.fixes.save_attempts(
                incident.id,
                [
                    SandboxedPatch(a.patch, a.result.to_json(), a.result.passed)
                    for a in outcome.attempts
                ],
                deps.now(),
            )
            if outcome.attempts
            else []
        )
        count = len(outcome.attempts)
        if outcome.status is VerificationStatus.ERROR:
            return FixOutcome(
                incident.id, FixKind.FAILED, outcome.reason, count, llm_calls=outcome.llm_calls
            )
        if outcome.status is VerificationStatus.NO_PATCH:
            return FixOutcome(
                incident.id, FixKind.NO_PATCH, outcome.reason, count, llm_calls=outcome.llm_calls
            )
        passed = outcome.verified
        if outcome.status is VerificationStatus.UNVERIFIED or passed is None:
            return FixOutcome(
                incident.id, FixKind.UNVERIFIED, outcome.reason, count, llm_calls=outcome.llm_calls
            )
        if deps.github is None:
            return FixOutcome(
                incident.id,
                FixKind.PATCH_VERIFIED,
                "stored; run with --publish to open the pull request",
                count,
                llm_calls=outcome.llm_calls,
            )
        stored = self._investigations.latest_for(incident.id)
        published = publish_patch(
            deps.fixes,
            deps.github,
            incident,
            ids[outcome.attempts.index(passed)],
            passed.patch,
            passed.result.to_json(),
            lineage_path=self._path(incident, root_node),
            evidence=evidence_lines(stored.evidence if stored else []),
            change=f"Changes {', '.join(passed.patch.files)}.",
            at=deps.now(),
            dashboard_url=deps.dashboard_url,
        )
        return FixOutcome(
            incident.id,
            FixKind.PATCH_PUBLISHED,
            passed.result.reason,
            count,
            pull_request_url=published.url,
            llm_calls=outcome.llm_calls,
        )

    def _path(self, incident: StoredIncident, root_node: str) -> list[str]:
        """Nodes from the root cause to the first failing column, or just the root cause."""
        for column in incident.affected.get("columns", ()):
            try:
                path = self._deps.lineage.path(root_node, column)
            except UnknownNodeError:
                continue
            if path is not None:
                return list(path.nodes)
        return [root_node]
