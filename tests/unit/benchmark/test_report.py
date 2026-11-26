"""FR-BEN-06 and NFR-REPRO-01: the report says what produced it, and JSON and Markdown agree."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.metrics import compute_metrics
from lineageheal.benchmark.models import CaseResult, Diagnosis, PatchRecord
from lineageheal.benchmark.report import (
    build_report,
    duration,
    pct,
    render_markdown,
    write_report,
)
from lineageheal.benchmark.results import ResultStore
from lineageheal.benchmark.spec import FaultCase
from lineageheal.cli import app

ROOT = Path(__file__).parents[3]
CASES = {
    c.id: c
    for c in [*load_cases(ROOT / "benchmark/cases"), *load_cases(ROOT / "benchmark/heldout")]
}
TEST = [c for c in CASES.values() if c.split == "test"]
NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
MANIFEST = {
    "run_id": "r1",
    "created_at": NOW.isoformat(),
    "lockfile_sha256": "abc123",
    "models": {"gemini": "g", "groq": "q"},
    "temperature": 0,
    "prompts": ["baseline.plain.v1", "investigator.step.v1"],
    "systems": ["lineageheal", "plain-llm"],
    "cases": [c.id for c in TEST],
}


def result(case: FaultCase, system: str, *, right: bool, patch: bool | None = None) -> CaseResult:
    node = sorted(case.expected.accepted)[0] if right else "raw.amfi.navall.Nothing"
    return CaseResult.model_validate(
        {
            "run_id": "r1",
            "case_id": case.id,
            "system": system,
            "split": case.split,
            "fault_class": case.fault_class,
            "status": "completed",
            "diagnosis": Diagnosis(
                root_causes=(node,),
                fix_location=case.expected.fix_location,
                patch=None
                if patch is None
                else PatchRecord(diff="d", files=("f",), changed_lines=1),
                sandbox_passed=patch,
                seconds=90.0,
                sandbox_seconds=30.0,
                llm_calls=3,
                input_tokens=500,
                output_tokens=100,
            ),
            "started_at": NOW,
            "finished_at": NOW,
        }
    )


def two_systems() -> list[CaseResult]:
    """LineageHeal right on every fault; the plain LLM right on every second one."""
    out: list[CaseResult] = []
    for n, c in enumerate(TEST[:20]):  # a mix of classes: the corpus is ordered by id
        out.append(
            result(c, "lineageheal", right=True, patch=c.expected.fix_location == "dbt" or None)
        )
        out.append(result(c, "plain-llm", right=n % 2 == 0))
    return out


def build(results: list[CaseResult]) -> dict[str, object]:
    metrics = compute_metrics(results, CASES, order=MANIFEST["systems"])  # type: ignore[arg-type]
    return build_report(metrics, MANIFEST, generated_at=NOW)


def test_the_report_carries_the_manifest_that_produced_it() -> None:
    report = build(two_systems())

    assert report["run_id"] == "r1"
    assert report["manifest"]["lockfile_sha256"] == "abc123"  # type: ignore[index]
    assert report["manifest"]["prompts"] == MANIFEST["prompts"]  # type: ignore[index]
    markdown = render_markdown(report)
    assert "`abc123`" in markdown
    assert "baseline.plain.v1" in markdown


def test_the_markdown_has_the_overall_and_per_class_tables_with_intervals() -> None:
    markdown = render_markdown(build(two_systems()))

    assert "| lineageheal | 100.0% (" in markdown
    assert "| plain-llm | 50.0% (" in markdown
    assert "none produced" in markdown  # the plain-LLM system made no patches here
    assert "## Top-1 root-cause accuracy by fault class" in markdown
    assert "## Patches passing the sandbox by fault class" in markdown


def test_the_goals_compare_lineageheal_with_the_best_baseline_class_by_class() -> None:
    goals = build(two_systems())["goals"]

    assert goals["G1_accuracy"]["met"] is True  # type: ignore[index]
    assert goals["G2_time"]["status"] == "target met"  # type: ignore[index]  # 120 s
    assert goals["G3_patches"]["met"] is True  # type: ignore[index]
    classes = goals["G4_by_class"]  # type: ignore[index]
    assert classes
    assert {row["result"] for row in classes.values()} <= {"ahead", "tied"}
    assert any(row["result"] == "ahead" for row in classes.values())


def test_goals_are_left_out_when_lineageheal_was_not_run() -> None:
    only_baseline = [r for r in two_systems() if r.system == "plain-llm"]

    report = build(only_baseline)

    assert report["goals"] is None
    assert "## Goals" not in render_markdown(report)


def test_held_out_results_are_a_table_of_their_own_and_never_in_the_rates() -> None:
    held = [
        result(c, "lineageheal", right=True, patch=True)
        for c in CASES.values()
        if c.split == "heldout"
    ]

    report = build([*two_systems(), *held])
    markdown = render_markdown(report)

    assert "## Held-out replay: NSE, July 2024" in markdown
    assert "nse-heldout-002" in markdown
    system = next(s for s in report["systems"] if s["name"] == "lineageheal")  # type: ignore[attr-defined]
    assert system["overall"]["attempted"] == 20
    assert len(system["heldout"]) == 2


def test_the_report_is_the_same_whenever_it_is_regenerated() -> None:
    first, second = build(two_systems()), build(two_systems())

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert render_markdown(first) == render_markdown(second)


def test_seed_results_are_noted_not_scored() -> None:
    seed = next(c for c in CASES.values() if c.split == "seed")

    report = build([*two_systems(), result(seed, "lineageheal", right=True)])

    assert report["ignored_seed_results"] == 1
    assert "not scored (FR-BEN-07)" in render_markdown(report)


def test_formatting_handles_nothing_measured_and_longer_times() -> None:
    assert pct({"value": None, "low": None, "high": None}) == "n/a"
    assert pct({"value": 0.625, "low": 0.5, "high": 0.73}) == "62.5% (50-73)"
    assert duration(None) == "n/a"
    assert (duration(45), duration(185)) == ("45s", "3m 05s")


def test_write_report_makes_both_files_and_they_read_back(tmp_path: Path) -> None:
    report = build(two_systems())

    json_path, md_path = write_report(tmp_path / "r1", report)

    assert json.loads(json_path.read_text())["run_id"] == "r1"
    assert md_path.read_text().startswith("# Benchmark report: r1")


def test_the_report_command_scores_a_stored_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is not read
    monkeypatch.setenv("BENCH_DIR", str(tmp_path / "bench"))
    store = ResultStore(tmp_path / "bench" / "results", "r1")
    store.write_manifest(MANIFEST)
    for item in two_systems()[:6]:
        store.save(item)

    out = CliRunner().invoke(app, ["bench", "report", "--run-id", "r1"])

    assert out.exit_code == 0, out.output
    assert "# Benchmark report: r1" in out.output
    assert (store.root / "report.json").is_file()
    assert (store.root / "report.md").is_file()


def test_the_report_command_says_so_for_a_run_with_no_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BENCH_DIR", str(tmp_path / "bench"))
    ResultStore(tmp_path / "bench" / "results", "empty").write_manifest(MANIFEST)

    out = CliRunner().invoke(app, ["bench", "report", "--run-id", "empty"])

    assert out.exit_code == 1
    assert "no results yet" in out.output
