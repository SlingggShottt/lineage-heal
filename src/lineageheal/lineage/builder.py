"""Building the column-level lineage graph from a compiled dbt manifest (FR-LIN-02, FR-LIN-05).

dbt's own lineage stops at tables. Each model's compiled SQL is traced with sqlglot, column by
column, down to the upstream model, seed or raw file column it reads, and every edge is labelled
with how the value is produced. Nothing here runs SQL or touches the warehouse or the clock, so
one manifest always gives the same graph.

Raw files are read by DuckDB table functions (`read_csv(...)`), so the compiled SQL names no
table for them: the trace ends at an anonymous column. The raw columns come from the source
declarations instead, and the one source a bronze model depends on says which file it is.
A model that cannot be traced is not skipped: it gets table-level edges marked `inferred`, so a
walk upstream still reaches its parents, only without column precision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from lineageheal.core.errors import LineageHealError
from lineageheal.lineage.graph import (
    ROW_SET,
    EdgeKind,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeKind,
)

DIALECT = "duckdb"

# When a column reaches a target several ways, the strongest relationship is kept.
_STRENGTH = {EdgeKind.DIRECT: 0, EdgeKind.TRANSFORM: 1, EdgeKind.AGGREGATE: 2}

# A compiled raw-file path starts with RAW_ZONE_DIR, which differs per machine and run.
_RAW_ZONE_PATH = re.compile(r"'[^']*?/((?:raw|staged)/source=)")


class LineageBuildError(LineageHealError):
    """The manifest cannot be turned into a graph at all."""


class _UntraceableError(Exception):
    """One model's SQL could not be traced to known upstream columns."""


def manifest_version(manifest: Mapping[str, Any]) -> str:
    """Identify the project a graph was built from, independent of where it was compiled.

    The hash covers every model's compiled SQL and every declared column, with the machine's
    raw-zone path removed, so the same project has the same version on any laptop and any change
    that can alter lineage changes it (FR-LIN-04).
    """
    parts: list[Any] = []
    for section in ("nodes", "sources"):
        for unique_id, node in sorted(manifest.get(section, {}).items()):
            if node.get("resource_type") not in ("model", "seed", "source"):
                continue
            sql = _RAW_ZONE_PATH.sub(r"'<raw_zone>/\1", node.get("compiled_code") or "")
            parts.append([unique_id, sql, sorted(node.get("columns", {}))])
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def build_lineage(manifest: Mapping[str, Any]) -> LineageGraph:
    """Build the lineage graph of a dbt project from its compiled manifest."""
    return _Builder(manifest).build()


