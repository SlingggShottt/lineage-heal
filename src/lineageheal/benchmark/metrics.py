"""Scoring a benchmark run (FR-BEN-04).

Everything here is a pure function of the stored results and the fault specifications, so a
report can be regenerated at any time and two people get the same numbers. The rules:

- Only the test split is scored (FR-BEN-07); seed results, if a run included any, are ignored.
- A fault whose sandbox could not be built (`scenario_failed`, usually missing data) is the
  benchmark's problem, equal for every system, and is left out of every denominator. A system that
  crashes (`errored`) counts as a miss: it had the same scenario as the others.
- A diagnosis is right when a ranked node is in the case's accepted set (`expected.accepted`).
- Time is the system's own seconds plus its sandbox seconds; building the scenario is overhead.
- Rates carry a Wilson 95% interval, because a class has only about a dozen faults.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lineageheal.benchmark.models import CaseResult
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError

FAULT_CLASSES = ("schema_drift", "nulls", "duplicates", "unit_change", "late_data")
Z_95 = 1.96


@dataclass(frozen=True)
class Rate:
    """A share with its Wilson 95% interval."""

    hits: int
    total: int
    low: float | None = None
    high: float | None = None

    @property
    def value(self) -> float | None:
        """The share, or None when there was nothing to measure."""
        return self.hits / self.total if self.total else None


def rate(hits: int, total: int) -> Rate:
    """A `Rate` with its Wilson score interval (95%), which behaves at small counts and extremes."""
    if total == 0:
        return Rate(hits, total)
    p = hits / total
    denominator = 1 + Z_95**2 / total
    centre = (p + Z_95**2 / (2 * total)) / denominator
    margin = Z_95 * math.sqrt(p * (1 - p) / total + Z_95**2 / (4 * total**2)) / denominator
    return Rate(hits, total, max(0.0, centre - margin), min(1.0, centre + margin))


def percentile(values: Sequence[float], q: float) -> float | None:
    """The nearest-rank percentile (`q` in 0 to 1), or None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


@dataclass(frozen=True)
class Scope:
    """One system's scores over a set of faults: overall or one fault class."""

    attempted: int  # faults the system was given and answered or crashed on
    errored: int
    scenario_failed: int  # left out of every rate
    top1: Rate
    top3: Rate
    fix_location: Rate
    patches_produced: int
    patch_pass: Rate  # passing patches over patches produced (FR-BEN-04)
    patch_coverage: Rate  # passing patches over faults whose fix belongs in dbt
    time_median: float | None
    time_p90: float | None
    calls_mean: float | None
    tokens_mean: float | None
    tokens_total: int


@dataclass(frozen=True)
class HeldoutRow:
    """How one system did on one replay case."""

    case_id: str
    status: str
    top1: bool
    top3: bool
    fix_location_ok: bool | None
    patch: str  # `passed`, `failed` or `none`
    seconds: float | None


@dataclass(frozen=True)
class SystemMetrics:
    """Everything reported for one system."""

    name: str
    overall: Scope
    by_class: Mapping[str, Scope]
    heldout: tuple[HeldoutRow, ...] = ()


@dataclass(frozen=True)
class Metrics:
    """A run's scores."""

    systems: tuple[SystemMetrics, ...]
    ignored_seed_results: int = 0
    test_cases: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def compute_metrics(
    results: Sequence[CaseResult],
    cases: Mapping[str, FaultCase],
    *,
    order: Sequence[str] = (),
) -> Metrics:
    """Score every system in a run.

    Args:
        results: Every stored result of the run.
        cases: The specifications of the cases the run covers, by id, for the ground truth.
        order: The systems in the order to report them; any others follow alphabetically.

    Raises:
        BenchmarkError: A result belongs to a case that is not in `cases`.
    """
    missing = sorted({r.case_id for r in results} - set(cases))
    if missing:
        raise BenchmarkError(f"no specification for {', '.join(missing[:5])}; pass its folder")
    names = [*(n for n in order if any(r.system == n for r in results))]
    names += sorted({r.system for r in results} - set(names))
    scored = [r for r in results if r.split == "test"]
    systems = tuple(
        SystemMetrics(
            name,
            overall=_scope([r for r in scored if r.system == name], cases),
            by_class={
                c: _scope([r for r in scored if r.system == name and r.fault_class == c], cases)
                for c in FAULT_CLASSES
                if any(r.fault_class == c for r in scored)
            },
            heldout=tuple(
                _heldout(r, cases[r.case_id])
                for r in sorted(results, key=lambda r: r.case_id)
                if r.system == name and r.split == "heldout"
            ),
        )
        for name in names
    )
    return Metrics(
        systems,
        ignored_seed_results=sum(1 for r in results if r.split == "seed"),
        test_cases=len({r.case_id for r in scored}),
    )


