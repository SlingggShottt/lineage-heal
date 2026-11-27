"""The shape of a benchmark report (FR-BEN-06, FR-UI-07).

`benchmark/report.py` builds a report as plain data, and the API serves it to the dashboard. This
module is the one definition of that shape, so both agree and the dashboard's TypeScript types
(generated from the API's schema) are real types and not untyped JSON. A report that does not fit
is refused on the way in, so a stale or damaged one is never published.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Rate(_Model):
    """A share with its 95% Wilson interval; `value` is null when nothing was measured."""

    hits: int
    total: int
    value: float | None
    low: float | None
    high: float | None


class Scope(_Model):
    """One system's scores over all test faults, or over one fault class."""

    attempted: int
    errored: int
    scenario_failed: int
    top1: Rate
    top3: Rate
    fix_location: Rate
    patches_produced: int
    patch_pass: Rate
    patch_coverage: Rate
    time_median: float | None
    time_p90: float | None
    calls_mean: float | None
    tokens_mean: float | None
    tokens_total: int


class HeldoutRow(_Model):
    """How one system did on one replay case."""

    case_id: str
    status: str
    top1: bool
    top3: bool
    fix_location_ok: bool | None
    patch: Literal["passed", "failed", "none"]
    seconds: float | None


class SystemReport(_Model):
    """Everything reported for one system."""

    name: str
    overall: Scope
    by_class: dict[str, Scope]
    heldout: list[HeldoutRow]


class ReportManifest(_Model):
    """What produced the run: code, models, prompts and how many faults."""

    created_at: str | None
    lockfile_sha256: str | None
    models: dict[str, str] | None
    temperature: float | None
    prompts: list[str] | None
    systems: list[str] | None
    cases: int


class AccuracyGoal(_Model):
    """A goal that is a share at or above a threshold (G1, G3)."""

    value: float | None
    goal: float
    met: bool | None


class TimeGoal(_Model):
    """Goal G2: median time to a fix against the target and the limit."""

    median_seconds: float | None
    target_seconds: float
    required_seconds: float
    status: Literal["target met", "required met", "missed", "n/a"]


class BaselineComparison(_Model):
    """Goal G4 for one fault class: LineageHeal's top-1 against the best baseline's."""

    lineageheal: float
    best_baseline: str
    best_baseline_value: float
    result: Literal["ahead", "tied", "behind"]
    separated: bool  # the intervals do not overlap, so the gap is not chance


class Goals(_Model):
    """The PRD goals checked against a run."""

    g1_accuracy: AccuracyGoal
    g2_time: TimeGoal
    g3_patches: AccuracyGoal
    g4_by_class: dict[str, BaselineComparison]


class BenchmarkReport(_Model):
    """A scored benchmark run."""

    run_id: str
    generated_at: str
    manifest: ReportManifest
    test_cases: int
    ignored_seed_results: int
    systems: list[SystemReport]
    goals: Goals | None
