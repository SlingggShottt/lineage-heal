"""Dependencies shared by the routers: settings and the database engine.

Both hang off `app.state`, so a test builds an app with its own settings and engine and never
touches the process environment.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine

from lineageheal.api.problems import ApiError
from lineageheal.core.settings import Settings
from lineageheal.store.database import create_db_engine


def settings_of(request: Request) -> Settings:
    """The settings the app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def engine_of(request: Request) -> Engine:
    """The database engine, created on first use so the app starts without a database.

    Raises:
        ApiError: DATABASE_URL is not set (503), which says the service is not ready, not that
            the request was wrong.
    """
    state = request.app.state
    if state.engine is None:
        url = state.settings.database_url
        if url is None:
            raise ApiError(
                503, "database-unavailable", "Database unavailable", "No database is configured."
            )
        state.engine = create_db_engine(url.get_secret_value())
    engine: Engine = state.engine
    return engine


SettingsDep = Annotated[Settings, Depends(settings_of)]
EngineDep = Annotated[Engine, Depends(engine_of)]
