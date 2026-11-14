"""Database connections."""

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import SQLAlchemyError


def create_db_engine(url: str) -> Engine:
    """Create an engine for a database URL.

    Connections are checked before use, so a database that restarted since the last
    command does not cause a spurious failure. SQLite, used by the unit tests, ignores
    foreign keys unless asked, so they are switched on to make it behave like Postgres.
    """
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enforce_foreign_keys)
    return engine


def _enforce_foreign_keys(connection: Any, _record: object) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def explain_database_error(exc: SQLAlchemyError) -> str:
    """Summarise a database error without echoing statements or parameters."""
    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    detail = lines[0] if lines else ""
    return f"{type(cause).__name__}: {detail}"
