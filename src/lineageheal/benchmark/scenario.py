"""Building one fault's sandbox (design 12.2, steps 1 to 3).

The sandbox is a throwaway raw zone and DuckDB file. It receives a copy of the feed's recent clean
files, the fault is injected into the copy of the fault date's file only, and `dbt build` runs on
the `bench` target against it. Nothing is read from or written to the source raw zone except
reading: the benchmark can never damage real data (FR-BEN-02, D-11). A missing clean file is an
error that says to backfill, never a download, so a run neither hits a publisher nor silently
benchmarks on partial history.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog

from lineageheal.benchmark.injection import apply_fault
from lineageheal.benchmark.models import Scenario
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import ObjectStore, RawZone
from lineageheal.ingestion.staging import ZipStager
from lineageheal.orchestration.run_artifacts import record_dbt_run

log = structlog.get_logger(__name__)

DBT_TARGET = "bench"  # local DuckDB only; the benchmark never runs against prod (D-11)
HISTORY_DAYS = 15  # the monitors compare a date with about ten recent dates of its own kind
MIN_HISTORY = 10  # fewer publishing days before the fault and the monitors have nothing to judge
LOOKBACK_DAYS = 60  # how far back to look for those days
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"
LOG_TAIL = 2000

# The feeds a scenario contains. The fault's own feed gets the history window; the others are
# copied for the same dates so the cross-feed models have their inputs.
FEEDS: tuple[tuple[str, str], ...] = (
    ("amfi", "navall"),
    ("nse", "cm_bhavcopy"),
    ("nse", "corporate_actions"),
)
REFERENCE = ("nse", "security_master")  # one file, no history: the newest on or before the date

DbtBuild = Callable[[Path, Path, Path, Path, float], tuple[int, str]]
"""Runs `dbt build`: (project, data, raw zone, target, timeout) to (exit code, log tail)."""


@dataclass(frozen=True)
class ScenarioBuilder:
    """Turns a fault specification into a built sandbox."""

    source: ObjectStore
    repo_root: Path
    scratch_root: Path
    timeout_seconds: float = 900.0
    dbt_build: DbtBuild | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    def build(self, case: FaultCase) -> Scenario:
        """Build the sandbox for one fault.

        Raises:
            BenchmarkError: Clean files the scenario needs are missing from the source raw zone
                (the message says which feed and dates to backfill), or injection failed.
        """
        started = time.monotonic()
        root = self.scratch_root / case.id
        raw_dir = root / "raw"
        _remove(root)  # a crashed earlier attempt may have left this case's folder behind
        raw_dir.mkdir(parents=True)
        sandbox = LocalObjectStore(raw_dir)
        try:
            self._fill(case, sandbox)
            exit_code, tail = (self.dbt_build or run_dbt_build)(
                self.repo_root / "dbt",
                root / "data",
                raw_dir,
                root / "target",
                self.timeout_seconds,
            )
        except BaseException:
            # A half-built sandbox is of no use to anyone, and the next attempt needs the name.
            _remove(root)
            raise
        run_id = f"bench-{case.id}"[:64]
        summary = record_dbt_run(
            sandbox, run_id, root / "target", recorded_at=self.now(), error=tail
        )
        return Scenario(
            case=case,
            root=root,
            raw_zone=raw_dir,
            warehouse=root / "data" / f"{DBT_TARGET}.duckdb",
            run_id=run_id,
            summary=summary,
            dbt_exit_code=exit_code,
            build_seconds=time.monotonic() - started,
        )

    @staticmethod
    def discard(scenario: Scenario) -> None:
        """Delete a scenario's sandbox."""
        _remove(scenario.root)

    def _fill(self, case: FaultCase, sandbox: ObjectStore) -> None:
        zone = RawZone(self.source)
        target = RawZone(sandbox)
        stager = ZipStager(sandbox)
        for source, dataset in FEEDS:
            own = (source, dataset) == (case.source, case.dataset)
            dates = _history(zone, source, dataset, case.trade_date)
            if own:
                self._require_history(case, dates)
            for day in dates:
                if own and day == case.trade_date:
                    continue  # the damaged version replaces it below
                self._copy(zone, target, stager, source, dataset, day)
        self._copy_reference(zone, target, stager, case.trade_date)
        self._inject(case, zone, target, stager)

    @staticmethod
    def _require_history(case: FaultCase, dates: list[date]) -> None:
        before = [d for d in dates if d < case.trade_date]
        if len(before) < MIN_HISTORY:
            raise BenchmarkError(
                f"{case.id}: only {len(before)} clean {case.source}/{case.dataset} files exist "
                f"before {case.trade_date} (need {MIN_HISTORY}); backfill that feed around the "
                "date and run again"
            )

    def _inject(self, case: FaultCase, zone: RawZone, target: RawZone, stager: ZipStager) -> None:
        keys = zone.stored_keys(
            source=case.source, dataset=case.dataset, trade_date=case.trade_date
        )
        if not keys:
            if case.injection.type == "withhold_file":
                return  # nothing was published to withhold; the sandbox already lacks the day
            raise BenchmarkError(
                f"{case.id}: no clean {case.source}/{case.dataset} file for {case.trade_date}; "
                "backfill it and run again"
            )
        damaged = apply_fault(case, zone.read(keys[-1]))
        if damaged is None:
            return
        stored = target.put_if_absent(
            source=case.source,
            dataset=case.dataset,
            trade_date=case.trade_date,
            filename=_original_name(keys[-1]),
            content=damaged,
        )
        if stored.key.endswith(".zip"):
            stager.stage(stored.key)

    @staticmethod
    def _copy(
        zone: RawZone, target: RawZone, stager: ZipStager, source: str, dataset: str, day: date
    ) -> None:
        keys = zone.stored_keys(source=source, dataset=dataset, trade_date=day)
        if not keys:
            return
        stored = target.put_if_absent(
            source=source,
            dataset=dataset,
            trade_date=day,
            filename=_original_name(keys[-1]),
            content=zone.read(keys[-1]),
        )
        if stored.key.endswith(".zip"):
            stager.stage(stored.key)

    def _copy_reference(self, zone: RawZone, target: RawZone, stager: ZipStager, day: date) -> None:
        source, dataset = REFERENCE
        found = _history(zone, source, dataset, day, count=1)
        if not found:
            return  # silver flags unmatched symbols rather than dropping rows (see the risks)
        self._copy(zone, target, stager, source, dataset, found[0])


