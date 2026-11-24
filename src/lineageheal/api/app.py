"""The FastAPI application factory (FR-API-01)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lineageheal.api.problems import PROBLEM_RESPONSES, install_problem_handlers
from lineageheal.api.routers import auth, decisions, feeds, health, incidents, runbooks
from lineageheal.core.settings import Settings, get_settings

TAGS = [
    {"name": "health", "description": "Liveness and readiness."},
    {"name": "auth", "description": "GitHub sign-in and the current session."},
    {"name": "incidents", "description": "Detected failures, their diagnosis, lineage and fixes."},
    {"name": "decisions", "description": "Approvers approve or reject a proposed fix."},
    {"name": "feeds", "description": "Feed freshness and the latest pipeline run."},
    {"name": "runbooks", "description": "Procedures for each class of fault."},
]


def create_app(
    settings: Settings | None = None, *, clock: Callable[[], datetime] | None = None
) -> FastAPI:
    """Build the API.

    A factory rather than a module-level app, so a test gets its own settings and database and
    uvicorn can start it with `--factory`.

    Args:
        settings: Configuration; read from the environment when omitted.
        clock: The source of the current time, for session expiry; tests pass a fixed one.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.engine is not None:
            app.state.engine.dispose()

    app = FastAPI(
        title="LineageHeal API",
        version=version("lineageheal"),
        description="Incidents, lineage, fixes and review decisions for the dashboard.",
        openapi_tags=TAGS,
        responses=PROBLEM_RESPONSES,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.engine = None
    app.state.oauth = None
    app.state.cipher = None
    app.state.github = None
    # When each fix was last compared with GitHub (api/reconcile.py).
    app.state.reconciled = {}
    app.state.clock = clock or (lambda: datetime.now(UTC))
    install_problem_handlers(app)

    # The dashboard is on another origin (Cloudflare Pages). Credentials are allowed because
    # sign-in (LH-059) uses a session cookie, which is why an origin list is required: a
    # wildcard origin cannot be combined with credentials.
    origins = [o.strip() for o in resolved.api_cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(incidents.router)
    app.include_router(decisions.router)
    app.include_router(feeds.router)
    app.include_router(runbooks.router)
    return app


def openapi_document() -> str:
    """The API's OpenAPI schema as stable text, for the dashboard's generated client.

    The schema does not depend on any setting, so a clean `Settings` is used. Keys are sorted
    so a regenerated file differs only when the API does.
    """
    schema = create_app(Settings(_env_file=None)).openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"
