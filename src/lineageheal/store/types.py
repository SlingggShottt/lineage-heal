"""Column types shared by the store's models."""

from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware timestamp that is always stored and returned in UTC.

    Postgres keeps the zone natively; SQLite, used in unit tests, drops it, so values
    read back are re-labelled as UTC. A naive datetime is rejected on the way in, because
    its meaning would be a guess.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert to UTC before storing; refuse a datetime without a timezone."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware; got a naive datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Return an aware UTC datetime, restoring the zone SQLite drops."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# JSON is stored as JSONB on Postgres, which can be indexed and queried, and as plain JSON on
# SQLite, which the unit tests use.
JSONType = JSON().with_variant(JSONB(), "postgresql")

# Size of the vectors the knowledge index stores. The column type fixes it, so an embedding model
# with a different size needs a migration, not just a setting.
EMBEDDING_DIMENSIONS = 384

# pgvector's column on Postgres. SQLite, used by the unit tests, has no vector type, so the same
# list of numbers is kept as JSON there; the tests then exercise everything except the search.
EmbeddingType = JSON().with_variant(Vector(EMBEDDING_DIMENSIONS), "postgresql")
