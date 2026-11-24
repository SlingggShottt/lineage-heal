"""The zip helpers keep the entry's name and produce the same bytes for the same input."""

import io
import zipfile

import pytest

from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.core.errors import BenchmarkError

NAME = "BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv"


def test_a_file_survives_a_round_trip_under_its_own_name() -> None:
    assert read_single_csv(write_single_csv(NAME, b"a,b\n1,2\n")) == (NAME, b"a,b\n1,2\n")


def test_the_same_input_gives_identical_bytes() -> None:
    assert write_single_csv(NAME, b"x") == write_single_csv(NAME, b"x")


def test_the_written_zip_passes_the_integrity_check_the_loader_uses() -> None:
    with zipfile.ZipFile(io.BytesIO(write_single_csv(NAME, b"x"))) as archive:
        assert archive.testzip() is None


def test_bytes_that_are_not_a_zip_are_refused() -> None:
    with pytest.raises(BenchmarkError, match="not a valid zip"):
        read_single_csv(b"plain text")


def test_a_zip_with_two_files_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.csv", "1")
        archive.writestr("b.csv", "2")

    with pytest.raises(BenchmarkError, match="found 2"):
        read_single_csv(buffer.getvalue())
