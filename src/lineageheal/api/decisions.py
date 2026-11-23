"""Approving and rejecting a fix (FR-REV-01, FR-REV-02, IF-SW-06).

GitHub is asked first and the decision is recorded only once GitHub has done it, so what is
stored always matches what happened. Approving uses the approver's own GitHub token for both the
approving review and the merge: the App never merges, so a merge is always attributable to a
person. Rejecting is closing a pull request, which the App may do, with the reason as a comment.

If GitHub merged but recording failed, the pull request stays merged and the error says so; it
is the one gap between the two systems, and reconciling on read (LH-103) closes it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog
from pydantic import SecretStr

from lineageheal.api.problems import ApiError
from lineageheal.api.security import TokenCipher, TokenError
from lineageheal.core.errors import StorageError, VCSError
from lineageheal.store.reviews import NotDecidableError, PendingPatch, ReviewRepository
from lineageheal.store.sessions import StoredSession
from lineageheal.vcs.client import GitHubClient
from lineageheal.vcs.text import plain

log = structlog.get_logger()

REASON_COMMENT_LIMIT = 600


@dataclass(frozen=True)
class DecisionResult:
    """What a decision did."""

    incident_id: int
    fix_id: int
    decision: str
    reviewer: str
    decided_at: datetime
    pull_request_number: int
    pull_request_url: str
    merge_commit: str | None


class DecisionService:
    """Carries out an approver's decision on GitHub and records it."""

    def __init__(
        self,
        reviews: ReviewRepository,
        github: GitHubClient,
        cipher: TokenCipher,
        now: Callable[[], datetime],
    ) -> None:
        """Create the service.

        Args:
            reviews: Finds what can be decided and records decisions.
            github: The GitHub client; it acts as the App unless given a user's token.
            cipher: Decrypts the approver's GitHub token from their session.
            now: The current time.
        """
        self._reviews = reviews
        self._github = github
        self._cipher = cipher
        self._now = now

    def approve(self, incident_id: int, session: StoredSession) -> DecisionResult:
        """Approve and squash-merge the incident's pull request as the signed-in approver.

        Raises:
            ApiError: The incident cannot be decided (404 or 409), GitHub refused (401, 403 or
                409) or could not be reached (503), or the decision could not be recorded.
        """
        pending = self._pending(incident_id)
        token = self._user_token(session)
        self._require_open_pull_request(pending)
        try:
            self._github.approve(
                pending.number,
                token,
                f"Approved from the LineageHeal dashboard by {session.login}.",
            )
            merge_commit = self._github.squash_merge(pending.number, token)
        except VCSError as exc:
            raise _github_problem(exc, "merge this pull request") from exc
        return self._record(pending, "approved", session.login, None, merge_commit=merge_commit)

    def reject(self, incident_id: int, session: StoredSession, reason: str) -> DecisionResult:
        """Close the incident's pull request unmerged, with the approver's reason.

        Raises:
            ApiError: As for `approve`; the App closes the pull request, so a refusal is a 409.
        """
        pending = self._pending(incident_id)
        self._require_open_pull_request(pending)
        comment = (
            f"Rejected from the LineageHeal dashboard by {session.login}.\n\n"
            f"> {plain(reason, REASON_COMMENT_LIMIT)}"
        )
        try:
            self._github.close_pull_request(pending.number, comment)
        except VCSError as exc:
            raise _github_problem(exc, "close this pull request") from exc
        return self._record(pending, "rejected", session.login, reason, merge_commit=None)

    # -- steps -----------------------------------------------------------------------------

    def _pending(self, incident_id: int) -> PendingPatch:
        try:
            return self._reviews.pending_patch(incident_id)
        except NotDecidableError as exc:
            raise _conflict(exc) from exc

    def _user_token(self, session: StoredSession) -> SecretStr:
        try:
            return SecretStr(self._cipher.decrypt(session.token_encrypted))
        except TokenError as exc:
            raise ApiError(
                401,
                "github-token-unusable",
                "Sign in again",
                "Your GitHub sign-in can no longer be used; sign in again to decide.",
            ) from exc

    def _require_open_pull_request(self, pending: PendingPatch) -> None:
        """Act only on the pull request this fix was opened as, and only while it is open."""
        try:
            pull_request = self._github.pull_request(pending.number)
        except VCSError as exc:
            raise _github_problem(exc, "read this pull request") from exc
        if pull_request.head != pending.branch:
            raise ApiError(
                409,
                "not-decidable",
                "Pull request does not match",
                f"Pull request #{pending.number} is not the one opened for this fix.",
            )
        if pull_request.state != "open":
            raise ApiError(
                409,
                "not-decidable",
                "Pull request already closed",
                f"Pull request #{pending.number} was already "
                f"{'merged' if pull_request.merged else 'closed'} on GitHub.",
            )

    def _record(
        self,
        pending: PendingPatch,
        decision: str,
        login: str,
        reason: str | None,
        *,
        merge_commit: str | None,
    ) -> DecisionResult:
        at = self._now()
        try:
            self._reviews.record_decision(
                pending, decision=decision, reviewer_login=login, reason=reason, at=at
            )
        except (NotDecidableError, StorageError) as exc:
            # GitHub has already done it; say so, because retrying cannot help.
            log.error(
                "decision_not_recorded",
                incident=pending.incident_id,
                decision=decision,
                pull_request=pending.number,
            )
            raise ApiError(
                503,
                "decision-not-recorded",
                "Decision not recorded",
                f"GitHub carried out the decision on pull request #{pending.number}, but "
                "recording it here failed. Reload the incident before trying again.",
            ) from exc
        log.info(
            "decision_recorded",
            incident=pending.incident_id,
            decision=decision,
            reviewer=login,
            pull_request=pending.number,
        )
        return DecisionResult(
            incident_id=pending.incident_id,
            fix_id=pending.fix_id,
            decision=decision,
            reviewer=login,
            decided_at=at,
            pull_request_number=pending.number,
            pull_request_url=pending.url,
            merge_commit=merge_commit,
        )


def _conflict(exc: NotDecidableError) -> ApiError:
    if exc.code == "not_found":
        return ApiError(404, "not-found", "Incident not found", str(exc))
    return ApiError(409, "not-decidable", "Nothing to decide", str(exc))


def _github_problem(exc: VCSError, action: str) -> ApiError:
    """Turn a GitHub failure into the problem an approver can act on, without leaking detail."""
    status = exc.status
    if status == 401:
        return ApiError(
            401,
            "github-token-unusable",
            "Sign in again",
            "GitHub no longer accepts your sign-in; sign in again to decide.",
        )
    if status == 403 and (exc.reason or "").lower().startswith("api rate limit"):
        status = 429
    if status is None or status == 429 or status >= 500:
        return ApiError(
            503,
            "github-unavailable",
            "GitHub unavailable",
            "GitHub could not be reached or is limiting requests; try again shortly.",
        )
    reason = exc.reason or "GitHub gave no reason."
    if status == 403:
        return ApiError(
            403,
            "github-forbidden",
            "GitHub does not allow this",
            f"GitHub would not let you {action}: {reason}",
        )
    return ApiError(
        409,
        "github-refused",
        "GitHub refused",
        f"GitHub would not {action}: {reason}",
    )
