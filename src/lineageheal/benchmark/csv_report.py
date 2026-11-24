"""Line helpers for comma-separated reports such as NSE's bhavcopy (FR-BEN-02).

The counterparts of the AMFI helpers in `faults.py`. Lines are kept as published, so an injector
that changes one field leaves every other byte alone, including the line ending the file uses.
Fields are split on commas outside double quotes and keep their raw text (quotes included), so
a field that is not changed is never re-quoted.
"""

from __future__ import annotations

from lineageheal.core.errors import BenchmarkError


def split_lines(content: bytes) -> tuple[list[str], str]:
    """A file's lines and the line ending it uses (CRLF if it has any, else LF)."""
    text = content.decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.split(eol), eol


def split_fields(line: str) -> list[str]:
    """A line's fields, split on commas outside quotes, with their raw text."""
    fields: list[str] = []
    start = 0
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            fields.append(line[start:i])
            start = i + 1
    fields.append(line[start:])
    return fields


def column_index(header: str, column: str) -> int:
    """Where a column is in the header, found by name."""
    fields = split_fields(header)
    if column not in fields:
        raise BenchmarkError(f"Report has no '{column}' column, so the fault cannot be injected")
    return fields.index(column)


def data_row_indexes(lines: list[str]) -> list[int]:
    """Indexes of the data rows: non-blank lines with as many fields as the header."""
    width = len(split_fields(lines[0]))
    return [
        i for i, line in enumerate(lines[1:], start=1) if line and len(split_fields(line)) == width
    ]
