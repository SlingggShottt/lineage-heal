"""D-22: archives are unzipped into a derived staged area, never into the raw zone."""

import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

from lineageheal.core.errors import IngestionError, StorageError
from lineageheal.ingestion import staging
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager, staged_key_for

CSV = b"TradDt,TckrSymb\n2024-07-05,TCS\n"
RAW_KEY = "raw/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/0123456789abcdef__file.csv.zip"


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
