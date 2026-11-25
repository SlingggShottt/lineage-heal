"""D-22, D-81: archives and HTML tables are staged as readable copies, never in the raw zone."""

import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

from lineageheal.core.errors import IngestionError, StorageError
from lineageheal.ingestion import staging
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import (
    HtmlTableStager,
    ZipStager,
    parse_html_table,
    staged_key_for,
    table_staged_key_for,
)

CSV = b"TradDt,TckrSymb\n2024-07-05,TCS\n"
RAW_KEY = "raw/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/0123456789abcdef__file.csv.zip"
TABLE_RAW_KEY = (
    "raw/source=fbil/dataset=reference_rates/trade_date=2026-11-20/0123456789abcdef__rates.xls"
)
RATES = (
    Path(__file__).parents[2]
    / "fixtures"
    / "fbil"
    / "reference_rates_2026-11-16_2026-11-20_sample.xls"
).read_bytes()
RATES_HEADER = ["Date", "USD (INR / 1 USD)", "GBP (INR / 1 GBP)", "EUR (INR / 1 EUR)"]


class RecordingStore(LocalObjectStore):
    """A local store that remembers every write."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writes: list[tuple[str, dict[str, str]]] = []

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        self.writes.append((key, metadata))
        super().put(key, data, metadata=metadata)


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def store_raw_zip(store: RecordingStore, archive: bytes) -> str:
    stored = RawZone(store).put_if_absent(
        source="nse",
        dataset="cm_bhavcopy",
        trade_date=date(2024, 7, 5),
        filename="file.csv.zip",
        content=archive,
    )
    store.writes.clear()  # only count what staging writes
    return stored.key


@pytest.fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path)


def test_a_staged_key_is_the_raw_key_without_the_zip_suffix() -> None:
    assert staged_key_for(RAW_KEY) == (
        "staged/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/0123456789abcdef__file.csv"
    )


@pytest.mark.parametrize(
    "key",
    [
        "staged/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/abc__file.csv.zip",
        "raw/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/abc__file.csv",
    ],
)
def test_only_raw_zip_keys_can_be_staged(key: str) -> None:
    with pytest.raises(StorageError, match="Not a raw zip key"):
        staged_key_for(key)


def test_staging_extracts_the_file_unchanged_and_records_where_it_came_from(
    store: RecordingStore,
) -> None:
    raw_key = store_raw_zip(store, make_zip({"file.csv": CSV}))

    staged_key = ZipStager(store).stage(raw_key)

    assert store.get(staged_key) == CSV
    ((written_key, metadata),) = store.writes
    assert written_key == staged_key
    assert metadata["raw_key"] == raw_key
    assert len(metadata["sha256"]) == 64


def test_the_raw_zip_is_left_untouched(store: RecordingStore) -> None:
    archive = make_zip({"file.csv": CSV})
    raw_key = store_raw_zip(store, archive)

    ZipStager(store).stage(raw_key)

    assert store.get(raw_key) == archive


def test_staging_the_same_file_twice_writes_once(store: RecordingStore) -> None:
    raw_key = store_raw_zip(store, make_zip({"file.csv": CSV}))
    stager = ZipStager(store)

    first = stager.stage(raw_key)
    second = stager.stage(raw_key)

    assert first == second
    assert len(store.writes) == 1


@pytest.mark.parametrize(
    "files",
    [{}, {"a.csv": CSV, "b.csv": CSV}],
    ids=["empty archive", "two files"],
)
def test_only_single_file_archives_are_staged(
    store: RecordingStore, files: dict[str, bytes]
) -> None:
    raw_key = store_raw_zip(store, make_zip(files))

    with pytest.raises(IngestionError, match="Expected one file"):
        ZipStager(store).stage(raw_key)

    assert store.writes == []


def test_an_archive_that_expands_too_far_is_refused(
    store: RecordingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(staging, "MAX_STAGED_BYTES", 10)
    raw_key = store_raw_zip(store, make_zip({"file.csv": b"x" * 100}))

    with pytest.raises(IngestionError, match="too large"):
        ZipStager(store).stage(raw_key)

    assert store.writes == []


def test_a_raw_object_that_is_not_a_zip_is_refused(store: RecordingStore) -> None:
    raw_key = store_raw_zip(store, b"this is not a zip file")

    with pytest.raises(IngestionError, match="Not a valid zip"):
        ZipStager(store).stage(raw_key)


def test_a_raw_object_that_no_longer_matches_its_hash_is_refused(
    store: RecordingStore, tmp_path: Path
) -> None:
    raw_key = store_raw_zip(store, make_zip({"file.csv": CSV}))
    (tmp_path / raw_key).write_bytes(make_zip({"file.csv": b"tampered"}))

    with pytest.raises(StorageError, match="Checksum mismatch"):
        ZipStager(store).stage(raw_key)


def test_a_missing_raw_object_is_reported(store: RecordingStore) -> None:
    with pytest.raises(StorageError):
        ZipStager(store).stage(RAW_KEY)


def store_raw_table(store: RecordingStore, document: bytes) -> str:
    stored = RawZone(store).put_if_absent(
        source="fbil",
        dataset="reference_rates",
        trade_date=date(2026, 11, 20),
        filename="rates.xls",
        content=document,
    )
    store.writes.clear()  # only count what staging writes
    return stored.key


def test_a_table_staged_key_is_the_raw_key_with_a_csv_suffix() -> None:
    assert table_staged_key_for(TABLE_RAW_KEY) == (
        "staged/source=fbil/dataset=reference_rates/trade_date=2026-11-20/0123456789abcdef__rates.csv"
    )


@pytest.mark.parametrize(
    "key",
    [
        "staged/source=fbil/dataset=reference_rates/trade_date=2026-11-20/abc__rates.xls",
        "raw/source=fbil/dataset=reference_rates/trade_date=2026-11-20/abc__rates.csv",
    ],
)
def test_only_raw_xls_keys_can_be_staged_as_tables(key: str) -> None:
    with pytest.raises(StorageError, match="Not a raw table key"):
        table_staged_key_for(key)


def test_the_real_sample_is_a_header_and_five_days_of_rates() -> None:
    rows = parse_html_table(RATES)

    assert len(rows) == 6
    assert rows[0][:4] == RATES_HEADER
    assert rows[1] == ["20/11/2026", "92.9699", "121.9337", "102.1504", "58.4300"]
    assert rows[-1][0] == "16/11/2026"


def test_a_table_with_only_a_header_is_one_row() -> None:
    """A day with no rate comes back like this; it is for the loader to call it no data."""
    assert parse_html_table(b"<table><tr><td>Date</td><td>USD</td></tr></table>") == [
        ["Date", "USD"]
    ]


def test_cell_text_is_trimmed_and_entities_are_decoded() -> None:
    document = (
        b"<table><tr><th> Date </th><th>A &amp; B</th></tr>"
        b"<tr><td> 1 </td><td>x&nbsp;y</td></tr></table>"
    )

    assert parse_html_table(document) == [["Date", "A & B"], ["1", "x y"]]


def test_a_style_block_and_text_outside_the_cells_are_ignored() -> None:
    document = b"<style> td { color: red; } </style>words<table><tr><td>a</td></tr></table>more"

    assert parse_html_table(document) == [["a"]]


def test_rows_and_cells_left_open_are_closed_by_the_next_one() -> None:
    document = b"<table><tr><td>a<td>b<tr><td>c<td>d</table>"

    assert parse_html_table(document) == [["a", "b"], ["c", "d"]]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"<html>Unauthorised Access</html>", "found 0"),
        (b"<table><tr><td>a</td></tr></table><table><tr><td>b</td></tr></table>", "found 2"),
        (b"<table><tr><td>a</td></tr>", "cut off"),
        (b"<table><tr><td>a</td>", "cut off"),
        (b"<table></table>", "no rows"),
        (b"<table><tr></tr></table>", "no rows"),
        (b"<table><tr><td>a</td></tr><tr><td>b</td><td>c</td></tr></table>", "different numbers"),
        (b"\xff\xfe<table><tr><td>a</td></tr></table>", "not UTF-8"),
    ],
    ids=[
        "no table",
        "two tables",
        "no closing table tag",
        "ends inside a row",
        "no rows",
        "a row without cells",
        "ragged rows",
        "not utf-8",
    ],
)
def test_a_document_that_is_not_one_whole_regular_table_is_refused(
    document: bytes, message: str
) -> None:
    with pytest.raises(IngestionError, match=message):
        parse_html_table(document)


def test_a_table_larger_than_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "MAX_TABLE_BYTES", 10)

    with pytest.raises(IngestionError, match="too large"):
        parse_html_table(b"<table><tr><td>aaaaaaaaaa</td></tr></table>")


def test_a_raw_table_is_staged_as_csv_and_records_where_it_came_from(
    store: RecordingStore,
) -> None:
    raw_key = store_raw_table(store, RATES)

    staged_key = HtmlTableStager(store).stage(raw_key)

    lines = store.get(staged_key).decode().splitlines()
    assert staged_key.endswith(".csv")
    assert len(lines) == 6
    assert lines[0].startswith("Date,USD (INR / 1 USD),GBP")
    assert lines[1] == "20/11/2026,92.9699,121.9337,102.1504,58.4300"
    assert b"\r" not in store.get(staged_key)
    ((written_key, metadata),) = store.writes
    assert written_key == staged_key
    assert metadata["raw_key"] == raw_key
    assert len(metadata["sha256"]) == 64


def test_a_cell_holding_a_comma_is_quoted(store: RecordingStore) -> None:
    raw_key = store_raw_table(store, b"<table><tr><td>a,b</td><td>c</td></tr></table>")

    staged_key = HtmlTableStager(store).stage(raw_key)

    assert store.get(staged_key) == b'"a,b",c\n'


def test_the_raw_table_is_left_untouched(store: RecordingStore) -> None:
    raw_key = store_raw_table(store, RATES)

    HtmlTableStager(store).stage(raw_key)

    assert store.get(raw_key) == RATES


def test_staging_the_same_table_twice_writes_once(store: RecordingStore) -> None:
    raw_key = store_raw_table(store, RATES)
    stager = HtmlTableStager(store)

    first = stager.stage(raw_key)
    second = stager.stage(raw_key)

    assert first == second
    assert len(store.writes) == 1


def test_a_raw_object_that_is_not_a_table_is_refused_and_nothing_is_staged(
    store: RecordingStore,
) -> None:
    raw_key = store_raw_table(store, b"<html>Unauthorised Access</html>")

    with pytest.raises(IngestionError, match="Expected one table"):
        HtmlTableStager(store).stage(raw_key)

    assert store.writes == []


def test_a_raw_table_that_no_longer_matches_its_hash_is_refused(
    store: RecordingStore, tmp_path: Path
) -> None:
    raw_key = store_raw_table(store, RATES)
    (tmp_path / raw_key).write_bytes(b"<table><tr><td>tampered</td></tr></table>")

    with pytest.raises(StorageError, match="Checksum mismatch"):
        HtmlTableStager(store).stage(raw_key)


def test_a_missing_raw_table_is_reported(store: RecordingStore) -> None:
    with pytest.raises(StorageError):
        HtmlTableStager(store).stage(TABLE_RAW_KEY)