def _history(
    zone: RawZone, source: str, dataset: str, end: date, *, count: int = HISTORY_DAYS
) -> list[date]:
    """The newest `count` dates up to `end` that have a stored file, oldest first."""
    found: list[date] = []
    day = end
    for _ in range(LOOKBACK_DAYS):
        if zone.stored_keys(source=source, dataset=dataset, trade_date=day):
            found.append(day)
            if len(found) == count:
                break
        day -= timedelta(days=1)
    return sorted(found)


def _original_name(key: str) -> str:
    """The file name as published, without the content-hash prefix the raw zone adds."""
    name = key.rsplit("/", 1)[-1]
    return name.split("__", 1)[1] if "__" in name else name


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def run_dbt_build(
    project: Path, data_dir: Path, raw_zone: Path, target_path: Path, timeout: float
) -> tuple[int, str]:
    """Run `dbt build` on the bench target with a minimal environment.

    A test failure makes dbt exit non-zero, which is the expected outcome for a fault, so the
    exit code is returned, not raised. The environment holds no credential and `PATH` is fixed
    so a model-written macro could not see one (D-55).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": SAFE_PATH,
        "HOME": str(data_dir),
        "DBT_DATA_DIR": str(data_dir),
        "RAW_ZONE_DIR": str(raw_zone.resolve()),
    }
    command = [
        sys.executable,
        "-c",
        "from dbt.cli.main import cli; cli()",
        "build",
        "--project-dir", str(project),
        "--profiles-dir", str(project),
        "--target", DBT_TARGET,
        "--target-path", str(target_path),
        "--log-path", str(target_path / "logs"),
    ]  # fmt: skip
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            cwd=project,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"dbt build timed out after {timeout:.0f} seconds"
    return completed.returncode, (completed.stdout + completed.stderr)[-LOG_TAIL:]
