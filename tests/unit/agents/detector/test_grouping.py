"""FR-DET-02, FR-DET-03: signals with a shared upstream cause become one group."""

from __future__ import annotations

from detector_graphs import failing, market_graph

from lineageheal.agents.detector.grouping import group_signals, node_of
from lineageheal.core.signals import Severity, Signal, SignalKind

AMFI_BRONZE = ("brz_amfi__navall", "nav_inr")
AMFI_SILVER = ("slv_amfi__nav", "nav_inr")
AMFI_GOLD = ("gld_mf_nav_daily", "nav_inr")
NSE_SILVER = ("slv_nse__equity_eod", "close_price_inr")


def test_failures_along_one_chain_are_one_group_rooted_at_the_nearest_shared_node() -> None:
    signals = [failing(*AMFI_BRONZE), failing(*AMFI_SILVER, check="volume_anomaly")]

    [group] = group_signals(signals, market_graph())

    assert group.root_node == "brz_amfi__navall.nav_inr"
    assert len(group.signals) == 2


def test_failures_in_unrelated_feeds_are_separate_groups() -> None:
    signals = [failing(*AMFI_SILVER), failing(*NSE_SILVER)]

    groups = group_signals(signals, market_graph())

    assert [g.root_node for g in groups] == [
        "slv_amfi__nav.nav_inr",
        "slv_nse__equity_eod.close_price_inr",
    ]


def test_a_shared_calendar_filter_does_not_join_unrelated_failures() -> None:
    signals = [
        failing("slv_amfi__nav", None, kind=SignalKind.MODEL_ERROR, check="model"),
        failing("slv_nse__equity_eod", None, kind=SignalKind.MODEL_ERROR, check="model"),
    ]

    assert len(group_signals(signals, market_graph())) == 2


def test_the_depth_limit_decides_how_far_a_shared_cause_may_be() -> None:
    signals = [failing(*AMFI_BRONZE), failing(*AMFI_GOLD)]

    assert len(group_signals(signals, market_graph(), depth=1)) == 2
    assert len(group_signals(signals, market_graph(), depth=3)) == 1


def test_a_signal_that_cannot_be_placed_is_its_own_group_with_no_root() -> None:
    run_error = failing(None, None, kind=SignalKind.RUN_ERROR, check="run", name="dbt")
    signals = [failing(*AMFI_BRONZE), run_error]

    groups = group_signals(signals, market_graph())

    assert [g.root_node for g in groups] == ["brz_amfi__navall.nav_inr", None]
    assert groups[1].signals == (run_error,)


def test_a_group_lists_what_it_affects_including_gold_tables_downstream() -> None:
    [group] = group_signals([failing(*AMFI_BRONZE)], market_graph())

    assert group.columns == ("brz_amfi__navall.nav_inr",)
    assert group.gold_tables == ("gld_mf_nav_daily",)
    # What the failure reaches downstream, not the raw file above it.
    assert set(group.models) == {
        "brz_amfi__navall",
        "slv_amfi__nav",
        "gld_mf_nav_daily",
    }


def test_a_group_of_warnings_is_a_warning_and_any_error_makes_it_an_error() -> None:
    warn = failing(*AMFI_SILVER, severity=Severity.WARN, kind=SignalKind.TEST_WARNING)
    error = failing(*AMFI_BRONZE)

    assert group_signals([warn], market_graph())[0].severity is Severity.WARN
    assert group_signals([warn, error], market_graph())[0].severity is Severity.ERROR


def test_a_column_missing_from_the_graph_falls_back_to_the_models_row_set() -> None:
    known = {n.id for n in market_graph().nodes}

    assert node_of(failing("slv_amfi__nav", "renamed_away"), known) == "slv_amfi__nav.*"
    assert node_of(failing("not_a_model", "x"), known) is None


def test_a_failed_download_sits_on_the_raw_files_row_set() -> None:
    known = {n.id for n in market_graph().nodes}
    download = Signal(
        kind=SignalKind.INGESTION_ERROR,
        severity=Severity.ERROR,
        run_id="run-1",
        detected_at=failing(None, None).detected_at,
        name="amfi/navall/2026-10-15",
        check_type="ingestion",
    )

    assert node_of(download, known) == "raw.amfi.navall.*"


def test_the_same_signals_always_give_the_same_groups_in_the_same_order() -> None:
    signals = [failing(*NSE_SILVER), failing(*AMFI_BRONZE), failing(*AMFI_SILVER)]

    forward = group_signals(signals, market_graph())
    backward = group_signals(list(reversed(signals)), market_graph())

    assert [g.root_node for g in forward] == [g.root_node for g in backward]
    assert [sorted(s.name for s in g.signals) for g in forward] == [
        sorted(s.name for s in g.signals) for g in backward
    ]
