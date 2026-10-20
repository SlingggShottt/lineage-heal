"""Fixtures for the store's tests: a migrated SQLite database in a temporary folder."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from lineageheal.store.database import create_db_engine
from lineageheal.store.migrate import upgrade_database


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """A URL for a fresh SQLite file that has been brought up to the latest migration."""
    url = f"sqlite+pysqlite:///{tmp_path / 'log.db'}"
    upgrade_database(url)
    return url


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_db_engine(database_url)
    yield engine
    engine.dispose()
