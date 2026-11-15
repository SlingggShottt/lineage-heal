"""FR-LIN-03: upstream and downstream traversal with a depth limit, paths and shared ancestors.

The graph is small and written out so each expectation can be checked by eye:

    raw.a ──> b.x ──> c.x ──> d.x          b.k ··filter··> c.*   (c.* is c's row set)
    raw.a ──> b.y ──────────> d.y          c.* ~~inferred~~> d.*
"""

from __future__ import annotations

import pytest

from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind
from lineageheal.lineage.service import LineageService, UnknownNodeError

IDS = [
    "raw.a",
    "b.x",
    "b.y",
    "b.k",
    "c.x",
    "c.*",
    "d.x",
    "d.y",
    "d.*",
    "lonely.z",
]
EDGES = [
    LineageEdge("raw.a", "b.x", EdgeKind.TRANSFORM),
    LineageEdge("raw.a", "b.y", EdgeKind.DIRECT),
    LineageEdge("b.x", "c.x", EdgeKind.DIRECT),
    LineageEdge("c.x", "d.x", EdgeKind.DIRECT),
    LineageEdge("b.y", "d.y", EdgeKind.DIRECT),
    LineageEdge("b.k", "c.*", EdgeKind.FILTER),
    LineageEdge("c.*", "d.*", EdgeKind.TRANSFORM, inferred=True),
]


@pytest.fixture
def service() -> LineageService:
    nodes = []
    for node_id in IDS:
        table, column = node_id.rsplit(".", 1)
        nodes.append(LineageNode(table, column, NodeKind.MODEL))
    return LineageService(LineageGraph("v1", tuple(nodes), tuple(EDGES)))


def reached(traversal) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {r.node: r.distance for r in traversal.reached}


def test_upstream_reaches_every_ancestor_with_its_distance(service: LineageService) -> None:
    result = service.upstream("d.x")

    assert reached(result) == {"c.x": 1, "b.x": 2, "raw.a": 3}
    assert [r.node for r in result.reached] == ["c.x", "b.x", "raw.a"]  # nearest first


def test_downstream_reaches_every_descendant(service: LineageService) -> None:
    assert reached(service.downstream("raw.a")) == {
        "b.x": 1,
        "b.y": 1,
        "c.x": 2,
        "d.y": 2,
        "d.x": 3,
    }


def test_the_depth_limit_stops_the_walk(service: LineageService) -> None:
    assert reached(service.upstream("d.x", depth=2)) == {"c.x": 1, "b.x": 2}
    assert service.upstream("d.x", depth=0).reached == ()


def test_a_negative_depth_is_refused(service: LineageService) -> None:
    with pytest.raises(ValueError, match="negative"):
        service.upstream("d.x", depth=-1)


def test_the_edges_returned_are_those_between_the_nodes_reached(service: LineageService) -> None:
    result = service.upstream("d.x", depth=1)

    assert [(e.source, e.target) for e in result.edges] == [("c.x", "d.x")]


def test_a_filter_column_reaches_the_rows_it_decides_unless_left_out(
    service: LineageService,
) -> None:
    assert "b.k" in reached(service.upstream("c.*"))
    assert "b.k" not in reached(service.upstream("c.*", include_filters=False))
    assert "d.*" in reached(service.downstream("b.k"))
    assert service.downstream("b.k", include_filters=False).reached == ()


def test_a_traversal_says_when_it_crossed_a_guessed_edge(service: LineageService) -> None:
    assert service.upstream("d.*").inferred
    assert not service.upstream("d.x").inferred


def test_a_node_with_no_neighbours_has_empty_results(service: LineageService) -> None:
    assert service.upstream("lonely.z").reached == ()
    assert service.downstream("lonely.z").edges == ()


def test_path_follows_the_flow_and_returns_its_edges(service: LineageService) -> None:
    path = service.path("raw.a", "d.x")

    assert path is not None
    assert path.nodes == ("raw.a", "b.x", "c.x", "d.x")
    assert [e.kind for e in path.edges] == [EdgeKind.TRANSFORM, EdgeKind.DIRECT, EdgeKind.DIRECT]
    assert not path.inferred


def test_there_is_no_path_against_the_flow_or_between_unrelated_nodes(
    service: LineageService,
) -> None:
    assert service.path("d.x", "raw.a") is None
    assert service.path("b.y", "d.x") is None


def test_a_path_can_be_inferred(service: LineageService) -> None:
    path = service.path("b.k", "d.*")

    assert path is not None
    assert path.inferred
    assert service.path("b.k", "d.*", include_filters=False) is None


def test_common_ancestors_are_the_nearest_shared_causes(service: LineageService) -> None:
    # d.x and d.y both come from raw.a; b.x and b.y are each on one side only.
    assert service.common_ancestors(["d.x", "d.y"]) == ("raw.a",)


def test_a_failing_node_that_feeds_the_others_is_their_common_ancestor(
    service: LineageService,
) -> None:
    assert service.common_ancestors(["b.x", "d.x"]) == ("b.x",)


def test_unrelated_nodes_share_nothing(service: LineageService) -> None:
    assert service.common_ancestors(["d.x", "lonely.z"]) == ()
    assert service.common_ancestors([]) == ()


def test_an_unknown_node_is_an_error_that_names_it(service: LineageService) -> None:
    with pytest.raises(UnknownNodeError, match=r"nope\.col"):
        service.upstream("nope.col")
    with pytest.raises(UnknownNodeError, match=r"nope\.col"):
        service.path("raw.a", "nope.col")
    with pytest.raises(UnknownNodeError, match=r"nope\.col"):
        service.common_ancestors(["d.x", "nope.col"])


def test_the_service_reports_the_graph_version(service: LineageService) -> None:
    assert service.version == "v1"
