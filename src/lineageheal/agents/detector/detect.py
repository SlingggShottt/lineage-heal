"""The Detector: turns a run's signals into incidents, without duplicating one (FR-DET-02 to 05).

Signals are grouped by shared upstream cause, each group gets a fingerprint from its root node and
its rule-based fault class, and an open incident with that fingerprint is reused instead of a
second one being opened. Only a group with no open incident is classified, so a re-run costs no
model call.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

from lineageheal.agents.detector.classify import Classifier, rule_class
from lineageheal.agents.detector.grouping import DEFAULT_DEPTH, SignalGroup, group_signals
from lineageheal.core.signals import Signal
from lineageheal.lineage.graph import LineageGraph
from lineageheal.store.incidents import IncidentDraft, IncidentRepository, StoredIncident

log = structlog.get_logger(__name__)


class Outcome(StrEnum):
    """What detection did for one group of signals."""

    OPENED = "opened"  # a new incident
    UPDATED = "updated"  # an open incident for the same cause took more signals
    UNCHANGED = "unchanged"  # an open incident already held every signal


@dataclass(frozen=True, slots=True)
class DetectedIncident:
    """An incident and what detection did for it."""

    incident: StoredIncident
    outcome: Outcome
    signals: tuple[Signal, ...]


def fingerprint(group: SignalGroup, fault_class: str) -> str:
    """Identify a cause across runs: its root node (or its first signal) and the rule class."""
    first = group.signals[0]
    anchor = group.root_node or f"{first.check_type}:{first.name}"
    return hashlib.sha256(f"{anchor}|{fault_class}".encode()).hexdigest()[:32]


class Detector:
    """Groups a run's signals and opens or updates one incident for each group."""

    def __init__(
        self,
        incidents: IncidentRepository,
        classifier: Classifier | None = None,
        *,
        depth: int = DEFAULT_DEPTH,
    ) -> None:
        """Create a detector over the incident store.

        Args:
            incidents: Where incidents are opened and found.
            classifier: Decides each new incident's fault class; rules only when omitted.
            depth: How many edges upstream a shared cause may be.
        """
        self._incidents = incidents
        self._classifier = classifier or Classifier()
        self._depth = depth

    def detect(
        self, run_id: str, signals: Sequence[Signal], graph: LineageGraph, *, at: datetime
    ) -> list[DetectedIncident]:
        """Open or update an incident for each group of signals.

        The graph must already be stored (`LineageRepository.save`), because an incident points
        at the version it was diagnosed against. Detection is idempotent: running it again for
        the same run changes nothing.

        Args:
            run_id: The run the signals belong to, which must be recorded.
            signals: Every signal of the run.
            graph: The lineage graph of the run's models.
            at: When detection happens. Callers pass the run's own time, not the clock, so a
                replay gives the same incidents.

        Raises:
            StorageError: The database could not be read or written.
        """
        detected = []
        for group in group_signals(signals, graph, depth=self._depth):
            fault, _ = rule_class(group.signals)
            key = fingerprint(group, fault)
            existing = self._incidents.find_open(key)
            if existing is not None:
                added = self._incidents.attach(existing.id, run_id, group.signals, at=at)
                outcome = Outcome.UPDATED if added else Outcome.UNCHANGED
                incident = self._incidents.get(existing.id) or existing
            else:
                verdict = self._classifier.classify(group)
                incident = self._incidents.create(
                    IncidentDraft(
                        summary=verdict.summary,
                        severity=group.severity.value,
                        fingerprint=key,
                        root_node=group.root_node,
                        affected={
                            "models": list(group.models),
                            "columns": list(group.columns),
                            "gold_tables": list(group.gold_tables),
                        },
                        lineage_version=graph.version,
                        fault_class=verdict.fault_class,
                        confidence=verdict.confidence,
                        classified_by=verdict.classified_by,
                        opened_at=at,
                    )
                )
                self._incidents.attach(incident.id, run_id, group.signals, at=at)
                outcome = Outcome.OPENED
            log.info(
                "detector_group",
                run_id=run_id,
                incident_id=incident.id,
                outcome=outcome.value,
                signals=len(group.signals),
            )
            detected.append(DetectedIncident(incident, outcome, group.signals))
        return detected
