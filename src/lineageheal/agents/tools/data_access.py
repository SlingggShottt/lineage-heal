"""Read-only access to the data the Investigator inspects: stored raw files and the warehouse.

This is the only place the agent tools touch data, so the rules that keep them safe live here
(NFR-SEC-04). The warehouse is opened read-only, so no statement can change it. A table or
column is used only if the warehouse's own catalogue lists it under exactly that name, and is
then quoted, so a model can never put SQL where a name goes. Values reach queries as bound
parameters. A query that runs longer than a few seconds is interrupted. Raw files are read
through the raw zone's own checksum check and never written.
"""

import gzip
import json
import threading
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import duckdb

from lineageheal.core.errors import LineageHealError, StorageError
from lineageheal.ingestion.raw_zone import RawZone

# Longest a single query may run before it is interrupted.
QUERY_TIMEOUT_SECONDS = 5.0
# Only the header of a file is needed; reading this much of a zip member or gzip stream is
# enough for any real header and keeps a huge file from being expanded.
HEADER_READ_BYTES = 64 * 1024
# A raw JSON file is parsed whole, so refuse one far larger than any real corporate actions day.
MAX_JSON_BYTES = 20 * 1024 * 1024

# AMFI is the only semicolon-delimited feed; the NSE files are comma-delimited.
_DELIMITERS = {"amfi": ";"}

# Columns that hold the date a row belongs to, in the order they are preferred.
DATE_COLUMNS = ("price_date", "nav_date", "ex_date", "calendar_date", "trade_date")
# The model the corporate actions tool reads, and the columns of it worth showing an agent.
ACTIONS_MODEL = "slv_nse__corporate_actions"
ACTION_COLUMNS = (
    "ex_date", "symbol", "series", "action_type", "subject", "cash_amount_inr",
    "split_old_face_value_inr", "split_new_face_value_inr", "ratio_new", "ratio_held",
)  # fmt: skip
_NUMERIC_TYPES = ("DECIMAL", "DOUBLE", "FLOAT", "INTEGER", "BIGINT", "HUGEINT", "SMALLINT")


class DataAccessError(LineageHealError):
    """The data cannot be read in the way asked; the message is meant for the agent to read."""


@dataclass(frozen=True)
class RawSchema:
    """The column names of one stored raw file, in file order."""

    key: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class TableColumn:
    """One warehouse column and its type."""

    name: str
    data_type: str

    @property
    def is_numeric(self) -> bool:
        """Whether averages mean something for this column."""
        return self.data_type.upper().startswith(_NUMERIC_TYPES)


@dataclass(frozen=True)
class Table:
    """A warehouse table the catalogue lists, with the schema it lives in."""

    schema: str
    name: str
    columns: tuple[TableColumn, ...]

    @property
    def qualified(self) -> str:
        """The quoted `schema.table`, safe to put in SQL because the catalogue supplied it."""
        return f"{_quote(self.schema)}.{_quote(self.name)}"

    def column(self, name: str) -> TableColumn:
        """The column called `name`, or an error that lists the real ones."""
        for column in self.columns:
            if column.name == name:
                return column
        raise DataAccessError(
            f"{self.name} has no column {name!r}. "
            f"Columns: {', '.join(c.name for c in self.columns)}"
        )

    @property
    def date_column(self) -> str | None:
        """The column that says which day a row belongs to, if the table has one."""
        names = {column.name for column in self.columns}
        return next((candidate for candidate in DATE_COLUMNS if candidate in names), None)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class RawFileReader:
    """Reads the header of a stored raw file, whatever its packaging."""

    def __init__(self, zone: RawZone) -> None:
        """Read through `zone`, which checks each file against the hash in its key."""
        self._zone = zone

    def schema(self, source: str, dataset: str, trade_date: date) -> RawSchema:
        """The columns of the latest stored version of a feed's file for a day.

        Raises:
            DataAccessError: Nothing is stored for the day, or the file cannot be read.
        """
        try:
            keys = self._zone.stored_keys(source=source, dataset=dataset, trade_date=trade_date)
        except StorageError as exc:
            raise DataAccessError(str(exc)) from exc
        if not keys:
            raise DataAccessError(
                f"No raw file is stored for {source}/{dataset} on {trade_date.isoformat()}"
            )
        key = keys[-1]
        try:
            data = self._zone.read(key)
        except StorageError as exc:
            raise DataAccessError(str(exc)) from exc
        return RawSchema(key, self._header(data, key, _DELIMITERS.get(source, ",")))

    @staticmethod
    def _header(data: bytes, key: str, delimiter: str) -> tuple[str, ...]:
        try:
            if key.endswith(".zip"):
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    members = [m for m in archive.infolist() if not m.is_dir()]
                    if len(members) != 1:
                        raise DataAccessError(f"Expected one file in the zip, found {len(members)}")
                    with archive.open(members[0]) as member:
                        head = member.read(HEADER_READ_BYTES)
            elif key.endswith(".gz"):
                with gzip.GzipFile(fileobj=BytesIO(data)) as stream:
                    head = stream.read(HEADER_READ_BYTES)
            else:
                head = data[:MAX_JSON_BYTES] if key.endswith(".json") else data[:HEADER_READ_BYTES]
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise DataAccessError(f"The stored file is not a readable archive: {exc}") from exc

        if key.endswith(".json"):
            return _json_keys(head, len(data))
        for line in head.decode("utf-8-sig", errors="replace").splitlines():
            if line.strip():
                return tuple(part.strip() for part in line.split(delimiter))
        raise DataAccessError("The stored file is empty")


