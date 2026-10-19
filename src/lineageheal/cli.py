"""The `lineageheal` command-line tool.

GitHub Actions and developers run the same commands, so there is one code path for
ingestion. Results go to stdout; logs go to stderr (see `core.logging`).
"""

import enum
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import typer

from lineageheal.core.errors import LineageHealError
from lineageheal.core.logging import configure_logging
from lineageheal.core.settings import get_settings
from lineageheal.ingestion.amfi import AmfiNavLoader
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.raw_zone import open_raw_zone
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult, Loader

# India has no daylight saving, so a fixed offset is exact and avoids needing tzdata.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
DATE_FORMAT = "%Y-%m-%d"
# FR-ING-08: backfills start no earlier than this.
EARLIEST_BACKFILL = date(2024, 1, 1)

app = typer.Typer(no_args_is_help=True, add_completion=False)


class Source(enum.StrEnum):
    """Feeds that can be ingested from the command line."""

    AMFI = "amfi"


SourceArgument = Annotated[Source, typer.Argument(help="Feed to ingest.")]


def today_ist(now: datetime | None = None) -> date:
    """Return the current calendar date in Asia/Kolkata, the project's trade-date zone."""
    return (now or datetime.now(tz=IST)).astimezone(IST).date()


@app.callback()
def main() -> None:
    """Self-healing lakehouse pipelines for Indian financial market data."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@contextmanager
def _open_loader(source: Source) -> Iterator[Loader]:
    """Build the loader for a source on the configured raw zone, closing it afterwards."""
    settings = get_settings()
    zone = open_raw_zone(settings)
    with PoliteFetcher.create(min_interval_seconds=settings.ingest_min_interval_seconds) as fetcher:
        match source:
            case Source.AMFI:
                yield AmfiNavLoader(fetcher, zone)


@contextmanager
def _exit_on_project_errors() -> Iterator[None]:
    """Turn a project error into a one-line message and exit code 1 instead of a traceback."""
    try:
        yield
    except LineageHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _describe(result: IngestionResult) -> str:
    line = f"{result.trade_date} {result.source}/{result.dataset} {result.outcome.value}"
    if result.stored is not None:
        line += f" {result.stored.size_bytes} bytes {result.stored.key}"
    return line


@app.command()
def ingest(
    source: SourceArgument,
    trade_date: Annotated[
        datetime | None,
        typer.Option(
            "--date",
            formats=[DATE_FORMAT],
            help="Trade date as YYYY-MM-DD in Asia/Kolkata. Defaults to today.",
        ),
    ] = None,
) -> None:
    """Download one day of a feed and store it unchanged in the raw zone."""
    day = trade_date.date() if trade_date else today_ist()
    with _exit_on_project_errors(), _open_loader(source) as loader:
        typer.echo(_describe(loader.load(day)))


@app.command()
def backfill(
    source: SourceArgument,
    start: Annotated[
        datetime,
        typer.Option(
            "--start",
            formats=[DATE_FORMAT],
            help="First trade date, YYYY-MM-DD. Not before 2024-01-01.",
        ),
    ],
    end: Annotated[
        datetime | None,
        typer.Option(
            "--end", formats=[DATE_FORMAT], help="Last trade date, YYYY-MM-DD. Defaults to today."
        ),
    ] = None,
) -> None:
    """Ingest every day in a range. Safe to re-run: stored days are skipped.

    A day that fails is reported and skipped so the rest still load; the exit code is 1
    if any day failed.
    """
    first = start.date()
    last = end.date() if end else today_ist()
    if first < EARLIEST_BACKFILL:
        raise typer.BadParameter(f"--start must be {EARLIEST_BACKFILL} or later")
    if last < first:
        raise typer.BadParameter("--end must not be before --start")

    outcomes: Counter[IngestionOutcome] = Counter()
    failed: list[date] = []
    with _exit_on_project_errors(), _open_loader(source) as loader:
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            try:
                result = loader.load(day)
            except LineageHealError as exc:
                failed.append(day)
                typer.echo(f"{day} {source.value} failed: {exc}", err=True)
                continue
            outcomes[result.outcome] += 1
            typer.echo(_describe(result))

    summary = ", ".join(f"{outcomes[o]} {o.value}" for o in IngestionOutcome)
    typer.echo(f"backfill {source.value} {first}..{last}: {summary}, {len(failed)} failed")
    if failed:
        raise typer.Exit(code=1)
