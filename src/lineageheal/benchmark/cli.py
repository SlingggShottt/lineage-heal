"""The `lineageheal bench` commands: list the corpus and run systems over it."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from lineageheal.benchmark.corpus import summarise
from lineageheal.benchmark.knowledge import build_index
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.models import System
from lineageheal.benchmark.provenance import build_manifest
from lineageheal.benchmark.results import ResultStore
from lineageheal.benchmark.runner import BenchmarkRunner, NoneSystem, scratch_folder
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.benchmark.systems.lineageheal import LineageHealSystem
from lineageheal.benchmark.systems.plain_llm import PlainLLMSystem
from lineageheal.benchmark.systems.rule_based import RuleBasedSystem
from lineageheal.core.errors import LineageHealError
from lineageheal.core.settings import Settings, get_settings
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.llm.factory import build_client
from lineageheal.rag.embeddings import build_embedder

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES = REPO_ROOT / "benchmark" / "cases"


@dataclass(frozen=True)
class BenchContext:
    """What a system may need to be built for one run."""

    settings: Settings
    store: ResultStore
    builder: ScenarioBuilder
    cases: list[FaultCase]  # the whole corpus, not just the selection


def _lineageheal(context: BenchContext) -> System:
    """LineageHeal with the run's isolated index, built once and reused when a run resumes."""
    client = build_client(context.settings)
    embedder = build_embedder(context.settings)
    ready = context.store.root / "knowledge.db"
    if not ready.exists():
        building = context.store.root / "knowledge.partial"
        building.unlink(missing_ok=True)
        context.store.root.mkdir(parents=True, exist_ok=True)
        seeds = [c for c in context.cases if c.split == "seed"]
        report = build_index(
            building, seeds, context.builder, embedder, context.settings.runbooks_dir
        )
        building.rename(ready)
        typer.echo(
            f"isolated index: {report.runbooks} runbooks, {len(report.incidents)} seed incidents"
            f", {len(report.skipped)} seed cases could not be built"
        )
    return LineageHealSystem(context.settings, client, ready, embedder)


# Systems the runner can evaluate by name. `plain-llm` and `lineageheal` need a model key and
# fail with a clear message without one.
SYSTEMS: dict[str, Callable[[BenchContext], System]] = {
    "none": lambda _: NoneSystem(),
    "rule-based": lambda _: RuleBasedSystem(),
    "plain-llm": lambda context: PlainLLMSystem(build_client(context.settings)),
    "lineageheal": _lineageheal,
}

bench_app = typer.Typer(no_args_is_help=True, help="The fault-injection benchmark.")

CasesOption = Annotated[Path, typer.Option("--cases", help="Folder of fault specifications.")]


def _select(
    cases: list[FaultCase], split: str | None, fault_class: str | None, limit: int | None
) -> list[FaultCase]:
    chosen = [
        c
        for c in cases
        if (split is None or c.split == split)
        and (fault_class is None or c.fault_class == fault_class)
    ]
    return chosen[:limit] if limit else chosen


@bench_app.command("list")
def list_cases(
    cases_dir: CasesOption = DEFAULT_CASES,
    split: Annotated[str | None, typer.Option(help="Only `seed` or `test`.")] = None,
    fault_class: Annotated[str | None, typer.Option(help="Only this fault class.")] = None,
) -> None:
    """Show the corpus: counts by split, class and feed, then the matching case ids."""
    try:
        cases = load_cases(cases_dir)
    except LineageHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    summary = summarise(cases)
    typer.echo(f"{summary.total} cases: {dict(sorted(summary.by_split.items()))}")
    typer.echo(f"by feed: {dict(sorted(summary.by_source.items()))}")
    for case in _select(cases, split, fault_class, None):
        typer.echo(f"{case.id:28} {case.split:5} {case.trade_date} {case.injection.type}")


@bench_app.command("run")
def run(
    run_id: Annotated[
        str, typer.Option(help="Names the run; reuse it to resume where it stopped.")
    ],
    system: Annotated[
        list[str], typer.Option("--system", help=f"A system to evaluate: {', '.join(SYSTEMS)}.")
    ],
    cases_dir: CasesOption = DEFAULT_CASES,
    split: Annotated[str | None, typer.Option(help="Only `seed` or `test`.")] = "test",
    fault_class: Annotated[str | None, typer.Option(help="Only this fault class.")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Run at most this many cases.")] = None,
    keep_scenarios: Annotated[
        bool, typer.Option(help="Keep each sandbox after use, for debugging.")
    ] = False,
) -> None:
    """Evaluate systems on the faults, storing a result per fault and system as it goes.

    Each fault is built into a sandbox (a copy of the feed's recent clean files with the fault
    injected, built by dbt on the local bench target) that every system sees. Clean files are
    read from BENCH_SOURCE_RAW_ZONE (default RAW_ZONE_DIR) and never changed; a fault whose
    dates are not backfilled is recorded as a failed scenario. Rerun with the same `--run-id` to
    resume.
    """
    unknown = [name for name in system if name not in SYSTEMS]
    if unknown:
        raise typer.BadParameter(f"unknown system {unknown}; choose from {', '.join(SYSTEMS)}")
    settings = get_settings()
    try:
        corpus = load_cases(cases_dir)
        cases = _select(corpus, split, fault_class, limit)
        if not cases:
            raise typer.BadParameter("no cases match those filters")
        store = ResultStore(settings.bench_dir / "results", run_id)
        builder = ScenarioBuilder(
            source=LocalObjectStore(settings.bench_source_raw_zone or settings.raw_zone_dir),
            repo_root=REPO_ROOT,
            scratch_root=scratch_folder(settings.bench_dir, run_id),
        )
        context = BenchContext(settings, store, builder, corpus)
        runner = BenchmarkRunner(
            builder, store, [SYSTEMS[n](context) for n in system], keep_scenarios=keep_scenarios
        )
        report = runner.run(cases, build_manifest(settings, REPO_ROOT, run_id, cases, system))
    except LineageHealError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"{report.ran} ran, {report.skipped} already done, {report.errored} errored, "
        f"{report.scenario_failed} scenarios failed; results in {store.root}"
    )
    for failure in report.failures[:10]:
        typer.echo(f"  {re.sub(r'\s+', ' ', failure)[:200]}")
    if report.errored or report.scenario_failed:
        raise typer.Exit(code=1)
