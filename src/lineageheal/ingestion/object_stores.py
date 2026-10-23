"""Storage backends for the raw zone: Cloudflare R2 and a local directory.

The local backend serves tests, offline development and benchmark sandboxes, which must
never write to the production bucket. Both expose the same three operations.
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Protocol, Self

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from lineageheal.core.errors import StorageError
from lineageheal.core.settings import R2Credentials

_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class LocalObjectStore:
    """Stores objects as files under a root directory."""

    def __init__(self, root: Path) -> None:
        """Create a store rooted at `root`, which is created on first write."""
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError(f"Key escapes the local store: {key}")
        return path

    def exists(self, key: str) -> bool:
        """Return whether a file is stored for `key`."""
        return self._path(key).is_file()

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        """Write `data` atomically, so a crash never leaves a half-written file.

        `metadata` is accepted for interface parity; only R2 keeps it.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, path)
        except OSError as exc:
            Path(tmp_name).unlink(missing_ok=True)
            raise StorageError(f"Local write failed for key {key}: {exc.strerror}") from exc

    def list_folder(self, folder: str) -> list[str]:
        """List the keys stored directly under a folder key, oldest first.

        Files are ordered by modification time, then name, so the first key is the
        earliest stored version. Unfinished writes are skipped.

        Args:
            folder: A key prefix that names a folder, for example
                `raw/source=amfi/dataset=navall/trade_date=2026-10-15`.

        Returns:
            The keys, or an empty list if nothing is stored there.
        """
        path = self._path(folder)
        if not path.is_dir():
            return []
        files = [p for p in path.iterdir() if p.is_file() and not p.name.startswith(".partial-")]
        files.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
        return [p.relative_to(self._root).as_posix() for p in files]

    def get(self, key: str) -> bytes:
        """Return the stored bytes, or raise `StorageError` if the key is absent."""
        try:
            return self._path(key).read_bytes()
        except OSError as exc:
            raise StorageError(f"Local read failed for key {key}: {exc.strerror}") from exc


class S3Client(Protocol):
    """The slice of the boto3 S3 client used here.

    boto3 ships no type information, so this small protocol stands in for it and lets
    tests pass a fake. Keyword arguments are `Any` because boto3 builds its methods
    dynamically.
    """

    def head_object(self, **kwargs: Any) -> Any:
        """Fetch an object's headers; raises `ClientError` if it does not exist."""
        ...

    def put_object(self, **kwargs: Any) -> Any:
        """Upload an object."""
        ...

    def list_objects_v2(self, **kwargs: Any) -> Any:
        """List objects under a prefix, one page at a time."""
        ...

    def get_object(self, **kwargs: Any) -> Any:
        """Download an object; the bytes are in the response's "Body"."""
        ...


class R2ObjectStore:
    """Stores objects in a Cloudflare R2 bucket through its S3-compatible API."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        """Create a store over an S3 client and bucket name."""
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_credentials(cls, credentials: R2Credentials) -> Self:
        """Build a store with a boto3 client configured for R2.

        boto3 handles its own HTTP and retries (the project's httpx and tenacity rule
        covers feed downloads), so timeouts and retries are set on the client here.
        """
        client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            region_name="auto",  # R2 ignores regions but boto3 requires one
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
        return cls(client, credentials.bucket)

    def exists(self, key: str) -> bool:
        """Return whether the object exists. Errors other than "not found" are raised."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _MISSING_CODES:
                return False
            raise _storage_error("check", key, exc) from exc
        except BotoCoreError as exc:
            raise _storage_error("check", key, exc) from exc
        return True

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        """Upload `data` with the given object metadata."""
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, Metadata=metadata)
        except (ClientError, BotoCoreError) as exc:
            raise _storage_error("write", key, exc) from exc

    def list_folder(self, folder: str) -> list[str]:
        """List the keys stored directly under a folder key, oldest first.

        Same contract as the local store: ordered by when each object was stored, then by
        name, so the first key is the earliest stored version.

        Raises:
            StorageError: The bucket could not be listed.
        """
        prefix = folder.rstrip("/") + "/"
        found: list[tuple[Any, str]] = []
        token: str | None = None
        try:
            while True:
                request: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "Delimiter": "/",
                }
                if token is not None:
                    request["ContinuationToken"] = token
                response = self._client.list_objects_v2(**request)
                found += [
                    (item["LastModified"], item["Key"]) for item in response.get("Contents", [])
                ]
                if not response.get("IsTruncated"):
                    break
                token = response["NextContinuationToken"]
        except (ClientError, BotoCoreError) as exc:
            raise _storage_error("list", folder, exc) from exc
        return [key for _, key in sorted(found)]

    def get(self, key: str) -> bytes:
        """Download the object's bytes."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
        except (ClientError, BotoCoreError) as exc:
            raise _storage_error("read", key, exc) from exc
        return body


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _storage_error(operation: str, key: str, error: Exception) -> StorageError:
    """Describe a failure with the key and error code only; responses may hold request detail."""
    code = _error_code(error) if isinstance(error, ClientError) else type(error).__name__
    return StorageError(f"R2 {operation} failed for key {key}: {code}")
