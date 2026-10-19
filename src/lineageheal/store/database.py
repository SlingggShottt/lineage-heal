"""Database connections."""

from sqlalchemy import Engine, create_engine


def create_db_engine(url: str) -> Engine:
    """Create an engine for a database URL.

    Connections are checked before use, so a database that restarted since the last
    command does not cause a spurious failure.
    """
    return create_engine(url, pool_pre_ping=True)
