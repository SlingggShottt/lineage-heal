"""Keeping what a dbt run produced: its artifacts and the failure signals read from them.

FR-WH-07 asks for every run's `manifest.json` and `run_results.json` to be kept for detection
and lineage. They are written beside the raw zone, in the same object store (a folder locally,
the bucket on R2), under `runs/<run_id>/`, with a `signals.json` summary:

    runs/<run_id>/manifest.json
    runs/<run_id>/run_results.json
    runs/<run_id>/signals.json

The summary is written last, so its presence means the folder is complete. A run that failed
before dbt wrote any results still gets a summary, with a `run_error` signal, because the
Detector must hear about it. The application database will import these summaries (LH-036).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from lineageheal.core.errors import ArtifactError
from lineageheal.core.signals import RunSummary
from lineageheal.ingestion.raw_zone import ObjectStore
from lineageheal.lineage.dbt_artifacts import run_error_summary, summarize_run

log = structlog.get_logger(__name__)

RUNS_FOLDER = "runs"
ARTIFACT_FILES = ("manifest.json", "run_results.json")
SUMMARY_FILE = "signals.json"

# Dagster run ids are UUIDs; anything else would not be a safe folder name.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def run_prefix(run_id: str) -> str:
    """The object-store folder for one run's artifacts.

    Raises:
        ArtifactError: The run id is not a plain identifier.
    """
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ArtifactError(f"Not a usable run id: {run_id!r}")
    return f"{RUNS_FOLDER}/{run_id}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ArtifactError(f"Cannot read dbt artifact {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtifactError(f"{path.name} is not a JSON object")
    return data


def record_dbt_run(
    store: ObjectStore,
    run_id: str,
    target_path: Path,
    *,
    recorded_at: datetime,
    error: str | None = None,
) -> RunSummary:
    """Keep a finished dbt run's artifacts and write its failure signals.

    Args:
        store: Where to keep them, normally the same store as the raw zone.
        run_id: The pipeline run, used as the folder name.
        target_path: The folder dbt wrote this run's artifacts to.
        recorded_at: When the run finished, used if dbt wrote no results.
        error: Why dbt failed, if it did; used only when there are no results to read.

    Returns:
        The summary that was written.

    Raises:
        ArtifactError: The run id is unusable, or an artifact exists but cannot be read.
        StorageError: The store rejected a write.
    """
    prefix = run_prefix(run_id)
    paths = {name: target_path / name for name in ARTIFACT_FILES}
    if all(path.is_file() for path in paths.values()):
        summary = summarize_run(
            _read_json(paths["run_results.json"]), _read_json(paths["manifest.json"]), run_id=run_id
        )
        for name, path in paths.items():
            store.put(f"{prefix}/{name}", path.read_bytes(), metadata={"run_id": run_id})
    else:
        message = error or "dbt stopped without writing run_results.json"
        summary = run_error_summary(run_id, message, at=recorded_at)
    store.put(
        f"{prefix}/{SUMMARY_FILE}",
        summary.model_dump_json(indent=2).encode("utf-8"),
        metadata={"run_id": run_id},
    )
    log.info(
        "dbt_run_recorded",
        run_id=run_id,
        signals=len(summary.signals),
        has_failures=summary.has_failures,
        prefix=prefix,
    )
    return summary


def load_run_summary(store: ObjectStore, run_id: str) -> RunSummary:
    """Read back the summary that `record_dbt_run` wrote.

    Raises:
        ArtifactError: The run id is unusable or the summary is not valid.
        StorageError: There is no summary for this run.
    """
    raw = store.get(f"{run_prefix(run_id)}/{SUMMARY_FILE}")
    try:
        return RunSummary.model_validate_json(raw)
    except ValueError as exc:
        raise ArtifactError(f"Invalid signals summary for run {run_id}: {exc}") from exc
