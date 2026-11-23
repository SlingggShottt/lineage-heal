"""Approver decisions in the application database (FR-REV-01, FR-REV-02).

The API talks to GitHub first and records the decision here only once GitHub has done what was
asked, so a recorded decision always matches what happened. Recording is one transaction that
checks the states again, writes the review and moves the fix and the incident together; the
database allows one review per fix, so two approvers racing cannot both record one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import LineageHealError, StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import Fix, Incident, Review


class NotDecidableError(LineageHealError):
    """The incident cannot take a decision now. `code` says why, for the API to answer with.

    Codes: `not_found`, `wrong_state`, `no_pull_request`, `already_decided`.
    """

    def __init__(self, code: str, message: str) -> None:
        """Create the conflict."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PendingPatch:
    """The pull request an approver is being asked to decide on."""

    incident_id: int
    fix_id: int
    number: int
    branch: str
    url: str


@dataclass(frozen=True)
class StoredReview:
    """A decision as stored."""

    id: int
    fix_id: int
    reviewer_login: str
    decision: str
    reason: str | None
    decided_at: datetime


class ReviewRepository:
    """Finds what can be decided and records decisions."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def pending_patch(self, incident_id: int) -> PendingPatch:
        """The published pull request of an incident that is waiting for a decision.

        Raises:
            NotDecidableError: There is no such incident, it is not `fix_proposed`, or it has no
                published pull request.
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                incident = session.get(Incident, incident_id)
                if incident is None:
                    raise NotDecidableError("not_found", f"There is no incident {incident_id}.")
                if incident.status != "fix_proposed":
                    raise NotDecidableError(
                        "wrong_state",
                        f"Incident {incident_id} is {incident.status.replace('_', ' ')}, "
                        "so there is no fix waiting for a decision.",
                    )
                fix = session.scalars(
                    select(Fix)
                    .where(
                        Fix.incident_id == incident_id,
                        Fix.kind == "dbt_patch",
                        Fix.status == "published",
                    )
                    .order_by(Fix.id.desc())
                    .limit(1)
                ).first()
                if fix is None or fix.github_number is None or fix.branch is None:
                    raise NotDecidableError(
                        "no_pull_request",
                        f"Incident {incident_id} has no pull request open for a decision.",
                    )
                return PendingPatch(
                    incident_id=incident_id,
                    fix_id=fix.id,
                    number=fix.github_number,
                    branch=fix.branch,
                    url=fix.github_url or "",
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the fix: {explain_database_error(exc)}") from exc

    def record_decision(
        self,
        pending: PendingPatch,
        *,
        decision: str,
        reviewer_login: str,
        reason: str | None,
        at: datetime,
    ) -> StoredReview:
        """Record a decision GitHub has already carried out, in one transaction.

        An approval marks the fix `merged` and the incident `approved`; a rejection marks the fix
        `closed` and the incident `rejected`.

        Raises:
            NotDecidableError: The states changed since `pending_patch`, or the fix already has a
                decision.
            StorageError: The database could not be written.
        """
        merged = decision == "approved"
        try:
            with Session(self._engine) as session, session.begin():
                incident = session.get(Incident, pending.incident_id)
                fix = session.get(Fix, pending.fix_id)
                if (
                    incident is None
                    or fix is None
                    or incident.status != "fix_proposed"
                    or fix.status != "published"
                ):
                    raise NotDecidableError(
                        "already_decided", "This fix was decided while the request was running."
                    )
                review = Review(
                    fix_id=fix.id,
                    reviewer_login=reviewer_login,
                    decision=decision,
                    reason=reason,
                    decided_at=at,
                )
                session.add(review)
                session.flush()
                fix.status = "merged" if merged else "closed"
                fix.updated_at = at
                incident.status = "approved" if merged else "rejected"
                incident.updated_at = at
                return StoredReview(review.id, fix.id, reviewer_login, decision, reason, at)
        except IntegrityError as exc:
            # The unique constraint on the fix: another decision got there first.
            raise NotDecidableError("already_decided", "This fix already has a decision.") from exc
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not record the decision: {explain_database_error(exc)}"
            ) from exc

    def latest_for_incident(self, incident_id: int) -> StoredReview | None:
        """The newest decision on any of an incident's fixes, or None.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                row = session.scalars(
                    select(Review)
                    .join(Fix, Fix.id == Review.fix_id)
                    .where(Fix.incident_id == incident_id)
                    .order_by(Review.id.desc())
                    .limit(1)
                ).first()
                if row is None:
                    return None
                return StoredReview(
                    row.id, row.fix_id, row.reviewer_login, row.decision, row.reason, row.decided_at
                )
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not read the decision: {explain_database_error(exc)}"
            ) from exc
