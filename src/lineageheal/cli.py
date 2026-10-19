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

from lineageheal.benchmark.faults import AMFI_FAULTS
from lineageheal.core.errors import BenchmarkError, ConfigurationError, LineageHealError
from lineageheal.core.logging import configure_logging
from lineageheal.core.settings import get_settings
from lineageheal.ingestion.amfi import AmfiNavLoader, report_filename
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.nse import (
    NSE_BROWSER_HEADERS,
    NseBhavcopyLoader,
    NseCorporateActionsLoader,
    NseSecurityMasterLoader,
)
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone, open_store
from lineageheal.ingestion.staging import ZipStager
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult, Loader

# India has no daylight saving, so a fixed offset is exact and avoids needing tzdata.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
DATE_FORMAT = "%Y-%m-%d"
# FR-ING-08: backfills start no earlier than this.
EARLIEST_BACKFILL = date(2024, 1, 1)

app = typer.Typer(no_args_is_help=True, add_completion=False)
dev_app = typer.Typer(
    no_args_is_help=True, help="Developer tools that only ever touch a local sandbox, never R2."
)
app.add_typer(dev_app, name="dev")


class Source(enum.StrEnum):
    """Feeds that can be ingested from the command line."""

    AMFI = "amfi"
    NSE = "nse"


# The datasets each source offers; the first is the default.
DATASETS: dict[Source, tuple[str, ...]] = {
    Source.AMFI: (AmfiNavLoader.dataset,),
    Source.NSE: (
        NseBhavcopyLoader.dataset,
        NseSecurityMasterLoader.dataset,
        NseCorporateActionsLoader.dataset,
    ),
}

SourceArgument = Annotated[Source, typer.Argument(help="Data feed: amfi or nse.")]
DatasetOption = Annotated[
    str | None,
    typer.Option(
        "--dataset",
        help="Dataset to load. NSE: cm_bhavcopy (default), security_master or corporate_actions.",
    ),
]


def _resolve_dataset(source: Source, dataset: str | None) -> str:
    """Return the requested dataset, defaulting to the source's first, or reject an unknown one."""
    choices = DATASETS[source]
    if dataset is None:
        return choices[0]
    if dataset not in choices:
        raise typer.BadParameter(f"{source.value} datasets are: {', '.join(choices)}")
    return dataset


def today_ist(now: datetime | None = None) -> date:
    """Return the current calendar date in Asia/Kolkata, the project's trade-date zone."""
    return (now or datetime.now(tz=IST)).astimezone(IST).date()


@app.callback()
def main() -> None:
    """Self-healing lakehouse pipelines for Indian financial market data."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@contextmanager
def _open_loader(source: Source, dataset: str) -> Iterator[Loader]:
    """Build the loader for a source's dataset on the configured raw zone, closing it afterwards."""
    settings = get_settings()
    store = open_store(settings)
    zone = RawZone(store)
    headers = NSE_BROWSER_HEADERS if source is Source.NSE else None
    with PoliteFetcher.create(
        min_interval_seconds=settings.ingest_min_interval_seconds, headers=headers
    ) as fetcher:
        match (source, dataset):
            case (Source.AMFI, AmfiNavLoader.dataset):
                yield AmfiNavLoader(fetcher, zone)
            case (Source.NSE, NseBhavcopyLoader.dataset):
                yield NseBhavcopyLoader(fetcher, zone, ZipStager(store))
            case (Source.NSE, NseSecurityMasterLoader.dataset):
                yield NseSecurityMasterLoader(fetcher, zone)
            case (Source.NSE, NseCorporateActionsLoader.dataset):
                yield NseCorporateActionsLoader(fetcher, zone)
            case _:
                raise ConfigurationError(f"No loader for {source.value}/{dataset}")


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
    dataset: DatasetOption = None,
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
    chosen = _resolve_dataset(source, dataset)
    day = trade_date.date() if trade_date else today_ist()
    with _exit_on_project_errors(), _open_loader(source, chosen) as loader:
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
    dataset: DatasetOption = None,
) -> None:
    """Ingest every day in a range. Safe to re-run: stored days are skipped.

    A day that fails is reported and skipped so the rest still load; the exit code is 1
    if any day failed.
    """
    chosen = _resolve_dataset(source, dataset)
    first = start.date()
    last = end.date() if end else today_ist()
    if first < EARLIEST_BACKFILL:
        raise typer.BadParameter(f"--start must be {EARLIEST_BACKFILL} or later")
    if last < first:
        raise typer.BadParameter("--end must not be before --start")

    outcomes: Counter[IngestionOutcome] = Counter()
    failed: list[date] = []
    with _exit_on_project_errors(), _open_loader(source, chosen) as loader:
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            try:
                result = loader.load(day)
            except LineageHealError as exc:
                failed.append(day)
                typer.echo(f"{day} {source.value}/{chosen} failed: {exc}", err=True)
                continue
            outcomes[result.outcome] += 1
            typer.echo(_describe(result))

    summary = ", ".join(f"{outcomes[o]} {o.value}" for o in IngestionOutcome)
    typer.echo(f"backfill {source.value}/{chosen} {first}..{last}: {summary}, {len(failed)} failed")
    if failed:
        raise typer.Exit(code=1)


@dev_app.command("inject-fault")
def inject_fault(
    source: SourceArgument,
    fault: Annotated[
        str, typer.Option("--fault", help=f"Fault to inject: {', '.join(AMFI_FAULTS)}.")
    ],
    trade_date: Annotated[
        datetime,
        typer.Option(
            "--date",
            formats=[DATE_FORMAT],
            help="Trade date of the stored report to damage, YYYY-MM-DD.",
        ),
    ],
) -> None:
    """Store a damaged copy of an ingested report as a new version, to rehearse a failure.

    The copy is made from the earliest stored version of that day, so repeated
    injections do not stack on each other. Only the local raw zone is written; the
    command refuses to run when RAW_ZONE_BACKEND is r2.
    """
    if source is not Source.AMFI:
        raise typer.BadParameter("fault injectors exist for amfi only")
    if fault not in AMFI_FAULTS:
        raise typer.BadParameter(f"unknown fault, choose one of: {', '.join(AMFI_FAULTS)}")

    day = trade_date.date()
    spec = AMFI_FAULTS[fault]
    with _exit_on_project_errors():
        settings = get_settings()
        if settings.raw_zone_backend != "local":
            raise ConfigurationError(
                "Refusing to inject faults into the R2 raw zone; use RAW_ZONE_BACKEND=local"
            )
        store = LocalObjectStore(settings.raw_zone_dir)
        folder = f"raw/source={AmfiNavLoader.source}/dataset={AmfiNavLoader.dataset}"
        keys = store.list_folder(f"{folder}/trade_date={day.isoformat()}")
        if not keys:
            raise BenchmarkError(
                f"No report is stored for {day}; run: lineageheal ingest amfi --date {day}"
            )

        zone = RawZone(store)
        stored = zone.put_if_absent(
            source=AmfiNavLoader.source,
            dataset=AmfiNavLoader.dataset,
            trade_date=day,
            filename=report_filename(day),
            content=spec.apply(zone.read(keys[0])),
        )
        verb = "injected" if stored.created else "already injected"
        typer.echo(f"{verb} {fault} ({spec.fault_class}) into amfi/navall {day}: {stored.key}")
