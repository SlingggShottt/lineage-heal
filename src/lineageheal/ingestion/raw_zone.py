"""Content-addressed raw zone.

Raw files are stored exactly as published. The SHA-256 of the content is part of the
object key, so re-downloading an identical file resolves to a key that already exists
and is skipped, while a changed re-publication resolves to a new key. Nothing is ever
overwritten (FR-ING-05). Key layout is documented in docs/design_architecture.md.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Protocol

import structlog

from lineageheal.core.errors import StorageError
from lineageheal.core.settings import Settings
from lineageheal.ingestion.object_stores import LocalObjectStore, R2ObjectStore

log = structlog.get_logger(__name__)

# 64 bits of the digest: a collision between two versions of one day's file is
# astronomically unlikely, and the full digest is stored with the object anyway.
HASH_PREFIX_LENGTH = 16

_NAME = re.compile(r"[a-z0-9_]+")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_KEY_HASH_PREFIX = re.compile(rf"/([0-9a-f]{{{HASH_PREFIX_LENGTH}}})__[^/]+$")


class ObjectStore(Protocol):
    """The three operations the raw zone needs from a storage backend."""

    def exists(self, key: str) -> bool:
        """Return whether an object is stored under `key`."""
        ...

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        """Store `data` under `key`."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes stored under `key`."""
        ...


@dataclass(frozen=True)
class StoredObject:
    """The outcome of storing one raw file."""

    key: str
    sha256: str
    size_bytes: int
    created: bool  # False when an identical file was already stored


def build_key(source: str, dataset: str, trade_date: date, filename: str, sha256: str) -> str:
    """Build the object key for a raw file.

    Args:
        source: Publisher, lowercase letters, digits and underscores (for example "amfi").
        dataset: Feed name in the same form (for example "navall").
        trade_date: The trade date in Asia/Kolkata.
        filename: The file name as published; unsafe characters are replaced.
        sha256: Hex digest of the file content.

    Returns:
        A key of the form
        `raw/source=<source>/dataset=<dataset>/trade_date=<date>/<hash prefix>__<filename>`.

    Raises:
        StorageError: A name is empty or contains characters that are not allowed.
    """
    for label, value in (("source", source), ("dataset", dataset)):
        if not _NAME.fullmatch(value):
            raise StorageError(f"Invalid {label} for raw zone key: {value!r}")

    safe_name = _UNSAFE_FILENAME_CHARS.sub("_", PurePosixPath(filename.replace("\\", "/")).name)
    if safe_name.strip(".") == "":
        raise StorageError(f"Invalid file name for raw zone key: {filename!r}")

    prefix = sha256[:HASH_PREFIX_LENGTH]
    return (
        f"raw/source={source}/dataset={dataset}/trade_date={trade_date.isoformat()}/"
        f"{prefix}__{safe_name}"
    )


class RawZone:
    """Write-once storage for raw source files."""

    def __init__(self, store: ObjectStore) -> None:
        """Create a raw zone on top of a storage backend."""
        self._store = store

    def put_if_absent(
        self,
        *,
        source: str,
        dataset: str,
        trade_date: date,
        filename: str,
        content: bytes,
    ) -> StoredObject:
        """Store a file unless this exact content is already stored for the trade date.

        Args:
            source: Publisher, for example "amfi".
            dataset: Feed name, for example "navall".
            trade_date: The trade date in Asia/Kolkata.
            filename: The file name as published.
            content: The file bytes, exactly as downloaded.

        Returns:
            The stored object. `created` is False if an identical file already existed.

        Raises:
            StorageError: The key is invalid or the backend failed.
        """
        digest = hashlib.sha256(content).hexdigest()
        key = build_key(source, dataset, trade_date, filename, digest)

        # A concurrent writer could only write the same bytes to this same key, so the
        # gap between this check and the write is harmless.
        if self._store.exists(key):
            log.info("file_already_stored", key=key, size_bytes=len(content), sha256=digest)
            return StoredObject(key=key, sha256=digest, size_bytes=len(content), created=False)

        self._store.put(key, content, metadata={"sha256": digest})
        log.info("file_stored", key=key, size_bytes=len(content), sha256=digest)
        return StoredObject(key=key, sha256=digest, size_bytes=len(content), created=True)

    def read(self, key: str) -> bytes:
        """Return a stored file after checking it still matches the hash in its key.

        Args:
            key: A key returned by `put_if_absent`.

        Returns:
            The file bytes.

        Raises:
            StorageError: The key is not content-addressed, the object is missing, or
                its content no longer matches the hash in the key.
        """
        match = _KEY_HASH_PREFIX.search(key)
        if match is None:
            raise StorageError(f"Not a content-addressed raw zone key: {key}")

        data = self._store.get(key)
        if not hashlib.sha256(data).hexdigest().startswith(match.group(1)):
            raise StorageError(f"Checksum mismatch for raw zone object: {key}")
        return data


def open_store(settings: Settings) -> ObjectStore:
    """Open the object store selected by `RAW_ZONE_BACKEND`.

    Args:
        settings: Application settings.

    Returns:
        A store backed by R2 or by a local directory.

    Raises:
        ConfigurationError: R2 is selected but its credentials are incomplete.
    """
    if settings.raw_zone_backend == "r2":
        return R2ObjectStore.from_credentials(settings.r2_credentials())
    return LocalObjectStore(settings.raw_zone_dir)


def open_raw_zone(settings: Settings) -> RawZone:
    """Open the raw zone on the store selected by `RAW_ZONE_BACKEND`.

    Raises:
        ConfigurationError: R2 is selected but its credentials are incomplete.
    """
    return RawZone(open_store(settings))
