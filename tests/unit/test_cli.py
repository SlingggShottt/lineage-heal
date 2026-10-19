"""FR-ING-08 and FR-ORC-05: the ingest and backfill commands."""

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
from lineageheal.ingestion.amfi import AMFI_REPORT_URL
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

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
        == "backfill amfi 2026-10-13..2026-10-15: 2 stored, 0 unchanged, 1 no_data, 0 failed"
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
    assert "2026-10-14 amfi failed: AMFI returned HTTP 403" in result.stderr
    assert (
        result.stdout.strip()
        .splitlines()[-1]
        .endswith("2 stored, 0 unchanged, 0 no_data, 1 failed")
    )
    assert len(stored_files(tmp_path)) == 2


@respx.mock
def test_backfill_can_be_re_run_without_duplicating_data(tmp_path: Path) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=report_response())
    arguments = ["backfill", "amfi", "--start", "2026-10-15", "--end", "2026-10-15"]

    runner.invoke(app, arguments)
    second = runner.invoke(app, arguments)

    assert "1 unchanged" in second.stdout
    assert len(stored_files(tmp_path)) == 1


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
