"""Column types shared by the store's models."""

from datetime import UTC, datetime

from sqlalchemy import DateTime
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
