"""What the Investigator looks at first, and which nodes it considers next.

The opening evidence needs no model: the incident's own failing checks, similar past
knowledge and the lineage above the failure. After that a model chooses, but only among nodes
this module offers, so the search stays on the graph.
"""

from __future__ import annotations

from typing import Any

from lineageheal.agents.detector.classify import Verdict
from lineageheal.agents.detector.grouping import SignalGroup
from lineageheal.core.signals import Signal, SignalKind
from lineageheal.lineage.service import LineageService

# Failing checks looked at up front; the model can ask for more.
MAX_SEED_CHECKS = 3
# Nodes offered to the model at once, nearest first.
FRONTIER_SIZE = 10
FRONTIER_DEPTH = 6
SEED_LINEAGE_DEPTH = 4
RAW_PREFIX = "raw."


def seed_calls(group: SignalGroup, verdict: Verdict) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls made before any model is asked anything."""
    columns = " ".join(group.columns[:5])
    query = f"{verdict.summary} {columns}".strip()[:500]
    calls: list[tuple[str, dict[str, Any]]] = [("search_knowledge", {"query": query, "limit": 3})]
    for signal in group.signals[:MAX_SEED_CHECKS]:
        call = _call_for(signal)
        if call is not None:
            calls.append(call)
    if group.root_node is not None:
        calls.append(
            (
                "get_lineage",
                {"node": group.root_node, "direction": "upstream", "depth": SEED_LINEAGE_DEPTH},
            )
        )
    return calls


def _call_for(signal: Signal) -> tuple[str, dict[str, Any]] | None:
    if signal.kind is SignalKind.INGESTION_ERROR:
        source, dataset, day = signal.name.split("/", 2)
        return "get_ingestion_log", {"source": source, "dataset": dataset, "trade_date": day}
    if signal.model is None and signal.unique_id is None:
        return None
    return "get_test_failure", {"test": signal.unique_id or signal.name}


def frontier(
    service: LineageService, start: str | None, refuted: set[str]
) -> list[tuple[str, int]]:
    """Unrefuted nodes at or above `start`, nearest first, as `(node, distance)`.

    The failing node itself comes first (the fault may be in its own logic). Raw file nodes
    are always offered, however far away, because comparing a raw file's columns is the
    cheapest way to rule a whole feed in or out.
    """
    if start is None:
        return []
    walk = service.upstream(start, FRONTIER_DEPTH)
    nodes = [(start, 0), *((r.node, r.distance) for r in walk.reached)]
    chosen = [(n, d) for n, d in nodes if n not in refuted][:FRONTIER_SIZE]
    listed = {node for node, _ in chosen}
    chosen.extend(
        (n, d) for n, d in nodes if n.startswith(RAW_PREFIX) and n not in listed | refuted
    )
    return chosen
