"""Proposed fixes in the application database.

A drafted patch is stored as `unverified` (LH-052). The sandbox's verdicts are stored with
`save_attempts` (LH-053), which also moves the incident to `fix_proposed` or `fix_unverified`
in the same transaction. Publishing belongs to the GitHub client, and the database already
refuses a patch marked verified without a sandbox result on record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.agents.fixer.patch import Patch
from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import Fix, Incident

# A human has decided or the incident is closed, so a sandbox result must not reopen it.
_DECIDED = ("approved", "rejected", "resolved")


@dataclass(frozen=True)
class SandboxedPatch:
    """One patch the sandbox judged, with the record of how."""

    patch: Patch
    sandbox_result: dict[str, Any]
    passed: bool


@dataclass(frozen=True)
class StoredFix:
    """A fix as read back from the database."""

    id: int
    incident_id: int
    kind: str
    status: str
    patch: str | None
    files: list[str]
    sandbox_result: dict[str, Any] | None
    branch: str | None
    github_url: str | None
    created_at: datetime
    updated_at: datetime


def _to_stored(row: Fix) -> StoredFix:
    return StoredFix(
        id=row.id,
        incident_id=row.incident_id,
        kind=row.kind,
        status=row.status,
        patch=row.patch,
        files=list(row.files),
        sandbox_result=None if row.sandbox_result is None else dict(row.sandbox_result),
        branch=row.branch,
        github_url=row.github_url,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class FixRepository:
    """Stores drafted patches and reads an incident's fixes back."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def save_patch(self, incident_id: int, patch: Patch, at: datetime) -> int:
        """Store a checked patch as an `unverified` dbt patch and return its id.

        Raises:
            StorageError: The incident does not exist or the database could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                row = Fix(
                    incident_id=incident_id,
                    kind="dbt_patch",
                    status="unverified",
                    patch=patch.diff,
                    files=list(patch.files),
                    created_at=at,
                    updated_at=at,
                )
                session.add(row)
                session.flush()
                return row.id
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not save the patch: {explain_database_error(exc)}") from exc

    def save_attempts(
        self, incident_id: int, attempts: Sequence[SandboxedPatch], at: datetime
    ) -> list[int]:
        """Store every sandboxed attempt for an incident and set the incident's status.

        All attempts are kept, passing or not (FR-FIX-06). The incident becomes `fix_proposed`
        if any passed and `fix_unverified` if none did, unless a human has already decided it.

        Returns:
            The ids of the stored fixes, in attempt order.

        Raises:
            StorageError: The incident does not exist or the database could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                incident = session.get(Incident, incident_id)
                if incident is None:
                    raise StorageError(f"Incident {incident_id} does not exist")
                rows = [
                    Fix(
                        incident_id=incident_id,
                        kind="dbt_patch",
                        status="verified" if attempt.passed else "unverified",
                        patch=attempt.patch.diff,
                        files=list(attempt.patch.files),
                        sandbox_result=attempt.sandbox_result,
                        created_at=at,
                        updated_at=at,
                    )
                    for attempt in attempts
                ]
                session.add_all(rows)
                if attempts and incident.status not in _DECIDED:
                    incident.status = (
                        "fix_proposed" if any(a.passed for a in attempts) else "fix_unverified"
                    )
                    incident.updated_at = at
                session.flush()
                return [row.id for row in rows]
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not save the attempts: {explain_database_error(exc)}"
            ) from exc

    def for_incident(self, incident_id: int) -> list[StoredFix]:
        """An incident's fixes, oldest first.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                rows = session.scalars(
                    select(Fix).where(Fix.incident_id == incident_id).order_by(Fix.id)
                )
                return [_to_stored(row) for row in rows]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read fixes: {explain_database_error(exc)}") from exc