class _Builder:
    def __init__(self, manifest: Mapping[str, Any]) -> None:
        nodes: Mapping[str, Any] = manifest.get("nodes", {})
        self._manifest = manifest
        self._tables = {
            uid: n for uid, n in nodes.items() if n.get("resource_type") in ("model", "seed")
        }
        self._sources: Mapping[str, Any] = manifest.get("sources", {})
        self._nodes: dict[str, LineageNode] = {}
        self._data_edges: dict[tuple[str, str], tuple[EdgeKind, bool]] = {}
        self._filter_edges: set[LineageEdge] = set()
        # Column names per model, in the shape sqlglot wants, filled in dependency order.
        self._schema: dict[str, object] = {}

    def build(self) -> LineageGraph:
        for uid, source in self._sources.items():
            self._add_source(uid, source)
        for uid in self._in_dependency_order():
            table = self._tables[uid]
            if table["resource_type"] == "seed":
                self._add_seed(table)
            else:
                self._add_model(uid, table)
        edges = {
            LineageEdge(src, tgt, kind, inferred)
            for (src, tgt), (kind, inferred) in self._data_edges.items()
        } | self._filter_edges
        return LineageGraph(
            version=manifest_version(self._manifest),
            nodes=tuple(sorted(self._nodes.values())),
            edges=tuple(sorted(edges)),
        )

    # -- nodes ---------------------------------------------------------------------------

    def _node(self, table: str, column: str, kind: NodeKind) -> str:
        node = LineageNode(table, column, kind)
        self._nodes.setdefault(node.id, node)
        return node.id

    def _add_source(self, uid: str, source: Mapping[str, Any]) -> None:
        table = _source_table(source)
        self._node(table, ROW_SET, NodeKind.RAW)
        for column in source.get("columns", {}):
            self._node(table, column, NodeKind.RAW)

    def _add_seed(self, seed: Mapping[str, Any]) -> None:
        name = seed["name"]
        columns = list(seed.get("columns", {}))
        self._schema[name] = dict.fromkeys(columns, "text")
        self._node(name, ROW_SET, NodeKind.SEED)
        for column in columns:
            self._node(name, column, NodeKind.SEED)

    def _in_dependency_order(self) -> list[str]:
        order: list[str] = []
        seen: set[str] = set()

        def visit(uid: str) -> None:
            if uid in seen:
                return
            seen.add(uid)
            for parent in self._tables[uid].get("depends_on", {}).get("nodes", []):
                if parent in self._tables:
                    visit(parent)
            order.append(uid)

        for uid in sorted(self._tables):
            visit(uid)
        return order

    # -- models --------------------------------------------------------------------------

    def _add_model(self, uid: str, model: Mapping[str, Any]) -> None:
        name = model["name"]
        self._node(name, ROW_SET, NodeKind.MODEL)
        try:
            columns, data, filters = self._trace(model)
        except (_UntraceableError, sqlglot.errors.SqlglotError, KeyError, ValueError):
            declared = list(model.get("columns", {}))
            self._schema[name] = dict.fromkeys(declared, "text")
            for column in declared:
                self._node(name, column, NodeKind.MODEL)
            self._infer(uid, model)
            return
        self._schema[name] = dict.fromkeys(columns, "text")
        for column in columns:
            self._node(name, column, NodeKind.MODEL)
        for source, target, kind in data:
            self._link(source, target, kind)
        for source in filters:
            self._filter_edges.add(
                LineageEdge(source, f"{name}.{ROW_SET}", EdgeKind.FILTER, inferred=False)
            )

    def _link(self, source: str, target: str, kind: EdgeKind) -> None:
        current = self._data_edges.get((source, target))
        if current is None or _STRENGTH[kind] > _STRENGTH[current[0]]:
            self._data_edges[(source, target)] = (kind, False)

    def _infer(self, uid: str, model: Mapping[str, Any]) -> None:
        """Table-level edges from the parents dbt recorded, for a model that will not trace."""
        target = f"{model['name']}.{ROW_SET}"
        for parent in model.get("depends_on", {}).get("nodes", []):
            if parent in self._tables:
                upstream = self._tables[parent]["name"]
            elif parent in self._sources:
                upstream = _source_table(self._sources[parent])
            else:
                continue
            self._data_edges[(f"{upstream}.{ROW_SET}", target)] = (EdgeKind.TRANSFORM, True)

    def _trace(
        self, model: Mapping[str, Any]
    ) -> tuple[list[str], list[tuple[str, str, EdgeKind]], set[str]]:
        sql = model.get("compiled_code")
        if not sql:
            raise _UntraceableError("the manifest has no compiled SQL; run `dbt compile`")
        name = model["name"]
        qualified = qualify(
            sqlglot.parse_one(sql, dialect=DIALECT),
            schema=self._schema,
            dialect=DIALECT,
            validate_qualify_columns=False,
        )
        if not isinstance(qualified, exp.Query):
            raise _UntraceableError("the model is not a query")
        columns = list(qualified.named_selects)
        raw = self._raw_columns(model)

        data: list[tuple[str, str, EdgeKind]] = []
        for column in columns:
            root = lineage(column, sql, self._schema, dialect=DIALECT)
            for leaf, kind in _leaves(root):
                data.append((self._resolve(leaf, raw), f"{name}.{column}", kind))

        filters: set[str] = set()
        for scope in traverse_scope(qualified):
            for ref in _filter_columns(scope):
                filters |= self._filter_sources(qualified, scope, ref, raw)
        return columns, data, filters

    # -- resolving what the trace ends at --------------------------------------------------

    def _raw_columns(self, model: Mapping[str, Any]) -> dict[str, str] | None:
        """Declared columns of the one raw source a model reads, by lowercase name.

        None when the model reads no source. A model reading several would make a raw leaf
        ambiguous, so that is left to the fallback.
        """
        parents = [p for p in model.get("depends_on", {}).get("nodes", []) if p in self._sources]
        if not parents:
            return None
        if len(parents) > 1:
            raise _UntraceableError("reads more than one raw source")
        source = self._sources[parents[0]]
        table = _source_table(source)
        declared = {column.lower(): column for column in source.get("columns", {})}
        return {"": table, **declared}

    def _resolve(self, leaf: Node, raw: Mapping[str, str] | None) -> str:
        """The node id a leaf of a column trace stands for."""
        column = leaf.name.rsplit(".", 1)[-1].strip('"')
        if isinstance(leaf.source, exp.Table) and leaf.source.name in self._schema:
            return f"{leaf.source.name}.{column}"
        if isinstance(leaf.source, exp.Placeholder | exp.Table) and raw is not None:
            table = raw[""]
            return self._node(table, raw.get(column.lower(), column), NodeKind.RAW)
        raise _UntraceableError(f"unknown upstream table for {leaf.name!r}")

    def _filter_sources(
        self, root: exp.Expr, scope: Scope, ref: exp.Column, raw: Mapping[str, str] | None
    ) -> set[str]:
        """The upstream columns a WHERE, JOIN, GROUP BY or HAVING reference reads."""
        source = scope.sources.get(ref.table)
        if isinstance(source, Scope):
            inner = source.expression.copy()
            with_clause = root.args.get("with_")
            if with_clause is not None and "with_" not in inner.args:
                inner.set("with_", with_clause.copy())
            traced = lineage(ref.name, inner.sql(dialect=DIALECT), self._schema, dialect=DIALECT)
            return {self._resolve(leaf, raw) for leaf, _ in _leaves(traced)}
        if isinstance(source, exp.Table) and source.name in self._schema:
            return {f"{source.name}.{ref.name}"}
        if source is not None and raw is not None:
            return {self._node(raw[""], raw.get(ref.name.lower(), ref.name), NodeKind.RAW)}
        raise _UntraceableError(f"cannot place {ref.sql()!r}")


