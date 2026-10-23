"""FR-ING-08 and FR-ORC-05: the ingest and backfill commands."""

import gzip
import zipfile
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import Result
from typer.testing import CliRunner

from lineageheal import cli
from lineageheal.benchmark.faults import blank_nav, negative_nav
from lineageheal.cli import app, today_ist
from lineageheal.core.errors import ConfigurationError
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.ingestion.amfi import AMFI_REPORT_URL
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.orchestration.run_artifacts import SUMMARY_FILE, run_prefix
from lineageheal.store.database import create_db_engine
from lineageheal.store.ingestion_log import IngestionAttempt, IngestionLogRepository
from lineageheal.store.migrate import upgrade_database

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"
REPORT = (FIXTURES / "navall_history_2026-10-15_sample.txt").read_bytes()
NO_DATA_PAGE = (FIXTURES / "navall_no_data_response.html").read_bytes()

NSE_URL = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv.zip"
)
NSE_CSV = (FIXTURES.parent / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()

runner = CliRunner()


def make_nse_zip() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv", NSE_CSV)
    return buffer.getvalue()


def report_response(content: bytes = REPORT) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "text/plain"})


def no_data_response() -> httpx.Response:
    return httpx.Response(
        200, content=NO_DATA_PAGE, headers={"content-type": "text/html; charset=utf-8"}
    )


