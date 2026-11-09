"""FR-ING-07: the calendar separates a holiday from a missing file.

Dates are real ones from the NSE calendar seed: Friday 2 October 2026 is a holiday and Sunday
8 November 2026 is a Diwali trading session.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from conftest import BuildResult, Workspace

CHAIN = ("brz_meta__raw_file_versions", "seed_calendar_nse", "slv_meta__calendar")
CHECK = "file_present_when_expected_"

HOLIDAY = date(2026, 10, 2)  # Friday, Gandhi Jayanti
DIWALI_SESSION = date(2026, 11, 8)  # Sunday, Muhurat trading


def days(start: date, end: date, *, skip: tuple[date, ...] = ()) -> list[date]:
    """Every calendar date from start to end inclusive, without the skipped ones."""
    count = (end - start).days + 1
    return [d for d in (start + timedelta(days=n) for n in range(count)) if d not in skip]


def store_nse_days(workspace: Workspace, dates: list[date]) -> None:
    workspace.store_prices({d: [("S0", "EQ", "100.00", "100.00")] for d in dates})


def store_amfi_days(workspace: Workspace, dates: list[date]) -> None:
    workspace.store_navs({d: {1: "10.0000"} for d in dates})


def check_status(result: BuildResult) -> str:
    return next(status for name, status in result.statuses.items() if name.startswith(CHECK))


def missing(result: BuildResult) -> list[tuple[object, ...]]:
    """What the check reports: feed, dataset, date and why a file was expected."""
    return result.query(
        "select source, dataset, cast(calendar_date as varchar), expectation "
        "from silver.slv_meta__calendar where file_expected and not file_present "
        "order by all"
    )


def test_a_holiday_and_a_weekend_without_files_are_not_missing(workspace: Workspace) -> None:
    # Thursday 1 to Tuesday 6 October: the Friday holiday and the weekend have no file.
    trading = days(
        date(2026, 10, 1), date(2026, 10, 6), skip=(HOLIDAY, date(2026, 10, 3), date(2026, 10, 4))
    )
    store_nse_days(workspace, trading)

    result = workspace.build(*CHAIN)

    assert check_status(result) == "pass"
    assert missing(result) == []
    kinds = dict(
        result.query(
            "select cast(calendar_date as varchar), expectation from silver.slv_meta__calendar "
            "where dataset = 'cm_bhavcopy'"
        )
    )
    assert kinds["2026-10-02"] == "holiday"
    assert kinds["2026-10-03"] == "weekend"
    assert kinds["2026-10-05"] == "weekday"


def test_a_trading_day_without_a_file_is_reported_with_its_date(workspace: Workspace) -> None:
    gap = date(2026, 10, 1)
    store_nse_days(workspace, [date(2026, 9, 30), date(2026, 10, 5), date(2026, 10, 6)])

    result = workspace.build(*CHAIN)

    # A warning: the days that did arrive still feed the layers downstream.
    assert check_status(result) == "warn"
    assert result.success
    assert missing(result) == [("nse", "cm_bhavcopy", str(gap), "weekday")]


def test_a_weekend_session_without_a_file_is_missing(workspace: Workspace) -> None:
    store_nse_days(workspace, [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)])

    result = workspace.build(*CHAIN)

    assert check_status(result) == "warn"
    assert missing(result) == [("nse", "cm_bhavcopy", str(DIWALI_SESSION), "special_session")]


def test_amfi_is_expected_every_day_including_weekends(workspace: Workspace) -> None:
    saturday = date(2026, 10, 3)
    store_amfi_days(workspace, days(date(2026, 10, 1), date(2026, 10, 6), skip=(saturday,)))

    result = workspace.build(*CHAIN)

    assert missing(result) == [("amfi", "navall", str(saturday), "every_day")]


def test_judging_starts_at_each_feeds_first_stored_file(workspace: Workspace) -> None:
    """A backfill that has not run is not a run of missing files."""
    store_nse_days(workspace, [date(2026, 10, 5), date(2026, 10, 6)])
    store_amfi_days(workspace, days(date(2026, 10, 1), date(2026, 10, 6)))

    result = workspace.build(*CHAIN)

    first = dict(
        result.query(
            "select dataset, cast(min(calendar_date) as varchar) "
            "from silver.slv_meta__calendar group by dataset"
        )
    )
    assert first == {"cm_bhavcopy": "2026-10-05", "navall": "2026-10-01"}
    assert missing(result) == []


def test_a_feed_that_is_behind_the_others_is_caught_without_a_run_date(
    workspace: Workspace,
) -> None:
    store_amfi_days(workspace, days(date(2026, 10, 5), date(2026, 10, 8)))
    store_nse_days(workspace, [date(2026, 10, 5), date(2026, 10, 6)])

    result = workspace.build(*CHAIN)

    assert missing(result) == [
        ("nse", "cm_bhavcopy", "2026-10-07", "weekday"),
        ("nse", "cm_bhavcopy", "2026-10-08", "weekday"),
    ]


def test_the_run_date_extends_judging_past_the_newest_file(workspace: Workspace) -> None:
    store_nse_days(workspace, [date(2026, 10, 5), date(2026, 10, 6)])

    without_date = workspace.build(*CHAIN)
    with_date = workspace.build(*CHAIN, variables={"as_of_date": "2026-10-08"})

    # With every feed equally late, only the run's own date can say so.
    assert check_status(without_date) == "pass"
    assert check_status(with_date) == "warn"
    assert missing(with_date) == [
        ("nse", "cm_bhavcopy", "2026-10-07", "weekday"),
        ("nse", "cm_bhavcopy", "2026-10-08", "weekday"),
    ]


def test_corporate_actions_and_the_security_master_are_not_judged(workspace: Workspace) -> None:
    """The loader stores nothing for a day without actions, so absence there is normal."""
    store_nse_days(workspace, [date(2026, 10, 5), date(2026, 10, 6)])
    workspace.store_actions(
        date(2026, 10, 5),
        (
            "S0",
            "EQ",
            "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share",
        ),
    )

    result = workspace.build(*CHAIN)

    datasets = {
        row[0] for row in result.query("select distinct dataset from silver.slv_meta__calendar")
    }
    assert datasets == {"cm_bhavcopy"}
    assert missing(result) == []
