"""Fault injectors for AMFI NAV reports.

Each injector is a pure function from a report's bytes to a damaged copy that differs
from the original in one controlled way, so the tests and the benchmark know exactly
what went wrong. Injectors never write anything; callers store the result in a local
sandbox raw zone, never in production (see docs/design_architecture.md, section 5.1).

This is the first slice of the benchmark's injectors (LH-067): AMFI reports only.
"""

from collections.abc import Callable
from dataclasses import dataclass

from lineageheal.core.errors import BenchmarkError

NAV_COLUMN = "Net Asset Value"
DATE_COLUMN = "Date"
RENAMED_NAV_COLUMN = "NAV"
WRONG_DATE = "01-Jan-2020"

_SEPARATOR = "\r\n"


@dataclass(frozen=True)
class FaultSpec:
    """One named way of damaging a report."""

    name: str
    fault_class: str  # one of the benchmark's five classes, or "invalid_values"
    description: str
    apply: Callable[[bytes], bytes]


def _lines(content: bytes) -> list[str]:
    return content.decode("utf-8").split(_SEPARATOR)


def _join(lines: list[str]) -> bytes:
    return _SEPARATOR.join(lines).encode("utf-8")


def _column_index(header: str, column: str) -> int:
    fields = header.split(";")
    if column not in fields:
        raise BenchmarkError(f"Report has no '{column}' column, so the fault cannot be injected")
    return fields.index(column)


def _data_row_indexes(lines: list[str]) -> list[int]:
    """Indexes of the data rows: same width as the header and a numeric scheme code."""
    width = lines[0].count(";")
    return [
        i
        for i, line in enumerate(lines[1:], start=1)
        if line.count(";") == width and line.split(";")[0].isdigit()
    ]


def _first_data_row(lines: list[str]) -> int:
    rows = _data_row_indexes(lines)
    if not rows:
        raise BenchmarkError("Report has no data rows, so the fault cannot be injected")
    return rows[0]


def _edit_first_nav(content: bytes, edit: Callable[[str], str]) -> bytes:
    lines = _lines(content)
    nav = _column_index(lines[0], NAV_COLUMN)
    row = _first_data_row(lines)
    fields = lines[row].split(";")
    fields[nav] = edit(fields[nav])
    lines[row] = ";".join(fields)
    return _join(lines)


def blank_nav(content: bytes) -> bytes:
    """Empty the NAV of the first data row, as if a value went missing."""
    return _edit_first_nav(content, lambda _nav: "")


def negative_nav(content: bytes) -> bytes:
    """Flip the sign of the first data row's NAV."""
    return _edit_first_nav(content, lambda nav: f"-{nav}")


def duplicate_row(content: bytes) -> bytes:
    """Repeat the first data row directly after itself."""
    lines = _lines(content)
    row = _first_data_row(lines)
    lines.insert(row + 1, lines[row])
    return _join(lines)


def wrong_date(content: bytes) -> bytes:
    """Give every data row an old NAV date, as if a stale report were served."""
    lines = _lines(content)
    date = _column_index(lines[0], DATE_COLUMN)
    rows = _data_row_indexes(lines)
    if not rows:
        raise BenchmarkError("Report has no data rows, so the fault cannot be injected")
    for row in rows:
        fields = lines[row].split(";")
        fields[date] = WRONG_DATE
        lines[row] = ";".join(fields)
    return _join(lines)


def rename_nav_column(content: bytes) -> bytes:
    """Rename the NAV column in the header, as if the publisher changed its schema."""
    lines = _lines(content)
    fields = lines[0].split(";")
    fields[_column_index(lines[0], NAV_COLUMN)] = RENAMED_NAV_COLUMN
    lines[0] = ";".join(fields)
    return _join(lines)


AMFI_FAULTS: dict[str, FaultSpec] = {
    spec.name: spec
    for spec in (
        FaultSpec("blank-nav", "nulls", "One NAV is missing", blank_nav),
        FaultSpec("negative-nav", "invalid_values", "One NAV has the wrong sign", negative_nav),
        FaultSpec("duplicate-row", "duplicates", "One scheme appears twice", duplicate_row),
        FaultSpec("wrong-date", "late_data", "The report holds NAVs for an old date", wrong_date),
        FaultSpec(
            "rename-column", "schema_drift", "The NAV column has a new name", rename_nav_column
        ),
    )
}
