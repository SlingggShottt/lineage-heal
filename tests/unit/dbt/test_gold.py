"""FR-WH-03: gold gives consumers comparable prices and NAVs and one daily summary of both."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from conftest import BuildResult, Workspace

GOLD_PRICES = "gld_equity_daily_prices"
PRICES_CHAIN = (
    "brz_meta__raw_file_versions",
    "brz_nse__cm_bhavcopy",
    "brz_nse__corporate_actions",
    "slv_nse__equity_eod",
    "slv_nse__corporate_actions",
    GOLD_PRICES,
)
PARSED = "assert_slv_nse__corporate_actions_are_fully_parsed"
NAV_CHAIN = (
    "brz_meta__raw_file_versions",
    "brz_amfi__navall",
    "slv_amfi__nav",
    "gld_mf_nav_daily",
)
SUMMARY_CHAIN = (*PRICES_CHAIN, *NAV_CHAIN[1:], "gld_market_daily_summary")

# Every price row copies one real row, so each carries these totals (see bhavcopy()).
VOLUME_PER_ROW = 1_668_616
TURNOVER_PER_ROW = Decimal("6686808295.40")

DAY_1, DAY_2, DAY_3 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)

SPLIT_5_TO_1 = "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"
SPLIT_10_TO_5 = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"

# What gold says about one instrument on one day: (action factor, adjustment factor,
# adjusted close, daily return in percent)
GoldRow = tuple[Decimal, Decimal, Decimal, Decimal | None]


def gold_rows(result: BuildResult, symbol: str, series: str = "EQ") -> dict[date, GoldRow]:
    """Gold rows of one instrument, by date."""
    rows = result.query(
        "select price_date, action_factor, adjustment_factor, adj_close_price_inr, "
        "daily_return_pct from gold.gld_equity_daily_prices "
        "where symbol = ? and series = ? order by price_date",
        symbol,
        series,
    )
    return {price_date: (*rest,) for price_date, *rest in rows}  # type: ignore[misc]


# --- no change in share count -----------------------------------------------------------


def test_a_dividend_is_flagged_but_does_not_adjust_prices(workspace: Workspace) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "100.00", "100.00")],
            DAY_2: [("ABC", "EQ", "98.00", "100.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", "Dividend - Rs 2 Per Share"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert gold_rows(result, "ABC") == {
        DAY_1: (Decimal(1), Decimal(1), Decimal("100"), Decimal("0")),
        DAY_2: (Decimal(1), Decimal(1), Decimal("98"), Decimal("-2")),
    }
    assert result.query(
        "select price_date, has_corporate_action from gold.gld_equity_daily_prices "
        "order by price_date"
    ) == [(DAY_1, False), (DAY_2, True)]


def test_the_published_prices_are_carried_through_untouched(workspace: Workspace) -> None:
    workspace.store_prices({DAY_1: [("ABC", "EQ", "123.45", "120.00")]})
    workspace.store_actions(DAY_2, ("ABC", "EQ", "Dividend - Rs 2 Per Share"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.query(
        "select open_price_inr, high_price_inr, low_price_inr, close_price_inr, "
        "prev_close_price_inr, volume, turnover_inr, trades from gold.gld_equity_daily_prices"
    ) == result.query(
        "select open_price_inr, high_price_inr, low_price_inr, close_price_inr, "
        "prev_close_price_inr, volume, turnover_inr, trades from silver.slv_nse__equity_eod"
    )


def test_a_zero_previous_close_gives_no_return_instead_of_an_error(workspace: Workspace) -> None:
    workspace.store_prices({DAY_1: [("ABC", "EQ", "100.00", "0.00")]})
    workspace.store_actions(DAY_1, ("XYZ", "EQ", "Dividend - Rs 2 Per Share"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert gold_rows(result, "ABC") == {
        DAY_1: (Decimal(1), Decimal(1), Decimal("100"), None),
    }


# --- splits and bonuses ------------------------------------------------------------------


def test_a_split_restates_earlier_prices_and_the_move_on_the_ex_date_is_not_a_crash(
    workspace: Workspace,
) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "500.00", "500.00"), ("XYZ", "EQ", "50.00", "50.00")],
            DAY_2: [("ABC", "EQ", "100.00", "500.00"), ("XYZ", "EQ", "50.00", "50.00")],
            DAY_3: [("ABC", "EQ", "102.00", "100.00"), ("XYZ", "EQ", "50.00", "50.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", SPLIT_5_TO_1))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert gold_rows(result, "ABC") == {
        DAY_1: (Decimal(1), Decimal("0.2"), Decimal("100"), Decimal("0")),
        DAY_2: (Decimal(5), Decimal(1), Decimal("100"), Decimal("0")),
        DAY_3: (Decimal(1), Decimal(1), Decimal("102"), Decimal("2")),
    }
    # The published feed shows the split as an 80% fall, which is what gold exists to correct.
    assert result.query(
        "select change_pct from silver.slv_nse__equity_eod where symbol = 'ABC' and price_date = ?",
        DAY_2,
    ) == [(Decimal("-80.0000"),)]
    assert {row[:2] for row in gold_rows(result, "XYZ").values()} == {(Decimal(1), Decimal(1))}


def test_a_bonus_issue_adjusts_by_the_enlarged_share_count(workspace: Workspace) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ONE", "EQ", "200.00", "200.00"), ("SEVEN", "EQ", "100.00", "100.00")],
            DAY_2: [("ONE", "EQ", "100.00", "200.00"), ("SEVEN", "EQ", "70.00", "100.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ONE", "EQ", "Bonus 1:1"), ("SEVEN", "EQ", "Bonus 3:7"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert gold_rows(result, "ONE") == {
        DAY_1: (Decimal(1), Decimal("0.5"), Decimal("100"), Decimal("0")),
        DAY_2: (Decimal(2), Decimal(1), Decimal("100"), Decimal("0")),
    }
    # 3 new shares for every 7 held turn 7 shares into 10.
    assert gold_rows(result, "SEVEN") == {
        DAY_1: (Decimal(1), Decimal("0.7"), Decimal("70"), Decimal("0")),
        DAY_2: (Decimal("1.4285714286"), Decimal(1), Decimal("70"), Decimal("0")),
    }


def test_changes_on_different_dates_compound_for_the_days_before_both(
    workspace: Workspace,
) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "1000.00", "1000.00")],
            DAY_2: [("ABC", "EQ", "200.00", "1000.00")],
            DAY_3: [("ABC", "EQ", "100.00", "200.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", SPLIT_5_TO_1))
    workspace.store_actions(DAY_3, ("ABC", "EQ", "Bonus 1:1"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert gold_rows(result, "ABC") == {
        DAY_1: (Decimal(1), Decimal("0.1"), Decimal("100"), Decimal("0")),
        DAY_2: (Decimal(5), Decimal("0.5"), Decimal("100"), Decimal("0")),
        DAY_3: (Decimal(2), Decimal(1), Decimal("100"), Decimal("0")),
    }


def test_two_changes_on_the_same_ex_date_compound(workspace: Workspace) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "400.00", "400.00")],
            DAY_2: [("ABC", "EQ", "100.00", "400.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", SPLIT_10_TO_5), ("ABC", "EQ", "Bonus 1:1"))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert gold_rows(result, "ABC") == {
        DAY_1: (Decimal(1), Decimal("0.25"), Decimal("100"), Decimal("0")),
        DAY_2: (Decimal(4), Decimal(1), Decimal("100"), Decimal("0")),
    }


def test_an_announcement_listed_under_two_series_counts_once_and_covers_both(
    workspace: Workspace,
) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "500.00", "500.00"), ("ABC", "BE", "495.00", "495.00")],
            DAY_2: [("ABC", "EQ", "100.00", "500.00"), ("ABC", "BE", "99.00", "495.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", SPLIT_5_TO_1), ("ABC", "BE", SPLIT_5_TO_1))

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.failed() == set()  # in particular, no row was multiplied by the join
    for series in ("EQ", "BE"):
        rows = gold_rows(result, "ABC", series)
        assert [row[:2] for row in rows.values()] == [
            (Decimal(1), Decimal("0.2")),
            (Decimal(5), Decimal(1)),
        ]
    assert result.query("select count(*) from gold.gld_equity_daily_prices") == [(4,)]


def test_actions_outside_the_price_history_change_nothing(workspace: Workspace) -> None:
    workspace.store_prices(
        {
            DAY_2: [("ABC", "EQ", "100.00", "100.00")],
            DAY_3: [("ABC", "EQ", "101.00", "100.00")],
        },
    )
    workspace.store_actions(DAY_1, ("ABC", "EQ", SPLIT_5_TO_1))  # before every price
    workspace.store_actions(DAY_2, ("GHOST", "EQ", SPLIT_5_TO_1))  # no prices for it

    result = workspace.build(*PRICES_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert {row[:2] for row in gold_rows(result, "ABC").values()} == {(Decimal(1), Decimal(1))}
    assert result.query("select count(*) from gold.gld_equity_daily_prices") == [(2,)]


# --- gating ------------------------------------------------------------------------------


def test_a_split_whose_numbers_cannot_be_read_stops_the_build_before_gold(
    workspace: Workspace,
) -> None:
    """Silver fails loudly, so gold never publishes prices that quietly skipped the split."""
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "500.00", "500.00")],
            DAY_2: [("ABC", "EQ", "100.00", "500.00")],
        },
    )
    workspace.store_actions(
        DAY_2,
        ("ABC", "EQ", "Face Value Split (Sub-Division) - From Rs 5/- Per Share"),
    )

    result = workspace.build(*PRICES_CHAIN)

    assert result.failed() == {PARSED}
    assert result.statuses[GOLD_PRICES] == "skipped"


# --- mutual fund NAVs --------------------------------------------------------------------

# What gold says about one scheme on one day: (previous date, previous NAV, days since, change)
NavRow = tuple[date | None, Decimal | None, int | None, Decimal | None]


def nav_rows(result: BuildResult, scheme_code: int) -> dict[date, NavRow]:
    rows = result.query(
        "select nav_date, prev_nav_date, prev_nav_inr, days_since_prev_nav, nav_change_pct "
        "from gold.gld_mf_nav_daily where scheme_code = ? order by nav_date",
        scheme_code,
    )
    return {nav_date: (*rest,) for nav_date, *rest in rows}  # type: ignore[misc]


def test_each_nav_is_compared_with_the_same_schemes_previous_nav(workspace: Workspace) -> None:
    workspace.store_navs(
        {
            DAY_1: {1: "100.0000", 2: "50.0000"},
            DAY_2: {1: "102.0000", 2: "50.0000"},
            DAY_3: {1: "101.4900", 2: "55.0000"},
        },
    )

    result = workspace.build(*NAV_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert nav_rows(result, 1) == {
        DAY_1: (None, None, None, None),
        DAY_2: (DAY_1, Decimal("100"), 1, Decimal("2")),
        DAY_3: (DAY_2, Decimal("102"), 1, Decimal("-0.5")),
    }
    assert nav_rows(result, 2)[DAY_3] == (DAY_2, Decimal("50"), 1, Decimal("10"))


def test_a_gap_in_the_reports_is_visible_in_days_since_the_previous_nav(
    workspace: Workspace,
) -> None:
    workspace.store_navs(
        {DAY_1: {1: "100.0000"}, DAY_2: {2: "10.0000"}, DAY_3: {1: "110.0000"}},
    )

    result = workspace.build(*NAV_CHAIN)

    assert result.success
    # Scheme 1 has no NAV on DAY_2, so its previous NAV is from two days earlier.
    assert nav_rows(result, 1)[DAY_3] == (DAY_1, Decimal("100"), 2, Decimal("10"))
    assert nav_rows(result, 2) == {DAY_2: (None, None, None, None)}


def test_a_zero_nav_gives_no_change_from_it_and_a_fall_to_zero_is_reported(
    workspace: Workspace,
) -> None:
    workspace.store_navs(
        {
            DAY_1: {1: "0.0000", 2: "10.0000"},
            DAY_2: {1: "10.0000", 2: "0.0000"},
        },
    )

    result = workspace.build(*NAV_CHAIN)

    assert result.success
    assert nav_rows(result, 1)[DAY_2] == (DAY_1, Decimal("0"), 1, None)
    assert nav_rows(result, 2)[DAY_2] == (DAY_1, Decimal("10"), 1, Decimal("-100"))


def test_published_navs_are_carried_through_untouched(workspace: Workspace) -> None:
    workspace.store_navs({DAY_1: {1: "100.1234", 2: "7.0001"}, DAY_2: {1: "100.5000"}})

    result = workspace.build(*NAV_CHAIN)

    assert result.success
    assert result.query(
        "select scheme_code, nav_date, scheme_name, plan, option_type, nav_inr "
        "from gold.gld_mf_nav_daily order by 1, 2"
    ) == result.query(
        "select scheme_code, nav_date, scheme_name, plan, option_type, nav_inr "
        "from silver.slv_amfi__nav order by 1, 2"
    )


# --- the daily summary across both feeds -------------------------------------------------


def test_the_summary_totals_each_feed_and_keeps_a_date_with_only_one(
    workspace: Workspace,
) -> None:
    workspace.store_prices(
        {
            DAY_1: [
                ("AAA", "EQ", "110.00", "100.00"),  # +10
                ("BBB", "EQ", "95.00", "100.00"),  # -5
                ("CCC", "EQ", "102.00", "100.00"),  # +2
                ("FFF", "EQ", "100.00", "100.00"),  # unchanged
                ("DDD", "EQ", "100.00", "0.00"),  # no previous close, so no return
                # Other series count in the totals but not in breadth or the median. They include a
                # fall and two large rises, so counting them would change every one of those.
                ("EEE", "BE", "50.00", "40.00"),
                ("HHH", "BE", "45.00", "50.00"),
                ("JJJ", "BE", "65.00", "50.00"),
            ],
            DAY_3: [("GGG", "EQ", "100.00", "100.00")],
        },
    )
    workspace.store_actions(DAY_3, ("ZZZ", "EQ", "Dividend - Rs 2 Per Share"))
    workspace.store_navs(
        {
            DAY_1: {1: "100.0000", 2: "50.0000"},
            DAY_2: {1: "101.0000", 2: "51.0000"},  # no NSE session on this date
        },
    )

    result = workspace.build(*SUMMARY_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert result.query(
        "select summary_date, has_equity_data, has_nav_data, equity_instruments, equity_volume, "
        "equity_turnover_inr, eq_advancers, eq_decliners, eq_unchanged, eq_median_return_pct, "
        "nav_schemes_published, nav_median_change_pct "
        "from gold.gld_market_daily_summary order by summary_date"
    ) == [
        (
            DAY_1, True, True, 8, 8 * VOLUME_PER_ROW, 8 * TURNOVER_PER_ROW,
            2, 1, 1, Decimal("1"), 2, None,
        ),
        (DAY_2, False, True, None, None, None, None, None, None, None, 2, Decimal("1.5")),
        (
            DAY_3, True, False, 1, VOLUME_PER_ROW, TURNOVER_PER_ROW,
            0, 0, 1, Decimal("0"), None, None,
        ),
    ]  # fmt: skip


def test_a_split_day_is_not_counted_as_a_decliner(workspace: Workspace) -> None:
    workspace.store_prices(
        {
            DAY_1: [("ABC", "EQ", "500.00", "500.00")],
            DAY_2: [("ABC", "EQ", "100.00", "500.00")],
        },
    )
    workspace.store_actions(DAY_2, ("ABC", "EQ", SPLIT_5_TO_1))
    workspace.store_navs({DAY_1: {1: "100.0000"}})

    result = workspace.build(*SUMMARY_CHAIN)

    assert result.success
    assert result.query(
        "select eq_advancers, eq_decliners, eq_unchanged from gold.gld_market_daily_summary "
        "where summary_date = ?",
        DAY_2,
    ) == [(0, 0, 1)]


def test_a_failing_feed_stops_the_summary_but_not_the_other_feeds_gold_model(
    workspace: Workspace,
) -> None:
    """The summary reads both feeds, so it waits for both; the healthy side still builds."""
    workspace.store_prices({DAY_1: [("ABC", "EQ", "100.00", "100.00")]})
    workspace.store_actions(DAY_1, ("ZZZ", "EQ", "Dividend - Rs 2 Per Share"))
    workspace.store_navs({DAY_1: {1: "100.0000", 2: ""}})  # one NAV is blank

    result = workspace.build(*SUMMARY_CHAIN)

    assert result.failed() == {"not_null_brz_amfi__navall_nav_inr"}
    assert result.statuses[GOLD_PRICES] == "success"
    assert result.statuses["gld_market_daily_summary"] == "skipped"
    assert result.statuses["gld_mf_nav_daily"] == "skipped"
