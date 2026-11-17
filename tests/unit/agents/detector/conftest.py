"""A migrated SQLite database for the Detector's tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from lineageheal.store.database import create_db_engine
from lineageheal.store.migrate import upgrade_database


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'detector.db'}"
    upgrade_database(url)
    engine = create_db_engine(url)
    yield engine
    engine.dispose()
