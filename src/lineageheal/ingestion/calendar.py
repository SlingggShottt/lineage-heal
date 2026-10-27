"""Exchange calendars: the days a source is expected to publish, kept as a seed (FR-ING-07).

To tell "no file expected" (a holiday) from "file missing" there must be a record of which days
NSE trades that does not come from the files themselves: defined by what was stored, "expected"
would always equal "present" and a missing file could never show. The seed therefore holds
*exceptions* to the plain rule (weekdays open, weekends closed), each with where it came from:

- `nse_api`: NSE's own holiday list. It is official and looks forward, but the API serves only the
  current year, so it is fetched each January.
- `observed`: a weekday on which NSE published nothing, confirmed by asking again, or a weekend on
  which it published a file (a special session). This covers years the API no longer serves; it
  is good evidence for the past and says nothing about tomorrow.

On the 13 weeks checked, the three weekdays NSE published nothing for were exactly the official
weekday holidays, which is what makes the observed rows trustworthy.

AMFI needs no seed: it publishes a report for every calendar day. What varies is the size of the
report (a market holiday gets a weekend-sized one), and that follows the NSE calendar.
"""

import csv
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import structlog

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.nse import NseBhavcopyLoader
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

log = structlog.get_logger(__name__)

HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master"
NSE_SOURCE = NseBhavcopyLoader.source
BHAVCOPY_DATASET = NseBhavcopyLoader.dataset

# The repository root holds src/ and dbt/ side by side, in a checkout and in the container.
DEFAULT_SEED = Path(__file__).resolve().parents[3] / "dbt" / "seeds" / "seed_calendar_nse.csv"

SEED_COLUMNS = ("calendar_date", "status", "description", "source", "recorded_on")


class DayStatus(StrEnum):
    """How a date differs from the plain rule that weekdays are open and weekends closed."""

    CLOSED = "closed"  # a weekday on which the exchange is closed
    SPECIAL_SESSION = "special_session"  # a trading session on a day it would be closed


class EntrySource(StrEnum):
    """Where an entry came from; the first is more reliable and wins a disagreement."""

    NSE_API = "nse_api"
    OBSERVED = "observed"


@dataclass(frozen=True)
class CalendarEntry:
    """One date that is an exception to the plain rule."""

    calendar_date: date
    status: DayStatus
    description: str
    source: EntrySource
    recorded_on: date  # when it was fetched or observed


@dataclass(frozen=True)
class MergeResult:
    """The outcome of merging new entries into the existing ones."""

    entries: tuple[CalendarEntry, ...]
    added: int
    updated: int
    conflicts: tuple[str, ...]  # new entries that contradict an existing one, which is kept


_PRECEDENCE = {EntrySource.NSE_API: 0, EntrySource.OBSERVED: 1}


