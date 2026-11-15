"""The column-level lineage graph: nodes, edges and the version that identifies a build of it.

A node is one column of one table, named `<table>.<column>`; a raw file column is
`raw.<source>.<dataset>.<column>`. Each table also has a row-set node `<table>.*` that stands for
the rows as a whole. An edge points from the upstream node to the node it feeds (FR-LIN-02).
The graph is immutable, so a version can be compared, stored and traversed without surprises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# A node that stands for a table's rows rather than one of its columns.
ROW_SET = "*"


class EdgeKind(StrEnum):
    """How an upstream column feeds a downstream node."""

    DIRECT = "direct"  # copied or renamed, value unchanged
    TRANSFORM = "transform"  # computed from the column, row by row
    AGGREGATE = "aggregate"  # combined with other rows
    FILTER = "filter"  # decides which rows exist; points at the row-set node


class NodeKind(StrEnum):
    """What a node's table is."""

    RAW = "raw"
    SEED = "seed"
    MODEL = "model"


@dataclass(frozen=True, slots=True, order=True)
class LineageNode:
    """One column of one table, or a table's row-set when `column` is `*`."""

    table: str
    column: str
    kind: NodeKind

    @property
    def id(self) -> str:
        """The node's name in the graph and in stored rows."""
        return f"{self.table}.{self.column}"


@dataclass(frozen=True, slots=True, order=True)
class LineageEdge:
    """An upstream node feeding a downstream one.

    `inferred` is true when the edge is a guess at table level because the model's SQL could not
    be traced (FR-LIN-05): it then joins row-set nodes, and a reader should not trust the columns.
    """

    source: str
    target: str
    kind: EdgeKind
    inferred: bool = False


@dataclass(frozen=True, slots=True)
class LineageGraph:
    """Every node and edge of the project, tagged with the version of the project it came from."""

    version: str
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]
