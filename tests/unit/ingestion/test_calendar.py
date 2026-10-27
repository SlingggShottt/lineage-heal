"""FR-ING-07: a calendar separates "no file expected" from "file missing"."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.calendar import (
    DEFAULT_SEED,
    HOLIDAY_URL,
    CalendarEntry,
    DayStatus,
    EntrySource,
    fetch_official_holidays,
    merge_entries,
    observe_nse_sessions,
    parse_official_holidays,
    read_seed,
    write_seed,
)
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone, StoredObject
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

TODAY = date(2026, 10, 24)


def official(*rows: tuple[str, str]) -> dict[str, list[dict[str, str]]]:
    """NSE's holiday list with the given (date, description) rows in the CM section."""
    return {"CM": [{"tradingDate": day, "description": text, "weekDay": "?"} for day, text in rows]}


def entry(
    day: str,
    status: DayStatus = DayStatus.CLOSED,
    text: str = "Holiday",
    source: EntrySource = EntrySource.NSE_API,
    recorded: date = TODAY,
) -> CalendarEntry:
    return CalendarEntry(date.fromisoformat(day), status, text, source, recorded)


# --- reading NSE's list --------------------------------------------------------------------


def test_a_weekday_holiday_is_a_closure() -> None:
    entries = parse_official_holidays(official(("20-Oct-2026", "Dussehra")), recorded_on=TODAY)

    assert entries == [entry("2026-10-20", text="Dussehra")]


def test_a_holiday_on_a_weekend_changes_nothing_so_it_is_left_out() -> None:
    # 15 February 2026 is a Sunday, 21 March a Saturday
    entries = parse_official_holidays(
        official(("15-Feb-2026", "Mahashivratri"), ("21-Mar-2026", "Id-Ul-Fitr")),
        recorded_on=TODAY,
    )

    assert entries == []


def test_a_starred_holiday_has_a_muhurat_session_so_a_file_is_expected() -> None:
    entries = parse_official_holidays(
        official(("08-Nov-2026", "Diwali Laxmi Pujan*"), ("11-Nov-2026", "Some Diwali day*")),
        recorded_on=TODAY,
    )

    # the star is dropped from the description; a Sunday and a Wednesday are both sessions
    assert entries == [
        entry("2026-11-08", DayStatus.SPECIAL_SESSION, "Diwali Laxmi Pujan"),
        entry("2026-11-11", DayStatus.SPECIAL_SESSION, "Some Diwali day"),
    ]


def test_the_entries_come_back_in_date_order_without_repeats() -> None:
    entries = parse_official_holidays(
        official(
            ("26-Jan-2026", "Republic Day"),
            ("15-Jan-2026", "Election"),
            ("26-Jan-2026", "Republic Day"),
        ),
        recorded_on=TODAY,
    )

    assert [e.calendar_date for e in entries] == [date(2026, 1, 15), date(2026, 1, 26)]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"FO": []}, id="no CM section"),
        pytest.param({"CM": "nope"}, id="CM is not a list"),
        pytest.param(["not", "a", "dict"], id="not an object"),
        pytest.param(
            {"CM": [{"tradingDate": "2026-10-20", "description": "x"}]}, id="wrong date format"
        ),
        pytest.param({"CM": [{"description": "x"}]}, id="missing date"),
        pytest.param({"CM": [{"tradingDate": "20-Oct-2026"}]}, id="missing description"),
    ],
)
def test_a_list_that_is_not_what_nse_publishes_is_an_error(payload: object) -> None:
    with pytest.raises(IngestionError):
        parse_official_holidays(payload, recorded_on=TODAY)


@respx.mock
def test_the_official_list_is_fetched_for_trading_days() -> None:
    route = respx.get(HOLIDAY_URL).mock(
        return_value=httpx.Response(200, json=official(("20-Oct-2026", "Dussehra")))
    )

    with PoliteFetcher.create(min_interval_seconds=0) as fetcher:
        entries = fetch_official_holidays(fetcher, recorded_on=TODAY)

    assert route.calls.last.request.url.params["type"] == "trading"
    assert [e.calendar_date for e in entries] == [date(2026, 10, 20)]


@respx.mock
def test_a_refusal_from_nse_is_reported_not_read_as_an_empty_calendar() -> None:
    respx.get(HOLIDAY_URL).mock(return_value=httpx.Response(403))

    with (
        PoliteFetcher.create(min_interval_seconds=0) as fetcher,
        pytest.raises(SourceUnavailableError, match="HTTP 403"),
    ):
        fetch_official_holidays(fetcher, recorded_on=TODAY)


@respx.mock
def test_an_answer_that_is_not_json_is_reported() -> None:
    respx.get(HOLIDAY_URL).mock(return_value=httpx.Response(200, text="<html>blocked</html>"))

    with (
        PoliteFetcher.create(min_interval_seconds=0) as fetcher,
        pytest.raises(SourceUnavailableError, match="not JSON"),
    ):
        fetch_official_holidays(fetcher, recorded_on=TODAY)


# --- the seed file -------------------------------------------------------------------------


