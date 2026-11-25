"""LineageHeal as a benchmark system: detect and investigate inside the scenario's sandbox.

The same graph `lineageheal heal` runs, with three differences that keep a benchmark run safe and
fair: the application database is a throwaway SQLite copy of the run's isolated index (so nothing
live can be read and nothing written outside the scenario), the tools read the scenario's own
raw zone and warehouse, and nothing is opened on GitHub. Fixing is not part of this system yet;
patches are verified for every system the same way in LH-107.
"""

from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from lineageheal.agents.graph import run_heal_report
from lineageheal.agents.graph.nodes import HealDependencies
from lineageheal.agents.graph.state import OutcomeKind
from lineageheal.agents.wiring import Detection, build_investigator, detect_run
from lineageheal.benchmark.knowledge import BENCH_NAMESPACE
from lineageheal.benchmark.models import Diagnosis, Scenario
from lineageheal.core.settings import Settings
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.llm.client import LLMClient
from lineageheal.rag.embeddings import Embedder
from lineageheal.store.database import create_db_engine
from lineageheal.store.incidents import IncidentRepository, StoredIncident
from lineageheal.store.investigations import InvestigationRepository

log = structlog.get_logger(__name__)

TOP_N = 3


class LineageHealSystem:
    """Detector then Investigator, scored on the root cause and fix location they conclude."""

    name = "lineageheal"

    def __init__(
        self, settings: Settings, client: LLMClient, template_database: Path, embedder: Embedder
    ) -> None:
        """Create the system.

        Args:
            settings: Provides the budgets and model names; its paths are overridden per fault.
            client: The model client; its usage counter costs each fault, detection included.
            template_database: The run's isolated index (runbooks and seed incidents), copied
                for every fault.
            embedder: Loaded once and shared, since loading the model takes seconds.
        """
        self._settings = settings
        self._client = client
        self._template = template_database
        self._embedder = embedder

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Run detection and investigation on the scenario and return the ranked conclusion."""
        started = time.perf_counter()
        before = (
            self._client.usage.calls,
            self._client.usage.input_tokens,
            self._client.usage.output_tokens,
        )
        database = scenario.root / "application.db"
        shutil.copy2(self._template, database)
        engine = create_db_engine(f"sqlite+pysqlite:///{database}")
        try:
            incidents = self._heal(scenario, engine)
        finally:
            engine.dispose()
        usage = self._client.usage
        ranked = rank(incidents)
        top = ranked[0] if ranked else None
        return Diagnosis(
            root_causes=tuple(node for node, _ in _ranked_nodes(ranked))[:TOP_N],
            fix_location=_fix_location(top),
            seconds=time.perf_counter() - started,
            llm_calls=usage.calls - before[0],
            input_tokens=usage.input_tokens - before[1],
            output_tokens=usage.output_tokens - before[2],
            notes=_notes(incidents),
        )

    def _heal(self, scenario: Scenario, engine: Any) -> list[StoredIncident]:
        settings = self._settings.model_copy(
            update={
                "warehouse_path": scenario.warehouse,
                "raw_zone_dir": scenario.raw_zone,
                "raw_zone_backend": "local",
            }
        )
        store = LocalObjectStore(scenario.raw_zone)
        repository = IncidentRepository(engine)
        holder: list[Detection] = []

        def detect_step() -> list[int]:
            holder.append(detect_run(engine, store, scenario.run_id, self._client))
            return [item.incident.id for item in holder[0].detected]

        deps = HealDependencies(
            detect=detect_step,
            incidents=repository,
            investigations=InvestigationRepository(engine),
            make_investigator=lambda: build_investigator(
                settings,
                engine,
                store,
                holder[0],
                self._client,
                namespace=BENCH_NAMESPACE,
                embedder=self._embedder,
            ),
            now=lambda: datetime.now(UTC),
        )
        report = run_heal_report(deps, scenario.run_id)
        failed = [o for o in report.outcomes if o.kind is OutcomeKind.FAILED]
        for outcome in failed:
            log.warning("benchmark_investigation_failed", detail=outcome.detail)
        found = (repository.get(o.incident_id) for o in report.outcomes)
        return [i for i in found if i is not None]


def rank(incidents: list[StoredIncident]) -> list[StoredIncident]:
    """Incidents with a root cause, the most confident first; ties keep the order found."""
    with_cause = [i for i in incidents if i.root_cause and i.root_cause.get("node")]
    return sorted(with_cause, key=lambda i: -(_confidence(i)))


def _confidence(incident: StoredIncident) -> float:
    cause = incident.root_cause or {}
    return float(incident.confidence or cause.get("confidence") or 0.0)


def _ranked_nodes(ranked: list[StoredIncident]) -> list[tuple[str, float]]:
    """Each incident's root cause by confidence, then their alternatives, without repeats."""
    nodes: dict[str, float] = {}
    for incident in ranked:
        nodes.setdefault(str((incident.root_cause or {})["node"]), _confidence(incident))
    alternatives: list[tuple[str, float]] = []
    for incident in ranked:
        for alt in (incident.root_cause or {}).get("alternatives", []):
            alternatives.append((str(alt["node"]), float(alt.get("confidence", 0.0))))
    for node, confidence in sorted(alternatives, key=lambda pair: -pair[1]):
        nodes.setdefault(node, confidence)
    return list(nodes.items())


def _fix_location(incident: StoredIncident | None) -> Any:
    if incident is None or incident.root_cause is None:
        return None
    location = incident.root_cause.get("fix_location")
    return location if location in ("dbt", "outside_dbt") else None


def _notes(incidents: list[StoredIncident]) -> str:
    if not incidents:
        return "no incident was detected"
    partial = sum(1 for i in incidents if (i.root_cause or {}).get("partial"))
    text = f"{len(incidents)} incident(s)"
    return f"{text}, {partial} without a conclusion" if partial else text
