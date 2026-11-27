"""Publishing a scored run so the API can serve it (FR-API-01).

The report on disk is what is published, so what the dashboard shows is exactly the run's own
`report.json`; it is generated first if the run has not been scored yet. It is checked against
the shared report schema before anything is written, so a damaged or old-format report is refused
here, with the reason, and never reaches visitors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from lineageheal.benchmark.metrics import compute_metrics
from lineageheal.benchmark.report import build_report, write_report
from lineageheal.benchmark.results import ResultStore
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.benchmark_report import BenchmarkReport
from lineageheal.core.errors import BenchmarkError
from lineageheal.store.benchmark import BenchmarkRepository


def publish_run(
    store: ResultStore,
    specs: Mapping[str, FaultCase],
    repository: BenchmarkRepository,
    *,
    now: datetime | None = None,
) -> tuple[BenchmarkReport, bool]:
    """Publish a run's report, scoring the run first if it has no `report.json` yet.

    Returns:
        The published report and whether the run is new (False when it replaced an earlier
        publication).

    Raises:
        BenchmarkError: The run has no results, or its report does not fit the report schema.
        StorageError: The database rejected the write.
    """
    moment = now or datetime.now(UTC)
    path = store.root / "report.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        results = store.load_all()
        if not results:
            raise BenchmarkError(f"run {store.run_id} has no results to publish")
        manifest = store.read_manifest()
        metrics = compute_metrics(results, specs, order=manifest.get("systems") or ())
        data = build_report(metrics, manifest, generated_at=moment)
        write_report(store.root, data)
    try:
        report = BenchmarkReport.model_validate(data)
    except ValidationError as exc:
        raise BenchmarkError(
            f"{_where(path)} does not fit the report schema; score the run again with "
            f"`bench report`: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
        ) from exc
    created = repository.publish(
        store.run_id,
        report.model_dump(mode="json"),
        generated_at=datetime.fromisoformat(report.generated_at),
        at=moment,
    )
    return report, created


def _where(path: Path) -> str:
    return path.name
