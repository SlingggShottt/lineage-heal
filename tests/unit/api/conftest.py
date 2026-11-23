"""Helpers for the API tests: an app with its own settings and an in-memory database."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from lineageheal.api.app import create_app
from lineageheal.core.settings import Settings


@pytest.fixture
def memory_engine() -> Iterator[Engine]:
    """One SQLite database shared by every connection, so the app and the test see one."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    yield engine
    engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """An app with no database configured; the server's own errors are returned, not raised."""
    app = create_app(Settings())
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
