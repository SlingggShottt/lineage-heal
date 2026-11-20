"""Tools that read the failing run's own records: lineage, model SQL and test failures."""

from typing import Any, Literal

from pydantic import Field

from lineageheal.agents.tools.base import (
    MAX_ROWS,
    Payload,
    Tool,
    ToolArguments,
    ToolContext,
    ToolError,
    clip,
    limited,
)
from lineageheal.lineage.service import UnknownNodeError

MAX_REACHED = 40
MAX_EDGES = 60
MAX_SUGGESTIONS = 5
MAX_SQL_CHARACTERS = 3000


class GetLineageArguments(ToolArguments):
    """Which node to walk from, and how."""

    node: str = Field(max_length=300, description="A node id such as 'slv_amfi__nav.nav_inr'.")
    direction: Literal["upstream", "downstream"] = Field(
        description="upstream follows what feeds the node, downstream what it feeds."
    )
    depth: int = Field(default=3, ge=1, le=6, description="How many edges to follow.")
    include_filters: bool = Field(
        default=True, description="Also follow columns that only decide which rows exist."
    )


def get_lineage(context: ToolContext, args: GetLineageArguments) -> Payload:
    """Nodes and edges upstream or downstream of a node, nearest first, within bounds."""
    service = context.lineage
    walk = service.upstream if args.direction == "upstream" else service.downstream
    try:
        traversal = walk(args.node, args.depth, include_filters=args.include_filters)
    except UnknownNodeError as exc:
        raise ToolError(f"{exc}{_similar_nodes(context, args.node)}") from exc
    reached, reached_omitted = limited(
        [{"node": r.node, "distance": r.distance} for r in traversal.reached], MAX_REACHED
    )
    edges, edges_omitted = limited(
        [
            f"{e.source} -> {e.target} ({e.kind.value}{', inferred' if e.inferred else ''})"
            for e in traversal.edges
        ],
        MAX_EDGES,
    )
    return {
        "node": args.node,
        "direction": args.direction,
        "lineage_version": service.version,
        "inferred": traversal.inferred,
        "reached": reached,
        "reached_omitted": reached_omitted,
        "edges": edges,
        "edges_omitted": edges_omitted,
    }


def _similar_nodes(context: ToolContext, node: str) -> str:
    needle = node.lower()
    ids = context.lineage.node_ids
    matches = [n for n in ids if needle in n.lower()] or [
        n for n in ids if n.lower().endswith(needle.rsplit(".", 1)[-1])
    ]
    return f". Similar: {', '.join(matches[:MAX_SUGGESTIONS])}" if matches else ""


class GetModelSqlArguments(ToolArguments):
    """Which dbt model's SQL to read."""

    model: str = Field(max_length=200, description="A dbt model name such as 'slv_amfi__nav'.")


def get_model_sql(context: ToolContext, args: GetModelSqlArguments) -> Payload:
    """A model's SQL as written and as compiled, from the failing run's manifest."""
    models = {
        node["name"]: node
        for node in _nodes(context)
        if node.get("resource_type") == "model" and "name" in node
    }
    node = models.get(args.model)
    if node is None:
        needle = args.model.lower()
        close = [name for name in sorted(models) if needle in name.lower()] or sorted(models)
        raise ToolError(f"No dbt model named {args.model!r}. Known: {', '.join(close[:MAX_ROWS])}")
    compiled = node.get("compiled_code")
    return {
        "model": args.model,
        "path": node.get("original_file_path"),
        "description": clip(str(node.get("description") or ""), 500),
        "sql": clip(str(node.get("raw_code") or ""), MAX_SQL_CHARACTERS),
        "compiled_sql": clip(str(compiled), MAX_SQL_CHARACTERS) if compiled else None,
    }


class GetTestFailureArguments(ToolArguments):
    """Which failing test to look at."""

    test: str = Field(max_length=300, description="The test's name or dbt unique id.")


def get_test_failure(context: ToolContext, args: GetTestFailureArguments) -> Payload:
    """What a failing test reported, and how it is defined.

    dbt does not keep the failing rows in this project, so the count and message are what the
    run recorded; the data tools show the rows themselves.
    """
    signals = context.summary.signals
    found = next((s for s in signals if args.test in (s.name, s.unique_id)), None)
    if found is None:
        names = ", ".join(s.name for s in signals[:MAX_ROWS]) or "none"
        raise ToolError(f"The run has no signal named {args.test!r}. Signals: {names}")
    payload: Payload = {
        "name": found.name,
        "kind": found.kind.value,
        "severity": found.severity.value,
        "check_type": found.check_type,
        "models": list(found.models),
        "model": found.model,
        "column": found.column,
        "failures": found.failures,
        "message": clip(found.message, 500),
        "definition": None,
    }
    node = context.manifest.get("nodes", {}).get(found.unique_id) if found.unique_id else None
    if node:
        metadata = node.get("test_metadata") or {}
        payload["definition"] = {
            "generic_test": metadata.get("name"),
            "arguments": {
                key: clip(str(value), 200)
                for key, value in (metadata.get("kwargs") or {}).items()
                if key != "model"
            },
            "attached_to": node.get("attached_node"),
            "column": node.get("column_name"),
            "configured_severity": (node.get("config") or {}).get("severity"),
            "sql": clip(str(node.get("raw_code") or ""), MAX_SQL_CHARACTERS)
            if not metadata
            else None,
        }
    return payload


def _nodes(context: ToolContext) -> list[dict[str, Any]]:
    return list(context.manifest.get("nodes", {}).values())


RUN_TOOLS = (
    Tool(
        "get_lineage",
        "List the columns upstream (what feeds a node) or downstream (what it feeds) of a node "
        "in the lineage graph, nearest first. Inferred edges are table-level guesses.",
        GetLineageArguments,
        get_lineage,
    ),
    Tool(
        "get_model_sql",
        "Read a dbt model's SQL as written and as compiled.",
        GetModelSqlArguments,
        get_model_sql,
    ),
    Tool(
        "get_test_failure",
        "Read what a failing test reported (rows found, message) and how the test is defined.",
        GetTestFailureArguments,
        get_test_failure,
    ),
)
