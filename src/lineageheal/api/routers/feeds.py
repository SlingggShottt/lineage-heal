"""Feed freshness and the latest run, for the overview screen (FR-API-01, FR-UI-01).

Public and read-only. It reads the ingestion log and the run table only, since the API host has
no warehouse or raw zone.
"""

from __future__ import annotations

from fastapi import APIRouter

from lineageheal.api.dependencies import EngineDep
from lineageheal.api.feed_health import feeds_health
from lineageheal.api.health_schemas import FeedsHealth
from lineageheal.api.problems import PROBLEM_RESPONSES
from lineageheal.store.ingestion_log import IngestionLogRepository
from lineageheal.store.runs import RunRepository

router = APIRouter(tags=["feeds"], responses={503: PROBLEM_RESPONSES[503]})


@router.get("/feeds/health", summary="Feed freshness and the latest run")
def get_feeds_health(engine: EngineDep) -> FeedsHealth:
    """Each feed's newest stored day and latest attempt, and the newest dbt run's outcome."""
    return feeds_health(IngestionLogRepository(engine), RunRepository(engine))
