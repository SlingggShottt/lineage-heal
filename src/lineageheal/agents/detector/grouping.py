"""Grouping signals that share an upstream cause into one incident (FR-DET-02, FR-DET-03).

The grouping is deterministic: the same signals and graph always give the same groups, with no
model involved. Each signal is placed on a lineage node, two signals belong together when their
nodes have an upstream node in common within a depth limit, and a group's root is the nearest
node all of its members share.

Filter edges are left out when looking for a shared cause. Many models filter on the same
calendar, and treating that as a common cause would merge failures that have nothing to do with
each other; the Investigator still follows filters when it looks for the actual fault.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from lineageheal.core.signals import Severity, Signal, SignalKind
from lineageheal.lineage.graph import ROW_SET, LineageGraph
from lineageheal.lineage.service import LineageService

# How many edges up a shared cause may be. Deep enough to link a gold failure to the bronze column
# behind it, short enough that two unrelated feeds are not joined by something far upstream.
DEFAULT_DEPTH = 6

GOLD_PREFIX = "gld_"


@dataclass(frozen=True, slots=True)
class SignalGroup:
    """Signals that share one cause, and what that cause touches."""

    signals: tuple[Signal, ...]
    root_node: str | None  # None when no signal could be placed in the graph
    models: tuple[str, ...]  # every table the signals or their cause reach
    columns: tuple[str, ...]  # `model.column` of each signal that names a column
    gold_tables: tuple[str, ...]  # downstream tables a consumer reads

    @property
    def severity(self) -> Severity:
        """`error` when any signal is an error; a group of warnings stays a warning."""
        if any(s.severity is Severity.ERROR for s in self.signals):
            return Severity.ERROR
        return Severity.WARN


def node_of(signal: Signal, known: set[str]) -> str | None:
    """Place a signal on a lineage node, or return None when the graph has no such place.

    A column-level signal sits on its column, and falls back to the model's row-set when the
    column is not in the graph. A failed download sits on the raw file's row-set.
    """
    if signal.kind is SignalKind.INGESTION_ERROR:
        source, dataset, _ = signal.name.split("/", 2)
        candidate = f"raw.{source}.{dataset}.{ROW_SET}"
        return candidate if candidate in known else None
    if signal.model is None:
        return None
    if signal.column is not None and f"{signal.model}.{signal.column}" in known:
        return f"{signal.model}.{signal.column}"
    row_set = f"{signal.model}.{ROW_SET}"
    return row_set if row_set in known else None


def group_signals(
    signals: Sequence[Signal], graph: LineageGraph, *, depth: int = DEFAULT_DEPTH
) -> list[SignalGroup]:
    """Group signals by shared upstream cause, in a stable order.

    Args:
        signals: Every signal of a run.
        graph: The lineage graph the run's models were built from.
        depth: How many edges upstream a shared cause may be.
    """
    service = LineageService(graph)
    known = {node.id for node in graph.nodes}
    table_of = {node.id: node.table for node in graph.nodes}

    placed = [(signal, node_of(signal, known)) for signal in signals]
    ancestry: dict[str, dict[str, int]] = {}
    for _, node in placed:
        if node is not None and node not in ancestry:
            reached = service.upstream(node, depth, include_filters=False).reached
            ancestry[node] = {node: 0} | {r.node: r.distance for r in reached}

    # Union-find over the signals: two are joined when their ancestries overlap.
    parent = list(range(len(placed)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_, a) in enumerate(placed):
        for j in range(i + 1, len(placed)):
            b = placed[j][1]
            if a is not None and b is not None and ancestry[a].keys() & ancestry[b].keys():
                parent[find(j)] = find(i)

    members: dict[int, list[int]] = {}
    for i in range(len(placed)):
        members.setdefault(find(i), []).append(i)

    groups = []
    for indexes in members.values():
        nodes = [node for i in indexes if (node := placed[i][1]) is not None]
        root = _root(nodes, ancestry)
        groups.append(
            _describe(tuple(placed[i][0] for i in indexes), nodes, root, service, table_of)
        )
    return sorted(groups, key=lambda g: (g.root_node or "~", g.signals[0].name))


def _root(nodes: list[str], ancestry: dict[str, dict[str, int]]) -> str | None:
    """The nearest node the members share; the most widely shared one when none is in all."""
    if not nodes:
        return None
    shared = Counter(candidate for node in nodes for candidate in ancestry[node])
    best = max(shared.values())
    candidates = [c for c, count in shared.items() if count == best]
    return min(candidates, key=lambda c: (sum(ancestry[n].get(c, 0) for n in nodes), c))


def _describe(
    signals: tuple[Signal, ...],
    nodes: list[str],
    root: str | None,
    service: LineageService,
    table_of: dict[str, str],
) -> SignalGroup:
    affected_nodes = set(nodes)
    for node in [*nodes, *([root] if root else [])]:
        affected_nodes |= {r.node for r in service.downstream(node).reached}
    tables = {table_of[node] for node in affected_nodes | ({root} if root else set())}
    tables |= {name for s in signals for name in (*s.models, s.model) if name}
    columns = sorted({f"{s.model}.{s.column}" for s in signals if s.model and s.column})
    return SignalGroup(
        signals=signals,
        root_node=root,
        models=tuple(sorted(tables)),
        columns=tuple(columns),
        gold_tables=tuple(sorted(t for t in tables if t.startswith(GOLD_PREFIX))),
    )