@pytest.fixture(autouse=True)
def _local_quiet_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write to a temp raw zone, never wait between requests, and keep logs out of the way."""
    monkeypatch.setenv("RAW_ZONE_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("INGEST_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")


def stored_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "raw").rglob("*__*.txt"))


def test_today_is_the_calendar_date_in_india_not_in_utc() -> None:
    late_evening_utc = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 01:30 next day in India

    assert today_ist(late_evening_utc) == date(2026, 10, 15)


def test_running_with_no_arguments_shows_the_available_commands() -> None:
    result = runner.invoke(app, [])

    assert "ingest" in result.output
    assert "backfill" in result.output


@respx.mock
def test_ingest_stores_the_report_and_prints_one_summary_line(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert result.exit_code == 0
    assert result.stdout.startswith("2026-10-15 amfi/navall stored")
    assert "raw/source=amfi/dataset=navall/trade_date=2026-10-15/" in result.stdout
    (stored,) = stored_files(tmp_path)
    assert stored.read_bytes() == REPORT


@respx.mock
def test_ingesting_the_same_day_twice_reports_unchanged(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])
    second = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert second.exit_code == 0
    assert "unchanged" in second.stdout
    assert len(stored_files(tmp_path)) == 1


@respx.mock
def test_a_date_without_data_is_not_a_failure(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=no_data_response())

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2027-12-31"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "2027-12-31 amfi/navall no_data"
    assert stored_files(tmp_path) == []


@respx.mock
def test_ingest_defaults_to_todays_date_in_india(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())
    monkeypatch.setattr(cli, "today_ist", lambda: date(2026, 10, 15))

    result = runner.invoke(app, ["ingest", "amfi"])

    assert result.exit_code == 0
    assert route.calls.last.request.url.params["frmdt"] == "15-Oct-2026"


@respx.mock
def test_a_source_failure_exits_with_one_clear_line_not_a_traceback() -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=httpx.Response(403))

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert result.exit_code == 1
    assert result.stderr.strip() == "error: AMFI returned HTTP 403 for 2026-10-15"
    assert "Traceback" not in result.output


def test_missing_r2_settings_are_named_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAW_ZONE_BACKEND", "r2")

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert result.exit_code == 1
    assert "R2_BUCKET" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["ingest", "amfi", "--date", "15-10-2026"],
        ["ingest", "bse", "--date", "2026-10-15"],
    ],
)
def test_bad_input_is_a_usage_error(arguments: list[str]) -> None:
    assert runner.invoke(app, arguments).exit_code == 2


@respx.mock
def test_nse_ingest_stores_the_bhavcopy_zip_using_browser_headers(tmp_path: Path) -> None:
    published = make_nse_zip()
    route = respx.get(NSE_URL).mock(
        return_value=httpx.Response(
            200, content=published, headers={"content-type": "application/zip"}
        )
    )

    result = runner.invoke(app, ["ingest", "nse", "--date", "2024-07-05"])

    assert result.exit_code == 0
    assert result.stdout.startswith("2024-07-05 nse/cm_bhavcopy stored")
    assert route.calls.last.request.headers["user-agent"].startswith("Mozilla/5.0")
    (stored,) = sorted((tmp_path / "raw").rglob("*.zip"))
    assert stored.read_bytes() == published


@respx.mock
def test_nse_ingest_also_stages_an_unzipped_copy_for_dbt(tmp_path: Path) -> None:
    respx.get(NSE_URL).mock(
        return_value=httpx.Response(
            200, content=make_nse_zip(), headers={"content-type": "application/zip"}
        )
    )

    runner.invoke(app, ["ingest", "nse", "--date", "2024-07-05"])

    (staged,) = sorted((tmp_path / "raw" / "staged").rglob("*.csv"))
    assert staged.read_bytes() == NSE_CSV
    assert "trade_date=2024-07-05" in staged.as_posix()


@respx.mock
def test_nse_404_is_reported_as_no_data_not_a_failure() -> None:
    respx.get(NSE_URL).mock(return_value=httpx.Response(404))

    result = runner.invoke(app, ["ingest", "nse", "--date", "2024-07-05"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "2024-07-05 nse/cm_bhavcopy no_data"


@respx.mock
def test_amfi_requests_keep_the_project_user_agent() -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert route.calls.last.request.headers["user-agent"].startswith("lineageheal/")


@respx.mock
def test_backfill_loads_every_day_in_the_range_and_summarises(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[
            report_response(REPORT),
            no_data_response(),
            report_response(REPORT + b"extra;row\r\n"),
        ]
    )

    result = runner.invoke(
        app, ["backfill", "amfi", "--start", "2026-10-13", "--end", "2026-10-15"]
    )

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert [line.split()[0] for line in lines[:3]] == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert (
        lines[-1]
        == "backfill amfi/navall 2026-10-13..2026-10-15: 2 stored, 0 unchanged, 1 no_data, 0 failed"
    )
    assert len(stored_files(tmp_path)) == 2


@respx.mock
def test_backfill_carries_on_past_a_failed_day_and_exits_nonzero(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[
            report_response(REPORT),
            httpx.Response(403),
            report_response(REPORT + b"extra;row\r\n"),
        ]
    )

    result = runner.invoke(
        app, ["backfill", "amfi", "--start", "2026-10-13", "--end", "2026-10-15"]
    )

    assert result.exit_code == 1
    assert "2026-10-14 amfi/navall failed: AMFI returned HTTP 403" in result.stderr
    assert (
        result.stdout.strip()
        .splitlines()[-1]
        .endswith("2 stored, 0 unchanged, 0 no_data, 1 failed")
    )
    assert len(stored_files(tmp_path)) == 2


@respx.mock
def test_a_re_run_does_not_ask_the_source_again_for_days_it_already_has(tmp_path: Path) -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())
    arguments = ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-15"]

    runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert route.call_count == 1  # the second run sent no request
    assert second.stdout.strip().endswith(
        "0 stored, 0 unchanged, 0 no_data, 1 already stored, 0 failed"
    )
    assert len(stored_files(tmp_path)) == 1


@respx.mock
def test_refresh_downloads_stored_days_again_and_reports_them_unchanged(tmp_path: Path) -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())
    arguments = ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-15"]

    runner.invoke(app, arguments)
    second = runner.invoke(app, [*arguments, "--refresh"])

    assert route.call_count == 2
    assert "1 unchanged" in second.stdout
    assert "already stored" not in second.stdout
    assert len(stored_files(tmp_path)) == 1


@respx.mock
def test_refresh_picks_up_a_report_the_source_has_corrected(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[report_response(REPORT), report_response(REPORT + b"corrected;row\r\n")]
    )
    arguments = ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-15"]

    runner.invoke(app, arguments)
    second = runner.invoke(app, [*arguments, "--refresh"])

    assert "1 stored" in second.stdout
    assert len(stored_files(tmp_path)) == 2  # both versions kept, as the raw zone promises


@respx.mock
def test_an_interrupted_backfill_carries_on_with_only_the_missing_days(tmp_path: Path) -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())
    runner.invoke(app, ["backfill", "amfi", "--start", "2026-10-13", "--end", "2026-10-14"])
    assert route.call_count == 2

    resumed = runner.invoke(
        app, ["backfill", "amfi", "--start", "2026-10-13", "--end", "2026-10-16"]
    )

    assert route.call_count == 4  # only the 15th and the 16th were requested
    assert resumed.stdout.strip().endswith(
        "2 stored, 0 unchanged, 0 no_data, 2 already stored, 0 failed"
    )


@respx.mock
def test_a_day_with_no_file_is_asked_for_again_because_it_may_have_appeared_since() -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=no_data_response())
    arguments = ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-15"]

    runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert route.call_count == 2
    assert "1 no_data" in second.stdout
    assert "already stored" not in second.stdout


@respx.mock
def test_skip_weekends_asks_for_weekdays_only() -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    # Friday 16 October to Monday 19 October 2026
    result = runner.invoke(
        app, ["backfill", "amfi", "--start", "2026-10-16", "--end", "2026-10-19", "--skip-weekends"]
    )

    assert route.call_count == 2
    assert result.stdout.strip().endswith(
        "2 stored, 0 unchanged, 0 no_data, 2 weekend days skipped, 0 failed"
    )


def test_weekends_are_asked_for_unless_told_otherwise() -> None:
    with respx.mock:
        route = respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

        runner.invoke(app, ["backfill", "amfi", "--start", "2026-10-17", "--end", "2026-10-18"])

    assert route.call_count == 2


def test_backfill_refuses_dates_before_the_supported_window() -> None:
    result = runner.invoke(app, ["backfill", "amfi", "--start", "2023-12-31"])

    assert result.exit_code == 2
    assert "2024-01-01" in result.output


def test_backfill_refuses_an_end_before_the_start() -> None:
    result = runner.invoke(
        app, ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-14"]
    )

    assert result.exit_code == 2
    assert "must not be before" in result.output


def store_report(tmp_path: Path, content: bytes = REPORT) -> RawZone:
    """Put a report in the temporary raw zone the autouse fixture points the CLI at."""
    zone = RawZone(LocalObjectStore(tmp_path / "raw"))
    zone.put_if_absent(
        source="amfi",
        dataset="navall",
        trade_date=date(2026, 10, 15),
        filename="NAVHistoryReport151026.txt",
        content=content,
    )
    return zone


def inject(fault: str, day: str = "2026-10-15", source: str = "amfi") -> Result:
    return runner.invoke(app, ["dev", "inject-fault", source, "--fault", fault, "--date", day])


def test_the_dev_group_lists_its_commands() -> None:
    result = runner.invoke(app, ["dev"])

    assert "inject-fault" in result.output


def test_inject_fault_stores_a_damaged_copy_as_a_new_version(tmp_path: Path) -> None:
    store_report(tmp_path)

    result = inject("blank-nav")

    assert result.exit_code == 0
    assert result.stdout.startswith("injected blank-nav (nulls) into amfi/navall 2026-10-15")
    contents = [p.read_bytes() for p in stored_files(tmp_path)]
    assert len(contents) == 2
    assert REPORT in contents
    assert blank_nav(REPORT) in contents


def test_injecting_the_same_fault_twice_stores_nothing_new(tmp_path: Path) -> None:
    store_report(tmp_path)
    inject("blank-nav")

    second = inject("blank-nav")

    assert second.stdout.startswith("already injected blank-nav")
    assert len(stored_files(tmp_path)) == 2


def test_each_injection_starts_from_the_genuine_report_not_from_an_earlier_fault(
    tmp_path: Path,
) -> None:
    store_report(tmp_path)
    inject("blank-nav")

    inject("negative-nav")

    contents = [p.read_bytes() for p in stored_files(tmp_path)]
    assert len(contents) == 3
    assert negative_nav(REPORT) in contents  # not blank-then-negative


def test_inject_fault_refuses_to_touch_r2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected faults must never reach the production raw zone."""
    store_report(tmp_path)
    monkeypatch.setenv("RAW_ZONE_BACKEND", "r2")

    result = inject("blank-nav")

    assert result.exit_code == 1
    assert "Refusing to inject faults into the R2 raw zone" in result.stderr
    assert len(stored_files(tmp_path)) == 1


