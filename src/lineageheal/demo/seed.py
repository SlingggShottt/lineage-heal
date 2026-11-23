"""Store the scenarios in the application database, and remove them again (LH-100).

Everything goes through the repositories the healing graph uses, so what the dashboard reads is
what a real run would have written. A scenario is stored whole or not at all: if one step fails,
the incident it started is removed, so a re-run is never blocked by half of one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from lineageheal.agents.fixer.patch import build_patch
from lineageheal.core.errors import ConfigurationError
from lineageheal.core.signals import RunSummary
from lineageheal.demo.scenarios import (
    FINGERPRINT_PREFIX,
    AttemptPlan,
    Scenario,
    ScenarioError,
    build_scenarios,
)
from lineageheal.lineage.graph import LineageGraph
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.lineage import LineageRepository
from lineageheal.store.models import Incident, PipelineRun
from lineageheal.store.models import Signal as SignalRow
from lineageheal.store.runs import RunRepository

log = structlog.get_logger()

# Fake incidents must never reach a shared or deployed database.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class SeedResult:
    """What a seeding run did."""

    seeded: tuple[tuple[str, int], ...]  # scenario key and the incident id it got
    skipped: tuple[str, ...]  # scenarios that were already stored


def require_local_database(url: str) -> None:
    """Refuse a database that is not on this machine.

    A file database (SQLite) and a Postgres reached through a local socket have no host and are
    allowed.

    Raises:
        ConfigurationError: The URL names a host other than this machine.
    """
    host = make_url(url).host
    if host is not None and host not in LOCAL_HOSTS:
        raise ConfigurationError(
            "Refusing to seed sample incidents into a database that is not on this machine "
            f"(host {host!r}); point DATABASE_URL at the local one from `make up`"
        )


def require_compiled(manifest: Mapping[str, Any]) -> None:
    """Check the manifest carries compiled SQL, which the lineage builder needs.

    Raises:
        ConfigurationError: The manifest came from `dbt parse`, not `dbt compile` or a build.
    """
    models = [n for n in manifest.get("nodes", {}).values() if n.get("resource_type") == "model"]
    if not models or not any(n.get("compiled_code") for n in models):
        raise ConfigurationError(
            "The manifest has no compiled SQL; run `uv run dbt compile --project-dir dbt "
            "--profiles-dir dbt --target dev` and try again"
        )


def seed(
    engine: Engine,
    *,
    graph: LineageGraph,
    manifest: Mapping[str, Any],
    repo_root: Path,
    repository: str,
    now: datetime,
    first_number: int = 101,
) -> SeedResult:
    """Store each scenario that is not stored yet.

    Args:
        engine: The application database; the caller has checked it is local.
        graph: The project's lineage graph, stored once for the incidents to point at.
        manifest: The compiled manifest the graph came from.
        repo_root: The repository, whose models the patches are made from.
        repository: `owner/name`, for the pull request and issue links.
        now: The current time; the incidents are dated before it.
        first_number: The pull request or issue number of the first scenario.

    Raises:
        ScenarioError: The project no longer matches a scenario.
        StorageError: The database could not be written.
    """
    LineageRepository(engine).save(graph)
    incidents = IncidentRepository(engine)
    seeded: list[tuple[str, int]] = []
    skipped: list[str] = []
    for scenario in build_scenarios(
        now=now, graph=graph, manifest=manifest, repo_root=repo_root, first_number=first_number
    ):
        fingerprint = FINGERPRINT_PREFIX + scenario.key
        if incidents.find_open(fingerprint) is not None:
            skipped.append(scenario.key)
            continue
        incident_id = _store(engine, scenario, graph.version, repo_root, repository)
        seeded.append((scenario.key, incident_id))
        log.debug("scenario_seeded", scenario=scenario.key, incident=incident_id)
    return SeedResult(tuple(seeded), tuple(skipped))


def reset(engine: Engine) -> tuple[int, int]:
    """Remove every seeded incident and the runs that were stored for them.

    Real incidents are never touched: only a fingerprint with the seed prefix matches, and a run
    is removed only if every signal it holds belonged to a seeded incident.

    Returns:
        How many incidents and how many runs were removed.
    """
    with Session(engine) as session, session.begin():
        ids = list(
            session.scalars(
                select(Incident.id).where(Incident.fingerprint.like(f"{FINGERPRINT_PREFIX}%"))
            )
        )
        if not ids:
            return 0, 0
        run_ids = set(
            session.scalars(select(SignalRow.run_id).where(SignalRow.incident_id.in_(ids)))
        )
        shared = set(
            session.scalars(
                select(SignalRow.run_id).where(
                    SignalRow.run_id.in_(run_ids),
                    SignalRow.incident_id.is_(None) | SignalRow.incident_id.not_in(ids),
                )
            )
        )
        removable = run_ids - shared
        session.execute(delete(Incident).where(Incident.id.in_(ids)))
        if removable:
            session.execute(delete(PipelineRun).where(PipelineRun.run_id.in_(removable)))
        return len(ids), len(removable)


def _store(
    engine: Engine, scenario: Scenario, version: str, repo_root: Path, repository: str
) -> int:
    incidents = IncidentRepository(engine)
    opened = scenario.opened_at
    errors = sum(s.severity.value == "error" for s in scenario.signals)
    RunRepository(engine).record(
        RunSummary(
            run_id=scenario.run_id,
            generated_at=scenario.run_at,
            counts={
                "success": 13 - len(scenario.skipped_models),
                "pass": 209 - errors,
                "fail": errors,
                "warn": len(scenario.signals) - errors,
                "skipped": len(scenario.skipped_models),
            },
            skipped_models=scenario.skipped_models,
            signals=scenario.signals,
        ),
        recorded_at=scenario.run_at,
    )
    incident = incidents.create(
        IncidentDraft(
            summary=scenario.summary,
            severity=scenario.severity,
            fingerprint=FINGERPRINT_PREFIX + scenario.key,
            root_node=scenario.root_node,
            affected=scenario.affected,
            lineage_version=version,
            fault_class=scenario.fault_class,
            confidence=0.6,
            classified_by="rules",
            opened_at=opened,
        )
    )
    try:
        incidents.attach(incident.id, scenario.run_id, scenario.signals, at=opened)
        investigations = InvestigationRepository(engine)
        run = investigations.start(incident.id, opened + timedelta(seconds=75))
        investigations.finish(run, scenario.investigation, opened + timedelta(minutes=4))
        _store_fix(engine, scenario, incident.id, repo_root, repository)
    except Exception:
        _remove(engine, incident.id)
        raise
    return incident.id


def _store_fix(
    engine: Engine, scenario: Scenario, incident_id: int, repo_root: Path, repository: str
) -> None:
    fixes = FixRepository(engine)
    at = scenario.opened_at + timedelta(minutes=7)
    if scenario.suggestion is not None:
        fix_id = fixes.save_suggestion(incident_id, scenario.suggestion, at)
        if scenario.publish_number is not None:
            number = scenario.publish_number
            fixes.mark_filed(fix_id, number, f"https://github.com/{repository}/issues/{number}", at)
        return
    sandboxed = [_sandboxed(plan, repo_root) for plan in scenario.attempts]
    ids = fixes.save_attempts(incident_id, sandboxed, at)
    if scenario.publish_number is not None:
        passing = next(i for i, plan in zip(ids, sandboxed, strict=True) if plan.passed)
        number = scenario.publish_number
        fixes.mark_published(
            passing,
            f"heal/{incident_id}",
            number,
            f"https://github.com/{repository}/pull/{number}",
            at + timedelta(minutes=1),
        )


def _sandboxed(plan: AttemptPlan, repo_root: Path) -> SandboxedPatch:
    text = (repo_root / plan.path).read_text(encoding="utf-8")
    for edit in plan.edits:
        if text.count(edit.old) != 1:
            raise ScenarioError(
                f"{plan.path} no longer contains exactly one copy of the text a scenario edits: "
                f"{edit.old[:60]!r}"
            )
        text = text.replace(edit.old, edit.new)
    patch = build_patch(repo_root, [(plan.path, text)])
    return SandboxedPatch(patch, plan.sandbox, plan.passed)


def _remove(engine: Engine, incident_id: int) -> None:
    with Session(engine) as session, session.begin():
        session.execute(delete(Incident).where(Incident.id == incident_id))
