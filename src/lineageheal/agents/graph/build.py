"""Assembles the LangGraph graph and runs it (FR-ORC-04).

Today the graph is detect, then investigate each incident in turn. The patch, sandbox and pull
request branches of design section 8.1 are added with the Fixer; the review pause needs the
Postgres checkpointer, which comes with the task that adds the pause (LH-056).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from lineageheal.agents.graph.nodes import HealDependencies, HealNodes
from lineageheal.agents.graph.state import HealState, IncidentOutcome

# Each incident is one step of the loop, so the library's default of 25 would cap a run.
RECURSION_LIMIT = 1000


def build_heal_graph(deps: HealDependencies) -> Any:
    """Compile the healing graph over `deps`."""
    nodes = HealNodes(deps)
    graph = StateGraph(HealState)
    graph.add_node("detect", nodes.detect)
    graph.add_node("investigate", nodes.investigate)
    graph.add_edge(START, "detect")
    graph.add_conditional_edges(
        "detect", nodes.more_to_do, {"investigate": "investigate", "end": END}
    )
    graph.add_conditional_edges(
        "investigate", nodes.more_to_do, {"investigate": "investigate", "end": END}
    )
    return graph.compile()


def run_heal(
    deps: HealDependencies, run_id: str, *, only_incident: int | None = None
) -> list[IncidentOutcome]:
    """Detect and investigate a run's incidents.

    Args:
        deps: What the nodes call.
        run_id: The pipeline run to heal.
        only_incident: Investigate just this incident, if the run has it.

    Returns:
        One outcome per incident investigated or skipped, in the order they were handled.
    """
    initial: HealState = {
        "run_id": run_id,
        "only_incident": only_incident,
        "pending": [],
        "outcomes": [],
    }
    final = build_heal_graph(deps).invoke(initial, {"recursion_limit": RECURSION_LIMIT})
    outcomes: list[IncidentOutcome] = final["outcomes"]
    return outcomes
