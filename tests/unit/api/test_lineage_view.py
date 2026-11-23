"""FR-UI-04: the slice of the graph an incident draws."""

import pytest
from detector_graphs import market_graph

from lineageheal.api.lineage_view import MAX_TARGETS, build_view
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind
from lineageheal.lineage.service import LineageService, UnknownNodeError

SERVICE = LineageService(market_graph())


def roles(view: object) -> dict[str, str]:
    return {n.id: n.role for n in view.nodes}  # type: ignore[attr-defined]


def test_the_slice_holds_the_paths_from_the_root_to_what_broke_and_nothing_else() -> None:
    view = build_view(
        SERVICE, "raw.amfi.navall.nav", ["slv_amfi__nav.nav_inr"], ["gld_mf_nav_daily"]
    )

    assert roles(view) == {
        "raw.amfi.navall.nav": "root",
        "brz_amfi__navall.nav_inr": "path",
        "slv_amfi__nav.nav_inr": "affected",
        "gld_mf_nav_daily.nav_inr": "affected",
    }
    assert view.version == "v1"
    assert view.unreached == ()
    assert not view.truncated
    assert [(e.source, e.target) for e in view.edges] == [
        ("brz_amfi__navall.nav_inr", "slv_amfi__nav.nav_inr"),
        ("raw.amfi.navall.nav", "brz_amfi__navall.nav_inr"),
        ("slv_amfi__nav.nav_inr", "gld_mf_nav_daily.nav_inr"),
    ]


def test_an_affected_table_is_followed_to_its_columns_not_its_row_set() -> None:
    view = build_view(SERVICE, "raw.amfi.navall.nav", [], ["gld_mf_nav_daily"])

    assert roles(view)["gld_mf_nav_daily.nav_inr"] == "affected"
    assert "gld_mf_nav_daily.*" not in roles(view)


def test_a_table_the_root_does_not_feed_is_reported_by_name() -> None:
    view = build_view(SERVICE, "raw.amfi.navall.nav", [], ["gld_equity_daily_prices"])

    assert view.unreached == ("gld_equity_daily_prices",)
    assert roles(view) == {"raw.amfi.navall.nav": "root"}


def test_every_node_says_what_kind_of_table_it_is_in() -> None:
    view = build_view(SERVICE, "raw.amfi.navall.nav", ["gld_mf_nav_daily.nav_inr"], [])

    kinds = {n.id: n.kind for n in view.nodes}
    assert kinds["raw.amfi.navall.nav"] == "raw"
    assert kinds["gld_mf_nav_daily.nav_inr"] == "model"
    assert view.edges[0].kind is EdgeKind.DIRECT


def test_an_affected_node_the_root_does_not_feed_is_reported_not_drawn() -> None:
    view = build_view(
        SERVICE,
        "raw.amfi.navall.nav",
        ["gld_mf_nav_daily.nav_inr", "slv_nse__equity_eod.close_price_inr"],
        [],
    )

    assert view.unreached == ("slv_nse__equity_eod.close_price_inr",)
    assert "slv_nse__equity_eod.close_price_inr" not in roles(view)


def test_columns_the_graph_version_does_not_know_are_ignored() -> None:
    view = build_view(SERVICE, "raw.amfi.navall.nav", ["gone.column"], ["gone_table"])

    assert roles(view) == {"raw.amfi.navall.nav": "root"}
    assert not view.unreached


def test_a_root_that_is_not_in_this_version_is_an_error() -> None:
    with pytest.raises(UnknownNodeError):
        build_view(SERVICE, "raw.amfi.navall.renamed", [], [])


def test_a_very_wide_incident_is_cut_and_says_so() -> None:
    leaves = [f"gld.col{i}" for i in range(MAX_TARGETS + 5)]
    nodes = [LineageNode("raw", "id", NodeKind.RAW)] + [
        LineageNode("gld", f"col{i}", NodeKind.MODEL) for i in range(MAX_TARGETS + 5)
    ]
    edges = [LineageEdge("raw.id", leaf, EdgeKind.DIRECT) for leaf in leaves]
    service = LineageService(LineageGraph("wide", tuple(nodes), tuple(edges)))

    view = build_view(service, "raw.id", leaves, [])

    assert view.truncated
    assert len(view.nodes) == MAX_TARGETS + 1
