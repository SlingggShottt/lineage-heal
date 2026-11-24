"""Bringing a fix's pull request or issue up to date with GitHub when it is read (FR-API-04).

A person can merge or close a `heal/<id>` pull request on GitHub itself, and a decision made
through the dashboard can be recorded after GitHub acted but before the write succeeded. Either
way the stored state would stay "waiting for review". Reading an incident asks GitHub once in a
while and records what a person already did.

Reading must never fail because GitHub did: any problem is logged and the stored state is served.
Nothing is written unless GitHub gave a clear answer about the fix's own pull request or issue.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog

from lineageheal.store.fixes import FixRepository, StoredFix
from lineageheal.store.reviews import NotDecidableError, ReviewRepository
from lineageheal.vcs.client import GitHubClient

log = structlog.get_logger()

# One lookup per fix per minute: a page that is reloaded or read by three endpoints at once
# should cost GitHub one request, while a merge still shows up within a minute.
CACHE_SECONDS = 60
# GitHub's pull request payload names who merged but not who closed, and a close made outside the
# dashboard has no reason, so the record says plainly what is not known.
UNKNOWN_REVIEWER = "github"
CLOSED_ON_GITHUB = "Closed on GitHub; no reason was recorded."


class Reconciler:
    """Reconciles one incident's published fix with GitHub, at most once per cache period."""

    def __init__(
        self,
        fixes: FixRepository,
        reviews: ReviewRepository,
        github: Callable[[], GitHubClient | None],
        now: Callable[[], datetime],
        checked: dict[int, datetime],
    ) -> None:
        """Create the reconciler.

        Args:
            fixes: Reads the incident's fixes and records a closed issue.
            reviews: Finds the pull request awaiting a decision and records one.
            github: Returns the client, or None when GitHub is not configured here.
            now: The current time.
            checked: When each fix was last looked up, shared between requests so the cache
                outlives one reconciler.
        """
        self._fixes = fixes
        self._reviews = reviews
        self._github = github
        self._now = now
        self._checked = checked

    def reconcile(self, incident_id: int) -> None:
        """Update the incident's fix from GitHub if it is due. Never raises."""
        try:
            self._reconcile(incident_id)
        except Exception as exc:  # a read must not fail because GitHub did
            log.warning("reconcile_failed", incident=incident_id, error_type=type(exc).__name__)

    def _reconcile(self, incident_id: int) -> None:
        published = [f for f in self._fixes.for_incident(incident_id) if f.status == "published"]
        if not published:
            return
        fix = published[-1]
        number = fix.github_number
        if number is None:
            return
        now = self._now()
        last = self._checked.get(fix.id)
        if last is not None and now - last < timedelta(seconds=CACHE_SECONDS):
            return
        # Recorded before asking, so a GitHub outage is retried once a minute, not on every read.
        self._checked[fix.id] = now
        github = self._github()
        if github is None:
            return
        if fix.kind == "dbt_patch":
            self._pull_request(github, fix, number, incident_id, now)
        else:
            self._issue(github, fix, number, now)

    def _pull_request(
        self, github: GitHubClient, fix: StoredFix, number: int, incident_id: int, now: datetime
    ) -> None:
        pull_request = github.pull_request(number)
        if pull_request.head != fix.branch:
            # The number now belongs to another pull request; trust nothing it says.
            log.warning("reconcile_head_mismatch", incident=incident_id, number=number)
            return
        if pull_request.state == "open":
            return
        try:
            pending = self._reviews.pending_patch(incident_id)
        except NotDecidableError:
            return  # decided meanwhile, or not waiting for a decision
        merged = pull_request.merged
        try:
            self._reviews.record_decision(
                pending,
                decision="approved" if merged else "rejected",
                reviewer_login=(pull_request.merged_by or UNKNOWN_REVIEWER)
                if merged
                else UNKNOWN_REVIEWER,
                reason=None if merged else CLOSED_ON_GITHUB,
                at=now,
            )
        except NotDecidableError:
            return  # lost a race with a decision made through the dashboard
        log.info(
            "reconciled_from_github",
            incident=incident_id,
            pull_request=number,
            merged=merged,
        )

    def _issue(self, github: GitHubClient, fix: StoredFix, number: int, now: datetime) -> None:
        if not github.issue_is_open(number):
            self._fixes.mark_issue_closed(fix.id, now)
            log.info("reconciled_issue_closed", fix=fix.id, issue=number)
