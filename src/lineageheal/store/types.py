"""Column types shared by the store's models."""

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

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


def json_safe(value: Any) -> Any:
    """Return `value` as plain JSON data, for a column that takes whatever a tool returned.

    A warehouse row holds `date` and `Decimal` values, which JSON cannot encode. SQLite reports
    that as a database error, but psycopg encodes JSONB itself, inside the call, so on Postgres
    it escapes as a bare TypeError and the whole save is lost (LH-113). Losing a detail of how
    a value was written is better than losing the investigation, so each value becomes its
    closest JSON form: dates as ISO text, decimals as text (a number would lose their digits and
    become a float), sets as sorted lists, keys as text, and anything unknown as its `str`.
    Postgres also refuses `NaN` and infinity, so those become text too.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, date):  # datetime is a date, so this keeps its time and zone too
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((json_safe(item) for item in value), key=str)
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return str(value)


# Size of the vectors the knowledge index stores. The column type fixes it, so an embedding model
# with a different size needs a migration, not just a setting.
EMBEDDING_DIMENSIONS = 384

# pgvector's column on Postgres. SQLite, used by the unit tests, has no vector type, so the same
# list of numbers is kept as JSON there; the tests then exercise everything except the search.
EmbeddingType = JSON().with_variant(Vector(EMBEDDING_DIMENSIONS), "postgresql")
