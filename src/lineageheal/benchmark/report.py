"""Turning a run's metrics into `report.json` and `report.md` (FR-BEN-06, NFR-REPRO-01).

The JSON is the source of truth and holds everything a chart needs; the Markdown is rendered from
that same dictionary, so the two cannot disagree. The header repeats the run's manifest (lockfile
hash, models, prompt versions, seeds) so a table of numbers always says what produced it.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lineageheal.benchmark.metrics import FAULT_CLASSES, Metrics, Rate, Scope, SystemMetrics

LINEAGEHEAL = "lineageheal"
# PRD goals G1 and G3 (the lower end of "70 to 80%"), and NFR-PERF-01's required and target
# median detection-to-fix times.
ACCURACY_GOAL = 0.70
PATCH_GOAL = 0.70
TIME_REQUIRED_SECONDS = 600.0
TIME_TARGET_SECONDS = 300.0


def build_report(
    metrics: Metrics, manifest: dict[str, Any], *, generated_at: datetime
) -> dict[str, Any]:
    """The report as plain data: the manifest, every system's scores and the goal checks."""
    return {
        "run_id": manifest.get("run_id"),
        "generated_at": generated_at.isoformat(),
        "manifest": {
            key: manifest.get(key)
            for key in ("created_at", "lockfile_sha256", "models", "temperature", "prompts")
        }
        | {"systems": manifest.get("systems"), "cases": len(manifest.get("cases") or [])},
        "test_cases": metrics.test_cases,
        "ignored_seed_results": metrics.ignored_seed_results,
        "systems": [_system(s) for s in metrics.systems],
        "goals": _goals(metrics),
    }


