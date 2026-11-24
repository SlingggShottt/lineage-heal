"""The benchmark's isolated knowledge index (FR-KB-03, FR-BEN-07).

A benchmark run retrieves from its own database holding the runbooks and the seed split's
faults written up as resolved incidents, and nothing else: not the live index and never a test
case. The index is built once per run into a template database file that each scenario copies,
so scenarios cannot leak into one another either. A seed fault's symptoms are the checks its
own sandbox raised, which is what a real past incident would have on record.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError, LineageHealError
from lineageheal.core.signals import Signal
from lineageheal.rag.embeddings import Embedder
from lineageheal.rag.incidents import IncidentIndexer, ResolvedIncident
from lineageheal.rag.indexer import RunbookIndexer
from lineageheal.store.database import create_db_engine
from lineageheal.store.knowledge import KnowledgeRepository
from lineageheal.store.migrate import upgrade_database

log = structlog.get_logger(__name__)

BENCH_NAMESPACE = "bench"
REPORT_FILE = "knowledge.json"
FIX_SUMMARY = {
    "dbt": "A change to the dbt models read the data correctly again.",
    "outside_dbt": "The file itself had to be corrected or re-fetched; no dbt change helps.",
}


@dataclass(frozen=True)
class IndexReport:
    """What went into the isolated index."""

    runbooks: int
    incidents: tuple[str, ...]  # case ids indexed
    skipped: dict[str, str]  # case id -> why no incident could be written


def symptom_lines(signals: Sequence[Signal]) -> tuple[str, ...]:
    """One line per failing check: what failed, where, how many rows and dbt's first message."""
    lines = []
    for signal in signals:
        where = ".".join(p for p in (signal.model, signal.column) if p) or signal.name
        message = signal.message.splitlines()[0] if signal.message else ""
        failures = signal.failures if signal.failures else "-"
        lines.append(f"{signal.check_type} on {where}, {failures} rows. {message}".strip())
    return tuple(lines)


def resolved_incident(case: FaultCase, signals: Sequence[Signal]) -> ResolvedIncident:
    """A seed fault written up as a resolved incident, from its ground truth and its signals."""
    return ResolvedIncident(
        slug=case.id,
        fault_class=case.fault_class,
        feeds=case.source,
        symptoms=symptom_lines(signals),
        root_cause=case.expected.root_cause,
        fix_location=case.expected.fix_location,
        fix_summary=FIX_SUMMARY[case.expected.fix_location],
    )


def build_index(
    path: Path,
    cases: Sequence[FaultCase],
    builder: ScenarioBuilder,
    embedder: Embedder,
    runbooks_dir: Path,
) -> IndexReport:
    """Create the template database at `path`: migrated, with runbooks and seed incidents.

    Args:
        path: Where to create the SQLite file; it must not exist.
        cases: The seed cases to index. A test case is refused.
        builder: Builds each seed fault's sandbox, to learn its symptoms.
        embedder: Embeds the documents.
        runbooks_dir: The runbooks to index.

    Raises:
        BenchmarkError: A case is not in the seed split, or the file already exists.
    """
    leaked = [c.id for c in cases if c.split != "seed"]
    if leaked:
        raise BenchmarkError(f"only seed cases may be indexed, not {leaked}")
    if path.exists():
        raise BenchmarkError(f"{path} already exists; the index is built once per run")
    url = f"sqlite+pysqlite:///{path}"
    upgrade_database(url)
    engine = create_db_engine(url)
    try:
        repository = KnowledgeRepository(engine)
        runbooks = RunbookIndexer(repository, embedder).index(
            runbooks_dir, namespace=BENCH_NAMESPACE
        )
        indexer = IncidentIndexer(repository, embedder)
        indexed: list[str] = []
        skipped: dict[str, str] = {}
        for case in cases:
            try:
                scenario = builder.build(case)
            except LineageHealError as exc:
                log.warning("seed_incident_skipped", case=case.id, error=str(exc))
                skipped[case.id] = str(exc)
                continue
            try:
                indexer.index(
                    resolved_incident(case, scenario.summary.signals), namespace=BENCH_NAMESPACE
                )
                indexed.append(case.id)
            finally:
                builder.discard(scenario)
    finally:
        engine.dispose()
    report = IndexReport(len(runbooks.outcomes), tuple(indexed), skipped)
    path.with_name(REPORT_FILE).write_text(
        json.dumps(
            {"runbooks": report.runbooks, "incidents": indexed, "skipped": skipped}, indent=2
        ),
        encoding="utf-8",
    )
    return report
