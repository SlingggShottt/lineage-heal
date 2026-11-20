"""The reader's safety rules: read-only, catalogue-checked names, bounded raw file reads."""

import gzip
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import pytest

from lineageheal.agents.tools.data_access import (
    DataAccessError,
    RawFileReader,
    Warehouse,
)
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

DAY = date(2026, 10, 15)


@pytest.fixture
def zone(tmp_path: Path) -> RawZone:
    return RawZone(LocalObjectStore(tmp_path / "raw"))


def store(zone: RawZone, source: str, dataset: str, filename: str, content: bytes) -> None:
    zone.put_if_absent(
        source=source, dataset=dataset, trade_date=DAY, filename=filename, content=content
    )


def zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("file.csv", text)
    return buffer.getvalue()


def test_amfi_header_is_split_on_semicolons_after_blank_lines(zone: RawZone) -> None:
    store(zone, "amfi", "navall", "r.txt", b"\n\nScheme Code;NAV Name;Net Asset Value\n1;A;2\n")

    schema = RawFileReader(zone).schema("amfi", "navall", DAY)

    assert schema.columns == ("Scheme Code", "NAV Name", "Net Asset Value")


def test_a_zipped_csv_gzipped_csv_and_json_are_all_read(zone: RawZone) -> None:
    store(zone, "nse", "cm_bhavcopy", "b.csv.zip", zipped("TradDt,TckrSymb\n2026-10-15,X\n"))
    store(zone, "nse", "security_master", "s.csv.gz", gzip.compress(b"FinInstrmId,ISIN\n1,I\n"))
    store(
        zone,
        "nse",
        "corporate_actions",
        "c.json",
        json.dumps([{"symbol": "X", "exDate": "d"}]).encode(),
    )
    reader = RawFileReader(zone)

    assert reader.schema("nse", "cm_bhavcopy", DAY).columns == ("TradDt", "TckrSymb")
    assert reader.schema("nse", "security_master", DAY).columns == ("FinInstrmId", "ISIN")
    assert reader.schema("nse", "corporate_actions", DAY).columns == ("symbol", "exDate")


def test_the_latest_stored_version_is_the_one_read(zone: RawZone) -> None:
    store(zone, "amfi", "navall", "r.txt", b"A;B\n")
    store(zone, "amfi", "navall", "r.txt", b"A;B;C\n")

    assert RawFileReader(zone).schema("amfi", "navall", DAY).columns == ("A", "B", "C")


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("b.csv.zip", b"not a zip", "not a readable archive"),
        ("c.json", b"{}", "not a list of records"),
        ("c.json", b"[", "not valid JSON"),
        ("r.txt", b"\n\n", "empty"),
    ],
)
def test_an_unreadable_file_is_reported_not_raised_as_a_crash(
    zone: RawZone, filename: str, content: bytes, message: str
) -> None:
    store(zone, "nse", "x", filename, content)

    with pytest.raises(DataAccessError, match=message):
        RawFileReader(zone).schema("nse", "x", DAY)


def test_a_day_with_no_file_says_so(zone: RawZone) -> None:
    with pytest.raises(DataAccessError, match="No raw file is stored"):
        RawFileReader(zone).schema("amfi", "navall", DAY)


@pytest.fixture
def warehouse_file(tmp_path: Path) -> Path:
    path = tmp_path / "w.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("create schema silver")
    connection.execute(
        'create table silver.slv_amfi__nav (nav_date date, scheme_code bigint, "odd""name" varchar,'
        " nav_inr decimal(18, 4))"
    )
    connection.execute(
        "insert into silver.slv_amfi__nav values (date '2026-10-15', 1, 'a', 10.5),"
        " (date '2026-10-15', 2, 'b', null), (date '2026-10-16', 1, 'c', 1050.0)"
    )
    connection.execute("create schema gold")
    connection.execute("create table gold.slv_amfi__nav (x int)")
    connection.close()
    return path


def test_a_table_name_that_is_not_in_the_catalogue_never_reaches_sql(warehouse_file: Path) -> None:
    warehouse = Warehouse(warehouse_file)

    with pytest.raises(DataAccessError, match="no table named"):
        warehouse.table("slv_amfi__nav; drop table silver.slv_amfi__nav")


def test_a_name_in_two_schemas_is_ambiguous(warehouse_file: Path) -> None:
    with pytest.raises(DataAccessError, match="several schemas"):
        Warehouse(warehouse_file).table("slv_amfi__nav")


def test_a_missing_file_and_unknown_column_are_reported(
    tmp_path: Path, warehouse_file: Path
) -> None:
    with pytest.raises(DataAccessError, match="does not exist"):
        Warehouse(tmp_path / "none.duckdb").table("x")

    connection = duckdb.connect(str(warehouse_file))
    connection.execute("drop table gold.slv_amfi__nav")
    connection.close()
    table = Warehouse(warehouse_file).table("slv_amfi__nav")
    with pytest.raises(DataAccessError, match="no column 'nope'"):
        table.column("nope")


def test_the_connection_cannot_write(warehouse_file: Path) -> None:
    warehouse = Warehouse(warehouse_file)

    with pytest.raises(DataAccessError, match="could not answer"), warehouse._connect() as conn:
        conn.execute("create table silver.t (x int)")


def test_a_hostile_value_is_a_parameter_and_matches_nothing(warehouse_file: Path) -> None:
    connection = duckdb.connect(str(warehouse_file))
    connection.execute("drop table gold.slv_amfi__nav")
    connection.close()
    warehouse = Warehouse(warehouse_file)
    table = warehouse.table("slv_amfi__nav")

    rows = warehouse.sample(
        table, columns=[], day=None, match=('odd"name', "x' or '1'='1"), limit=5
    )

    assert rows == []
    assert warehouse.table("slv_amfi__nav").name == "slv_amfi__nav"


def test_a_column_with_a_quote_in_its_name_is_quoted_safely(warehouse_file: Path) -> None:
    connection = duckdb.connect(str(warehouse_file))
    connection.execute("drop table gold.slv_amfi__nav")
    connection.close()
    warehouse = Warehouse(warehouse_file)
    table = warehouse.table("slv_amfi__nav")

    rows = warehouse.sample(table, columns=['odd"name'], day=None, match=('odd"name', "a"), limit=5)

    assert rows == [{'odd"name': "a"}]
