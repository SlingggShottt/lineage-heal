"""FR-WH-04: domain checks catch implausible price and NAV moves that value checks accept."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from conftest import BuildResult, Workspace

PRICES_CHAIN = (
    "brz_meta__raw_file_versions",
    "brz_nse__cm_bhavcopy",
    "brz_nse__corporate_actions",
    "slv_nse__equity_eod",
    "slv_nse__corporate_actions",
    "gld_equity_daily_prices",
)
NAV_CHAIN = (
    "brz_meta__raw_file_versions",
    "brz_amfi__navall",
    "slv_amfi__nav",
    "gld_mf_nav_daily",
)
SUMMARY_CHAIN = (*PRICES_CHAIN, *NAV_CHAIN[1:], "gld_market_daily_summary")

PRICE_SCALE = "gld_equity_daily_prices_no_change_of_scale"
PRICE_BAND = "gld_equity_daily_prices_no_unexplained_large_move"
NAV_SCALE = "gld_mf_nav_daily_no_change_of_scale"
NAV_BAND = "gld_mf_nav_daily_no_large_move"

SPLIT_5_TO_1 = "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"
DIVIDEND = "Dividend - Rs 2 Per Share"


def days(count: int) -> list[date]:
    return [date(2026, 10, 12) + timedelta(days=offset) for offset in range(count)]


def store_closes(
    workspace: Workspace,
    closes: list[tuple[str, str, str]],
    *,
    ex_date: date | None = None,
    action: tuple[str, str] | None = None,
) -> list[date]:
    """Store a bhavcopy per day for one instrument, from (series, close, previous close) per day.

    A corporate action file is always stored, since the gold model cannot be built without
    one; `action` is (subject, symbol) announced on `ex_date`, otherwise an unrelated dividend.
    """
    stored = days(len(closes))
    for day, (series, close, previous) in zip(stored, closes, strict=True):
        workspace.store_prices({day: [("ABC", series, close, previous)]})
    if action is not None and ex_date is not None:
        workspace.store_actions(ex_date, (action[1], "EQ", action[0]))
    else:
        workspace.store_actions(stored[-1], ("ZZZ", "EQ", DIVIDEND))
    return stored


def steady(count: int, close: str = "100.00") -> list[tuple[str, str, str]]:
    return [("EQ", close, close)] * count


# --- price moves -------------------------------------------------------------------------


def test_an_ordinary_stretch_of_prices_passes_both_tiers(workspace: Workspace) -> None:
    store_closes(workspace, [("EQ", "100.00", "100.00"), ("EQ", "101.00", "100.00")] * 2)

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.statuses[PRICE_SCALE] == "pass"
    assert result.statuses[PRICE_BAND] == "pass"


def test_a_split_is_explained_by_its_adjustment_and_is_not_a_jump(workspace: Workspace) -> None:
    closes = [*steady(2, "500.00"), ("EQ", "100.00", "500.00"), *steady(1, "100.00")]
    store_closes(workspace, closes, ex_date=days(3)[2], action=(SPLIT_5_TO_1, "ABC"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.statuses[PRICE_SCALE] == "pass"
    assert result.statuses[PRICE_BAND] == "pass"


def test_prices_a_hundred_times_too_large_fail_the_build_and_stop_the_summary(
    workspace: Workspace,
) -> None:
    """Every value check accepts the day; only the move from the day before gives it away."""
    store_closes(workspace, [*steady(3), ("EQ", "10000.00", "100.00")])
    workspace.store_navs({date(2026, 10, 12): {1: "10.0000"}})

    result = workspace.build(*SUMMARY_CHAIN)

    assert result.failed() == {PRICE_SCALE}
    assert result.statuses[PRICE_BAND] == "warn"
    assert result.statuses["gld_market_daily_summary"] == "skipped"


def test_prices_a_hundred_times_too_small_fail_too(workspace: Workspace) -> None:
    store_closes(workspace, [*steady(3), ("EQ", "1.00", "100.00")])

    result = workspace.build(*PRICES_CHAIN)

    assert result.failed() == {PRICE_SCALE}


def test_a_move_beyond_the_price_band_only_warns(workspace: Workspace) -> None:
    store_closes(workspace, [*steady(3), ("EQ", "130.00", "100.00")])

    result = workspace.build(*PRICES_CHAIN)

    assert result.success  # a warning does not stop the build
    assert result.statuses[PRICE_BAND] == "warn"
    assert result.statuses[PRICE_SCALE] == "pass"


def test_the_warning_looks_at_the_main_series_only(workspace: Workspace) -> None:
    store_closes(workspace, [*steady(3), ("BE", "130.00", "100.00")])

    result = workspace.build(*PRICES_CHAIN)

    assert result.statuses[PRICE_BAND] == "pass"
    assert result.statuses[PRICE_SCALE] == "pass"  # a 30% move is no change of scale


def test_a_corporate_action_on_the_date_explains_a_large_move(workspace: Workspace) -> None:
    """Rights issues, demergers and buy-backs move prices and are not adjusted for."""
    closes = [*steady(3), ("EQ", "60.00", "100.00")]
    store_closes(workspace, closes, ex_date=days(4)[3], action=("Demerger - Scheme", "ABC"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.statuses[PRICE_BAND] == "pass"


def test_old_dates_are_not_judged(workspace: Workspace) -> None:
    """Only the latest three dates count, so an old odd day cannot block the build for ever."""
    closes = [("EQ", "100.00", "100.00"), ("EQ", "10000.00", "100.00"), *steady(3, "10000.00")]
    store_closes(workspace, closes)

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.statuses[PRICE_SCALE] == "pass"


def test_a_missing_previous_close_is_not_a_jump(workspace: Workspace) -> None:
    store_closes(workspace, [*steady(3), ("EQ", "100.00", "0.00")])

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.statuses[PRICE_SCALE] == "pass"


# --- NAV moves ---------------------------------------------------------------------------


def nav_history(*last_navs: str) -> dict[date, dict[int, str]]:
    """A steady scheme of 10.0 for three days and then the given NAVs, one per day."""
    series = ["10.0000"] * 3 + list(last_navs)
    return {day: {1: nav, 2: "50.0000"} for day, nav in zip(days(len(series)), series, strict=True)}


def nav_result(workspace: Workspace, *last_navs: str) -> BuildResult:
    workspace.store_navs(nav_history(*last_navs))
    return workspace.build(*NAV_CHAIN)


def test_ordinary_nav_movements_pass_both_tiers(workspace: Workspace) -> None:
    result = nav_result(workspace, "10.1000", "10.0500")

    assert result.success
    assert result.statuses[NAV_SCALE] == "pass"
    assert result.statuses[NAV_BAND] == "pass"


def test_a_nav_a_hundred_times_too_large_fails_the_build(workspace: Workspace) -> None:
    result = nav_result(workspace, "1000.0000")

    assert result.failed() == {NAV_SCALE}
    assert result.statuses["gld_mf_nav_daily"] == "success"  # built, then found wanting


def test_a_nav_that_collapses_to_a_small_positive_value_fails_the_build(
    workspace: Workspace,
) -> None:
    result = nav_result(workspace, "1.0000")

    assert result.failed() == {NAV_SCALE}


def test_a_nav_falling_to_zero_is_not_judged(workspace: Workspace) -> None:
    """Segregated portfolios and matured schemes publish zero; that says nothing about scale."""
    result = nav_result(workspace, "0.0000")

    assert result.success
    assert result.statuses[NAV_SCALE] == "pass"
    assert result.statuses[NAV_BAND] == "pass"


def test_a_large_but_possible_nav_fall_only_warns(workspace: Workspace) -> None:
    result = nav_result(workspace, "7.0000")

    assert result.success
    assert result.statuses[NAV_BAND] == "warn"
    assert result.statuses[NAV_SCALE] == "pass"


def test_old_nav_dates_are_not_judged(workspace: Workspace) -> None:
    """The jump to 1000 is four dates back, so it can no longer block the build."""
    result = nav_result(workspace, "1000.0000", "1000.0000", "1000.0000", "1000.0000")

    assert result.success
    assert result.statuses[NAV_SCALE] == "pass"
