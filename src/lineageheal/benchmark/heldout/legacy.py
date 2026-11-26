"""NSE's pre-July-2024 bhavcopy format, made from the UDiFF files we hold (D-20, D-81).

NSE served the old files only up to 5 July 2024, and the backfill stores UDiFF only, but the two
formats hold the same data: for 5 July 2024 every row matches on close, volume, value and trade
count (design 5.x, fixtures in `tests/fixtures/nse/`). So a faithful old-format file can be made
from a UDiFF one by renaming the columns, trimming the zeros UDiFF pads onto prices, writing the
date as `05-JUL-2024`, dropping the columns the old format did not have and ending every line with
a comma. The conversion is deterministic, and a test compares it byte for byte with the real old
file for that day.
"""

from __future__ import annotations

from datetime import date

from lineageheal.benchmark import csv_report
from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.core.errors import BenchmarkError

# Old column, UDiFF column: the old file's order, which is also its header.
LEGACY_TO_UDIFF: tuple[tuple[str, str], ...] = (
    ("SYMBOL", "TckrSymb"),
    ("SERIES", "SctySrs"),
    ("OPEN", "OpnPric"),
    ("HIGH", "HghPric"),
    ("LOW", "LwPric"),
    ("CLOSE", "ClsPric"),
    ("LAST", "LastPric"),
    ("PREVCLOSE", "PrvsClsgPric"),
    ("TOTTRDQTY", "TtlTradgVol"),
    ("TOTTRDVAL", "TtlTrfVal"),
    ("TIMESTAMP", "TradDt"),
    ("TOTALTRADES", "TtlNbOfTxsExctd"),
    ("ISIN", "ISIN"),
)
LEGACY_COLUMNS = tuple(old for old, _ in LEGACY_TO_UDIFF)
_NUMBERS = frozenset(
    {"OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL"}
)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def legacy_file_name(day: date) -> str:
    """The old archive's zip name: `cm05JUL2024bhav.csv.zip`."""
    return f"cm{day.day:02d}{_MONTHS[day.month - 1]}{day.year}bhav.csv.zip"


def to_legacy_csv(udiff: bytes) -> bytes:
    """A UDiFF bhavcopy CSV in the old format, rows ordered by symbol and series as NSE did.

    Raises:
        BenchmarkError: The file lacks a column the old format needs, or has no rows.
    """
    lines, _ = csv_report.split_lines(udiff)
    header = csv_report.split_fields(lines[0])
    missing = [new for _, new in LEGACY_TO_UDIFF if new not in header]
    if missing:
        raise BenchmarkError(f"not a UDiFF bhavcopy: no {missing} column")
    position = {name: header.index(name) for _, name in LEGACY_TO_UDIFF}
    rows: list[tuple[str, str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        fields = csv_report.split_fields(line)
        converted = [_legacy_value(old, fields[position[new]]) for old, new in LEGACY_TO_UDIFF]
        rows.append((converted[0], converted[1], ",".join(converted) + ","))
    if not rows:
        raise BenchmarkError("the file has no rows to convert")
    ordered = [text for _, _, text in sorted(rows)]
    return (",".join(LEGACY_COLUMNS) + ",\n" + "\n".join(ordered) + "\n").encode("utf-8")


def to_legacy_zip(udiff_zip: bytes, day: date) -> tuple[str, bytes]:
    """The old archive's zip for a day, made from that day's UDiFF zip: its name and bytes."""
    _, csv = read_single_csv(udiff_zip)
    name = legacy_file_name(day)
    return name, write_single_csv(name.removesuffix(".zip"), to_legacy_csv(csv))


def _legacy_value(column: str, value: str) -> str:
    if column in _NUMBERS and "." in value:
        return value.rstrip("0").rstrip(".")
    if column == "TIMESTAMP":
        year, month, day = value.split("-")
        return f"{day}-{_MONTHS[int(month) - 1]}-{year}"
    return value
