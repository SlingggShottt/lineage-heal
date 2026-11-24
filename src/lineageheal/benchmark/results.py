"""Storing benchmark results, one file per fault and system (FR-BEN-06).

A run is a folder: `<run_id>/manifest.json` records what the run was (lockfile hash, model ids,
prompt versions, seeds; NFR-REPRO-01) and `<run_id>/results/<case>__<system>.json` holds each
outcome. Each file is written atomically once its work is finished, so a run killed at any moment
leaves only whole results and `done()` tells the next attempt what to skip. The dashboard's
database tables are filled from these files later (LH-075).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lineageheal.benchmark.models import CaseResult
from lineageheal.core.errors import BenchmarkError

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MANIFEST = "manifest.json"
RESULTS = "results"


def lockfile_hash(path: Path) -> str | None:
    """The SHA-256 of the dependency lockfile, or None when there is none to hash."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class ResultStore:
    """The results folder of one run."""

    def __init__(self, base: Path, run_id: str) -> None:
        """Open (without creating) the folder for `run_id` under `base`.

        Raises:
            BenchmarkError: The run id is not a plain name, which would not be a safe folder.
        """
        if not _SAFE_NAME.fullmatch(run_id):
            raise BenchmarkError(f"Not a usable run id: {run_id!r}")
        self.run_id = run_id
        self._root = base / run_id

    @property
    def root(self) -> Path:
        """The run's folder."""
        return self._root

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        """Record what the run is. Written once; a resumed run keeps the original."""
        path = self._root / MANIFEST
        if path.exists():
            return
        _atomic_write(path, json.dumps(manifest, indent=2, default=_jsonable, sort_keys=True))

    def read_manifest(self) -> dict[str, Any]:
        """The run's manifest.

        Raises:
            BenchmarkError: There is no such run, or its manifest is unreadable.
        """
        try:
            data = json.loads((self._root / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BenchmarkError(f"No readable manifest for run {self.run_id}: {exc}") from exc
        if not isinstance(data, dict):
            raise BenchmarkError(f"The manifest of run {self.run_id} is not a JSON object")
        return data

    def done(self, case_id: str, system: str) -> bool:
        """Whether a result is already stored, so the work can be skipped."""
        return self._path(case_id, system).is_file()

    def save(self, result: CaseResult) -> None:
        """Store one result atomically."""
        _atomic_write(self._path(result.case_id, result.system), result.model_dump_json(indent=2))

    def load(self, case_id: str, system: str) -> CaseResult:
        """Read one result.

        Raises:
            BenchmarkError: It is missing or does not validate.
        """
        return self._read(self._path(case_id, system))

    def load_all(self) -> list[CaseResult]:
        """Every stored result, ordered by case then system."""
        folder = self._root / RESULTS
        return [self._read(path) for path in sorted(folder.glob("*.json"))]

    def _path(self, case_id: str, system: str) -> Path:
        for label, value in (("case id", case_id), ("system", system)):
            if not _SAFE_NAME.fullmatch(value) or "__" in value:
                raise BenchmarkError(f"Not a usable {label} for a result file: {value!r}")
        return self._root / RESULTS / f"{case_id}__{system}.json"

    @staticmethod
    def _read(path: Path) -> CaseResult:
        try:
            return CaseResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise BenchmarkError(f"Cannot read result {path.name}: {exc}") from exc


def _jsonable(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise BenchmarkError(f"Cannot write {path.name}: {exc.strerror}") from exc
