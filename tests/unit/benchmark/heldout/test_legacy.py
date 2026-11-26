"""D-81: the converter reproduces NSE's real pre-July-2024 file from the UDiFF one."""

from datetime import date
from pathlib import Path

import pytest

from lineageheal.benchmark.heldout.legacy import (
    LEGACY_COLUMNS,
    legacy_file_name,
    to_legacy_csv,
    to_legacy_zip,
)
from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.core.errors import BenchmarkError

NSE = Path(__file__).parents[3] / "fixtures" / "nse"
OLD = (NSE / "cm_bhavcopy_old_2024-07-05_sample.csv").read_bytes()
UDIFF = (NSE / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()


def test_the_converted_file_is_byte_for_byte_the_real_old_file() -> None:
    assert to_legacy_csv(UDIFF) == OLD


def test_the_header_and_every_row_end_with_the_old_formats_trailing_comma() -> None:
    lines = to_legacy_csv(UDIFF).decode().splitlines()

    assert lines[0] == ",".join(LEGACY_COLUMNS) + ","
    assert all(line.endswith(",") for line in lines)


def test_prices_lose_their_padding_zeros_and_dates_change_shape() -> None:
    text = to_legacy_csv(UDIFF).decode()

    assert "0MOFSL27,N3,990,990,990,990,990,980,50,49500,05-JUL-2024,1,INE338I07099," in text
    assert "990.00" not in text
    assert "2024-07-05" not in text


def test_the_conversion_is_deterministic_and_sorted_by_symbol() -> None:
    reversed_rows = UDIFF.decode().splitlines()
    shuffled = "\n".join([reversed_rows[0], *reversed(reversed_rows[1:])]).encode() + b"\n"

    assert to_legacy_csv(shuffled) == to_legacy_csv(UDIFF) == OLD


def test_a_zip_is_converted_under_the_old_archives_file_name() -> None:
    name, archive = to_legacy_zip(write_single_csv("BhavCopy.csv", UDIFF), date(2024, 7, 5))

    assert name == "cm05JUL2024bhav.csv.zip"
    assert read_single_csv(archive) == ("cm05JUL2024bhav.csv", OLD)
    assert to_legacy_zip(write_single_csv("x.csv", UDIFF), date(2024, 7, 5))[1] == archive


def test_the_old_archive_names_a_day_with_its_month_in_capitals() -> None:
    assert legacy_file_name(date(2024, 1, 2)) == "cm02JAN2024bhav.csv.zip"
    assert legacy_file_name(date(2024, 12, 31)) == "cm31DEC2024bhav.csv.zip"


def test_a_file_that_is_not_udiff_or_is_empty_is_refused() -> None:
    with pytest.raises(BenchmarkError, match="not a UDiFF bhavcopy"):
        to_legacy_csv(OLD)
    header_only = UDIFF.decode().splitlines()[0].encode() + b"\n"
    with pytest.raises(BenchmarkError, match="no rows"):
        to_legacy_csv(header_only)