def _json_keys(head: bytes, size: int) -> tuple[str, ...]:
    if size > MAX_JSON_BYTES:
        raise DataAccessError("The stored JSON file is too large to read")
    try:
        rows = json.loads(head)
    except ValueError as exc:
        raise DataAccessError("The stored JSON file is not valid JSON") from exc
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise DataAccessError("The stored JSON file is not a list of records")
    return tuple(str(name) for name in rows[0])


class Warehouse:
    """A read-only view of a DuckDB warehouse, for profiling and sampling tables."""

    def __init__(self, path: Path) -> None:
        """Remember the file; nothing is opened until a query runs."""
        self._path = path

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if not self._path.exists():
            raise DataAccessError("The warehouse file does not exist; run dbt build first")
        try:
            connection = duckdb.connect(str(self._path), read_only=True)
        except duckdb.Error as exc:
            raise DataAccessError(f"The warehouse cannot be opened: {exc}") from exc
        timer = threading.Timer(QUERY_TIMEOUT_SECONDS, connection.interrupt)
        timer.start()
        try:
            yield connection
        except duckdb.InterruptException as exc:
            raise DataAccessError("The query took too long and was stopped") from exc
        except duckdb.Error as exc:
            raise DataAccessError(f"The warehouse could not answer: {exc}") from exc
        finally:
            timer.cancel()
            connection.close()

    def table(self, name: str) -> Table:
        """Find a model's table by exact name, so a name that is not a table never reaches SQL.

        Raises:
            DataAccessError: No table, or more than one, has that name.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "select table_schema, column_name, data_type from information_schema.columns"
                " where table_name = ? order by table_schema, ordinal_position",
                [name],
            ).fetchall()
        schemas = sorted({row[0] for row in rows})
        if not schemas:
            raise DataAccessError(f"The warehouse has no table named {name!r}")
        if len(schemas) > 1:
            raise DataAccessError(f"{name!r} exists in several schemas: {', '.join(schemas)}")
        columns = tuple(TableColumn(row[1], row[2]) for row in rows)
        return Table(schemas[0], name, columns)

    def profile(self, table: Table, column: str, day: date | None) -> dict[str, Any]:
        """Row count, nulls, distinct values, range and mean of a column, optionally for a day.

        Figures are returned as text so decimals keep their exact value.
        """
        target = table.column(column)
        where, params = self._day_filter(table, day)
        quoted = _quote(target.name)
        mean = f"cast(avg({quoted}) as varchar)" if target.is_numeric else "null"
        # Names come from the catalogue and are quoted; values are bound parameters.
        sql = (
            f"select count(*), count(*) - count({quoted}), count(distinct {quoted}),"  # noqa: S608
            f" cast(min({quoted}) as varchar), cast(max({quoted}) as varchar), {mean}"
            f" from {table.qualified}{where}"
        )
        with self._connect() as connection:
            rows, nulls, distinct, low, high, average = connection.execute(sql, params).fetchone()  # type: ignore[misc]
        return {
            "rows": rows,
            "nulls": nulls,
            "null_share": round(nulls / rows, 6) if rows else None,
            "distinct": distinct,
            "min": low,
            "max": high,
            "mean": average,
        }

    def sample(
        self,
        table: Table,
        *,
        columns: Sequence[str],
        day: date | None,
        match: tuple[str, str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Up to `limit` rows of the chosen columns, for a day and one `column = value` if given."""
        chosen = [table.column(name).name for name in columns] or [c.name for c in table.columns]
        where, params = self._day_filter(table, day)
        if match is not None:
            column, value = match
            joiner = " and" if where else " where"
            where += f"{joiner} cast({_quote(table.column(column).name)} as varchar) = ?"
            params = [*params, value]
        sql = (
            f"select {', '.join(_quote(name) for name in chosen)} from {table.qualified}{where}"  # noqa: S608
            f" limit {int(limit)}"
        )
        with self._connect() as connection:
            result = connection.execute(sql, params).fetchall()
        return [dict(zip(chosen, row, strict=True)) for row in result]

    def actions_near(self, symbol: str, start: date, end: date, limit: int) -> list[dict[str, Any]]:
        """Corporate actions for a symbol with an ex-date from `start` to `end`, earliest first."""
        table = self.table(ACTIONS_MODEL)
        names = [c.name for c in table.columns if c.name in ACTION_COLUMNS]
        sql = (
            f"select {', '.join(_quote(name) for name in names)} from {table.qualified}"  # noqa: S608
            f" where symbol = ? and ex_date between ? and ? order by ex_date, action_type"
            f" limit {int(limit)}"
        )
        with self._connect() as connection:
            result = connection.execute(sql, [symbol, start, end]).fetchall()
        return [dict(zip(names, row, strict=True)) for row in result]

    @staticmethod
    def _day_filter(table: Table, day: date | None) -> tuple[str, list[Any]]:
        if day is None:
            return "", []
        date_column = table.date_column
        if date_column is None:
            raise DataAccessError(f"{table.name} has no date column to pick a day with")
        return f" where {_quote(date_column)} = ?", [day]
