"""FR-WH-05: monitors catch partial files, changes of scale and late data that value checks miss."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from conftest import BuildResult, Price, Workspace

EOD_CHAIN = ("brz_meta__raw_file_versions", "brz_nse__cm_bhavcopy", "slv_nse__equity_eod")
NAV_CHAIN = ("brz_meta__raw_file_versions", "brz_amfi__navall", "slv_amfi__nav")
MONITORS = ("volume_anomaly", "distribution_anomaly", "fresh_within")

MONDAY = date(2026, 9, 28)
FULL_DAY = 6  # instruments in an ordinary day's bhavcopy


def weekdays(count: int, *, skip: tuple[int, ...] = ()) -> list[date]:
    """Consecutive trading days from a Monday; `skip` lists positions that are holidays."""
    days: list[date] = []
    day = MONDAY
    while len(days) < count + len(skip):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return [d for position, d in enumerate(days) if position not in skip]


def day_prices(instruments: int, scale: int = 1) -> list[Price]:
    """Prices for some instruments, each valid on its own; the scale models a change of unit."""
    return [
        (f"S{i}", "EQ", f"{(100 + i) * scale}.00", f"{(100 + i) * scale}.00")
        for i in range(instruments)
    ]


def monitor_statuses(result: BuildResult) -> dict[str, str]:
    """The status of each monitor that ran, by kind. Each chain has one of each kind."""
    return {
        kind: status
        for name, status in result.statuses.items()
        for kind in MONITORS
        if name.startswith(f"{kind}_")
    }


# --- NSE end-of-day prices ---------------------------------------------------------------


def test_a_normal_history_passes_every_monitor(workspace: Workspace) -> None:
    workspace.store_prices({day: day_prices(FULL_DAY) for day in weekdays(8)})

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert result.failed() == set()
    # They ran and judged: a monitor that was skipped would not appear here.
    assert monitor_statuses(result) == {
        "volume_anomaly": "pass",
        "distribution_anomaly": "pass",
        "fresh_within": "pass",
    }


def test_a_partial_file_is_caught_by_the_volume_monitor_alone(workspace: Workspace) -> None:
    days = weekdays(8)
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(FULL_DAY // 2)})  # every row in it is valid

    result = workspace.build(*EOD_CHAIN)

    assert monitor_statuses(result)["volume_anomaly"] == "fail"
    assert monitor_statuses(result)["distribution_anomaly"] == "pass"
    assert {name.split("_slv_")[0] for name in result.failed()} == {"volume_anomaly"}


def test_prices_in_the_wrong_unit_pass_every_value_check_and_are_caught_by_the_monitor(
    workspace: Workspace,
) -> None:
    days = weekdays(8)
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(FULL_DAY, scale=100)})  # paise, not rupees

    result = workspace.build(*EOD_CHAIN)

    # Bronze and silver checks, which look at one row at a time, all pass.
    assert {name.split("_slv_")[0] for name in result.failed()} == {"distribution_anomaly"}
    assert monitor_statuses(result)["volume_anomaly"] == "pass"


def test_a_holiday_is_a_gap_and_not_a_zero(workspace: Workspace) -> None:
    """Two missing weekdays must not stretch the spread and blind the volume monitor."""
    workspace.store_prices({day: day_prices(FULL_DAY) for day in weekdays(8, skip=(2, 6))})

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert monitor_statuses(result)["volume_anomaly"] == "pass"


def test_a_gap_does_not_blind_the_monitor_to_a_partial_day_after_it(
    workspace: Workspace,
) -> None:
    days = weekdays(8, skip=(2, 3))
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(FULL_DAY // 2)})

    result = workspace.build(*EOD_CHAIN)

    assert monitor_statuses(result)["volume_anomaly"] == "fail"


def test_an_old_outlier_is_neither_reported_again_nor_distorts_later_days(
    workspace: Workspace,
) -> None:
    """Only the latest dates are judged, and the baseline is a median, not an average.

    The outlier has enough history to be judged, so it would fail if old dates were judged. An
    average would make it drag the baseline up and fail the ordinary days after it.
    """
    days = weekdays(10)
    prices = {day: day_prices(FULL_DAY) for day in days}
    prices[days[6]] = day_prices(60)
    workspace.store_prices(prices)

    result = workspace.build(*EOD_CHAIN)

    assert monitor_statuses(result)["volume_anomaly"] == "pass"


def test_a_duplicated_file_is_caught_as_well_as_a_partial_one(workspace: Workspace) -> None:
    days = weekdays(8)
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(FULL_DAY * 2)})

    result = workspace.build(*EOD_CHAIN)

    assert monitor_statuses(result)["volume_anomaly"] == "fail"


def test_nothing_is_judged_until_the_minimum_history_exists(workspace: Workspace) -> None:
    """Four earlier dates are one fewer than the five needed, and a date never counts itself."""
    days = weekdays(5)
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(1)})  # a large drop, but too little to compare

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert monitor_statuses(result)["volume_anomaly"] == "pass"


@pytest.mark.parametrize("years_away", [-1, 1])
def test_the_result_does_not_depend_on_the_clock(workspace: Workspace, years_away: int) -> None:
    """The same history a year earlier or later gives the same verdict: dates, not 'now'."""
    days = [day + timedelta(days=364 * years_away) for day in weekdays(8)]
    workspace.store_prices({day: day_prices(FULL_DAY) for day in days[:-1]})
    workspace.store_prices({days[-1]: day_prices(FULL_DAY // 2)})

    result = workspace.build(*EOD_CHAIN)

    assert monitor_statuses(result)["volume_anomaly"] == "fail"


# --- freshness ---------------------------------------------------------------------------


def test_freshness_is_judged_only_when_the_run_supplies_its_date(workspace: Workspace) -> None:
    workspace.store_prices({day: day_prices(FULL_DAY) for day in weekdays(3)})
    last = weekdays(3)[-1]

    unknown_date = workspace.build(*EOD_CHAIN)
    on_time = workspace.build(*EOD_CHAIN, variables={"as_of_date": str(last + timedelta(days=5))})
    late = workspace.build(*EOD_CHAIN, variables={"as_of_date": str(last + timedelta(days=6))})

    assert monitor_statuses(unknown_date)["fresh_within"] == "pass"  # nothing to compare with
    assert monitor_statuses(on_time)["fresh_within"] == "pass"
    # A warning: the table that did arrive still feeds the layers downstream.
    assert monitor_statuses(late)["fresh_within"] == "warn"
    assert late.success


# --- AMFI NAVs ---------------------------------------------------------------------------


def calendar_days(count: int) -> list[date]:
    return [MONDAY + timedelta(days=offset) for offset in range(count)]


def test_weekend_reports_are_judged_apart_from_weekday_ones(workspace: Workspace) -> None:
    """AMFI publishes few schemes, with very different NAVs, at weekends, and both are normal."""
    reports = {}
    for day in calendar_days(21):
        if day.weekday() >= 5:
            reports[day] = dict.fromkeys(range(3), "1000.0000")
        else:
            reports[day] = dict.fromkeys(range(20), "20.0000")
    workspace.store_navs(reports)

    result = workspace.build(*NAV_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert monitor_statuses(result)["volume_anomaly"] == "pass"
    assert monitor_statuses(result)["distribution_anomaly"] == "pass"


def test_a_bad_report_stops_the_gold_table_that_reads_it(workspace: Workspace) -> None:
    days = weekdays(8)
    reports = {day: dict.fromkeys(range(20), "20.0000") for day in days}
    reports[days[-1]] = dict.fromkeys(range(8), "20.0000")
    workspace.store_navs(reports)

    result = workspace.build(*NAV_CHAIN, "gld_mf_nav_daily")

    assert monitor_statuses(result)["volume_anomaly"] == "fail"
    assert result.statuses["gld_mf_nav_daily"] == "skipped"
