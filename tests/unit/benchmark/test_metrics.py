"""FR-BEN-04: every metric, with its denominator, on hand-made results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from lineageheal.benchmark.metrics import compute_metrics, percentile, rate
from lineageheal.benchmark.models import CaseResult, Diagnosis, PatchRecord
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError

NOW = datetime(2026, 11, 28, tzinfo=UTC)
NAV = "raw.amfi.navall.Net Asset Value"
DATE = "raw.amfi.navall.Date"


def case(case_id: str, fault_class: str, *, split: str = "test", fix: str = "dbt") -> FaultCase:
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
            "expected": {"root_cause": NAV, "fix_location": fix},
        }
    )


CASES = {
    c.id: c
    for c in (
        case("a-1", "nulls"),
        case("a-2", "nulls"),
        case("a-3", "schema_drift"),
        case("a-4", "schema_drift"),
        case("s-1", "nulls", split="seed"),
    )
}


def result(
    case_id: str,
    system: str,
    *,
    status: str = "completed",
    causes: tuple[str, ...] = (NAV,),
    fix: str | None = "dbt",
    patch: bool | None = None,  # None: no patch; True/False: its sandbox verdict
    seconds: float = 10.0,
    sandbox: float = 0.0,
    calls: int = 2,
    tokens: int = 100,
) -> CaseResult:
    diagnosis = (
        None
        if status != "completed"
        else Diagnosis(
            root_causes=causes,
            fix_location=fix,  # type: ignore[arg-type]
            patch=None if patch is None else PatchRecord(diff="d", files=("f",), changed_lines=1),
            sandbox_passed=patch,
            seconds=seconds,
            sandbox_seconds=sandbox,
            llm_calls=calls,
            input_tokens=tokens,
            output_tokens=tokens,
        )
    )
    return CaseResult.model_validate(
        {
            "run_id": "r",
            "case_id": case_id,
            "system": system,
            "split": CASES[case_id].split,
            "fault_class": CASES[case_id].fault_class,
            "status": status,
            "diagnosis": diagnosis,
            "started_at": NOW,
            "finished_at": NOW,
        }
    )


def scores(results: list[CaseResult], system: str = "lineageheal", **kwargs: Any) -> Any:
    metrics = compute_metrics(results, CASES, **kwargs)
    return next(s for s in metrics.systems if s.name == system)


def test_top_1_and_top_3_read_the_first_and_the_first_three_ranked_nodes() -> None:
    found = scores(
        [
            result("a-1", "lineageheal", causes=(NAV, DATE)),  # first
            result("a-2", "lineageheal", causes=(DATE, "raw.amfi.navall.Plan", NAV)),  # third
            result("a-3", "lineageheal", causes=(DATE, "x.y.z.1", "x.y.z.2", NAV)),  # fourth: miss
            result("a-4", "lineageheal", causes=()),  # nothing named
        ]
    )

    assert (found.overall.top1.hits, found.overall.top1.total) == (1, 4)
    assert (found.overall.top3.hits, found.overall.top3.total) == (2, 4)


def test_a_crash_counts_as_a_miss_but_a_scenario_that_could_not_be_built_does_not_count() -> None:
    found = scores(
        [
            result("a-1", "lineageheal"),
            result("a-2", "lineageheal", status="errored"),
            result("a-3", "lineageheal", status="scenario_failed"),
        ]
    )

    assert (found.overall.top1.hits, found.overall.top1.total) == (1, 2)
    assert (found.overall.attempted, found.overall.errored, found.overall.scenario_failed) == (
        2,
        1,
        1,
    )


def test_any_of_the_accepted_nodes_counts_and_the_fix_location_is_scored() -> None:
    accepted = FaultCase.model_validate(
        {
            **CASES["a-1"].model_dump(by_alias=True),
            "id": "a-9",
            "expected": {"root_cause": NAV, "fix_location": "dbt", "also_accept": [DATE]},
        }
    )
    cases = {**CASES, "a-9": accepted}
    stored = result("a-1", "lineageheal", causes=(DATE,), fix="outside_dbt").model_copy(
        update={"case_id": "a-9"}
    )

    found = next(s for s in compute_metrics([stored], cases).systems)

    assert found.overall.top1.hits == 1
    assert found.overall.fix_location.hits == 0


def test_patches_passing_is_over_patches_produced_and_coverage_is_over_dbt_faults() -> None:
    found = scores(
        [
            result("a-1", "lineageheal", patch=True),
            result("a-2", "lineageheal", patch=False),
            result("a-3", "lineageheal", patch=None),  # no patch proposed
            result("a-4", "lineageheal", patch=True),
        ]
    )

    assert (found.overall.patch_pass.hits, found.overall.patch_pass.total) == (2, 3)
    assert found.overall.patches_produced == 3
    assert (found.overall.patch_coverage.hits, found.overall.patch_coverage.total) == (2, 4)


def test_time_is_the_systems_own_seconds_plus_its_sandbox_and_percentiles_are_nearest_rank() -> (
    None
):
    results = [
        result("a-1", "lineageheal", seconds=60, sandbox=40),
        result("a-2", "lineageheal", seconds=10),
        result("a-3", "lineageheal", seconds=20, sandbox=10),
        result("a-4", "lineageheal", seconds=300),
    ]

    found = scores(results)

    assert found.overall.time_median == 65.0  # of 10, 30, 100, 300
    assert found.overall.time_p90 == 300.0


def test_calls_and_tokens_are_averaged_per_fault() -> None:
    found = scores(
        [
            result("a-1", "lineageheal", calls=4, tokens=100),
            result("a-2", "lineageheal", calls=8, tokens=300),
        ]
    )

    assert found.overall.calls_mean == 6.0
    assert found.overall.tokens_mean == 400.0
    assert found.overall.tokens_total == 800


def test_each_fault_class_is_scored_on_its_own() -> None:
    found = scores(
        [
            result("a-1", "lineageheal"),
            result("a-2", "lineageheal", causes=(DATE,)),
            result("a-3", "lineageheal"),
            result("a-4", "lineageheal"),
        ]
    )

    assert found.by_class["nulls"].top1.value == 0.5
    assert found.by_class["schema_drift"].top1.value == 1.0
    assert "duplicates" not in found.by_class


def test_seed_results_are_not_scored_and_are_counted() -> None:
    metrics = compute_metrics([result("a-1", "lineageheal"), result("s-1", "lineageheal")], CASES)

    assert metrics.ignored_seed_results == 1
    assert metrics.systems[0].overall.attempted == 1
    assert metrics.test_cases == 1


def test_systems_are_reported_in_the_run_order_then_alphabetically() -> None:
    metrics = compute_metrics(
        [result("a-1", s) for s in ("plain-llm", "zeta", "lineageheal", "alpha")],
        CASES,
        order=["lineageheal", "plain-llm"],
    )

    assert [s.name for s in metrics.systems] == ["lineageheal", "plain-llm", "alpha", "zeta"]


def test_a_result_whose_case_is_unknown_is_an_error_naming_it() -> None:
    with pytest.raises(BenchmarkError, match="no specification for a-1"):
        compute_metrics([result("a-1", "lineageheal")], {})


def test_the_wilson_interval_behaves_at_the_extremes_and_with_nothing() -> None:
    zero, full, none = rate(0, 10), rate(10, 10), rate(0, 0)

    assert zero.low == 0.0
    assert 0.0 < (zero.high or 0) < 0.35
    assert full.high == 1.0
    assert 0.65 < (full.low or 0) < 1.0
    assert none.value is None
    assert none.low is None
    half = rate(35, 70)
    assert half.low is not None
    assert half.high is not None
    assert 0.38 < half.low < 0.40
    assert 0.60 < half.high < 0.62


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.9) is None
    assert percentile([5.0], 0.9) == 5.0
    assert percentile([float(n) for n in range(1, 11)], 0.9) == 9.0
    assert percentile([float(n) for n in range(1, 11)], 0.5) == 5.0


def test_a_passing_patch_on_a_fault_no_dbt_change_can_fix_does_not_count_as_coverage() -> None:
    outside = case("a-5", "nulls", fix="outside_dbt")
    cases = {**CASES, "a-5": outside}
    results = [
        result("a-1", "lineageheal", patch=True),
        result("a-2", "lineageheal", patch=True).model_copy(update={"case_id": "a-5"}),
    ]

    found = next(s for s in compute_metrics(results, cases).systems)

    assert (found.overall.patch_pass.hits, found.overall.patch_pass.total) == (2, 2)
    assert (found.overall.patch_coverage.hits, found.overall.patch_coverage.total) == (1, 1)
