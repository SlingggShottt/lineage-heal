"""The `lineageheal` command-line tool.

GitHub Actions and developers run the same commands, so there is one code path for
ingestion. Results go to stdout; logs go to stderr (see `core.logging`).
"""

import enum
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated

import typer

from lineageheal.benchmark.faults import AMFI_FAULTS
from lineageheal.core.errors import BenchmarkError, ConfigurationError, LineageHealError
from lineageheal.core.logging import configure_logging
from lineageheal.core.settings import get_settings
from lineageheal.ingestion import runner
from lineageheal.ingestion.amfi import AmfiNavLoader, report_filename
from lineageheal.ingestion.log import AttemptRecorder, attempt_from_injection, open_recorder
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone, open_store
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult, Loader
from lineageheal.orchestration.run_artifacts import load_run_summary
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.migrate import current_revision, head_revision, upgrade_database

# India has no daylight saving, so a fixed offset is exact and avoids needing tzdata.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
DATE_FORMAT = "%Y-%m-%d"

app = typer.Typer(no_args_is_help=True, add_completion=False)
dev_app = typer.Typer(
    no_args_is_help=True, help="Developer tools that only ever touch a local sandbox, never R2."
)
app.add_typer(dev_app, name="dev")
db_app = typer.Typer(no_args_is_help=True, help="The application database (Postgres).")
app.add_typer(db_app, name="db")


class Source(enum.StrEnum):
    """Feeds that can be ingested from the command line."""

    AMFI = "amfi"
    NSE = "nse"


# The datasets each source offers; the first is the default.
EARLIEST_BACKFILL = runner.EARLIEST_BACKFILL

DATASETS: dict[Source, tuple[str, ...]] = {
    Source(source): datasets for source, datasets in runner.DATASETS.items()
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
    with runner.open_loader(get_settings(), source.value, dataset) as loader:
        yield loader


@contextmanager
def _open_recorder() -> Iterator[AttemptRecorder]:
    """Open the ingestion log if a database is configured, closing it afterwards."""
    recorder = open_recorder(get_settings())
    try:
        yield recorder
    finally:
        recorder.close()


def _load_and_record(loader: Loader, recorder: AttemptRecorder, day: date) -> IngestionResult:
    """Run one ingestion and log the attempt, whether it succeeds or fails."""
    return runner.load_and_record(loader, recorder, day)


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
    with (
        _exit_on_project_errors(),
        _open_recorder() as recorder,
        _open_loader(source, chosen) as loader,
    ):
        typer.echo(_describe(_load_and_record(loader, recorder, day)))


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
    with (
        _exit_on_project_errors(),
        _open_recorder() as recorder,
        _open_loader(source, chosen) as loader,
    ):
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            try:
                result = _load_and_record(loader, recorder, day)
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
    with _exit_on_project_errors(), _open_recorder() as recorder:
        started_at = datetime.now(UTC)
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
        recorder.record(
            attempt_from_injection(
                source=AmfiNavLoader.source,
                dataset=AmfiNavLoader.dataset,
                trade_date=day,
                stored=stored,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        )
        verb = "injected" if stored.created else "already injected"
        typer.echo(f"{verb} {fault} ({spec.fault_class}) into amfi/navall {day}: {stored.key}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Create or update the application database's tables (reads DATABASE_URL)."""
    with _exit_on_project_errors():
        url = get_settings().require_database_url()
        upgrade_database(url)
        typer.echo(f"database is at revision {current_revision(url)}")


@db_app.command("status")
def db_status() -> None:
    """Show which migration the database is at and whether it is up to date."""
    with _exit_on_project_errors():
        url = get_settings().require_database_url()
        current, head = current_revision(url), head_revision()
        if current is None:
            typer.echo(f"no migrations applied; run `lineageheal db upgrade` (latest is {head})")
        elif current == head:
            typer.echo(f"database is at revision {current}, which is up to date")
        else:
            typer.echo(
                f"database is at revision {current}, behind {head}; run `lineageheal db upgrade`"
            )


def _describe_attempt(attempt: IngestionAttempt) -> str:
    when = attempt.finished_at.strftime("%Y-%m-%d %H:%M:%S")
    head = (
        f"{attempt.id:>5}  {when}Z  {attempt.source}/{attempt.dataset}  "
        f"{attempt.trade_date}  {attempt.outcome:<9}"
    )
    if attempt.outcome in ("stored", "unchanged"):
        return f"{head} {attempt.size_bytes} bytes {attempt.object_key}"
    if attempt.outcome == "failed":
        return f"{head} {attempt.error}"
    return f"{head} http {attempt.http_status}"


@app.command("log")
def show_log(
    limit: Annotated[int, typer.Option("--limit", min=1, help="How many attempts to show.")] = 20,
    source: Annotated[str | None, typer.Option("--source", help="Only this source.")] = None,
    dataset: Annotated[str | None, typer.Option("--dataset", help="Only this dataset.")] = None,
    outcome: Annotated[
        str | None,
        typer.Option("--outcome", help="Only this outcome: stored, unchanged, no_data or failed."),
    ] = None,
) -> None:
    """Show recent ingestion attempts, newest first (reads DATABASE_URL)."""
    with _exit_on_project_errors():
        engine = create_db_engine(get_settings().require_database_url())
        try:
            attempts = IngestionLogRepository(engine).recent(
                limit=limit, source=source, dataset=dataset, outcome=outcome
            )
        finally:
            engine.dispose()
    if not attempts:
        typer.echo("no ingestion attempts recorded")
    for attempt in attempts:
        typer.echo(_describe_attempt(attempt))


@app.command("signals")
def show_signals(
    run_id: Annotated[str, typer.Argument(help="The pipeline run, as shown in the Dagster UI.")],
) -> None:
    """Show the failure signals a pipeline run recorded (reads the raw zone's runs/ folder)."""
    with _exit_on_project_errors():
        summary = load_run_summary(open_store(get_settings()), run_id)
    counts = ", ".join(f"{count} {status}" for status, count in sorted(summary.counts.items()))
    typer.echo(f"run {summary.run_id}: {counts or 'dbt wrote no results'}")
    if summary.skipped_models:
        typer.echo(f"skipped because of an upstream failure: {', '.join(summary.skipped_models)}")
    if not summary.signals:
        typer.echo("no failure signals")
    for signal in summary.signals:
        where = ".".join(part for part in (signal.model, signal.column) if part)
        found = f" ({signal.failures} rows)" if signal.failures else ""
        message = signal.message.splitlines()[0] if signal.message else ""
        typer.echo(
            f"{signal.severity.value:5s} {signal.kind.value} {signal.check_type} "
            f"{where or signal.name}{found}: {message}"
        )
