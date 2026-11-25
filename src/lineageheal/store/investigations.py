"""Investigations in the application database: the agent run, its evidence and the incident update.

The Investigator returns a plain `Investigation`; this module keeps it. A run is opened before
the search starts, so a crash leaves a visible `running` row rather than nothing, and it is
closed in one transaction that also writes the evidence and what the incident now says about
its root cause, so a reader never sees a finished run without its findings (FR-INV-04, FR-DASH-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.agents.investigator.models import (
    Evidence as FoundEvidence,
)
from lineageheal.agents.investigator.models import (
    Investigation,
    InvestigationStatus,
)
from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import AgentRun, Evidence, Incident
from lineageheal.store.types import json_safe

# Incidents an investigation may start on. Anything later (a fix proposed, a human decision)
# must not be pulled back by a new search.
INVESTIGABLE = ("detected", "investigating", "unresolved")


@dataclass(frozen=True)
class StoredInvestigation:
    """An agent run and its evidence, as read back."""

    agent_run_id: int
    incident_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    llm_calls: int
    input_tokens: int
    output_tokens: int
    model_ids: dict[str, str]
    prompt_versions: dict[str, str]
    error: str | None
    evidence: list[dict[str, Any]]


class InvestigationRepository:
    """Opens, finishes and reads an incident's investigations."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def start(self, incident_id: int, at: datetime) -> int:
        """Open a `running` agent run and mark the incident as being investigated.

        Returns:
            The agent run's id.

        Raises:
            StorageError: The incident does not exist, is past investigation, or the database
                could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                incident = _investigable(session, incident_id)
                run = AgentRun(
                    incident_id=incident_id,
                    status="running",
                    started_at=at,
                    model_ids={},
                    prompt_versions={},
                )
                session.add(run)
                incident.status = "investigating"
                incident.updated_at = at
                session.flush()
                return run.id
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not start the investigation: {explain_database_error(exc)}"
            ) from exc

    def finish(self, agent_run_id: int, investigation: Investigation, at: datetime) -> None:
        """Close a run with its findings, in one transaction.

        A concluded search records the Investigator's fault class, confidence and root cause on
        the incident. A search that ran out of budget marks the incident `unresolved` and keeps
        its best guess flagged `partial`, leaving the Detector's fault class alone.

        Raises:
            StorageError: The run does not exist or is already finished, or the database could
                not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                run = _running(session, agent_run_id)
                incident = session.get(Incident, run.incident_id)
                if incident is None:  # pragma: no cover - the foreign key forbids it
                    raise StorageError(f"Incident {run.incident_id} does not exist")
                conclusion = investigation.conclusion
                if investigation.status is InvestigationStatus.CONCLUDED and conclusion:
                    run.status = "succeeded"
                    incident.fault_class = conclusion.fault_class
                    incident.confidence = conclusion.confidence
                    incident.classified_by = "investigator"
                    incident.root_node = conclusion.root_cause_node
                    incident.root_cause = {
                        "node": conclusion.root_cause_node,
                        "explanation": conclusion.explanation,
                        "evidence_ids": conclusion.evidence_ids,
                        "alternatives": [a.model_dump() for a in conclusion.alternatives],
                        "fix_location": conclusion.fix_location,
                        "fix_reason": conclusion.fix_reason,
                        "partial": False,
                    }
                else:
                    run.status = "budget_exhausted"
                    incident.status = "unresolved"
                    guess = investigation.partial
                    incident.root_cause = {
                        "node": guess.node if guess else None,
                        "explanation": guess.explanation if guess else None,
                        "confidence": guess.confidence if guess else None,
                        "partial": True,
                        "notes": list(investigation.notes),
                    }
                run.finished_at = at
                run.llm_calls = investigation.llm_calls
                run.input_tokens = investigation.input_tokens
                run.output_tokens = investigation.output_tokens
                run.model_ids = dict(investigation.model_ids)
                run.prompt_versions = dict(investigation.prompt_hashes)
                incident.updated_at = at
                for found in investigation.evidence:
                    session.add(_evidence_row(agent_run_id, found, at))
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not save the investigation: {explain_database_error(exc)}"
            ) from exc

    def fail(self, agent_run_id: int, error: str, at: datetime) -> None:
        """Close a run that crashed and put the incident back to `detected` to be retried.

        Raises:
            StorageError: The run does not exist or is already finished, or the database could
                not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                run = _running(session, agent_run_id)
                run.status = "failed"
                run.error = error
                run.finished_at = at
                incident = session.get(Incident, run.incident_id)
                if incident is not None:
                    incident.status = "detected"
                    incident.updated_at = at
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not record the failure: {explain_database_error(exc)}"
            ) from exc

    def latest_for(self, incident_id: int) -> StoredInvestigation | None:
        """The incident's newest agent run with its evidence, or None if it has none.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                run = session.scalars(
                    select(AgentRun)
                    .where(AgentRun.incident_id == incident_id)
                    .order_by(AgentRun.id.desc())
                    .limit(1)
                ).first()
                if run is None:
                    return None
                rows = session.scalars(
                    select(Evidence).where(Evidence.agent_run_id == run.id).order_by(Evidence.id)
                )
                return StoredInvestigation(
                    agent_run_id=run.id,
                    incident_id=run.incident_id,
                    status=run.status,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    llm_calls=run.llm_calls,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    model_ids=dict(run.model_ids),
                    prompt_versions=dict(run.prompt_versions),
                    error=run.error,
                    evidence=[
                        {
                            "tool": row.tool,
                            "arguments": row.arguments,
                            "output": row.output,
                            "hypothesis": row.hypothesis,
                            "collected_at": row.collected_at,
                        }
                        for row in rows
                    ],
                )
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not read the investigation: {explain_database_error(exc)}"
            ) from exc


def _investigable(session: Session, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise StorageError(f"Incident {incident_id} does not exist")
    if incident.status not in INVESTIGABLE:
        raise StorageError(
            f"Incident {incident_id} is {incident.status} and cannot be investigated again"
        )
    return incident


def _running(session: Session, agent_run_id: int) -> AgentRun:
    run = session.get(AgentRun, agent_run_id)
    if run is None:
        raise StorageError(f"Agent run {agent_run_id} does not exist")
    if run.status != "running":
        raise StorageError(f"Agent run {agent_run_id} is already {run.status}")
    return run


def _evidence_row(agent_run_id: int, found: FoundEvidence, at: datetime) -> Evidence:
    hypothesis = f"{found.verdict} {found.node}" if found.node and found.verdict else None
    return Evidence(
        agent_run_id=agent_run_id,
        tool=found.tool,
        # A tool returns warehouse rows as they are, with `date` and `Decimal` values in them.
        arguments=json_safe(found.arguments),
        output=json_safe(found.payload),
        hypothesis=hypothesis,
        collected_at=found.collected_at or at,
    )
