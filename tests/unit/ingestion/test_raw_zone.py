"""FR-ING-05: idempotent, write-once raw storage."""

from datetime import date
from pathlib import Path

import pytest

from lineageheal.core.errors import ConfigurationError, StorageError
from lineageheal.core.settings import Settings
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone, StoredObject, build_key, open_raw_zone

TRADE_DATE = date(2026, 10, 15)
FIXTURES = Path(__file__).parents[2] / "fixtures"


class MemoryObjectStore:
    """Records every write so tests can assert what was and was not stored."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.put_count = 0

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        self.put_count += 1
        self.objects[key] = data
        self.metadata[key] = metadata

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise StorageError(f"missing {key}") from exc


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def zone(store: MemoryObjectStore) -> RawZone:
    return RawZone(store)


def put(zone: RawZone, content: bytes, trade_date: date = TRADE_DATE) -> StoredObject:
    return zone.put_if_absent(
        source="amfi",
        dataset="navall",
        trade_date=trade_date,
        filename="NAVHistoryReport151026.txt",
        content=content,
    )


def test_key_follows_the_documented_layout() -> None:
    key = build_key("amfi", "navall", TRADE_DATE, "report.txt", "ab" * 32)

    assert key == (
        "raw/source=amfi/dataset=navall/trade_date=2026-10-15/abababababababab__report.txt"
    )


def test_unsafe_characters_in_file_names_are_neutralised() -> None:
    key = build_key("amfi", "navall", TRADE_DATE, "../../etc/pass wd.txt", "ab" * 32)

    assert key.endswith("/abababababababab__pass_wd.txt")
    assert "../" not in key


@pytest.mark.parametrize(
    ("source", "dataset", "filename"),
    [
        ("AMFI", "navall", "a.txt"),
        ("amfi/x", "navall", "a.txt"),
        ("amfi", "", "a.txt"),
        ("amfi", "navall", ".."),
        ("amfi", "navall", ""),
    ],
)
def test_invalid_key_parts_are_rejected(source: str, dataset: str, filename: str) -> None:
    with pytest.raises(StorageError):
        build_key(source, dataset, TRADE_DATE, filename, "ab" * 32)


def test_a_new_file_is_stored_unchanged(zone: RawZone, store: MemoryObjectStore) -> None:
    content = b"Scheme Code;Date\r\n1;15-Oct-2026\r\n"

    stored = put(zone, content)

    assert stored.created is True
    assert store.objects[stored.key] == content
    assert stored.size_bytes == len(content)


def test_the_full_checksum_is_kept_as_object_metadata(
    zone: RawZone, store: MemoryObjectStore
) -> None:
    stored = put(zone, b"payload")

    assert store.metadata[stored.key] == {"sha256": stored.sha256}
    assert len(stored.sha256) == 64


def test_identical_file_is_not_stored_twice(zone: RawZone, store: MemoryObjectStore) -> None:
    first = put(zone, b"payload")
    second = put(zone, b"payload")

    assert first.created is True
    assert second.created is False
    assert second.key == first.key
    assert store.put_count == 1


def test_changed_file_is_stored_as_a_new_object_and_the_old_one_survives(
    zone: RawZone, store: MemoryObjectStore
) -> None:
    original = put(zone, b"original")
    revised = put(zone, b"revised")

    assert revised.created is True
    assert revised.key != original.key
    assert store.objects[original.key] == b"original"
    assert store.objects[revised.key] == b"revised"


def test_same_content_on_another_trade_date_is_a_separate_object(
    zone: RawZone, store: MemoryObjectStore
) -> None:
    first = put(zone, b"payload", date(2026, 10, 15))
    second = put(zone, b"payload", date(2026, 10, 16))

    assert second.created is True
    assert second.key != first.key
    assert store.put_count == 2


def test_read_returns_what_was_stored(zone: RawZone) -> None:
    stored = put(zone, b"payload")

    assert zone.read(stored.key) == b"payload"


def test_read_detects_content_that_no_longer_matches_its_key(
    zone: RawZone, store: MemoryObjectStore
) -> None:
    stored = put(zone, b"payload")
    store.objects[stored.key] = b"tampered"

    with pytest.raises(StorageError, match="Checksum mismatch"):
        zone.read(stored.key)


def test_read_rejects_keys_that_are_not_content_addressed(zone: RawZone) -> None:
    with pytest.raises(StorageError, match="content-addressed"):
        zone.read("raw/source=amfi/dataset=navall/trade_date=2026-10-15/report.txt")


def test_real_source_bytes_survive_a_round_trip_through_the_local_store(tmp_path: Path) -> None:
    """CRLF endings and stray spaces must come back exactly as published."""
    sample = (FIXTURES / "amfi" / "navall_live_sample.txt").read_bytes()
    local_zone = RawZone(LocalObjectStore(tmp_path))

    stored = put(local_zone, sample)

    assert local_zone.read(stored.key) == sample
    assert (tmp_path / stored.key).read_bytes() == sample


def test_open_raw_zone_uses_the_local_directory_by_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, raw_zone_dir=tmp_path)

    stored = put(open_raw_zone(settings), b"payload")

    assert (tmp_path / stored.key).read_bytes() == b"payload"


def test_open_raw_zone_for_r2_names_missing_credentials(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, raw_zone_backend="r2", raw_zone_dir=tmp_path)

    with pytest.raises(ConfigurationError, match="R2_BUCKET"):
        open_raw_zone(settings)