def test_the_seed_round_trips_and_is_written_in_date_order(tmp_path: Path) -> None:
    path = tmp_path / "seed.csv"
    entries = [
        entry("2026-10-20", text="Dussehra"),
        entry("2026-01-26", text="Republic Day, a day with a comma"),
        entry("2026-11-08", DayStatus.SPECIAL_SESSION, "Diwali", EntrySource.OBSERVED),
    ]

    write_seed(path, entries)

    assert read_seed(path) == sorted(entries, key=lambda e: e.calendar_date)
    lines = path.read_text().splitlines()
    assert lines[0] == "calendar_date,status,description,source,recorded_on"
    assert lines[1].startswith("2026-01-26,closed,")


def test_the_same_entries_always_give_the_same_bytes(tmp_path: Path) -> None:
    entries = [entry("2026-10-20"), entry("2026-01-26")]
    write_seed(tmp_path / "a.csv", entries)
    write_seed(tmp_path / "b.csv", list(reversed(entries)))

    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()
    assert b"\r" not in (tmp_path / "a.csv").read_bytes()


def test_a_missing_seed_reads_as_empty(tmp_path: Path) -> None:
    assert read_seed(tmp_path / "none.csv") == []


@pytest.mark.parametrize(
    "row",
    [
        "2026-10-20,half_open,x,nse_api,2026-10-24",
        "2026-10-20,closed,x,a_guess,2026-10-24",
        "20-Oct-2026,closed,x,nse_api,2026-10-24",
    ],
)
def test_a_seed_row_that_makes_no_sense_is_an_error(tmp_path: Path, row: str) -> None:
    path = tmp_path / "seed.csv"
    path.write_text(f"calendar_date,status,description,source,recorded_on\n{row}\n")

    with pytest.raises(IngestionError, match="Unreadable calendar seed row"):
        read_seed(path)


# --- merging ------------------------------------------------------------------------------


def test_new_dates_are_added_and_the_result_is_in_date_order() -> None:
    result = merge_entries([entry("2026-10-20")], [entry("2026-01-26"), entry("2026-12-25")])

    assert (result.added, result.updated, result.conflicts) == (2, 0, ())
    assert [e.calendar_date.isoformat() for e in result.entries] == [
        "2026-01-26",
        "2026-10-20",
        "2026-12-25",
    ]


def test_an_identical_entry_changes_nothing_and_keeps_its_original_date_of_record() -> None:
    old = entry("2026-10-20", recorded=date(2026, 1, 5))

    result = merge_entries([old], [entry("2026-10-20", recorded=TODAY)])

    assert (result.added, result.updated) == (0, 0)
    assert result.entries == (old,)


def test_an_official_row_replaces_an_observed_one_for_the_same_day() -> None:
    observed = entry("2026-10-20", text="No bhavcopy published", source=EntrySource.OBSERVED)

    result = merge_entries([observed], [entry("2026-10-20", text="Dussehra")])

    assert result.updated == 1
    assert result.entries[0].source is EntrySource.NSE_API
    assert result.entries[0].description == "Dussehra"


def test_an_observed_row_never_replaces_an_official_one() -> None:
    official_row = entry("2026-10-20", text="Dussehra")

    result = merge_entries(
        [official_row],
        [entry("2026-10-20", text="No bhavcopy published", source=EntrySource.OBSERVED)],
    )

    assert result.updated == 0
    assert result.entries == (official_row,)


def test_a_disagreement_is_reported_and_the_existing_entry_is_kept() -> None:
    kept = entry("2026-10-20", DayStatus.CLOSED, "Dussehra")

    result = merge_entries(
        [kept],
        [entry("2026-10-20", DayStatus.SPECIAL_SESSION, "A file exists", EntrySource.OBSERVED)],
    )

    assert result.entries == (kept,)
    assert result.added == result.updated == 0
    (conflict,) = result.conflicts
    assert "2026-10-20" in conflict
    assert "special_session" in conflict
    assert "closed" in conflict


# --- observing the raw zone ----------------------------------------------------------------

MONDAY = date(2026, 10, 12)  # a week: Mon 12 to Sun 18 October 2026


def zone_with_files(tmp_path: Path, days: list[date]) -> RawZone:
    zone = RawZone(LocalObjectStore(tmp_path / "raw"))
    for day in days:
        zone.put_if_absent(
            source="nse",
            dataset="cm_bhavcopy",
            trade_date=day,
            filename=f"BhavCopy_{day:%Y%m%d}.csv.zip",
            content=day.isoformat().encode(),
        )
    return zone


class Asked:
    """A stand-in for the loader that records which dates were asked for."""

    def __init__(self, outcome: IngestionOutcome) -> None:
        self.outcome = outcome
        self.dates: list[date] = []

    def __call__(self, day: date) -> IngestionResult:
        self.dates.append(day)
        stored = (
            StoredObject(key="k", sha256="0" * 64, size_bytes=1, created=True)
            if self.outcome is IngestionOutcome.STORED
            else None
        )
        return IngestionResult("nse", "cm_bhavcopy", day, self.outcome, 200, stored)


def day(offset: int) -> date:
    return date.fromordinal(MONDAY.toordinal() + offset)


