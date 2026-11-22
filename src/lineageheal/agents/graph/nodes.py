"""The graph's nodes, as plain methods over the project's own classes.

LangGraph only decides the order. Everything a node does is a call to the Detector, the
Investigator or a repository, so each can be tested without the graph and the graph can gain
nodes (the Fixer, in later tasks) without touching these.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from lineageheal.agents.detector.classify import Verdict
from lineageheal.agents.detector.grouping import SignalGroup
from lineageheal.agents.graph.fixing import FixDependencies, FixNodes
from lineageheal.agents.graph.state import HealState, IncidentOutcome, OutcomeKind
from lineageheal.agents.investigator import Investigation, InvestigationStatus, Investigator
from lineageheal.core.signals import Signal
from lineageheal.store.incidents import IncidentRepository, StoredIncident
from lineageheal.store.investigations import INVESTIGABLE, InvestigationRepository

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HealDependencies:
    """Everything the nodes call, passed in so tests can replace any of it."""

    detect: Callable[[], list[int]]  # opens or updates the run's incidents, returns their ids
    incidents: IncidentRepository
    investigations: InvestigationRepository
    make_investigator: Callable[[], Investigator]  # called after detection, which it may need
    now: Callable[[], datetime]
    # Called after detection, like `make_investigator`; None stops after investigating.
    make_fixing: Callable[[], FixDependencies] | None = None


def group_of(incident: StoredIncident, signals: list[Signal]) -> SignalGroup:
    """Rebuild what the Detector knew about an incident from what it stored."""
    affected = incident.affected
    return SignalGroup(
        signals=tuple(signals),
        root_node=incident.root_node,
        models=tuple(affected.get("models", ())),
        columns=tuple(affected.get("columns", ())),
        gold_tables=tuple(affected.get("gold_tables", ())),
    )


class HealNodes:
    """The graph's steps."""

    def __init__(self, deps: HealDependencies) -> None:
        """Bind the nodes to their dependencies."""
        self._deps = deps
        self._investigator: Investigator | None = None
        self._fix: FixNodes | None = None

    def detect(self, state: HealState) -> dict[str, Any]:
        """Open or update the run's incidents and queue them, or just the one asked for."""
        ids = self._deps.detect()
        only = state["only_incident"]
        pending = ids if only is None else [i for i in ids if i == only]
        log.info("heal_detected", run_id=state["run_id"], incidents=len(ids), queued=len(pending))
        return {"pending": pending}

    def investigate(self, state: HealState) -> dict[str, Any]:
        """Investigate the next queued incident and save the result."""
        incident_id, *rest = state["pending"]
        outcome = self._investigate_one(incident_id)
        concluded = outcome.kind is OutcomeKind.CONCLUDED
        return {
            "pending": rest,
            "outcomes": [*state["outcomes"], outcome],
            "to_fix": [*state["to_fix"], incident_id] if concluded else state["to_fix"],
        }

    def fix(self, state: HealState) -> dict[str, Any]:
        """Fix the next concluded incident: verify and publish a patch, or file a suggestion."""
        incident_id, *rest = state["to_fix"]
        make = self._deps.make_fixing
        assert make is not None  # noqa: S101  # the graph only routes here when fixing is on
        if self._fix is None:
            self._fix = FixNodes(make(), self._deps.incidents, self._deps.investigations)
        outcome = self._fix.fix_one(incident_id)
        return {"to_fix": rest, "fixes": [*state["fixes"], outcome]}

    def more_to_do(self, state: HealState) -> str:
        """Route to the next incident to investigate, then to the fixes, then end."""
        if state["pending"]:
            return "investigate"
        return "fix" if state["to_fix"] and self._deps.make_fixing is not None else "end"

    def more_to_fix(self, state: HealState) -> str:
        """Route to the next incident to fix, or end."""
        return "fix" if state["to_fix"] else "end"

    def _investigate_one(self, incident_id: int) -> IncidentOutcome:
        deps = self._deps
        incident = deps.incidents.get(incident_id)
        if incident is None:
            return IncidentOutcome(incident_id, OutcomeKind.SKIPPED, "the incident is not stored")
        if incident.status not in INVESTIGABLE:
            return IncidentOutcome(
                incident_id, OutcomeKind.SKIPPED, f"already {incident.status}; left alone"
            )
        group = group_of(incident, deps.incidents.signals_of(incident_id))
        verdict = Verdict(
            incident.fault_class or "unknown",  # type: ignore[arg-type]
            incident.confidence or 0.0,
            incident.summary,
            incident.classified_by or "rules",
        )
        agent_run_id = deps.investigations.start(incident_id, deps.now())
        try:
            if self._investigator is None:
                self._investigator = deps.make_investigator()
            investigation = self._investigator.investigate(group, verdict)
            deps.investigations.finish(agent_run_id, investigation, deps.now())
        except Exception as exc:
            # Whatever went wrong, the run must not stay `running` for ever.
            log.warning("heal_investigation_failed", incident_id=incident_id, error=str(exc))
            deps.investigations.fail(agent_run_id, f"{type(exc).__name__}: {exc}", deps.now())
            return IncidentOutcome(incident_id, OutcomeKind.FAILED, f"{type(exc).__name__}: {exc}")
        return _outcome(incident_id, investigation)


def _outcome(incident_id: int, investigation: Investigation) -> IncidentOutcome:
    conclusion = investigation.conclusion
    if investigation.status is InvestigationStatus.CONCLUDED and conclusion is not None:
        return IncidentOutcome(
            incident_id,
            OutcomeKind.CONCLUDED,
            conclusion.explanation,
            root_cause_node=conclusion.root_cause_node,
            fault_class=conclusion.fault_class,
            confidence=conclusion.confidence,
            fix_location=conclusion.fix_location,
            evidence_count=len(investigation.evidence),
            llm_calls=investigation.llm_calls,
        )
    guess = investigation.partial
    return IncidentOutcome(
        incident_id,
        OutcomeKind.BUDGET_EXHAUSTED,
        "; ".join(investigation.notes),
        root_cause_node=guess.node if guess else None,
        confidence=guess.confidence if guess else None,
        evidence_count=len(investigation.evidence),
        llm_calls=investigation.llm_calls,
    )
