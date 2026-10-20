"""Timestamps are timezone-aware and in UTC everywhere (style guide, section 3.2)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import sqlite

from lineageheal.store.types import UTCDateTime

IST = timezone(timedelta(hours=5, minutes=30))
DIALECT = sqlite.dialect()


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 10, 16, 12, 0), DIALECT)  # noqa: DTZ001


def test_a_datetime_in_another_zone_is_stored_as_the_same_instant_in_utc() -> None:
    stored = UTCDateTime().process_bind_param(datetime(2026, 10, 16, 18, 30, tzinfo=IST), DIALECT)

    assert stored == datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
    assert stored is not None
    assert stored.utcoffset() == timedelta(0)


def test_a_value_read_back_without_a_zone_is_labelled_utc() -> None:
    """SQLite drops the zone; the type restores it."""
    restored = UTCDateTime().process_result_value(datetime(2026, 10, 16, 13, 0), DIALECT)  # noqa: DTZ001

    assert restored == datetime(2026, 10, 16, 13, 0, tzinfo=UTC)


def test_nothing_stays_nothing() -> None:
    assert UTCDateTime().process_bind_param(None, DIALECT) is None
    assert UTCDateTime().process_result_value(None, DIALECT) is None


def test_a_value_read_back_with_a_zone_is_converted_to_utc() -> None:
    """Postgres returns the zone; whatever it is, the result is the same instant in UTC."""
    restored = UTCDateTime().process_result_value(
        datetime(2026, 10, 16, 18, 30, tzinfo=IST), DIALECT
    )

    assert restored == datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
    assert restored is not None
    assert restored.utcoffset() == timedelta(0)