def test_a_weekday_with_no_file_that_nse_still_has_nothing_for_is_a_confirmed_closure(
    tmp_path: Path,
) -> None:
    zone = zone_with_files(tmp_path, [day(0), day(1), day(3), day(4)])  # all but Wednesday
    asked = Asked(IngestionOutcome.NO_DATA)

    entries = observe_nse_sessions(zone, asked, start=day(0), end=day(6), recorded_on=TODAY)

    assert entries == [
        entry("2026-10-14", text="No bhavcopy published", source=EntrySource.OBSERVED)
    ]
    assert asked.dates == [
        day(2)
    ]  # only the gap was asked about; weekends and stored days were not


def test_a_gap_that_turns_out_to_be_a_missing_file_is_not_a_holiday(tmp_path: Path) -> None:
    zone = zone_with_files(tmp_path, [day(0), day(1), day(3), day(4)])
    asked = Asked(IngestionOutcome.STORED)

    entries = observe_nse_sessions(zone, asked, start=day(0), end=day(6), recorded_on=TODAY)

    assert entries == []
    assert asked.dates == [day(2)]


def test_a_weekend_with_a_file_is_a_special_session_with_no_request(tmp_path: Path) -> None:
    zone = zone_with_files(tmp_path, [day(0), day(1), day(2), day(3), day(4), day(5)])  # + Saturday
    asked = Asked(IngestionOutcome.NO_DATA)

    entries = observe_nse_sessions(zone, asked, start=day(0), end=day(6), recorded_on=TODAY)

    assert [(e.calendar_date, e.status) for e in entries] == [(day(5), DayStatus.SPECIAL_SESSION)]
    assert entries[0].source is EntrySource.OBSERVED
    assert asked.dates == []


def test_a_range_with_every_weekday_stored_and_no_weekend_files_has_nothing_to_record(
    tmp_path: Path,
) -> None:
    zone = zone_with_files(tmp_path, [day(i) for i in range(5)])
    asked = Asked(IngestionOutcome.NO_DATA)

    assert observe_nse_sessions(zone, asked, start=day(0), end=day(6), recorded_on=TODAY) == []
    assert asked.dates == []


# --- the seed committed to the repository ------------------------------------------------------


def test_the_committed_seed_is_in_the_form_the_tool_writes(tmp_path: Path) -> None:
    """A hand edit that reorders or reformats rows would make the next refresh a noisy diff."""
    write_seed(tmp_path / "again.csv", read_seed(DEFAULT_SEED))

    assert (tmp_path / "again.csv").read_bytes() == DEFAULT_SEED.read_bytes()


def test_every_closure_in_the_committed_seed_is_a_weekday() -> None:
    """A weekend is closed by the plain rule, so a weekend closure is noise or a mistake."""
    closures = [e for e in read_seed(DEFAULT_SEED) if e.status is DayStatus.CLOSED]

    assert closures
    assert [e.calendar_date for e in closures if e.calendar_date.weekday() >= 5] == []


def test_the_committed_seed_knows_the_holidays_the_real_data_showed() -> None:
    """Dussehra 2026 is the day AMFI published a weekend-sized report (D-34)."""
    by_date = {e.calendar_date: e for e in read_seed(DEFAULT_SEED)}

    dussehra = by_date[date(2026, 10, 20)]
    assert (dussehra.status, dussehra.source) == (DayStatus.CLOSED, EntrySource.NSE_API)
    assert dussehra.description == "Dussehra"
    assert by_date[date(2026, 9, 14)].description == "Ganesh Chaturthi"
    assert by_date[date(2026, 10, 2)].description == "Mahatma Gandhi Jayanti"


def test_the_diwali_session_in_the_committed_seed_is_a_special_session() -> None:
    """Treating it as a closure would make the Muhurat bhavcopy look like an unexpected file."""
    by_date = {e.calendar_date: e for e in read_seed(DEFAULT_SEED)}

    assert by_date[date(2026, 11, 8)].status is DayStatus.SPECIAL_SESSION


def test_the_committed_seed_has_the_weekend_sessions_the_year_long_probe_found() -> None:
    """Three special Saturdays in 2024 and the Union Budget days of 2025 and 2026."""
    sessions = {
        e.calendar_date
        for e in read_seed(DEFAULT_SEED)
        if e.status is DayStatus.SPECIAL_SESSION and e.source is EntrySource.OBSERVED
    }

    assert sessions == {
        date(2024, 1, 20),
        date(2024, 3, 2),
        date(2024, 5, 18),
        date(2025, 2, 1),
        date(2026, 2, 1),
    }


def test_the_committed_seed_covers_2024_and_2025_with_observed_closures() -> None:
    by_date = {e.calendar_date: e for e in read_seed(DEFAULT_SEED)}

    assert by_date[date(2024, 1, 26)].source is EntrySource.OBSERVED  # Republic Day, a Friday
    assert by_date[date(2025, 10, 22)].status is DayStatus.CLOSED  # Diwali Balipratipada
    # Muhurat days have a file, so they are open days and not exceptions
    assert date(2024, 11, 1) not in by_date
    assert date(2025, 10, 21) not in by_date
