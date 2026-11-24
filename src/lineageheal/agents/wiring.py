"""Builds the real detector and investigator from settings, for the command line.

Kept apart from the agents themselves so they stay free of settings, the object store and the
database, and so tests can build them from fakes instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from lineageheal.agents.detector.classify import Classifier
from lineageheal.agents.detector.collect import SignalCollector
from lineageheal.agents.detector.detect import DetectedIncident, Detector
from lineageheal.agents.fixer import Fixer, PatchLimits, Sandbox, Suggester
from lineageheal.agents.graph import FixDependencies
from lineageheal.agents.investigator import Budget, Investigator
from lineageheal.agents.tools import ToolContext, build_registry
from lineageheal.agents.tools.data_access import RawFileReader, Warehouse
from lineageheal.core.settings import Settings
from lineageheal.core.signals import RunSummary, Signal
from lineageheal.ingestion.raw_zone import ObjectStore, RawZone
from lineageheal.lineage.builder import build_lineage
from lineageheal.lineage.graph import LineageGraph
from lineageheal.lineage.service import LineageService
from lineageheal.llm.client import LLMClient
from lineageheal.orchestration.run_artifacts import load_manifest, load_run_summary, run_prefix
from lineageheal.rag.embeddings import Embedder, build_embedder
from lineageheal.rag.indexer import LIVE_NAMESPACE
from lineageheal.rag.retriever import Retriever
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import IncidentRepository
from lineageheal.store.ingestion_log import IngestionLogRepository
from lineageheal.store.knowledge import KnowledgeRepository
from lineageheal.store.lineage import LineageRepository
from lineageheal.store.runs import RunRepository
from lineageheal.vcs.factory import build_github_client

# The repository the dbt project and its git history live in: patches are paths relative to it.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Detection:
    """What detection found for a run, and the inputs it was made from."""

    summary: RunSummary
    manifest: dict[str, Any]
    graph: LineageGraph
    signals: list[Signal]
    detected: list[DetectedIncident]


def detect_run(
    engine: Engine, store: ObjectStore, run_id: str, client: LLMClient | None
) -> Detection:
    """Record a run's signals and open or update an incident for each shared cause.

    Args:
        engine: The application database.
        store: The object store holding the run's artifacts.
        run_id: The pipeline run.
        client: Refines each new incident's fault class; rules alone decide when None.

    Raises:
        LineageHealError: The run's artifacts are missing or the database cannot be used.
    """
    summary = load_run_summary(store, run_id)
    manifest = load_manifest(store, run_id)
    graph = build_lineage(manifest)
    signals = SignalCollector(RunRepository(engine), IngestionLogRepository(engine)).collect(
        summary, artifacts_prefix=run_prefix(run_id)
    )
    LineageRepository(engine).save(graph)
    detected = Detector(IncidentRepository(engine), Classifier(client)).detect(
        run_id, signals, graph, at=summary.generated_at
    )
    return Detection(summary, manifest, graph, signals, detected)


def build_investigator(
    settings: Settings,
    engine: Engine,
    store: ObjectStore,
    detection: Detection,
    client: LLMClient,
    *,
    namespace: str = LIVE_NAMESPACE,
    embedder: Embedder | None = None,
) -> Investigator:
    """An Investigator whose tools read this run's records, the raw zone and the warehouse.

    `namespace` picks which knowledge index it retrieves from (a benchmark run uses its own) and
    `embedder` lets a caller that investigates many faults load the model once.
    """
    lineage = LineageService(detection.graph)
    context = ToolContext(
        lineage=lineage,
        manifest=detection.manifest,
        summary=detection.summary,
        ingestion_log=IngestionLogRepository(engine),
        retriever=Retriever(
            KnowledgeRepository(engine), embedder or build_embedder(settings), namespace=namespace
        ),
        raw_files=RawFileReader(RawZone(store)),
        warehouse=Warehouse(settings.warehouse_path),
    )
    return Investigator(
        client, build_registry(context), lineage, budget=Budget.from_settings(settings)
    )


def build_fixing(
    settings: Settings,
    engine: Engine,
    detection: Detection,
    client: LLMClient,
    *,
    publish: bool,
) -> FixDependencies:
    """The Fixer, sandbox and suggester for a run, and the GitHub client when publishing.

    Raises:
        ConfigurationError: Publishing was asked for but the GitHub App is not configured.
    """
    lineage = LineageService(detection.graph)
    limits = PatchLimits(
        max_files=settings.fixer_max_files, max_changed_lines=settings.fixer_max_changed_lines
    )
    return FixDependencies(
        fixer=Fixer(
            client,
            lineage,
            REPO_ROOT,
            limits=limits,
            max_llm_calls=settings.fixer_max_llm_calls,
        ),
        sandbox=Sandbox(
            REPO_ROOT,
            settings.warehouse_path,
            settings.raw_zone_dir,
            timeout_seconds=settings.sandbox_timeout_seconds,
        ),
        suggester=Suggester(client),
        fixes=FixRepository(engine),
        lineage=lineage,
        now=lambda: datetime.now(UTC),
        max_attempts=settings.fixer_max_attempts,
        github=build_github_client(settings) if publish else None,
    )