def _source_table(source: Mapping[str, Any]) -> str:
    """`raw.<publisher>.<dataset>`: the source group `raw_nse` is the publisher `nse`."""
    publisher = str(source["source_name"]).removeprefix("raw_")
    return f"raw.{publisher}.{source['name']}"


def _expression_kind(expression: exp.Expr) -> EdgeKind:
    """How a select item produces its value from its inputs."""
    item = expression.unalias() if isinstance(expression, exp.Alias) else expression
    if isinstance(item, exp.Column):
        return EdgeKind.DIRECT
    for function in item.find_all(exp.AggFunc):
        if function.find_ancestor(exp.Window) is None:
            return EdgeKind.AGGREGATE
    return EdgeKind.TRANSFORM


def _leaves(node: Node, inherited: EdgeKind = EdgeKind.DIRECT) -> Iterator[tuple[Node, EdgeKind]]:
    """Every leaf of a column trace with the strongest relationship on the way to it."""
    if not node.downstream:
        if isinstance(node.source, exp.Table | exp.Placeholder):
            yield node, inherited
        return
    kind = max(inherited, _expression_kind(node.expression), key=_STRENGTH.__getitem__)
    for child in node.downstream:
        yield from _leaves(child, kind)


def _filter_columns(scope: Scope) -> Iterator[exp.Column]:
    """Columns this scope uses to choose, group or order rows rather than to produce values."""
    select = scope.expression
    if not isinstance(select, exp.Select):
        return
    clauses = [select.args.get(key) for key in ("where", "group", "having", "qualify")]
    clauses += [join.args.get("on") for join in select.args.get("joins", [])]
    for clause in clauses:
        if clause is not None:
            yield from (c for c in clause.find_all(exp.Column) if c.table)