def write_report(directory: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """Write `report.json` and `report.md` into a run's folder; return their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "report.json", directory / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# -- the data ----------------------------------------------------------------------------------


def _rate(r: Rate) -> dict[str, Any]:
    return {"hits": r.hits, "total": r.total, "value": r.value, "low": r.low, "high": r.high}


def _scope(s: Scope) -> dict[str, Any]:
    data = asdict(s)
    for key in ("top1", "top3", "fix_location", "patch_pass", "patch_coverage"):
        data[key] = _rate(getattr(s, key))
    return data


def _system(s: SystemMetrics) -> dict[str, Any]:
    return {
        "name": s.name,
        "overall": _scope(s.overall),
        "by_class": {c: _scope(scope) for c, scope in s.by_class.items()},
        "heldout": [asdict(row) for row in s.heldout],
    }


def _goals(metrics: Metrics) -> dict[str, Any] | None:
    ours = next((s for s in metrics.systems if s.name == LINEAGEHEAL), None)
    if ours is None:
        return None
    others = [s for s in metrics.systems if s.name != LINEAGEHEAL]
    median = ours.overall.time_median
    if median is None:
        time_status = "n/a"
    elif median < TIME_TARGET_SECONDS:
        time_status = "target met"
    elif median < TIME_REQUIRED_SECONDS:
        time_status = "required met"
    else:
        time_status = "missed"
    classes: dict[str, Any] = {}
    for name, scope in ours.by_class.items():
        rivals = [(s.name, s.by_class[name].top1) for s in others if name in s.by_class]
        scored = [(n, r) for n, r in rivals if r.value is not None]
        if scope.top1.value is None or not scored:
            continue
        best_name, best = max(scored, key=lambda pair: pair[1].value or 0.0)
        mine = scope.top1.value
        theirs = best.value or 0.0
        classes[name] = {
            "lineageheal": mine,
            "best_baseline": best_name,
            "best_baseline_value": theirs,
            "result": "ahead" if mine > theirs else "tied" if mine == theirs else "behind",
            # Intervals that do not overlap are the evidence that a gap is not chance.
            "separated": bool(
                scope.top1.low is not None
                and best.high is not None
                and (scope.top1.low > best.high or (scope.top1.high or 0) < (best.low or 0))
            ),
        }
    return {
        "g1_accuracy": {"value": ours.overall.top1.value, "goal": ACCURACY_GOAL,
                        "met": _at_least(ours.overall.top1.value, ACCURACY_GOAL)},
        "g2_time": {"median_seconds": median, "target_seconds": TIME_TARGET_SECONDS,
                    "required_seconds": TIME_REQUIRED_SECONDS, "status": time_status},
        "g3_patches": {"value": ours.overall.patch_pass.value, "goal": PATCH_GOAL,
                       "met": _at_least(ours.overall.patch_pass.value, PATCH_GOAL)},
        "g4_by_class": classes,
    }  # fmt: skip


def _at_least(value: float | None, goal: float) -> bool | None:
    return None if value is None else value >= goal


# -- the Markdown ------------------------------------------------------------------------------


def pct(r: dict[str, Any]) -> str:
    """A share as `62.0% (50-73)`, or `n/a` when nothing was measured."""
    if r["value"] is None:
        return "n/a"
    return f"{r['value'] * 100:.1f}% ({r['low'] * 100:.0f}-{r['high'] * 100:.0f})"


def duration(seconds: float | None) -> str:
    """Seconds as `45s` or `3m 05s`."""
    if seconds is None:
        return "n/a"
    whole = round(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m {whole % 60:02d}s"


def _number(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    """The report as a Markdown document."""
    lines = [f"# Benchmark report: {report['run_id']}", ""]
    manifest = report["manifest"]
    lines += [
        f"Generated {report['generated_at']}. Run created {manifest.get('created_at')}.",
        "",
        f"- **Systems:** {', '.join(manifest.get('systems') or [])}",
        f"- **Test faults scored:** {report['test_cases']} "
        f"(of {manifest['cases']} faults in the run's manifest)",
        f"- **Lockfile SHA-256:** `{manifest.get('lockfile_sha256')}`",
        f"- **Models:** {json.dumps(manifest.get('models'), sort_keys=True)}, "
        f"temperature {manifest.get('temperature')}",
        f"- **Prompts:** {', '.join(manifest.get('prompts') or [])}",
    ]
    if report["ignored_seed_results"]:
        ignored = report["ignored_seed_results"]
        lines.append(f"- {ignored} results on the seed split are not scored (FR-BEN-07).")
    systems = report["systems"]
    lines += ["", "## Overall, test split", "", _overall_table(systems), ""]
    for title, key in (
        ("Top-1 root-cause accuracy by fault class", "top1"),
        ("Top-3 root-cause accuracy by fault class", "top3"),
        ("Patches passing the sandbox by fault class", "patch_pass"),
    ):
        lines += [f"## {title}", "", _class_table(systems, key), ""]
    if report["goals"]:
        lines += ["## Goals", "", _goals_text(report["goals"]), ""]
    if any(s["heldout"] for s in systems):
        lines += ["## Held-out replay: NSE, July 2024", "", _heldout_table(systems), ""]
    lines += [_NOTES]
    return "\n".join(lines) + "\n"


_NOTES = """## How to read this

- A diagnosis is right when a ranked root-cause node is in the fault's accepted set. Top-1 looks at
  the first, top-3 at the first three. Rates show a 95% Wilson interval in brackets.
- Only the test split is scored. A fault whose sandbox could not be built is left out for every
  system; a system that crashed counts as a miss.
- Patches passing is passing patches over patches produced; the sandbox must fix every check that
  failed and break nothing that passed. Time is the system's own time plus its sandbox time, from
  detection to a verified patch or diagnosis, and does not include building the scenario."""


def _overall_table(systems: list[dict[str, Any]]) -> str:
    rows = [
        "| System | Top-1 | Top-3 | Fix location | Patches passing | Median time | p90 time "
        "| LLM calls | Tokens | Faults | Crashed | Not built |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in systems:
        o = s["overall"]
        rows.append(
            f"| {s['name']} | {pct(o['top1'])} | {pct(o['top3'])} | {pct(o['fix_location'])} "
            f"| {_patches(o)} | {duration(o['time_median'])} "
            f"| {duration(o['time_p90'])} | {_number(o['calls_mean'])} "
            f"| {_number(o['tokens_mean'], 0)} | {o['attempted']} | {o['errored']} "
            f"| {o['scenario_failed']} |"
        )
    return "\n".join(rows)


def _patches(scope: dict[str, Any]) -> str:
    if not scope["patches_produced"]:
        return "none produced"
    return f"{pct(scope['patch_pass'])} of {scope['patches_produced']}"


def _class_table(systems: list[dict[str, Any]], key: str) -> str:
    rows = [
        "| Fault class | " + " | ".join(s["name"] for s in systems) + " |",
        "|---|" + "---|" * len(systems),
    ]
    for fault_class in FAULT_CLASSES:
        cells = [
            pct(s["by_class"][fault_class][key]) if fault_class in s["by_class"] else "n/a"
            for s in systems
        ]
        rows.append(f"| {fault_class} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _goals_text(goals: dict[str, Any]) -> str:
    def mark(met: bool | None) -> str:
        return "n/a" if met is None else ("met" if met else "not met")

    g1, g2, g3 = goals["g1_accuracy"], goals["g2_time"], goals["g3_patches"]
    lines = [
        f"- **G1, find the root cause:** top-1 "
        f"{'n/a' if g1['value'] is None else f'{g1["value"] * 100:.1f}%'} against "
        f"{g1['goal'] * 100:.0f}% or more: {mark(g1['met'])}",
        f"- **G2, time to a fix:** median {duration(g2['median_seconds'])} against a target of "
        f"{duration(g2['target_seconds'])} and a limit of {duration(g2['required_seconds'])}: "
        f"{g2['status']}",
        f"- **G3, patches that work:** "
        f"{'n/a' if g3['value'] is None else f'{g3["value"] * 100:.1f}%'} against "
        f"{g3['goal'] * 100:.0f}% or more: {mark(g3['met'])}",
        "- **G4, beat the baselines (top-1, per fault class):**",
    ]
    for fault_class, row in goals["g4_by_class"].items():
        gap = "gap beyond chance" if row["separated"] else "gap within chance"
        lines.append(
            f"  - {fault_class}: {row['result']} "
            f"({row['lineageheal'] * 100:.0f}% against {row['best_baseline_value'] * 100:.0f}% "
            f"for {row['best_baseline']}; {gap})"
        )
    return "\n".join(lines)


def _heldout_table(systems: list[dict[str, Any]]) -> str:
    rows = [
        "| Case | System | Status | Top-1 | Top-3 | Fix location | Patch | Time |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in systems:
        for h in s["heldout"]:
            ok = {True: "right", False: "wrong", None: "n/a"}[h["fix_location_ok"]]
            rows.append(
                f"| {h['case_id']} | {s['name']} | {h['status']} "
                f"| {'hit' if h['top1'] else 'miss'} | {'hit' if h['top3'] else 'miss'} "
                f"| {ok} | {h['patch']} | {duration(h['seconds'])} |"
            )
    return "\n".join([*rows[:2], *sorted(rows[2:])])