def parse_official_holidays(payload: object, *, recorded_on: date) -> list[CalendarEntry]:
    """Read the capital-market holidays out of NSE's holiday list.

    A weekday holiday is a closure. A holiday on a weekend changes nothing, since the exchange
    is closed anyway, so it is left out. NSE marks a holiday that has a Muhurat (Diwali) trading
    session with a trailing `*`; that date is a special session, because a file is expected.

    Raises:
        IngestionError: The payload is not NSE's holiday list, or a row cannot be read.
    """
    section = payload.get("CM") if isinstance(payload, Mapping) else None
    if not isinstance(section, list):
        raise IngestionError("NSE's holiday list has no capital-market (CM) section")
    entries: dict[date, CalendarEntry] = {}
    for row in section:
        try:
            day = datetime.strptime(str(row["tradingDate"]).strip(), "%d-%b-%Y").date()  # noqa: DTZ007
            text = str(row["description"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise IngestionError(f"Unreadable row in NSE's holiday list: {row!r}") from exc
        if text.endswith("*"):
            status = DayStatus.SPECIAL_SESSION
        elif day.weekday() < 5:
            status = DayStatus.CLOSED
        else:
            continue
        entries[day] = CalendarEntry(
            day, status, text.rstrip("*").strip(), EntrySource.NSE_API, recorded_on
        )
    return sorted(entries.values(), key=lambda entry: entry.calendar_date)


def fetch_official_holidays(fetcher: PoliteFetcher, *, recorded_on: date) -> list[CalendarEntry]:
    """Download NSE's holiday list for the current year.

    Raises:
        SourceUnavailableError: NSE did not answer, or answered with something other than the list.
        IngestionError: The list could not be read.
    """
    response = fetcher.get(HOLIDAY_URL, params={"type": "trading"})
    if response.status_code != 200:
        raise SourceUnavailableError(f"NSE holiday list: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceUnavailableError("NSE holiday list is not JSON") from exc
    return parse_official_holidays(payload, recorded_on=recorded_on)


def read_seed(path: Path) -> list[CalendarEntry]:
    """Read the calendar seed, or an empty list if there is none yet.

    Raises:
        IngestionError: A row has an unknown status or source, or a bad date.
    """
    if not path.exists():
        return []
    entries = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                entries.append(
                    CalendarEntry(
                        calendar_date=date.fromisoformat(row["calendar_date"]),
                        status=DayStatus(row["status"]),
                        description=row["description"],
                        source=EntrySource(row["source"]),
                        recorded_on=date.fromisoformat(row["recorded_on"]),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise IngestionError(f"Unreadable calendar seed row {row!r}: {exc}") from exc
    return entries


def write_seed(path: Path, entries: Sequence[CalendarEntry]) -> None:
    """Write the seed, sorted by date, so the same entries always give the same file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SEED_COLUMNS)
        for entry in sorted(entries, key=lambda e: e.calendar_date):
            writer.writerow(
                [
                    entry.calendar_date.isoformat(),
                    entry.status.value,
                    entry.description,
                    entry.source.value,
                    entry.recorded_on.isoformat(),
                ]
            )


def merge_entries(
    existing: Sequence[CalendarEntry], incoming: Sequence[CalendarEntry]
) -> MergeResult:
    """Merge new entries into the existing ones without losing or contradicting any.

    For a date already present: the same status keeps the more reliable source (an official row
    replaces an observed one) and refreshes the description; a different status is a conflict,
    reported and not applied, because one of the two is wrong and a person should look.
    """
    merged = {entry.calendar_date: entry for entry in existing}
    added = updated = 0
    conflicts: list[str] = []
    for new in incoming:
        old = merged.get(new.calendar_date)
        if old is None:
            merged[new.calendar_date] = new
            added += 1
        elif old.status != new.status:
            conflicts.append(
                f"{new.calendar_date}: {new.source.value} says {new.status.value} "
                f"({new.description}), the seed has {old.status.value} from {old.source.value}"
            )
        elif _PRECEDENCE[new.source] <= _PRECEDENCE[old.source] and (
            (old.source, old.description) != (new.source, new.description)
        ):
            merged[new.calendar_date] = new
            updated += 1
    return MergeResult(
        entries=tuple(sorted(merged.values(), key=lambda e: e.calendar_date)),
        added=added,
        updated=updated,
        conflicts=tuple(conflicts),
    )


def observe_nse_sessions(
    zone: RawZone,
    load: Callable[[date], IngestionResult],
    *,
    start: date,
    end: date,
    recorded_on: date,
) -> list[CalendarEntry]:
    """Work out from the raw zone which dates were exceptions, confirming closures live.

    A weekday with no stored bhavcopy might be a holiday, or a download that failed. So each
    such weekday is asked for once more: only if NSE answers that it has nothing is it recorded
    as closed, and if the file turns up it is stored and the day is not a holiday. A weekend
    with a stored file is a special session, with no request needed.

    Args:
        zone: The raw zone holding the bhavcopies.
        load: Downloads and stores one date's bhavcopy, as the loader does (and logs the attempt
            when an ingestion log is configured). Used to confirm closures.
        start: First date to look at.
        end: Last date to look at.
        recorded_on: The date to record as the day of observation.

    Returns:
        An entry for each closure confirmed and each weekend session found.
    """
    entries: list[CalendarEntry] = []
    day = start
    while day <= end:
        stored = zone.stored_keys(source=NSE_SOURCE, dataset=BHAVCOPY_DATASET, trade_date=day)
        if day.weekday() >= 5:
            if stored:
                entries.append(
                    CalendarEntry(
                        day,
                        DayStatus.SPECIAL_SESSION,
                        "Trading session on a weekend",
                        EntrySource.OBSERVED,
                        recorded_on,
                    )
                )
        elif not stored:
            result = load(day)
            if result.outcome is IngestionOutcome.NO_DATA:
                entries.append(
                    CalendarEntry(
                        day,
                        DayStatus.CLOSED,
                        "No bhavcopy published",
                        EntrySource.OBSERVED,
                        recorded_on,
                    )
                )
            else:
                log.info("calendar_gap_was_a_missing_file", trade_date=day.isoformat())
        day += timedelta(days=1)
    return entries
