"""Liveness and readiness (FR-API-01).

Two endpoints because they answer different questions: a host's health check must stay cheap and
never depend on the database (a sleeping free-tier database would then restart a working API),
while a person or a deploy script wants to know the database is reachable and migrated.
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lineageheal.api.dependencies import EngineDep
from lineageheal.api.problems import PROBLEM_RESPONSES, ApiError
from lineageheal.store.migrate import engine_revision, head_revision

router = APIRouter(tags=["health"])


class Liveness(BaseModel):
    """The process is up."""

    status: str
    version: str


class Readiness(BaseModel):
    """The process is up and its database is reachable and migrated."""

    status: str
    revision: str
    head: str


@router.get("/health", summary="Liveness")
def liveness() -> Liveness:
    """Answer without touching any other service."""
    return Liveness(status="ok", version=version("lineageheal"))


@router.get(
    "/health/ready",
    summary="Readiness",
    responses={503: PROBLEM_RESPONSES[503]},
)
def readiness(engine: EngineDep) -> Readiness:
    """Check the database answers and is at the revision this code expects."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        current = engine_revision(engine)
    except SQLAlchemyError as exc:
        raise ApiError(
            503, "database-unavailable", "Database unavailable", "The database did not answer."
        ) from exc
    head = head_revision()
    if current != head:
        raise ApiError(
            503,
            "schema-out-of-date",
            "Database schema out of date",
            f"The database is at revision {current or 'none'}; this version needs {head}.",
        )
    return Readiness(status="ready", revision=head, head=head)
