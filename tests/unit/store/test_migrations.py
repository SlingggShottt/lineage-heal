"""The migrations create what the models describe, and can be run from Python or the CLI."""

import os
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import main as alembic_main
from alembic.script import ScriptDirectory
from schema_drift import schema_differences
from sqlalchemy import CheckConstraint, Connection, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import TextClause

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


def test_the_newest_revision_allows_one_decision_per_fix() -> None:
    assert head_revision() == "0009"


def test_a_fresh_database_has_no_revision_until_upgraded(blank_url: str) -> None:
    assert current_revision(blank_url) is None

    upgrade_database(blank_url)

    assert current_revision(blank_url) == "0009"


def test_upgrading_creates_the_ingestion_log_with_its_indexes(blank_url: str) -> None:
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("ingestion_log")}
    indexes = {i["name"] for i in inspector.get_indexes("ingestion_log")}
    engine.dispose()

    assert {"id", "source", "dataset", "trade_date", "outcome", "object_key", "sha256"} <= columns
    assert indexes == {"ix_ingestion_log_lookup", "ix_ingestion_log_object_key"}


def test_upgrading_creates_every_application_table(blank_url: str) -> None:
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)

    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert tables >= {
        "pipeline_run",
        "signal",
        "incident",
        "agent_run",
        "evidence",
        "fix",
        "review",
        "lineage_version",
        "lineage_node",
        "lineage_edge",
        "knowledge_document",
        "knowledge_chunk",
    }


def test_the_migrations_and_the_models_carry_the_same_check_constraints(blank_url: str) -> None:
    """Alembic's comparison skips check constraints, which here are the database's rules."""
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)
    inspector = inspect(engine)

    in_database = {
        (table, check["name"])
        for table in inspector.get_table_names()
        for check in inspector.get_check_constraints(table)
    }
    engine.dispose()
    in_models = {
        (table.name, constraint.name)
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert in_database == in_models


def test_upgrading_twice_changes_nothing(blank_url: str) -> None:
    upgrade_database(blank_url)
    upgrade_database(blank_url)

    assert current_revision(blank_url) == "0009"


def test_downgrading_removes_the_tables(blank_url: str) -> None:
    upgrade_database(blank_url)

    downgrade_database(blank_url, "0001")
    engine = create_db_engine(blank_url)
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == {"ingestion_log"}
    engine.dispose()

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
        differences = schema_differences(connection)
    engine.dispose()

    assert differences == []


def test_a_percent_sign_in_the_database_path_survives(tmp_path: Path) -> None:
    """The URL is passed as an attribute, because option values would mangle a percent sign."""
    url = f"sqlite+pysqlite:///{tmp_path / '100%.db'}"

    upgrade_database(url)

    assert current_revision(url) == "0009"


def test_the_plain_alembic_command_reads_the_url_from_the_environment(
    blank_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("DATABASE_URL", blank_url)

    alembic_main(argv=["-c", "alembic.ini", "upgrade", "head"])

    assert current_revision(blank_url) == "0009"


def test_the_plain_alembic_command_says_what_is_missing_without_a_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        alembic_main(argv=["-c", "alembic.ini", "upgrade", "head"])


def test_the_sql_can_be_reviewed_without_a_database() -> None:
    # Rendered for Postgres, which is what a reviewer reads. SQLite rebuilds a table to change
    # a constraint, and that needs a live connection to reflect it.
    config = _config("postgresql+psycopg://reviewer@localhost/none")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)

    sql = config.output_buffer.getvalue()
    assert "CREATE TABLE ingestion_log" in sql
    assert "ck_ingestion_log_failures_say_why" in sql
    assert "'ingestion_error'" in sql


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


def test_the_ingestion_error_kind_keeps_existing_signals_and_downgrades_cleanly(
    blank_url: str,
) -> None:
    upgrade_database(blank_url, "0003")
    engine = create_db_engine(blank_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO pipeline_run (run_id, status, generated_at, counts, skipped_models,"
                " recorded_at) VALUES ('r1', 'failed', '2026-11-17 10:00:00.000000', '{}', '[]',"
                " '2026-11-17 10:00:00.000000')"
            )
        )
        connection.execute(_signal_insert("test_failed", "kept"))

    upgrade_database(blank_url)
    with engine.begin() as connection:
        connection.execute(_signal_insert("ingestion_error", "amfi/navall/2026-10-15"))
        assert _signal_names(connection) == ["amfi/navall/2026-10-15", "kept"]

    downgrade_database(blank_url, "0003")
    with engine.begin() as connection:
        assert _signal_names(connection) == ["kept"]
        with pytest.raises(IntegrityError):
            connection.execute(_signal_insert("ingestion_error", "again"))
    engine.dispose()


def _signal_insert(kind: str, name: str) -> TextClause:
    return text(
        "INSERT INTO signal (run_id, kind, severity, detected_at, name, check_type, models,"
        " message) VALUES ('r1', :kind, 'error', '2026-11-17 10:00:00.000000', :name, 'x', '[]',"
        " '')"
    ).bindparams(kind=kind, name=name)


def _signal_names(connection: Connection) -> list[str]:
    rows = connection.execute(text("SELECT name FROM signal ORDER BY name"))
    return [row[0] for row in rows]


def test_the_incident_columns_keep_existing_incidents_with_safe_defaults(blank_url: str) -> None:
    upgrade_database(blank_url, "0004")
    engine = create_db_engine(blank_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO incident (status, summary, opened_at, updated_at) VALUES "
                "('detected', 'old incident', '2026-11-17 10:00:00.000000',"
                " '2026-11-17 10:00:00.000000')"
            )
        )

    upgrade_database(blank_url)
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT summary, severity, fingerprint, root_node, lineage_version FROM incident")
        ).one()
    assert tuple(row) == ("old incident", "error", "", None, None)

    downgrade_database(blank_url, "0004")
    with engine.begin() as connection:
        assert (
            connection.execute(text("SELECT summary FROM incident")).scalar_one() == "old incident"
        )
    engine.dispose()


def test_upgrading_creates_the_knowledge_tables_with_their_indexes(blank_url: str) -> None:
    upgrade_database(blank_url)
    engine = create_db_engine(blank_url)

    inspector = inspect(engine)
    chunk_columns = {c["name"] for c in inspector.get_columns("knowledge_chunk")}
    indexes = {i["name"] for i in inspector.get_indexes("knowledge_chunk")}
    engine.dispose()

    assert {"document_id", "position", "text", "embedding", "embedding_model"} <= chunk_columns
    assert "ix_knowledge_chunk_embedding" in indexes


def test_the_offline_sql_enables_pgvector_and_adds_the_search_indexes() -> None:
    config = _config("postgresql+psycopg://reviewer@localhost/none")
    config.output_buffer = StringIO()

    command.upgrade(config, "head", sql=True)

    sql = config.output_buffer.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "USING gin (to_tsvector('english', text))" in sql
