"""Read-only incident endpoints: the list, one incident, its lineage and its fix (FR-API-01).

Public on purpose (FR-API-02): anyone may look, only approvers may act, and acting lives in other
endpoints guarded by `approver_action`. Nothing here changes state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from lineageheal.api.dependencies import EngineDep
from lineageheal.api.incident_schemas import (
    FaultClass,
    Feed,
    FixDetail,
    IncidentDetail,
    IncidentStatus,
    IncidentSummary,
    LineageOut,
    Severity,
    to_detail,
    to_fix_detail,
    to_lineage,
    to_summary,
)
from lineageheal.api.lineage_view import build_view
from lineageheal.api.pagination import Page, PageQuery
from lineageheal.api.problems import PROBLEM_RESPONSES, ApiError
from lineageheal.lineage.service import LineageService, UnknownNodeError
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import IncidentRepository, StoredIncident
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.lineage import LineageRepository

router = APIRouter(tags=["incidents"], responses={404: PROBLEM_RESPONSES[404]})

# Database ids are 64-bit; a larger number is not an incident, and would only reach Postgres to
# fail there.
IncidentId = Annotated[int, Path(ge=1, le=2**63 - 1, description="The incident's id.")]


@router.get("/incidents", summary="List incidents")
def list_incidents(
    engine: EngineDep,
    page: PageQuery,
    status: Annotated[IncidentStatus | None, Query()] = None,
    fault_class: Annotated[FaultClass | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    feed: Annotated[Feed | None, Query(description="The feed the root cause is in.")] = None,
) -> Page[IncidentSummary]:
    """Incidents, newest first, optionally filtered."""
    incidents, total = IncidentRepository(engine).list_incidents(
        limit=page.limit,
        offset=page.offset,
        status=status,
        fault_class=fault_class,
        severity=severity,
        feed=feed,
    )
    return page.page_of([to_summary(i) for i in incidents], total)


@router.get("/incidents/{incident_id}", summary="One incident")
def get_incident(incident_id: IncidentId, engine: EngineDep) -> IncidentDetail:
    """The incident with its root cause, signals, evidence, similar knowledge and fixes."""
    incident = _incident(engine, incident_id)
    return to_detail(
        incident,
        IncidentRepository(engine).signals_of(incident_id),
        InvestigationRepository(engine).latest_for(incident_id),
        FixRepository(engine).for_incident(incident_id),
    )


@router.get("/incidents/{incident_id}/lineage", summary="An incident's lineage slice")
def get_lineage(incident_id: IncidentId, engine: EngineDep) -> LineageOut:
    """The paths from the root cause to the failing columns and gold tables."""
    incident = _incident(engine, incident_id)
    if incident.root_node is None:
        raise _no_lineage("The incident has no root cause node yet.")
    repository = LineageRepository(engine)
    graph = (
        repository.load(incident.lineage_version)
        if incident.lineage_version
        else repository.load_current()
    )
    if graph is None:
        raise _no_lineage("The lineage graph this incident was diagnosed against is not stored.")
    try:
        view = build_view(
            LineageService(graph),
            incident.root_node,
            incident.affected.get("columns", []),
            incident.affected.get("gold_tables", []),
        )
    except UnknownNodeError as exc:
        raise _no_lineage(str(exc)) from exc
    return to_lineage(view)


@router.get("/incidents/{incident_id}/fix", summary="An incident's latest fix")
def get_fix(incident_id: IncidentId, engine: EngineDep) -> FixDetail:
    """The newest patch or suggestion, with its diff and sandbox verdict."""
    _incident(engine, incident_id)
    fixes = FixRepository(engine).for_incident(incident_id)
    if not fixes:
        raise ApiError(404, "not-found", "Fix not found", f"Incident {incident_id} has no fix yet.")
    return to_fix_detail(fixes[-1])


def _incident(engine: EngineDep, incident_id: int) -> StoredIncident:
    incident = IncidentRepository(engine).get(incident_id)
    if incident is None:
        raise ApiError(
            404, "not-found", "Incident not found", f"There is no incident {incident_id}."
        )
    return incident


def _no_lineage(detail: str) -> ApiError:
    return ApiError(404, "lineage-unavailable", "Lineage unavailable", detail)
