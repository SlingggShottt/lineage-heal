"""Staging: derived copies of raw files that DuckDB cannot read.

DuckDB, and therefore dbt, cannot read zip archives or HTML tables, but the raw zone keeps
files exactly as published. Staging bridges the two without touching the raw zone: each raw
zip is unzipped, and each raw HTML table written as CSV, into a sibling `staged/` area. A
staged key is derived from its raw key, so the two are linked one to one, and rebuilding the
staged area from the raw zone always gives the same result (decisions D-22 and D-81).
"""

import csv
import hashlib
import zipfile
from html.parser import HTMLParser
from io import BytesIO, StringIO

import structlog

from lineageheal.core.errors import IngestionError, StorageError
from lineageheal.ingestion.raw_zone import ObjectStore, RawZone

log = structlog.get_logger(__name__)

RAW_ROOT = "raw/"
STAGED_ROOT = "staged/"

# A zip is untrusted input; refuse one that would expand far beyond any real bhavcopy
# (about 0.6 MB).
MAX_STAGED_BYTES = 256 * 1024 * 1024

# The same caution for a table: a year of FBIL rates is about 100 KB.
MAX_TABLE_BYTES = 16 * 1024 * 1024


def staged_key_for(raw_key: str) -> str:
    """Return the staged key for a raw zip key.

    `raw/source=nse/.../<hash>__file.csv.zip` becomes `staged/source=nse/.../<hash>__file.csv`,
    so the hash prefix still identifies which raw version the file came from.

    Raises:
        StorageError: The key is not a raw zip key.
    """
    if not raw_key.startswith(RAW_ROOT) or not raw_key.endswith(".zip"):
        raise StorageError(f"Not a raw zip key, cannot stage: {raw_key}")
    return STAGED_ROOT + raw_key.removeprefix(RAW_ROOT).removesuffix(".zip")


class ZipStager:
    """Unzips raw archives into the staged area of the same store."""

    def __init__(self, store: ObjectStore) -> None:
        """Create a stager that reads and writes through `store`."""
        self._store = store

    def stage(self, raw_key: str) -> str:
        """Unzip a raw archive into the staged area, unless it is already there.

        Args:
            raw_key: The key of a raw zip holding exactly one file.

        Returns:
            The key of the staged copy.

        Raises:
            StorageError: The key is not a raw zip key, or the raw object is missing or
                fails its checksum.
            IngestionError: The archive is not a single-file zip, or is too large when
                expanded.
        """
        staged_key = staged_key_for(raw_key)
        if self._store.exists(staged_key):
            return staged_key

        archive = RawZone(self._store).read(raw_key)  # verifies the hash in the key
        content = self._extract_single_file(archive, raw_key)
        self._store.put(
            staged_key,
            content,
            metadata={"sha256": hashlib.sha256(content).hexdigest(), "raw_key": raw_key},
        )
        log.info("file_staged", raw_key=raw_key, staged_key=staged_key, size_bytes=len(content))
        return staged_key

    @staticmethod
    def _extract_single_file(archive: bytes, raw_key: str) -> bytes:
        try:
            with zipfile.ZipFile(BytesIO(archive)) as zipped:
                members = [m for m in zipped.infolist() if not m.is_dir()]
                if len(members) != 1:
                    raise IngestionError(f"Expected one file in {raw_key}, found {len(members)}")
                if members[0].file_size > MAX_STAGED_BYTES:
                    raise IngestionError(f"File in {raw_key} is too large to stage")
                return zipped.read(members[0])
        except zipfile.BadZipFile as exc:
            raise IngestionError(f"Not a valid zip, cannot stage: {raw_key}") from exc


def table_staged_key_for(raw_key: str) -> str:
    """Return the staged key for a raw HTML table key.

    `raw/source=fbil/.../<hash>__file.xls` becomes `staged/source=fbil/.../<hash>__file.csv`.

    Raises:
        StorageError: The key is not a raw `.xls` key.
    """
    if not raw_key.startswith(RAW_ROOT) or not raw_key.endswith(".xls"):
        raise StorageError(f"Not a raw table key, cannot stage: {raw_key}")
    return STAGED_ROOT + raw_key.removeprefix(RAW_ROOT).removesuffix(".xls") + ".csv"


class _TableParser(HTMLParser):
    """Collects the cell text of every row, and notes whether the table was closed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables = 0
        self.ended = False
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    @property
    def is_cut_off(self) -> bool:
        """Whether the document ended inside a row or a cell, or never closed its table."""
        return not self.ended or self._row is not None or self._cell is not None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables += 1
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()
            self.ended = True

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    # HTML lets a row or cell stay open until the next one starts, so both are closed lazily.
    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None


def parse_html_table(content: bytes) -> list[list[str]]:
    """Return the rows of the one table in an HTML document, as the text of each cell.

    Column names are not checked: a renamed or reordered column must reach dbt, where schema
    drift is reported. Only a document that is not a whole, regular table is refused.

    Args:
        content: The document, UTF-8 encoded.

    Returns:
        Every row including the header row, each with the same number of cells.

    Raises:
        IngestionError: The document is too large, is not UTF-8, does not hold exactly one
            table, is cut off, or has rows with different numbers of cells.
    """
    if len(content) > MAX_TABLE_BYTES:
        raise IngestionError("The table is too large to stage")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestionError("The table is not UTF-8 text") from exc

    parser = _TableParser()
    parser.feed(text)
    parser.close()
    if parser.tables != 1:
        raise IngestionError(f"Expected one table, found {parser.tables}")
    if parser.is_cut_off:
        raise IngestionError("The table is cut off before it ends")
    if not parser.rows or not parser.rows[0]:
        raise IngestionError("The table has no rows or no cells")
    if len({len(row) for row in parser.rows}) != 1:
        raise IngestionError("The rows of the table have different numbers of cells")
    return parser.rows


class HtmlTableStager:
    """Writes raw HTML tables as CSV into the staged area of the same store."""

    def __init__(self, store: ObjectStore) -> None:
        """Create a stager that reads and writes through `store`."""
        self._store = store

    def stage(self, raw_key: str) -> str:
        """Write a raw table as CSV into the staged area, unless it is already there.

        Args:
            raw_key: The key of a raw `.xls` file that holds one HTML table.

        Returns:
            The key of the staged copy.

        Raises:
            StorageError: The key is not a raw table key, or the raw object is missing or
                fails its checksum.
            IngestionError: The file is not a whole table (see `parse_html_table`).
        """
        staged_key = table_staged_key_for(raw_key)
        if self._store.exists(staged_key):
            return staged_key

        rows = parse_html_table(RawZone(self._store).read(raw_key))  # verifies the hash in the key
        buffer = StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        content = buffer.getvalue().encode("utf-8")
        self._store.put(
            staged_key,
            content,
            metadata={"sha256": hashlib.sha256(content).hexdigest(), "raw_key": raw_key},
        )
        log.info("table_staged", raw_key=raw_key, staged_key=staged_key, size_bytes=len(content))
        return staged_key
