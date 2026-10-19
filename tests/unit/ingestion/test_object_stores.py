"""Storage backends: the local directory store and the R2 store over a fake S3 client."""

import os
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from pydantic import SecretStr

from lineageheal.core.errors import StorageError
from lineageheal.core.settings import R2Credentials
from lineageheal.ingestion import object_stores
from lineageheal.ingestion.object_stores import LocalObjectStore, R2ObjectStore

KEY = "raw/source=amfi/dataset=navall/trade_date=2026-10-15/abababababababab__report.txt"


def client_error(code: str, operation: str = "HeadObject") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "details"}}, operation)


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """Stands in for boto3's S3 client; `fail_with` makes every call raise it."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_with = fail_with

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        if self.fail_with:
            raise self.fail_with
        if kwargs["Key"] not in self.objects:
            raise client_error("404")
        return {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        if self.fail_with:
            raise self.fail_with
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        if self.fail_with:
            raise self.fail_with
        return {"Body": _Body(self.objects[kwargs["Key"]])}


# --- LocalObjectStore --------------------------------------------------------


def test_local_store_round_trips_bytes(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)

    store.put(KEY, b"payload", metadata={"sha256": "x"})

    assert store.exists(KEY)
    assert store.get(KEY) == b"payload"


def test_local_store_reports_missing_keys(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)

    assert store.exists(KEY) is False
    with pytest.raises(StorageError, match="Local read failed"):
        store.get(KEY)


def test_local_store_leaves_no_partial_files_behind(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)

    store.put(KEY, b"payload", metadata={})

    assert [p.name for p in (tmp_path / KEY).parent.iterdir()] == ["abababababababab__report.txt"]


def test_local_store_refuses_keys_that_escape_its_root(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "root")

    with pytest.raises(StorageError, match="escapes"):
        store.put("../outside.txt", b"x", metadata={})


FOLDER = "raw/source=amfi/dataset=navall/trade_date=2026-10-15"


def test_local_store_lists_a_folder_oldest_first(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    for name, age_seconds in (("c.txt", 30), ("a.txt", 10), ("b.txt", 20)):
        store.put(f"{FOLDER}/{name}", b"x", metadata={})
        stamp = 1_700_000_000 - age_seconds  # larger age means older
        os.utime(tmp_path / FOLDER / name, (stamp, stamp))

    assert store.list_folder(FOLDER) == [f"{FOLDER}/c.txt", f"{FOLDER}/b.txt", f"{FOLDER}/a.txt"]


def test_local_store_listing_skips_unfinished_writes_and_subfolders(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    store.put(f"{FOLDER}/real.txt", b"x", metadata={})
    (tmp_path / FOLDER / ".partial-abc123").write_bytes(b"half")
    store.put(f"{FOLDER}/nested/inner.txt", b"x", metadata={})

    assert store.list_folder(FOLDER) == [f"{FOLDER}/real.txt"]


def test_local_store_listing_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert LocalObjectStore(tmp_path).list_folder(FOLDER) == []


# --- R2ObjectStore -----------------------------------------------------------


def test_r2_store_uploads_with_bucket_key_body_and_metadata() -> None:
    client = FakeS3Client()
    store = R2ObjectStore(client, "raw-bucket")

    store.put(KEY, b"payload", metadata={"sha256": "abc"})

    assert client.calls == [
        (
            "put_object",
            {"Bucket": "raw-bucket", "Key": KEY, "Body": b"payload", "Metadata": {"sha256": "abc"}},
        )
    ]


def test_r2_store_downloads_bytes() -> None:
    client = FakeS3Client()
    client.objects[KEY] = b"payload"

    assert R2ObjectStore(client, "raw-bucket").get(KEY) == b"payload"


def test_r2_store_treats_not_found_as_absent_and_found_as_present() -> None:
    client = FakeS3Client()
    store = R2ObjectStore(client, "raw-bucket")
    assert store.exists(KEY) is False

    client.objects[KEY] = b"payload"

    assert store.exists(KEY) is True


def test_r2_store_does_not_hide_permission_errors_as_missing_objects() -> None:
    store = R2ObjectStore(FakeS3Client(fail_with=client_error("403")), "raw-bucket")

    with pytest.raises(StorageError) as raised:
        store.exists(KEY)

    assert "403" in str(raised.value)
    assert KEY in str(raised.value)


def test_r2_store_wraps_network_failures() -> None:
    failure = EndpointConnectionError(
        endpoint_url="https://secret-account.r2.cloudflarestorage.com"
    )
    store = R2ObjectStore(FakeS3Client(fail_with=failure), "raw-bucket")

    with pytest.raises(StorageError, match="EndpointConnectionError") as raised:
        store.put(KEY, b"payload", metadata={})

    assert "secret-account" not in str(raised.value)


def test_r2_store_wraps_download_failures() -> None:
    store = R2ObjectStore(FakeS3Client(fail_with=client_error("NoSuchKey", "GetObject")), "b")

    with pytest.raises(StorageError, match="read failed"):
        store.get(KEY)


def test_r2_client_is_built_for_the_account_endpoint_with_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(service: str, **kwargs: Any) -> FakeS3Client:
        captured["service"] = service
        captured.update(kwargs)
        return FakeS3Client()

    monkeypatch.setattr(object_stores.boto3, "client", fake_client)
    credentials = R2Credentials(
        account_id="abc123",
        access_key_id=SecretStr("key-id"),
        secret_access_key=SecretStr("shh"),
        bucket="raw-bucket",
    )

    R2ObjectStore.from_credentials(credentials)

    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://abc123.r2.cloudflarestorage.com"
    assert captured["aws_access_key_id"] == "key-id"
    assert captured["aws_secret_access_key"] == "shh"
    assert captured["config"].connect_timeout == 10
    assert captured["config"].read_timeout == 60
