"""FR-INV-02: lineage, model SQL and test-failure tools read the run's own records."""

from lineageheal.agents.tools import ToolContext, ToolRegistry, build_registry
from lineageheal.agents.tools.run_tools import MAX_EDGES, MAX_REACHED, MAX_SQL_CHARACTERS
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind
from lineageheal.lineage.service import LineageService


def test_upstream_lists_what_feeds_a_node_nearest_first(registry: ToolRegistry) -> None:
    result = registry.call(
        "get_lineage", {"node": "gld_mf_nav_daily.nav_inr", "direction": "upstream", "depth": 3}
    )

    assert result.ok
    assert [r["node"] for r in result.payload["reached"]] == [
        "slv_amfi__nav.nav_inr",
        "brz_amfi__navall.nav_inr",
        "raw.amfi.navall.nav",
    ]
    assert result.payload["lineage_version"] == "v1"
    assert "slv_amfi__nav.nav_inr -> gld_mf_nav_daily.nav_inr (direct)" in result.payload["edges"]


def test_depth_limits_how_far_the_walk_goes(registry: ToolRegistry) -> None:
    result = registry.call(
        "get_lineage", {"node": "gld_mf_nav_daily.nav_inr", "direction": "upstream", "depth": 1}
    )

    assert [r["node"] for r in result.payload["reached"]] == ["slv_amfi__nav.nav_inr"]


def test_downstream_shows_what_a_node_feeds(registry: ToolRegistry) -> None:
    result = registry.call(
        "get_lineage", {"node": "brz_amfi__navall.nav_inr", "direction": "downstream"}
    )

    assert "gld_mf_nav_daily.nav_inr" in {r["node"] for r in result.payload["reached"]}


def test_filter_edges_can_be_left_out(registry: ToolRegistry) -> None:
    arguments = {"node": "slv_amfi__nav.*", "direction": "upstream"}

    with_filters = registry.call("get_lineage", arguments)
    without = registry.call("get_lineage", {**arguments, "include_filters": False})

    assert "seed_calendar_nse.day" in {r["node"] for r in with_filters.payload["reached"]}
    assert without.payload["reached"] == []


def test_an_unknown_node_comes_back_with_close_matches(registry: ToolRegistry) -> None:
    result = registry.call("get_lineage", {"node": "nav_inr", "direction": "upstream"})

    assert not result.ok
    assert "slv_amfi__nav.nav_inr" in result.payload["error"]


def test_a_depth_beyond_the_limit_is_refused(registry: ToolRegistry) -> None:
    result = registry.call("get_lineage", {"node": "x", "direction": "upstream", "depth": 50})

    assert not result.ok
    assert "depth" in result.payload["error"]


def test_a_wide_graph_is_cut_to_the_limits_and_says_how_much_is_left(
    context: ToolContext,
) -> None:
    sources = [LineageNode(f"raw.t{i}", "c", NodeKind.RAW) for i in range(100)]
    target = LineageNode("m", "c", NodeKind.MODEL)
    edges = tuple(LineageEdge(s.id, target.id, EdgeKind.DIRECT) for s in sources)
    graph = LineageGraph("wide", tuple(sorted([*sources, target])), tuple(sorted(edges)))
    wide = ToolContext(
        lineage=LineageService(graph), manifest=context.manifest, summary=context.summary
    )

    result = build_registry(wide).call("get_lineage", {"node": "m.c", "direction": "upstream"})

    assert len(result.payload["reached"]) == MAX_REACHED
    assert result.payload["reached_omitted"] == 100 - MAX_REACHED
    assert len(result.payload["edges"]) == MAX_EDGES
    assert result.payload["edges_omitted"] == 100 - MAX_EDGES


def test_an_inferred_edge_is_marked_so_the_agent_does_not_trust_its_columns(
    context: ToolContext,
) -> None:
    a, b = LineageNode("raw.a", "*", NodeKind.RAW), LineageNode("m", "*", NodeKind.MODEL)
    guess = LineageGraph("g", (a, b), (LineageEdge(a.id, b.id, EdgeKind.DIRECT, inferred=True),))
    guessed = ToolContext(
        lineage=LineageService(guess), manifest=context.manifest, summary=context.summary
    )

    result = build_registry(guessed).call("get_lineage", {"node": "m.*", "direction": "upstream"})

    assert result.payload["inferred"] is True
    assert result.payload["edges"] == ["raw.a.* -> m.* (direct, inferred)"]


def test_model_sql_gives_the_source_and_the_compiled_text(registry: ToolRegistry) -> None:
    result = registry.call("get_model_sql", {"model": "slv_amfi__nav"})

    assert result.ok
    assert result.payload["path"] == "models/silver/slv_amfi__nav.sql"
    assert "ref('brz_amfi__navall')" in result.payload["sql"]
    assert result.payload["compiled_sql"] == "select nav as nav_inr from bronze.brz_amfi__navall"


def test_a_model_that_was_not_compiled_has_no_compiled_sql(registry: ToolRegistry) -> None:
    result = registry.call("get_model_sql", {"model": "brz_amfi__navall"})

    assert result.payload["compiled_sql"] is None


def test_an_unknown_model_lists_the_known_ones(registry: ToolRegistry) -> None:
    result = registry.call("get_model_sql", {"model": "nope"})

    assert not result.ok
    assert "slv_amfi__nav" in result.payload["error"]


def test_very_long_sql_is_cut(context: ToolContext) -> None:
    node = {"name": "big", "resource_type": "model", "raw_code": "select 1 -- " + "x" * 20_000}
    big = ToolContext(
        lineage=context.lineage, manifest={"nodes": {"m": node}}, summary=context.summary
    )

    result = build_registry(big).call("get_model_sql", {"model": "big"})

    assert len(result.payload["sql"]) < MAX_SQL_CHARACTERS + 20
    assert result.payload["sql"].endswith("[truncated]")


def test_a_failing_test_reports_its_count_message_and_definition(registry: ToolRegistry) -> None:
    result = registry.call("get_test_failure", {"test": "not_null_brz_amfi__navall_nav_inr"})

    assert result.ok
    assert result.payload["failures"] == 1
    assert result.payload["column"] == "nav_inr"
    assert result.payload["definition"]["generic_test"] == "not_null"
    assert result.payload["definition"]["arguments"] == {"column_name": "nav_inr"}
    assert result.payload["definition"]["sql"] is None


def test_a_singular_test_shows_its_sql(registry: ToolRegistry) -> None:
    result = registry.call("get_test_failure", {"test": "assert_gold_untouched"})

    assert result.payload["definition"]["generic_test"] is None
    assert result.payload["definition"]["sql"] == "select 1 where false"


def test_an_unknown_test_lists_the_run_signals(registry: ToolRegistry) -> None:
    result = registry.call("get_test_failure", {"test": "nope"})

    assert not result.ok
    assert "not_null_brz_amfi__navall_nav_inr" in result.payload["error"]
