"""Incidents in the application database: opened by the Detector, read by everything after it.

An incident groups the signals that share one cause (FR-DET-02). This module only stores and
finds them; deciding which signals belong together is `agents.detector`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.core.signals import Signal
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import Incident
from lineageheal.store.models import Signal as SignalRow
from lineageheal.store.runs import to_signal


@dataclass(frozen=True)
class IncidentDraft:
    """What the Detector knows when it opens an incident."""

    summary: str
    severity: str  # error or warn
    fingerprint: str
    root_node: str | None
    affected: dict[str, Any]
    lineage_version: str | None
    fault_class: str
    confidence: float
    classified_by: str
    opened_at: datetime


@dataclass(frozen=True)
class StoredIncident:
    """An incident as read back from the database."""

    id: int
    status: str
    summary: str
    severity: str
    fingerprint: str
    root_node: str | None
    affected: dict[str, Any]
    lineage_version: str | None
    fault_class: str | None
    confidence: float | None
    classified_by: str | None
    opened_at: datetime
    updated_at: datetime


def _to_stored(row: Incident) -> StoredIncident:
    return StoredIncident(
        id=row.id,
        status=row.status,
        summary=row.summary,
        severity=row.severity,
        fingerprint=row.fingerprint,
        root_node=row.root_node,
        affected=dict(row.affected),
        lineage_version=row.lineage_version,
        fault_class=row.fault_class,
        confidence=row.confidence,
        classified_by=row.classified_by,
        opened_at=row.opened_at,
        updated_at=row.updated_at,
    )


class IncidentRepository:
    """Opens incidents, attaches signals to them and reads them back."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def create(self, draft: IncidentDraft) -> StoredIncident:
        """Open an incident in the `detected` state.

        Raises:
            StorageError: The database rejected it, for example because the lineage version it
                names is not stored.
        """
        try:
            with Session(self._engine) as session, session.begin():
                row = Incident(
                    status="detected",
                    fault_class=draft.fault_class,
                    summary=draft.summary,
                    confidence=draft.confidence,
                    severity=draft.severity,
                    root_node=draft.root_node,
                    fingerprint=draft.fingerprint,
                    affected=draft.affected,
                    lineage_version=draft.lineage_version,
                    classified_by=draft.classified_by,
                    opened_at=draft.opened_at,
                    updated_at=draft.opened_at,
                )
                session.add(row)
                session.flush()
                return _to_stored(row)
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not open the incident: {explain_database_error(exc)}"
            ) from exc

    def find_open(self, fingerprint: str) -> StoredIncident | None:
        """Return the newest incident with this fingerprint that is not resolved yet.

        Raises:
            StorageError: The database could not be read.
        """
        statement = (
            select(Incident)
            .where(Incident.fingerprint == fingerprint, Incident.status != "resolved")
            .order_by(Incident.id.desc())
            .limit(1)
        )
        try:
            with Session(self._engine) as session:
                row = session.scalars(statement).first()
                return None if row is None else _to_stored(row)
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read incidents: {explain_database_error(exc)}") from exc

    def attach(
        self, incident_id: int, run_id: str, signals: Sequence[Signal], *, at: datetime
    ) -> int:
        """Put a run's signals under an incident, leaving ones it already holds alone.

        Args:
            incident_id: An existing incident.
            run_id: The run the signals belong to, which must already be recorded.
            signals: Signals of that run.
            at: When the incident is touched, if anything was attached.

        Returns:
            How many signals were newly attached.

        Raises:
            StorageError: The incident does not exist or the database could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                incident = session.get(Incident, incident_id)
                if incident is None:
                    raise StorageError(f"Incident {incident_id} does not exist")
                attached = 0
                for signal in signals:
                    result = cast(
                        CursorResult[Any],
                        session.execute(
                            update(SignalRow)
                            .where(
                                SignalRow.run_id == run_id,
                                SignalRow.kind == signal.kind.value,
                                SignalRow.name == signal.name,
                                SignalRow.incident_id.is_distinct_from(incident_id),
                            )
                            .values(incident_id=incident_id)
                        ),
                    )
                    attached += result.rowcount
                if attached:
                    incident.updated_at = at
                return attached
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not attach signals: {explain_database_error(exc)}") from exc

    def get(self, incident_id: int) -> StoredIncident | None:
        """Return one incident, or None.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                row = session.get(Incident, incident_id)
                return None if row is None else _to_stored(row)
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read incidents: {explain_database_error(exc)}") from exc

    def signals_of(self, incident_id: int) -> list[Signal]:
        """Return an incident's signals in the order they were recorded.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                rows = session.scalars(
                    select(SignalRow)
                    .where(SignalRow.incident_id == incident_id)
                    .order_by(SignalRow.id)
                )
                return [to_signal(row) for row in rows]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read signals: {explain_database_error(exc)}") from exc
