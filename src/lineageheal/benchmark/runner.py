"""Running systems over the corpus (FR-BEN-03, FR-BEN-06).

For each fault the runner builds the scenario once and shows that same scenario to every system,
so they are compared under identical conditions. A result is stored the moment a system answers;
rerunning the same run id skips finished (fault, system) pairs and builds a scenario only if a
system still needs it. A failure in one system, or in building one scenario, is stored as that
result's outcome and never stops the run.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from lineageheal.benchmark.models import CaseResult, Diagnosis, Scenario, System
from lineageheal.benchmark.results import ResultStore
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError

log = structlog.get_logger(__name__)


@dataclass
class RunReport:
    """What one invocation did."""

    ran: int = 0
    skipped: int = 0
    errored: int = 0
    scenario_failed: int = 0
    failures: list[str] = field(default_factory=list)


class NoneSystem:
    """Builds the scenario and answers nothing: checks that the corpus is runnable."""

    name = "none"

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Return an empty diagnosis."""
        return Diagnosis(notes=f"{len(scenario.summary.signals)} signal(s) raised")


class BenchmarkRunner:
    """Runs a list of systems over a list of faults, resumably."""

    def __init__(
        self,
        builder: ScenarioBuilder,
        store: ResultStore,
        systems: Sequence[System],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        keep_scenarios: bool = False,
    ) -> None:
        """Create a runner.

        Args:
            builder: Builds each fault's sandbox.
            store: Where results go; its run id names the run.
            systems: The systems to evaluate, in order.
            now: The clock, replaced in tests.
            keep_scenarios: Keep sandboxes after use, for debugging.
        """
        if not systems:
            raise BenchmarkError("At least one system is required")
        names = [s.name for s in systems]
        if len(set(names)) != len(names):
            raise BenchmarkError(f"System names must be unique: {names}")
        self._builder = builder
        self._store = store
        self._systems = list(systems)
        self._now = now
        self._keep = keep_scenarios

    def run(self, cases: Sequence[FaultCase], manifest: dict[str, Any]) -> RunReport:
        """Evaluate every system on every case, skipping what is already stored."""
        self._store.write_manifest(manifest)
        report = RunReport()
        for case in cases:
            pending = [s for s in self._systems if not self._store.done(case.id, s.name)]
            report.skipped += len(self._systems) - len(pending)
            if not pending:
                continue
            self._run_case(case, pending, report)
        return report

    def _run_case(self, case: FaultCase, pending: list[System], report: RunReport) -> None:
        started = self._now()
        began = time.monotonic()
        try:
            scenario = self._builder.build(case)
        except Exception as exc:
            log.warning("scenario_failed", case=case.id, error=str(exc))
            for system in pending:
                self._store.save(
                    self._result(case, system.name, "scenario_failed", started, error=str(exc))
                )
                report.scenario_failed += 1
                report.failures.append(f"{case.id}: {exc}")
            return
        try:
            for system in pending:
                self._run_system(case, system, scenario, began, report)
        finally:
            if not self._keep:
                self._builder.discard(scenario)

    def _run_system(
        self, case: FaultCase, system: System, scenario: Scenario, began: float, report: RunReport
    ) -> None:
        started = self._now()
        try:
            diagnosis = system.diagnose(scenario)
        except Exception as exc:
            log.warning("system_errored", case=case.id, system=system.name, error=str(exc))
            self._store.save(
                self._result(
                    case, system.name, "errored", started, scenario=scenario, error=str(exc)
                )
            )
            report.errored += 1
            report.failures.append(f"{case.id}/{system.name}: {exc}")
            return
        self._store.save(
            self._result(
                case, system.name, "completed", started, scenario=scenario, diagnosis=diagnosis
            )
        )
        report.ran += 1
        log.info("case_done", case=case.id, system=system.name)

    def _result(
        self,
        case: FaultCase,
        system: str,
        status: str,
        started: datetime,
        *,
        scenario: Scenario | None = None,
        diagnosis: Diagnosis | None = None,
        error: str | None = None,
    ) -> CaseResult:
        return CaseResult.model_validate(
            {
                "run_id": self._store.run_id,
                "case_id": case.id,
                "system": system,
                "split": case.split,
                "fault_class": case.fault_class,
                "status": status,
                "detected": scenario.detected if scenario else False,
                "signal_count": len(scenario.summary.signals) if scenario else 0,
                "diagnosis": diagnosis,
                "error": error,
                "started_at": started,
                "finished_at": self._now(),
                "scenario_seconds": scenario.build_seconds if scenario else 0.0,
            }
        )


def scratch_folder(base: Path, run_id: str) -> Path:
    """A fresh folder for a run's sandboxes, removed if left over from a crashed attempt."""
    folder = base / "scratch" / run_id
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder
