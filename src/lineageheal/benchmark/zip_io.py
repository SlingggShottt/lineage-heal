"""Reading and writing the single-CSV zip files NSE publishes (FR-BEN-02).

Raw bhavcopy files are zip archives holding one CSV. An injector has to open one, damage the CSV
and close it again, and the result must be the same bytes every time the same fault is applied:
the entry keeps its published name and the zip's own metadata (time, compression, attributes) is
fixed, since two runs a day apart would otherwise differ in the timestamp alone.
"""

from __future__ import annotations

import io
import zipfile

from lineageheal.core.errors import BenchmarkError

# The earliest date a zip entry can carry; fixed so the archive is reproducible.
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def read_single_csv(content: bytes) -> tuple[str, bytes]:
    """The name and bytes of the one file inside a zip.

    Raises:
        BenchmarkError: The bytes are not a zip, or the zip does not hold exactly one file.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            if len(names) != 1:
                raise BenchmarkError(f"expected one file in the zip, found {len(names)}")
            return names[0], archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise BenchmarkError("the file is not a valid zip") from exc


def write_single_csv(name: str, data: bytes) -> bytes:
    """A zip holding one file under `name`, identical for identical input."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, data)
    return buffer.getvalue()
