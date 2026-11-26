"""Applying a fault specification to a raw file (FR-BEN-02).

An injector is a pure function from a file's bytes and a specification to the damaged bytes, or
to None when the fault is that the file never arrived. Nothing is written here: the caller stores
the result in a sandbox copy, never in the real raw zone. For a given specification and seed the
output is always the same, because the row to damage is chosen from a hash of the seed, not from
a random generator whose sequence could change between Python versions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from lineageheal.benchmark import csv_report, faults
from lineageheal.benchmark.html_table import HtmlTable
from lineageheal.benchmark.spec import (
    BlankValue,
    DuplicateRow,
    FaultCase,
    RealFile,
    RenameColumn,
    ScaleValues,
    StaleDate,
    WithholdFile,
)
from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.core.errors import BenchmarkError

# Sources and datasets the injectors know how to read. The other NSE datasets are not covered.
SUPPORTED: frozenset[tuple[str, str]] = frozenset(
    {("amfi", "navall"), ("nse", "cm_bhavcopy"), ("fbil", "reference_rates")}
)


class _Editable(Protocol):
    """What an injector needs from a file, whatever its format."""

    def fields(self, row: int) -> list[str]:
        """The fields of one row, 0 being the header."""

    def set_fields(self, row: int, fields: list[str]) -> None:
        """Replace one row's fields."""

    def column(self, name: str) -> int:
        """Where a column is, found by name in the header."""

    def rows(self) -> list[int]:
        """Indexes of the data rows."""

    def duplicate(self, row: int) -> None:
        """Repeat a row directly after itself."""

    def to_bytes(self) -> bytes:
        """The file as bytes."""


@dataclass
class _Table:
    """A report's lines with what is needed to edit fields in them and write them back."""

    lines: list[str]
    separator: str
    eol: str
    column_index: Callable[[str, str], int]
    data_rows: Callable[[list[str]], list[int]]
    split: Callable[[str], list[str]]

    def fields(self, row: int) -> list[str]:
        """The fields of one line."""
        return self.split(self.lines[row])

    def set_fields(self, row: int, fields: list[str]) -> None:
        """Replace one line with these fields."""
        self.lines[row] = self.separator.join(fields)

    def column(self, name: str) -> int:
        """Where a column is, found by name in the header."""
        return self.column_index(self.lines[0], name)

    def rows(self) -> list[int]:
        """Indexes of the data rows."""
        return self.data_rows(self.lines)

    def duplicate(self, row: int) -> None:
        """Repeat a line directly after itself."""
        self.lines.insert(row + 1, self.lines[row])

    def to_bytes(self) -> bytes:
        """The report as bytes, with its original line ending."""
        return self.eol.join(self.lines).encode("utf-8")


def _amfi_table(content: bytes) -> _Table:
    return _Table(
        faults.report_lines(content),
        ";",
        "\r\n",
        faults.column_index,
        faults.data_row_indexes,
        lambda line: line.split(";"),
    )


def _csv_table(content: bytes) -> _Table:
    lines, eol = csv_report.split_lines(content)
    return _Table(
        lines,
        ",",
        eol,
        csv_report.column_index,
        csv_report.data_row_indexes,
        csv_report.split_fields,
    )


def apply_fault(case: FaultCase, content: bytes) -> bytes | None:
    """Damage a file as the specification says.

    Args:
        case: The fault to inject.
        content: The file as published (a zip for NSE bhavcopy).

    Returns:
        The damaged bytes, or None when the fault is that no file arrives.

    Raises:
        BenchmarkError: The dataset has no injector yet, or the file does not have what the fault
            needs (a column, a data row), so it cannot be damaged as specified.
    """
    key = (case.source, case.dataset)
    if key not in SUPPORTED:
        raise BenchmarkError(
            f"{case.id}: there is no injector for {case.source}/{case.dataset} yet"
        )
    if isinstance(case.injection, WithholdFile):
        return None
    if isinstance(case.injection, RealFile):
        return content
    if key == ("nse", "cm_bhavcopy"):
        # The zip is opened, its CSV damaged and a new zip written with the same entry name.
        name, csv_bytes = read_single_csv(content)
        return write_single_csv(name, _damage(case, _csv_table(csv_bytes)))
    if key == ("fbil", "reference_rates"):
        return _damage(case, HtmlTable(content))
    return _damage(case, _amfi_table(content))


def _damage(case: FaultCase, table: _Editable) -> bytes:
    injection = case.injection
    if isinstance(injection, RenameColumn):
        return _rename_column(table, injection)
    if isinstance(injection, StaleDate):
        return _stale_date(table, injection)
    if isinstance(injection, ScaleValues):
        return _scale_values(table, injection)
    rows = table.rows()
    if not rows:
        raise BenchmarkError(f"{case.id}: the file has no data rows, so it cannot be damaged")
    row = rows[_pick(case.seed, len(rows))]
    if isinstance(injection, BlankValue):
        return _blank_value(table, row, injection)
    if isinstance(injection, DuplicateRow):
        table.duplicate(row)
        return table.to_bytes()
    raise BenchmarkError(f"{case.id}: unhandled injection {injection.type}")  # pragma: no cover


def _pick(seed: int, count: int) -> int:
    """A stable index in `range(count)` for a seed."""
    return int(hashlib.sha256(f"fault-row:{seed}".encode()).hexdigest(), 16) % count


def _rename_column(table: _Editable, injection: RenameColumn) -> bytes:
    fields = table.fields(0)
    fields[table.column(injection.column)] = injection.new_name
    table.set_fields(0, fields)
    return table.to_bytes()


def _blank_value(table: _Editable, row: int, injection: BlankValue) -> bytes:
    index = table.column(injection.column)
    fields = table.fields(row)
    fields[index] = ""
    table.set_fields(row, fields)
    return table.to_bytes()


def _stale_date(table: _Editable, injection: StaleDate) -> bytes:
    index = table.column(injection.column)
    for row in _require_rows(table):
        fields = table.fields(row)
        fields[index] = injection.value
        table.set_fields(row, fields)
    return table.to_bytes()


def _scale_values(table: _Editable, injection: ScaleValues) -> bytes:
    """Multiply every numeric value in a column, keeping its decimal places.

    Non-numeric values (AMFI publishes `N.A.` for some schemes; bhavcopy leaves some prices
    empty) are left exactly as they were.
    """
    index = table.column(injection.column)
    changed = 0
    for row in _require_rows(table):
        fields = table.fields(row)
        try:
            fields[index] = str(Decimal(fields[index]) * injection.factor)
        except InvalidOperation:
            continue
        table.set_fields(row, fields)
        changed += 1
    if changed == 0:
        raise BenchmarkError(f"no numeric values in {injection.column!r} to scale")
    return table.to_bytes()


def _require_rows(table: _Editable) -> list[int]:
    rows = table.rows()
    if not rows:
        raise BenchmarkError("the file has no data rows, so it cannot be damaged")
    return rows
