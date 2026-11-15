"""FR-LIN-02, FR-LIN-04, FR-LIN-05: the lineage graph built from a compiled manifest.

The manifests here are small and hand-written so each test shows one rule; the real project is
traced in tests/unit/dbt/test_lineage_project.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from lineageheal.lineage.builder import build_lineage, manifest_version
from lineageheal.lineage.graph import EdgeKind, LineageGraph

PARENT = "model.p.parent"
CHILD = "model.p.child"
SOURCE = "source.p.raw_pub.files"


def manifest(child_sql: str, *, raw_path: str = "/home/a/raw") -> dict[str, Any]:
    """A source read by a table function, a bronze-like parent and one child model."""
    return {
        "sources": {
            SOURCE: {
                "resource_type": "source",
                "source_name": "raw_pub",
                "name": "files",
                "columns": {"Unit Price": {}, "Qty": {}},
            }
        },
        "nodes": {
            PARENT: {
                "resource_type": "model",
                "name": "parent",
                "depends_on": {"nodes": [SOURCE]},
                "compiled_code": (
                    'select cast("Unit Price" as decimal(18, 4)) as price, '
                    '"Qty" as qty, "Qty" as other '
                    "from read_csv('RAW/raw/source=pub/*.csv', header = true)".replace(
                        "RAW", raw_path
                    )
                ),
                "columns": {},
            },
            CHILD: {
                "resource_type": "model",
                "name": "child",
                "depends_on": {"nodes": [PARENT]},
                "compiled_code": child_sql,
                "columns": {"a": {}, "b": {}},
            },
        },
    }


def edges(graph: LineageGraph, target: str) -> dict[str, tuple[str, bool]]:
    return {e.source: (e.kind.value, e.inferred) for e in graph.edges if e.target == target}


def test_raw_columns_keep_their_published_names_and_feed_bronze() -> None:
    graph = build_lineage(manifest("select price from parent"))

    # DuckDB lowercases the quoted name; the declared one is "Unit Price".
    assert edges(graph, "parent.price") == {"raw.pub.files.Unit Price": ("transform", False)}
    assert edges(graph, "parent.qty") == {"raw.pub.files.Qty": ("direct", False)}


def test_a_copy_a_rename_a_computation_and_an_aggregate_are_told_apart() -> None:
    graph = build_lineage(
        manifest(
            "select price as a, sum(price * qty) over () as b, "
            "max(qty) as c, price + 1 as d from parent group by price, qty"
        )
    )

    assert edges(graph, "child.a")["parent.price"] == ("direct", False)
    assert edges(graph, "child.b")["parent.price"] == ("transform", False)
    assert edges(graph, "child.c") == {"parent.qty": ("aggregate", False)}
    assert edges(graph, "child.d") == {"parent.price": ("transform", False)}


def test_the_strongest_relationship_along_a_path_wins() -> None:
    graph = build_lineage(
        manifest(
            "with totals as (select sum(qty) as total from parent) select total as a from totals"
        )
    )

    assert edges(graph, "child.a") == {"parent.qty": ("aggregate", False)}


def test_a_column_used_only_to_filter_points_at_the_row_set() -> None:
    graph = build_lineage(manifest("select price as a from parent where qty is not null"))

    filters = {(e.source, e.target) for e in graph.edges if e.kind is EdgeKind.FILTER}
    assert filters == {("parent.qty", "child.*")}


def test_a_filter_through_a_cte_reaches_the_column_it_reads() -> None:
    graph = build_lineage(
        manifest(
            "with kept as (select price, qty from parent) select price as a from kept where qty > 0"
        )
    )

    filters = {(e.source, e.target) for e in graph.edges if e.kind is EdgeKind.FILTER}
    assert filters == {("parent.qty", "child.*")}


def test_sql_that_will_not_trace_falls_back_to_table_level_edges() -> None:
    graph = build_lineage(manifest("select from where"))

    assert {(e.source, e.target, e.inferred) for e in graph.edges if e.target == "child.*"} == {
        ("parent.*", "child.*", True)
    }
    # Its declared columns still exist as nodes, so later models can build on them.
    assert {"child.a", "child.b"} <= {n.id for n in graph.nodes}


def test_a_model_with_no_compiled_sql_falls_back_too() -> None:
    data = manifest("select 1")
    data["nodes"][CHILD]["compiled_code"] = None

    graph = build_lineage(data)

    assert any(e.inferred and e.source == "parent.*" for e in graph.edges)


def test_the_version_ignores_where_the_project_was_compiled() -> None:
    one = manifest_version(manifest("select price as a from parent", raw_path="/home/a/raw"))
    other = manifest_version(manifest("select price as a from parent", raw_path="/srv/ci/zone"))

    assert one == other


@pytest.mark.parametrize(
    "child_sql", ["select qty as a from parent", "select price as a from parent where qty > 1"]
)
def test_the_version_changes_when_a_models_sql_changes(child_sql: str) -> None:
    base = manifest_version(manifest("select price as a from parent"))

    assert manifest_version(manifest(child_sql)) != base


def test_the_same_manifest_gives_the_same_graph() -> None:
    sql = "select price as a, qty as b from parent where qty > 0"

    assert build_lineage(manifest(sql)) == build_lineage(manifest(sql))
