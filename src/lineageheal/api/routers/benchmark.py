"""Benchmark results for the dashboard's benchmark screen (FR-API-01, FR-UI-07).

Public and read-only: there is no write endpoint. Runs are published from the command line
(`lineageheal bench publish`) by whoever ran the benchmark, so the API never has to trust a
request to put numbers in front of visitors.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ValidationError

from lineageheal.api.dependencies import EngineDep
from lineageheal.api.pagination import Page, PageQuery
from lineageheal.api.problems import PROBLEM_RESPONSES, ApiError
from lineageheal.core.benchmark_report import BenchmarkReport, Rate
from lineageheal.store.benchmark import BenchmarkRepository

router = APIRouter(
    tags=["benchmark"],
    responses={404: PROBLEM_RESPONSES[404], 503: PROBLEM_RESPONSES[503]},
)

RunId = Annotated[
    str,
    Path(pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="The id the run was named with."),
]


class SystemHeadline(BaseModel):
    """The three numbers a run's row in the list shows for one system."""

    name: str
    top1: Rate
    patch_pass: Rate
    median_seconds: float | None


class BenchmarkRunSummary(BaseModel):
    """One published run, for the list."""

    run_id: str
    generated_at: str
    test_cases: int
    systems: list[SystemHeadline]


def _summary(report: BenchmarkReport) -> BenchmarkRunSummary:
    return BenchmarkRunSummary(
        run_id=report.run_id,
        generated_at=report.generated_at,
        test_cases=report.test_cases,
        systems=[
            SystemHeadline(
                name=s.name,
                top1=s.overall.top1,
                patch_pass=s.overall.patch_pass,
                median_seconds=s.overall.time_median,
            )
            for s in report.systems
        ],
    )


def _validated(raw: dict[str, object], run_id: str | None = None) -> BenchmarkReport:
    try:
        return BenchmarkReport.model_validate(raw)
    except ValidationError as exc:
        # A report in an older shape: say so without echoing its contents.
        raise ApiError(
            503,
            "benchmark-report-unreadable",
            "A published run cannot be read",
            f"Run {run_id or raw.get('run_id')!r} was published in a shape this version of "
            "the API does not understand; publish it again.",
        ) from exc


@router.get("/benchmark/runs", summary="List published benchmark runs")
def get_runs(engine: EngineDep, page: PageQuery) -> Page[BenchmarkRunSummary]:
    """Published runs, the newest scored first, each with its headline numbers per system."""
    rows, total = BenchmarkRepository(engine).list(limit=page.limit, offset=page.offset)
    return page.page_of([_summary(_validated(row)) for row in rows], total)


@router.get("/benchmark/runs/{run_id}", summary="One benchmark run's report")
def get_run(run_id: RunId, engine: EngineDep) -> BenchmarkReport:
    """The full scored report.

    Every system overall and per fault class, the PRD goal checks and the held-out replay.
    """
    raw = BenchmarkRepository(engine).get(run_id)
    if raw is None:
        raise ApiError(
            404, "not-found", "Run not found", f"No benchmark run {run_id!r} has been published."
        )
    return _validated(raw, run_id)