def test_inject_fault_explains_how_to_fetch_a_missing_report() -> None:
    result = inject("blank-nav")

    assert result.exit_code == 1
    assert "lineageheal ingest amfi --date 2026-10-15" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["dev", "inject-fault", "amfi", "--fault", "no-such-fault", "--date", "2026-10-15"],
        ["dev", "inject-fault", "nse", "--fault", "blank-nav", "--date", "2026-10-15"],
        ["dev", "inject-fault", "amfi", "--fault", "blank-nav"],
    ],
)
def test_inject_fault_rejects_bad_input(arguments: list[str]) -> None:
    assert runner.invoke(app, arguments).exit_code == 2


# --- NSE datasets ------------------------------------------------------------------------

MASTER_URL = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_05072024.csv.gz"
ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions"


@respx.mock
def test_ingest_nse_security_master_stores_the_gzip(tmp_path: Path) -> None:
    published = gzip.compress(b"FinInstrmId,TckrSymb\n1,TCS\n")
    respx.get(MASTER_URL).mock(return_value=httpx.Response(200, content=published))

    result = runner.invoke(
        app, ["ingest", "nse", "--dataset", "security_master", "--date", "2024-07-05"]
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("2024-07-05 nse/security_master stored")
    (stored,) = sorted((tmp_path / "raw").rglob("*.csv.gz"))
    assert stored.read_bytes() == published
    assert not (tmp_path / "raw" / "staged").exists()  # DuckDB reads gzip natively


@respx.mock
def test_ingest_nse_corporate_actions_stores_the_response() -> None:
    respx.get(ACTIONS_URL).mock(return_value=httpx.Response(200, content=b'[{"symbol": "TCS"}]'))

    result = runner.invoke(
        app, ["ingest", "nse", "--dataset", "corporate_actions", "--date", "2024-07-05"]
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("2024-07-05 nse/corporate_actions stored")


@respx.mock
def test_a_day_without_corporate_actions_is_reported_as_no_data() -> None:
    respx.get(ACTIONS_URL).mock(return_value=httpx.Response(200, content=b"[]"))

    result = runner.invoke(
        app, ["ingest", "nse", "--dataset", "corporate_actions", "--date", "2024-07-06"]
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "2024-07-06 nse/corporate_actions no_data"


@respx.mock
def test_backfill_accepts_a_dataset_and_names_it_in_the_summary() -> None:
    respx.get(ACTIONS_URL).mock(return_value=httpx.Response(200, content=b"[]"))

    result = runner.invoke(
        app,
        [
            "backfill", "nse", "--dataset", "corporate_actions",
            "--start", "2024-07-05", "--end", "2024-07-06",
        ],
    )  # fmt: skip

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines()[-1] == (
        "backfill nse/corporate_actions 2024-07-05..2024-07-06: "
        "0 stored, 0 unchanged, 2 no_data, 0 failed"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["ingest", "amfi", "--dataset", "security_master", "--date", "2024-07-05"],
        ["ingest", "nse", "--dataset", "no_such_dataset", "--date", "2024-07-05"],
        ["backfill", "amfi", "--dataset", "cm_bhavcopy", "--start", "2024-07-05"],
    ],
)
def test_an_unknown_dataset_for_a_source_is_a_usage_error(arguments: list[str]) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "datasets are" in result.output


def test_the_loader_registry_refuses_a_combination_it_has_no_loader_for() -> None:
    """Defensive: the commands validate the dataset first, so this guards a registry gap."""
    with (
        pytest.raises(ConfigurationError, match="No loader for amfi/nope"),
        cli._open_loader(cli.Source.AMFI, "nope"),
    ):
        pass


# --- the ingestion log -------------------------------------------------------------------


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the CLI at a migrated SQLite database, as DATABASE_URL would point at Postgres."""
    url = f"sqlite+pysqlite:///{tmp_path / 'log.db'}"
    upgrade_database(url)
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def logged(url: str) -> list[IngestionAttempt]:
    """Every attempt in the database, oldest first."""
    engine = create_db_engine(url)
    try:
        return list(reversed(IngestionLogRepository(engine).recent(limit=100)))
    finally:
        engine.dispose()


def test_db_upgrade_creates_the_tables_and_says_which_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'new.db'}")

    result = runner.invoke(app, ["db", "upgrade"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "database is at revision 0001"


def test_db_status_reports_missing_current_and_outdated_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'new.db'}"
    monkeypatch.setenv("DATABASE_URL", url)

    assert "no migrations applied" in runner.invoke(app, ["db", "status"]).stdout

    upgrade_database(url)
    assert "up to date" in runner.invoke(app, ["db", "status"]).stdout

    monkeypatch.setattr(cli, "head_revision", lambda: "9999")
    assert "behind 9999" in runner.invoke(app, ["db", "status"]).stdout


@pytest.mark.parametrize("arguments", [["db", "upgrade"], ["db", "status"], ["log"]])
def test_database_commands_name_the_missing_setting(arguments: list[str]) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code == 1
    assert "DATABASE_URL" in result.stderr


@respx.mock
def test_every_kind_of_attempt_is_logged(database: str) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[report_response(), report_response(), httpx.Response(403)]
    )
    respx.get(NSE_URL).mock(return_value=httpx.Response(404))
    date_args = ["--date", "2026-10-15"]

    runner.invoke(app, ["ingest", "amfi", *date_args])  # stored
    runner.invoke(app, ["ingest", "amfi", *date_args])  # unchanged
    runner.invoke(app, ["ingest", "nse", "--date", "2024-07-05"])  # no data
    failure = runner.invoke(app, ["ingest", "amfi", *date_args])  # failed

    assert failure.exit_code == 1
    attempts = logged(database)
    assert [a.outcome for a in attempts] == ["stored", "unchanged", "no_data", "failed"]
    stored_attempt, unchanged, no_data, failed = attempts
    assert stored_attempt.object_key is not None
    assert stored_attempt.size_bytes == len(REPORT)
    assert stored_attempt.http_status == 200
    assert unchanged.object_key == stored_attempt.object_key
    assert (no_data.source, no_data.dataset, no_data.http_status) == ("nse", "cm_bhavcopy", 404)
    assert failed.error is not None
    assert "HTTP 403" in failed.error


@respx.mock
def test_a_backfill_logs_each_day_including_the_failed_one(database: str) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[report_response(), httpx.Response(403), no_data_response()]
    )

    runner.invoke(app, ["backfill", "amfi", "--start", "2026-10-13", "--end", "2026-10-15"])

    attempts = logged(database)
    assert [a.trade_date.day for a in attempts] == [13, 14, 15]
    assert [a.outcome for a in attempts] == ["stored", "failed", "no_data"]


def test_an_injected_fault_is_logged_as_a_new_stored_version(tmp_path: Path, database: str) -> None:
    store_report(tmp_path)

    inject("blank-nav")

    (attempt,) = logged(database)
    assert attempt.outcome == "stored"
    assert (attempt.source, attempt.dataset) == ("amfi", "navall")
    assert attempt.http_status is None
    assert attempt.object_key is not None
    assert attempt.object_key.endswith("__NAVHistoryReport151026.txt")


@respx.mock
def test_the_log_command_lists_newest_first_and_can_filter(database: str) -> None:
    respx.get(AMFI_REPORT_URL).mock(side_effect=[report_response(), httpx.Response(403)])
    runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])
    runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    everything = runner.invoke(app, ["log"]).stdout.strip().splitlines()
    failures = runner.invoke(app, ["log", "--outcome", "failed"]).stdout.strip().splitlines()
    newest_only = runner.invoke(app, ["log", "--limit", "1"]).stdout.strip().splitlines()

    assert len(everything) == 2
    assert "failed" in everything[0]
    assert "stored" in everything[1]
    assert len(failures) == 1
    assert "HTTP 403" in failures[0]
    assert newest_only == everything[:1]


@respx.mock
def test_the_log_shows_the_status_for_a_day_with_no_data(database: str) -> None:
    respx.get(NSE_URL).mock(return_value=httpx.Response(404))
    runner.invoke(app, ["ingest", "nse", "--date", "2024-07-05"])

    (line,) = runner.invoke(app, ["log"]).stdout.strip().splitlines()

    assert "nse/cm_bhavcopy" in line
    assert "no_data" in line
    assert line.endswith("http 404")


def test_the_log_command_says_so_when_there_is_nothing_to_show(database: str) -> None:
    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "no ingestion attempts recorded"


@respx.mock
def test_a_database_that_cannot_be_written_stops_the_command_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gap in the log would hide late data later, so this is an error, not a warning."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:////no/such/folder/log.db")
    respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert result.exit_code == 1
    assert "Could not write to the ingestion log" in result.stderr


@respx.mock
def test_without_a_database_ingestion_works_and_keeps_no_log() -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=report_response())

    result = runner.invoke(app, ["ingest", "amfi", "--date", "2026-10-15"])

    assert result.exit_code == 0
    assert "ingestion log" not in result.output


# --- failure signals ----------------------------------------------------------------------

RUN_ID = "5b1f2c3e-1111-4222-8333-444455556666"


def store_summary(
    tmp_path: Path, *, signals: tuple[Signal, ...], skipped: tuple[str, ...] = ()
) -> None:
    summary = RunSummary(
        run_id=RUN_ID,
        generated_at=datetime(2026, 10, 22, 9, 40, tzinfo=UTC),
        counts={"success": 9, "pass": 153, "fail": len(signals), "skipped": len(skipped)},
        skipped_models=skipped,
        signals=signals,
    )
    LocalObjectStore(tmp_path / "raw").put(
        f"{run_prefix(RUN_ID)}/{SUMMARY_FILE}",
        summary.model_dump_json().encode("utf-8"),
        metadata={},
    )


def test_signals_lists_what_a_run_recorded(tmp_path: Path) -> None:
    signal = Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id=RUN_ID,
        detected_at=datetime(2026, 10, 22, 9, 40, tzinfo=UTC),
        name="not_null_brz_amfi__navall_nav_inr",
        check_type="not_null",
        models=("brz_amfi__navall",),
        model="brz_amfi__navall",
        column="nav_inr",
        failures=1,
        message="Got 1 result, configured to fail if != 0\nmore detail",
    )
    store_summary(tmp_path, signals=(signal,), skipped=("slv_amfi__nav",))

    result = runner.invoke(app, ["signals", RUN_ID])

    assert result.exit_code == 0
    assert f"run {RUN_ID}: 1 fail, 153 pass, 1 skipped, 9 success" in result.output
    assert "skipped because of an upstream failure: slv_amfi__nav" in result.output
    assert (
        "error test_failed not_null brz_amfi__navall.nav_inr (1 rows): "
        "Got 1 result, configured to fail if != 0"
    ) in result.output
    assert "more detail" not in result.output


def test_signals_says_so_when_a_run_raised_none(tmp_path: Path) -> None:
    store_summary(tmp_path, signals=())

    result = runner.invoke(app, ["signals", RUN_ID])

    assert result.exit_code == 0
    assert "no failure signals" in result.output


def test_signals_for_a_run_that_was_never_recorded_is_a_one_line_error() -> None:
    result = runner.invoke(app, ["signals", RUN_ID])

    assert result.exit_code == 1
    assert result.output.startswith("error:")
    assert "Traceback" not in result.output


def test_signals_refuses_a_run_id_that_is_not_a_plain_identifier() -> None:
    result = runner.invoke(app, ["signals", "../etc"])

    assert result.exit_code == 1
    assert "usable run id" in result.output
