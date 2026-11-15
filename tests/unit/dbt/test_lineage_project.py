"""FR-LIN-02: the lineage of the real dbt project is complete and exact.

A model that cannot be traced would silently become a table-level guess, so this test fails if
any edge of the real project is inferred.
"""

from __future__ import annotations

from typing import Any

import pytest

from lineageheal.lineage.builder import build_lineage
from lineageheal.lineage.graph import LineageGraph
from lineageheal.lineage.service import LineageService


@pytest.fixture(scope="module")
def graph(compiled_manifest: dict[str, Any]) -> LineageGraph:
    return build_lineage(compiled_manifest)


def sources_of(graph: LineageGraph, target: str) -> dict[str, str]:
    return {e.source: e.kind.value for e in graph.edges if e.target == target}


def test_no_model_falls_back_to_a_guess(graph: LineageGraph) -> None:
    assert [e for e in graph.edges if e.inferred] == []


def test_every_edge_joins_two_known_nodes(graph: LineageGraph) -> None:
    ids = {n.id for n in graph.nodes}
    assert [e for e in graph.edges if e.source not in ids or e.target not in ids] == []


def test_a_nav_traces_from_the_published_report_column_to_gold(graph: LineageGraph) -> None:
    assert sources_of(graph, "brz_amfi__navall.nav_inr") == {
        "raw.amfi.navall.Net Asset Value": "transform"
    }
    assert sources_of(graph, "slv_amfi__nav.nav_inr") == {"brz_amfi__navall.nav_inr": "direct"}
    assert sources_of(graph, "gld_mf_nav_daily.nav_inr") == {"slv_amfi__nav.nav_inr": "direct"}


def test_gold_counts_are_aggregates_of_the_price_columns(graph: LineageGraph) -> None:
    upstream = sources_of(graph, "gld_market_daily_summary.eq_advancers")

    assert upstream["gld_equity_daily_prices.daily_return_pct"] == "aggregate"


def test_a_split_adjusted_price_depends_on_corporate_actions(graph: LineageGraph) -> None:
    upstream = sources_of(graph, "gld_equity_daily_prices.adj_close_price_inr")

    assert upstream["slv_nse__equity_eod.close_price_inr"] == "transform"
    assert "slv_nse__corporate_actions.split_old_face_value_inr" in upstream


def test_the_version_is_stable_across_builds(graph: LineageGraph) -> None:
    assert len(graph.version) == 16


def test_the_raw_columns_behind_a_gold_nav_are_found_by_traversal(graph: LineageGraph) -> None:
    service = LineageService(graph)

    upstream = service.upstream("gld_mf_nav_daily.nav_inr", include_filters=False)

    assert {r.node for r in upstream.reached if r.node.startswith("raw.")} == {
        "raw.amfi.navall.Net Asset Value"
    }
    assert not upstream.inferred


def test_the_summary_figures_share_a_cause_in_the_price_table(graph: LineageGraph) -> None:
    service = LineageService(graph)

    shared = service.common_ancestors(
        ["gld_market_daily_summary.eq_advancers", "gld_market_daily_summary.eq_decliners"]
    )

    assert "gld_equity_daily_prices.daily_return_pct" in shared
