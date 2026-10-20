"""Running Alembic migrations from Python.

The same migrations back both `lineageheal db upgrade` and the plain `alembic` command
(configured by alembic.ini); this module passes the database URL in directly so the
command-line tool never depends on a configuration file.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

from lineageheal.core.errors import StorageError
from lineageheal.store.database import create_db_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url is not None:
        # An attribute, not an option: option values go through string interpolation, which
        # would mangle a password containing a percent sign.
        config.attributes["database_url"] = url
    return config


def head_revision() -> str:
    """Return the newest revision the code knows about."""
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:
        raise StorageError("The migrations folder has no revisions")
    return head


def current_revision(url: str) -> str | None:
    """Return the revision the database is at, or None if no migration has run.

    Raises:
        StorageError: The database could not be reached.
    """
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    except SQLAlchemyError as exc:
        raise StorageError(f"Could not read the database revision: {_summary(exc)}") from exc
    finally:
        engine.dispose()


def upgrade_database(url: str, revision: str = "head") -> None:
    """Apply migrations up to `revision`.

    Raises:
        StorageError: The database could not be reached or a migration failed.
    """
    try:
        command.upgrade(_config(url), revision)
    except SQLAlchemyError as exc:
        raise StorageError(f"Could not migrate the database: {_summary(exc)}") from exc


def downgrade_database(url: str, revision: str) -> None:
    """Revert migrations down to `revision`.

    Raises:
        StorageError: The database could not be reached or a migration failed.
    """
    try:
        command.downgrade(_config(url), revision)
    except SQLAlchemyError as exc:
        raise StorageError(f"Could not migrate the database: {_summary(exc)}") from exc


def _summary(exc: SQLAlchemyError) -> str:
    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    return f"{type(cause).__name__}: {lines[0] if lines else ''}"
