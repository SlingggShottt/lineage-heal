"""Guards that sample source files keep the exact bytes the publishers serve.

Hooks, editors and git can all "tidy" a text file. For fixtures that would hide the
very quirks (CRLF endings, stray spaces) the loaders and bronze models must cope with.
"""

import csv
from pathlib import Path

import pytest

AMFI_FIXTURES = sorted((Path(__file__).parents[1] / "fixtures" / "amfi").glob("*.txt"))


def test_amfi_fixtures_are_present() -> None:
    assert len(AMFI_FIXTURES) == 2


@pytest.mark.parametrize("path", AMFI_FIXTURES, ids=lambda p: p.name)
def test_amfi_fixtures_keep_crlf_line_endings(path: Path) -> None:
    raw = path.read_bytes()

    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


@pytest.mark.parametrize("path", AMFI_FIXTURES, ids=lambda p: p.name)
def test_amfi_fixtures_start_with_an_eight_column_header(path: Path) -> None:
    header = path.read_bytes().split(b"\r\n", maxsplit=1)[0].decode("utf-8")

    assert header.startswith("Scheme Code;")
    assert header.count(";") == 7


def _read_nse_sample(name: str, symbol: str, series: str, close: str) -> dict[str, float]:
    """Map "SYMBOL|SERIES" to the closing price, reading columns by header name."""
    path = Path(__file__).parents[1] / "fixtures" / "nse" / name
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    return {f"{row[symbol]}|{row[series]}": float(row[close]) for row in rows}


def test_nse_samples_are_the_same_trading_day_in_both_formats() -> None:
    """The old-format and UDiFF samples must pair up so a replay has ground truth."""
    old = _read_nse_sample("cm_bhavcopy_old_2024-07-05_sample.csv", "SYMBOL", "SERIES", "CLOSE")
    udiff = _read_nse_sample(
        "cm_bhavcopy_udiff_2024-07-05_sample.csv", "TckrSymb", "SctySrs", "ClsPric"
    )

    assert old == udiff
    assert len(old) == 7


def test_live_sample_keeps_its_space_only_separator_lines() -> None:
    live = next(p for p in AMFI_FIXTURES if "live" in p.name)

    assert b" \r\n" in live.read_bytes()
