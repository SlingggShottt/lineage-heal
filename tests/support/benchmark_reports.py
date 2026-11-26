"""A scored benchmark report built the way `bench report` builds one.

For tests of what stores, serves or publishes it. It is not hand-written JSON, so a test breaks if
the report's shape and its schema drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lineageheal.benchmark.metrics import compute_metrics
from lineageheal.benchmark.models import CaseResult, Diagnosis, PatchRecord
from lineageheal.benchmark.report import build_report
from lineageheal.benchmark.spec import FaultCase

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
NAV = "raw.amfi.navall.Net Asset Value"


def _case(case_id: str, fault_class: str, split: str = "test") -> FaultCase:
    injection = {
        "nulls": {"type": "blank_value", "column": "Net Asset Value"},
        "schema_drift": {"type": "rename_column", "column": "Net Asset Value", "new_name": "NAV"},
    }[fault_class]
    return FaultCase.model_validate(
        {
            "id": case_id,
            "class": fault_class,
            "source": "amfi",
            "dataset": "navall",
            "trade_date": "2026-10-15",
            "split": split,
            "seed": 1,
            "injection": injection,
            "expected": {"root_cause": NAV, "fix_location": "dbt"},
        }
    )


def sample_run(
    run_id: str = "r1",
) -> tuple[list[CaseResult], dict[str, FaultCase], dict[str, Any]]:
    """The results, specifications and manifest of a run of two systems on four faults."""
    cases = {
        c.id: c
        for c in (
            _case("a-1", "nulls"),
            _case("a-2", "nulls"),
            _case("a-3", "schema_drift"),
            _case("a-4", "schema_drift"),
        )
    }
    results = []
    for system, right in (("lineageheal", {"a-1", "a-2", "a-3", "a-4"}), ("plain-llm", {"a-1"})):
        for case_id, case in cases.items():
            results.append(
                CaseResult(
                    run_id=run_id,
                    case_id=case_id,
                    system=system,
                    split="test",
                    fault_class=case.fault_class,
                    status="completed",
                    diagnosis=Diagnosis(
                        root_causes=(NAV,) if case_id in right else ("raw.amfi.navall.Date",),
                        fix_location="dbt",
                        patch=PatchRecord(diff="d", files=("f",), changed_lines=1)
                        if system == "lineageheal"
                        else None,
                        sandbox_passed=True if system == "lineageheal" else None,
                        seconds=60.0,
                        sandbox_seconds=20.0,
                        llm_calls=3,
                        input_tokens=400,
                        output_tokens=100,
                    ),
                    started_at=NOW,
                    finished_at=NOW,
                )
            )
    manifest = {
        "run_id": run_id,
        "created_at": NOW.isoformat(),
        "lockfile_sha256": "abc123",
        "models": {"gemini": "g", "groq": "q"},
        "temperature": 0,
        "prompts": ["baseline.plain.v1"],
        "systems": ["lineageheal", "plain-llm"],
        "cases": list(cases),
    }
    return results, cases, manifest


def sample_report(run_id: str = "r1", *, generated_at: datetime = NOW) -> dict[str, Any]:
    """A report for two systems on four faults, with a patch, a miss and a goal comparison."""
    results, cases, manifest = sample_run(run_id)
    metrics = compute_metrics(results, cases, order=manifest["systems"])
    return build_report(metrics, manifest, generated_at=generated_at)
