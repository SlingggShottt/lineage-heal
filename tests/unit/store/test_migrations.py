"""The migrations create what the models describe, and can be run from Python or the CLI."""

import os
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import main as alembic_main
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from lineageheal.core.errors import ConfigurationError, StorageError
from lineageheal.store.database import create_db_engine
from lineageheal.store.migrate import (
    _config,
    current_revision,
    downgrade_database,
    head_revision,
    upgrade_database,
)
from lineageheal.store.models import Base

REPO_ROOT = Path(__file__).parents[3]


@pytest.fixture
def blank_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'blank.db'}"


def test_there_is_one_revision_and_it_is_the_ingestion_log() -> None:
    assert head_revision() == "0001"


def test_a_fresh_database_has_no_revision_until_upgraded(blank_url: str) -> None:
    assert current_revision(blank_url) is None

    upgrade_database(blank_url)

    assert current_revision(blank_url) == "0001"


def test_upgrading_creates_the_ingestion_log_with_its_indexes(blank_url: str) -> None:
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("ingestion_log")}
    indexes = {i["name"] for i in inspector.get_indexes("ingestion_log")}
    engine.dispose()

    assert {"id", "source", "dataset", "trade_date", "outcome", "object_key", "sha256"} <= columns
    assert indexes == {"ix_ingestion_log_lookup", "ix_ingestion_log_object_key"}


def test_upgrading_twice_changes_nothing(blank_url: str) -> None:
    upgrade_database(blank_url)
    upgrade_database(blank_url)

    assert current_revision(blank_url) == "0001"


def test_downgrading_removes_the_table(blank_url: str) -> None:
    upgrade_database(blank_url)

    downgrade_database(blank_url, "base")

    engine = create_db_engine(blank_url)
    assert "ingestion_log" not in inspect(engine).get_table_names()
    engine.dispose()
    assert current_revision(blank_url) is None


def test_the_models_and_the_migrations_describe_the_same_schema(blank_url: str) -> None:
    """If someone edits a model without writing a migration, this fails."""
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)

    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()

    assert differences == []


def test_a_percent_sign_in_the_database_path_survives(tmp_path: Path) -> None:
    """The URL is passed as an attribute, because option values would mangle a percent sign."""
    url = f"sqlite+pysqlite:///{tmp_path / '100%.db'}"

    upgrade_database(url)

    assert current_revision(url) == "0001"


def test_the_plain_alembic_command_reads_the_url_from_the_environment(
    blank_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("DATABASE_URL", blank_url)

    alembic_main(argv=["-c", "alembic.ini", "upgrade", "head"])

    assert current_revision(blank_url) == "0001"


def test_the_plain_alembic_command_says_what_is_missing_without_a_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        alembic_main(argv=["-c", "alembic.ini", "upgrade", "head"])


def test_the_sql_can_be_reviewed_without_a_database() -> None:
    config = _config("sqlite+pysqlite://")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)

    sql = config.output_buffer.getvalue()
    assert "CREATE TABLE ingestion_log" in sql
    assert "ck_ingestion_log_failures_say_why" in sql


def test_an_unreachable_database_is_reported_not_crashed_on() -> None:
    url = "sqlite+pysqlite:////no/such/folder/x.db"

    with pytest.raises(StorageError, match="Could not read the database revision"):
        current_revision(url)
    with pytest.raises(StorageError, match="Could not migrate the database"):
        upgrade_database(url)
    with pytest.raises(StorageError, match="Could not migrate the database"):
        downgrade_database(url, "base")


def test_no_credential_is_written_into_alembic_ini() -> None:
    ini = (REPO_ROOT / "alembic.ini").read_text()

    assert "sqlalchemy.url" not in ini
    assert "password" not in ini.lower()


def test_environment_is_not_polluted_by_these_tests() -> None:
    assert "DATABASE_URL" not in os.environ or os.environ["DATABASE_URL"] == ""


def test_a_migrations_folder_without_revisions_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ScriptDirectory, "get_current_head", lambda self: None)

    with pytest.raises(StorageError, match="no revisions"):
        head_revision()
