"""Assembles the LangGraph graph and runs it (FR-ORC-04).

The graph is detect, investigate each incident, then fix each concluded one (verify and publish
a dbt patch, or file a suggestion) when fixing is configured. The review pause needs the
Postgres checkpointer and comes with LH-097.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from lineageheal.agents.graph.nodes import HealDependencies, HealNodes
from lineageheal.agents.graph.state import FixOutcome, HealState, IncidentOutcome

# Each incident is one step of the loop, so the library's default of 25 would cap a run.
RECURSION_LIMIT = 1000


def build_heal_graph(deps: HealDependencies) -> Any:
    """Compile the healing graph over `deps`."""
    nodes = HealNodes(deps)
    graph = StateGraph(HealState)
    graph.add_node("detect", nodes.detect)
    graph.add_node("investigate", nodes.investigate)
    graph.add_node("fix", nodes.fix)
    graph.add_edge(START, "detect")
    routes: dict[Hashable, str] = {"investigate": "investigate", "fix": "fix", "end": END}
    graph.add_conditional_edges("detect", nodes.more_to_do, routes)
    graph.add_conditional_edges("investigate", nodes.more_to_do, routes)
    graph.add_conditional_edges("fix", nodes.more_to_fix, routes)
    return graph.compile()


@dataclass(frozen=True)
class HealReport:
    """What a heal run did: the investigations, and the fixes when fixing was configured."""

    outcomes: list[IncidentOutcome]
    fixes: list[FixOutcome]


def run_heal_report(
    deps: HealDependencies, run_id: str, *, only_incident: int | None = None
) -> HealReport:
    """Detect, investigate and (if configured) fix a run's incidents.

    Args:
        deps: What the nodes call.
        run_id: The pipeline run to heal.
        only_incident: Handle just this incident, if the run has it.
    """
    initial: HealState = {
        "run_id": run_id,
        "only_incident": only_incident,
        "pending": [],
        "outcomes": [],
        "to_fix": [],
        "fixes": [],
    }
    final = build_heal_graph(deps).invoke(initial, {"recursion_limit": RECURSION_LIMIT})
    return HealReport(final["outcomes"], final["fixes"])


def run_heal(
    deps: HealDependencies, run_id: str, *, only_incident: int | None = None
) -> list[IncidentOutcome]:
    """Detect and investigate a run's incidents, returning one outcome per incident handled."""
    return run_heal_report(deps, run_id, only_incident=only_incident).outcomes
