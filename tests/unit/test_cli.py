"""FR-ING-08 and FR-ORC-05: the ingest and backfill commands."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from lineageheal import cli
from lineageheal.cli import app, today_ist
from lineageheal.ingestion.amfi import AMFI_REPORT_URL

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"
REPORT = (FIXTURES / "navall_history_2026-10-15_sample.txt").read_bytes()
NO_DATA_PAGE = (FIXTURES / "navall_no_data_response.html").read_bytes()

runner = CliRunner()


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
        ["ingest", "nse", "--date", "2026-10-15"],
    ],
)
def test_bad_input_is_a_usage_error(arguments: list[str]) -> None:
    assert runner.invoke(app, arguments).exit_code == 2


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
