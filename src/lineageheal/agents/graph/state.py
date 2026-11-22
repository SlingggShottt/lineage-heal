"""What flows through the healing graph (design section 8.2).

The state is deliberately small: the run being healed, the incidents still waiting to be
investigated and what happened to those already done. Evidence and conclusions live in the
database, not in the state, so the state stays cheap to checkpoint once review pauses a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict


class OutcomeKind(StrEnum):
    """How one incident's investigation ended."""

    CONCLUDED = "concluded"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"  # the investigation crashed; the incident is back to `detected`
    SKIPPED = "skipped"  # already past investigation, so left alone


@dataclass(frozen=True, slots=True)
class IncidentOutcome:
    """What the graph did for one incident."""

    incident_id: int
    kind: OutcomeKind
    detail: str = ""
    root_cause_node: str | None = None
    fault_class: str | None = None
    confidence: float | None = None
    fix_location: str | None = None
    evidence_count: int = 0
    llm_calls: int = 0


class HealState(TypedDict):
    """The graph state."""

    run_id: str
    only_incident: int | None  # investigate just this incident, if the run has it
    pending: list[int]
    outcomes: list[IncidentOutcome]
