"""Applying a fault specification to a raw file (FR-BEN-02).

An injector is a pure function from a file's bytes and a specification to the damaged bytes, or
to None when the fault is that the file never arrived. Nothing is written here: the caller stores
the result in a sandbox copy, never in the real raw zone. For a given specification and seed the
output is always the same, because the row to damage is chosen from a hash of the seed, not from
a random generator whose sequence could change between Python versions.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from lineageheal.benchmark.faults import (
    column_index,
    data_row_indexes,
    join_lines,
    report_lines,
)
from lineageheal.benchmark.spec import (
    BlankValue,
    DuplicateRow,
    FaultCase,
    RenameColumn,
    ScaleValues,
    StaleDate,
    WithholdFile,
)
from lineageheal.core.errors import BenchmarkError

# Sources and datasets the injectors know how to read. NSE and FBIL follow (LH-105, LH-080).
SUPPORTED: frozenset[tuple[str, str]] = frozenset({("amfi", "navall")})


def apply_fault(case: FaultCase, content: bytes) -> bytes | None:
    """Damage a file as the specification says.

    Args:
        case: The fault to inject.
        content: The file as published.

    Returns:
        The damaged bytes, or None when the fault is that no file arrives.

    Raises:
        BenchmarkError: The dataset has no injector yet, or the file does not have what the fault
            needs (a column, a data row), so it cannot be damaged as specified.
    """
    if (case.source, case.dataset) not in SUPPORTED:
        raise BenchmarkError(
            f"{case.id}: there is no injector for {case.source}/{case.dataset} yet"
        )
    injection = case.injection
    if isinstance(injection, WithholdFile):
        return None
    lines = report_lines(content)
    if isinstance(injection, RenameColumn):
        return _rename_column(lines, injection)
    if isinstance(injection, StaleDate):
        return _stale_date(lines, injection)
    if isinstance(injection, ScaleValues):
        return _scale_values(lines, injection)
    rows = data_row_indexes(lines)
    if not rows:
        raise BenchmarkError(f"{case.id}: the file has no data rows, so it cannot be damaged")
    row = rows[_pick(case.seed, len(rows))]
    if isinstance(injection, BlankValue):
        return _blank_value(lines, row, injection)
    if isinstance(injection, DuplicateRow):
        lines.insert(row + 1, lines[row])
        return join_lines(lines)
    raise BenchmarkError(f"{case.id}: unhandled injection {injection.type}")  # pragma: no cover


def _pick(seed: int, count: int) -> int:
    """A stable index in `range(count)` for a seed."""
    return int(hashlib.sha256(f"fault-row:{seed}".encode()).hexdigest(), 16) % count


def _rename_column(lines: list[str], injection: RenameColumn) -> bytes:
    fields = lines[0].split(";")
    fields[column_index(lines[0], injection.column)] = injection.new_name
    lines[0] = ";".join(fields)
    return join_lines(lines)


def _blank_value(lines: list[str], row: int, injection: BlankValue) -> bytes:
    index = column_index(lines[0], injection.column)
    fields = lines[row].split(";")
    fields[index] = ""
    lines[row] = ";".join(fields)
    return join_lines(lines)


def _stale_date(lines: list[str], injection: StaleDate) -> bytes:
    index = column_index(lines[0], injection.column)
    rows = _require_rows(lines)
    for row in rows:
        fields = lines[row].split(";")
        fields[index] = injection.value
        lines[row] = ";".join(fields)
    return join_lines(lines)


def _scale_values(lines: list[str], injection: ScaleValues) -> bytes:
    """Multiply every numeric value in a column, keeping its decimal places.

    Non-numeric values (AMFI publishes `N.A.` for some schemes) are left exactly as they were.
    """
    index = column_index(lines[0], injection.column)
    changed = 0
    for row in _require_rows(lines):
        fields = lines[row].split(";")
        try:
            fields[index] = str(Decimal(fields[index]) * injection.factor)
        except InvalidOperation:
            continue
        lines[row] = ";".join(fields)
        changed += 1
    if changed == 0:
        raise BenchmarkError(f"no numeric values in {injection.column!r} to scale")
    return join_lines(lines)


def _require_rows(lines: list[str]) -> list[int]:
    rows = data_row_indexes(lines)
    if not rows:
        raise BenchmarkError("the file has no data rows, so it cannot be damaged")
    return rows
