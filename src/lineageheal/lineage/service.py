"""Walking the lineage graph: what feeds a column, what it feeds, what failures share (FR-LIN-03).

The Investigator follows a failing column upstream to its root cause, and the Detector asks which
upstream nodes several failures have in common. Both only read a finished `LineageGraph`, so the
service holds no state beyond it and gives the same answer for the same graph.

Filter edges (a column that decides which rows exist) are followed by default, since a wrong
filter column can corrupt everything downstream; callers can leave them out to follow values only.
Every result says whether it crossed an `inferred` edge, which is a table-level guess (FR-LIN-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import networkx as nx

from lineageheal.core.errors import LineageHealError
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph


class UnknownNodeError(LineageHealError):
    """A node id that is not in the graph."""


@dataclass(frozen=True, slots=True)
class Reach:
    """A node reached by a traversal and how many edges away it is."""

    node: str
    distance: int


@dataclass(frozen=True, slots=True)
class Traversal:
    """Everything reachable from `start` within the depth limit, nearest first."""

    start: str
    reached: tuple[Reach, ...]
    edges: tuple[LineageEdge, ...]  # every edge between the start and the reached nodes

    @property
    def inferred(self) -> bool:
        """True when any edge is a table-level guess, so column precision is lost."""
        return any(edge.inferred for edge in self.edges)


@dataclass(frozen=True, slots=True)
class LineagePath:
    """One path from an upstream node to a downstream one, in flow order."""

    nodes: tuple[str, ...]
    edges: tuple[LineageEdge, ...]

    @property
    def inferred(self) -> bool:
        """True when any edge on the path is a table-level guess."""
        return any(edge.inferred for edge in self.edges)


class LineageService:
    """Upstream and downstream traversal over one version of the lineage graph."""

    def __init__(self, graph: LineageGraph) -> None:
        """Index the graph's edges for traversal; the graph itself is not changed."""
        self._graph = graph
        self._flow: nx.DiGraph[str] = nx.DiGraph()
        self._flow.add_nodes_from(node.id for node in graph.nodes)
        self._kinds = {node.id: node.kind.value for node in graph.nodes}
        for edge in graph.edges:
            self._flow.add_edge(edge.source, edge.target, edge=edge)

    @property
    def version(self) -> str:
        """The manifest version the graph was built from."""
        return self._graph.version

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Every node id in the graph, sorted; callers use it to suggest a near match."""
        return tuple(sorted(self._flow))

    def kind_of(self, node: str) -> str:
        """What the node's table is: `raw`, `seed` or `model`."""
        self._require(node)
        return str(self._kinds[node])

    def upstream(
        self, node: str, depth: int | None = None, *, include_filters: bool = True
    ) -> Traversal:
        """Nodes that feed `node`, up to `depth` edges away (all of them when depth is None)."""
        return self._traverse(node, depth, include_filters, reverse=True)

    def downstream(
        self, node: str, depth: int | None = None, *, include_filters: bool = True
    ) -> Traversal:
        """Nodes that `node` feeds, up to `depth` edges away (all of them when depth is None)."""
        return self._traverse(node, depth, include_filters, reverse=False)

    def path(self, source: str, target: str, *, include_filters: bool = True) -> LineagePath | None:
        """The shortest path from `source` to `target`, or None when `source` does not feed it."""
        view = self._view(include_filters)
        self._require(source, target)
        try:
            nodes = tuple(nx.shortest_path(view, source, target))
        except nx.NetworkXNoPath:
            return None
        return LineagePath(nodes, tuple(view[a][b]["edge"] for a, b in pairwise(nodes)))

    def common_ancestors(
        self, nodes: list[str], *, include_filters: bool = True
    ) -> tuple[str, ...]:
        """The closest nodes that feed every one of `nodes`, sorted by id.

        A node counts as its own ancestor, so when one failing node feeds the others it is the
        answer. Ancestors that only reach the others through another common ancestor are dropped:
        the Detector wants the nearest shared cause, not everything above it.
        """
        self._require(*nodes)
        if not nodes:
            return ()
        view = self._view(include_filters)
        shared = set.intersection(*({n} | nx.ancestors(view, n) for n in nodes))
        return tuple(sorted(node for node in shared if not (nx.descendants(view, node) & shared)))

    def _traverse(
        self, node: str, depth: int | None, include_filters: bool, *, reverse: bool
    ) -> Traversal:
        if depth is not None and depth < 0:
            raise ValueError("depth must not be negative")
        self._require(node)
        view = self._view(include_filters)
        walked = nx.reverse_view(view) if reverse else view
        distances = nx.single_source_shortest_path_length(walked, node, cutoff=depth)
        distances.pop(node)
        reached = tuple(sorted((Reach(n, d) for n, d in distances.items()), key=_nearest_first))
        members = {node, *distances}
        edges = tuple(
            sorted(
                data["edge"]
                for source, target, data in view.edges(data=True)
                if source in members and target in members
            )
        )
        return Traversal(node, reached, edges)

    def _view(self, include_filters: bool) -> nx.DiGraph[str]:
        if include_filters:
            return self._flow
        return nx.subgraph_view(
            self._flow,
            filter_edge=lambda a, b: self._flow[a][b]["edge"].kind is not EdgeKind.FILTER,
        )

    def _require(self, *nodes: str) -> None:
        for node in nodes:
            if node not in self._flow:
                raise UnknownNodeError(f"{node!r} is not in the lineage graph")


def _nearest_first(reach: Reach) -> tuple[int, str]:
    return reach.distance, reach.node