def _scope(results: Sequence[CaseResult], cases: Mapping[str, FaultCase]) -> Scope:
    attempted = [r for r in results if r.status != "scenario_failed"]
    answered = [r for r in attempted if r.diagnosis is not None]
    top1 = top3 = fix = 0
    for r in answered:
        assert r.diagnosis is not None  # noqa: S101 - narrowed by `answered`
        accepted = cases[r.case_id].expected.accepted
        ranked = r.diagnosis.root_causes
        top1 += bool(ranked[:1]) and ranked[0] in accepted
        top3 += any(node in accepted for node in ranked[:3])
        fix += r.diagnosis.fix_location == cases[r.case_id].expected.fix_location
    patched = [r.diagnosis for r in answered if r.diagnosis and r.diagnosis.patch is not None]
    passing = sum(1 for d in patched if d.sandbox_passed)
    dbt_faults = sum(1 for r in attempted if cases[r.case_id].expected.fix_location == "dbt")
    # Coverage counts a passing patch only on a fault a dbt change can fix, so it is a share.
    covered = sum(
        1
        for r in answered
        if r.diagnosis
        and r.diagnosis.patch is not None
        and r.diagnosis.sandbox_passed
        and cases[r.case_id].expected.fix_location == "dbt"
    )
    times = [r.diagnosis.seconds + r.diagnosis.sandbox_seconds for r in answered if r.diagnosis]
    calls = [r.diagnosis.llm_calls for r in answered if r.diagnosis]
    tokens = [r.diagnosis.input_tokens + r.diagnosis.output_tokens for r in answered if r.diagnosis]
    return Scope(
        attempted=len(attempted),
        errored=sum(1 for r in attempted if r.status == "errored"),
        scenario_failed=sum(1 for r in results if r.status == "scenario_failed"),
        top1=rate(top1, len(attempted)),
        top3=rate(top3, len(attempted)),
        fix_location=rate(fix, len(attempted)),
        patches_produced=len(patched),
        patch_pass=rate(passing, len(patched)),
        patch_coverage=rate(covered, dbt_faults),
        time_median=statistics.median(times) if times else None,
        time_p90=percentile(times, 0.9),
        calls_mean=statistics.fmean(calls) if calls else None,
        tokens_mean=statistics.fmean(tokens) if tokens else None,
        tokens_total=sum(tokens),
    )


def _heldout(result: CaseResult, case: FaultCase) -> HeldoutRow:
    diagnosis = result.diagnosis
    if diagnosis is None:
        return HeldoutRow(result.case_id, result.status, False, False, None, "none", None)
    ranked = diagnosis.root_causes
    accepted = case.expected.accepted
    patch = (
        "none" if diagnosis.patch is None else ("passed" if diagnosis.sandbox_passed else "failed")
    )
    return HeldoutRow(
        result.case_id,
        result.status,
        top1=bool(ranked[:1]) and ranked[0] in accepted,
        top3=any(node in accepted for node in ranked[:3]),
        fix_location_ok=diagnosis.fix_location == case.expected.fix_location,
        patch=patch,
        seconds=diagnosis.seconds + diagnosis.sandbox_seconds,
    )
