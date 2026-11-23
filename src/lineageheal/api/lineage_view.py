"""The slice of the lineage graph an incident page draws (FR-UI-04).

The full graph holds hundreds of columns; an incident needs only the paths from its root cause to
what broke. This builds that slice from a `LineageService`, tagging each node so the dashboard
can highlight the root and the failing columns without knowing how the graph is stored.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lineageheal.lineage.graph import ROW_SET, LineageEdge
from lineageheal.lineage.service import LineageService, UnknownNodeError

# An incident lists every column and table it touches; drawing hundreds of paths would not be
# readable, so the slice follows the first few and says it stopped.
MAX_TARGETS = 40

Role = Literal["root", "affected", "path"]


@dataclass(frozen=True)
class ViewNode:
    """One node of the slice and why it is there."""

    id: str
    table: str
    column: str
    kind: str
    role: Role


@dataclass(frozen=True)
class LineageView:
    """The paths from a root cause to what it broke."""

    version: str
    root_node: str
    nodes: tuple[ViewNode, ...]
    edges: tuple[LineageEdge, ...]
    unreached: tuple[str, ...]  # affected nodes with no path from the root in this graph version
    truncated: bool  # more targets existed than `MAX_TARGETS`


def build_view(
    service: LineageService,
    root: str,
    columns: Sequence[str],
    tables: Sequence[str],
) -> LineageView:
    """Collect the shortest path from `root` to each affected column and table.

    Args:
        service: The graph version the incident was diagnosed against.
        root: The root-cause node.
        columns: Affected columns, as `<table>.<column>`.
        tables: Affected tables; each is followed to the columns it has that the root feeds.

    Raises:
        UnknownNodeError: `root` is not in this graph version.
    """
    known = set(service.node_ids)
    if root not in known:
        raise UnknownNodeError(f"{root!r} is not in the lineage graph")

    # A table's row-set node only receives filter edges, so a column root rarely reaches it.
    # An affected table is followed to its columns instead.
    table_columns = {
        table: sorted(
            node
            for node in known
            if node.rpartition(".")[0] == table and not node.endswith(f".{ROW_SET}")
        )
        for table in dict.fromkeys(tables)
    }
    explicit = [node for node in dict.fromkeys(columns) if node in known and node != root]
    expanded = [
        node
        for nodes in table_columns.values()
        for node in nodes
        if node != root and node not in explicit
    ]
    present = [*explicit, *expanded]
    truncated = len(present) > MAX_TARGETS
    targets = present[:MAX_TARGETS]

    on_path: dict[str, None] = {root: None}
    edges: dict[tuple[str, str], LineageEdge] = {}
    reached: set[str] = set()
    for target in targets:
        path = service.path(root, target)
        if path is None:
            continue
        reached.add(target)
        on_path.update(dict.fromkeys(path.nodes))
        for edge in path.edges:
            edges[(edge.source, edge.target)] = edge

    unreached = [node for node in explicit if node in targets and node not in reached]
    unreached += [
        table
        for table, nodes in table_columns.items()
        if nodes and not reached.intersection(nodes) and set(nodes) <= set(targets)
    ]

    nodes = tuple(_node(service, node, root, reached) for node in on_path)
    return LineageView(
        version=service.version,
        root_node=root,
        nodes=nodes,
        edges=tuple(sorted(edges.values())),
        unreached=tuple(unreached),
        truncated=truncated,
    )


def _node(service: LineageService, node_id: str, root: str, reached: set[str]) -> ViewNode:
    table, _, column = node_id.rpartition(".")
    role: Role = "root" if node_id == root else "affected" if node_id in reached else "path"
    return ViewNode(node_id, table, column, service.kind_of(node_id), role)
