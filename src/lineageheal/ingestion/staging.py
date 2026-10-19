"""Staging: derived, uncompressed copies of archived raw files.

DuckDB, and therefore dbt, cannot read zip archives, but the raw zone keeps files exactly
as published. Staging bridges the two without touching the raw zone: each raw zip is
unzipped into a sibling `staged/` area. A staged key is derived from its raw key, so the
two are linked one to one, and rebuilding the staged area from the raw zone always gives
the same result (decision D-22).
"""

import hashlib
import zipfile
from io import BytesIO

import structlog

from lineageheal.core.errors import IngestionError, StorageError
from lineageheal.ingestion.raw_zone import ObjectStore, RawZone

log = structlog.get_logger(__name__)

RAW_ROOT = "raw/"
STAGED_ROOT = "staged/"

# A zip is untrusted input; refuse one that would expand far beyond any real bhavcopy
# (about 0.6 MB).
MAX_STAGED_BYTES = 256 * 1024 * 1024


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
