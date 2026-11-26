"""Editing the HTML table FBIL's reference rates are stored as (FR-BEN-02).

The RBI archive answers with an HTML document holding one table, saved as published. An injector
has to change one header cell, one value or one row and leave every other byte alone, as
`csv_report.py` does for CSV. Rows and cells are found with patterns over the original text and
only the changed span is rewritten, so the document's own styling and whitespace survive.
"""

from __future__ import annotations

import re

from lineageheal.core.errors import BenchmarkError

_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL = re.compile(r"(<t[dh]\b[^>]*>)(.*?)(</t[dh]>)", re.DOTALL | re.IGNORECASE)


class HtmlTable:
    """One table's text, with its rows and cells addressable by index."""

    def __init__(self, content: bytes) -> None:
        """Parse a document that holds a table.

        Raises:
            BenchmarkError: The bytes are not UTF-8 or hold no table rows.
        """
        try:
            self._text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BenchmarkError("the table is not UTF-8 text") from exc
        if not _ROW.search(self._text):
            raise BenchmarkError("the file holds no table rows")

    def fields(self, row: int) -> list[str]:
        """The text of each cell of a row, 0 being the header."""
        return [match.group(2) for match in _CELL.finditer(self._row(row).group(0))]

    def set_fields(self, row: int, fields: list[str]) -> None:
        """Rewrite the text of a row's cells, leaving the tags around them as they were."""
        span = self._row(row)
        values = iter(fields)
        rebuilt = _CELL.sub(lambda m: f"{m.group(1)}{next(values)}{m.group(3)}", span.group(0))
        self._text = self._text[: span.start()] + rebuilt + self._text[span.end() :]

    def column(self, name: str) -> int:
        """Where a column is, found by name in the header."""
        header = self.fields(0)
        if name not in header:
            raise BenchmarkError(f"Report has no '{name}' column, so the fault cannot be injected")
        return header.index(name)

    def rows(self) -> list[int]:
        """Indexes of the data rows: those with as many cells as the header."""
        width = len(self.fields(0))
        count = len(_ROW.findall(self._text))
        return [i for i in range(1, count) if len(self.fields(i)) == width]

    def duplicate(self, row: int) -> None:
        """Repeat a row directly after itself."""
        span = self._row(row)
        self._text = self._text[: span.end()] + span.group(0) + self._text[span.end() :]

    def to_bytes(self) -> bytes:
        """The document as bytes."""
        return self._text.encode("utf-8")

    def _row(self, index: int) -> re.Match[str]:
        for position, match in enumerate(_ROW.finditer(self._text)):
            if position == index:
                return match
        raise BenchmarkError(f"the table has no row {index}")
