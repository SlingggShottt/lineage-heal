"""Timestamps are timezone-aware and in UTC everywhere (style guide, section 3.2)."""

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.dialects import sqlite

from lineageheal.store.types import UTCDateTime, json_safe

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


# --- json_safe (LH-113) -----------------------------------------------------------------------


def test_values_json_already_holds_come_back_unchanged() -> None:
    value = {"a": [1, 2.5, "x", None, True], "b": {"c": False}}

    assert json_safe(value) == value


def test_a_date_becomes_its_iso_text_and_a_datetime_keeps_its_time_and_zone() -> None:
    assert json_safe(date(2026, 11, 20)) == "2026-11-20"
    assert json_safe(datetime(2026, 11, 20, 9, 30, tzinfo=UTC)) == "2026-11-20T09:30:00+00:00"


def test_a_decimal_becomes_text_so_its_digits_are_kept() -> None:
    """A JSON number would be read back as a float, and 21.8600 would become 21.86."""
    assert json_safe(Decimal("21.8600")) == "21.8600"


def test_a_warehouse_row_is_converted_all_the_way_down() -> None:
    row = {"rows": [{"nav_date": date(2026, 11, 20), "nav_inr": Decimal("21.8600")}], "n": 1}

    assert json_safe(row) == {"rows": [{"nav_date": "2026-11-20", "nav_inr": "21.8600"}], "n": 1}


def test_the_result_can_be_encoded_by_the_standard_json_module() -> None:
    row = {"d": date(2026, 11, 20), "x": Decimal("1.5"), "s": {3, 1, 2}, "t": (1, 2)}

    assert json.loads(json.dumps(json_safe(row))) == {
        "d": "2026-11-20",
        "x": "1.5",
        "s": [1, 2, 3],
        "t": [1, 2],
    }


def test_sets_become_sorted_lists_so_the_same_set_always_stores_the_same_way() -> None:
    assert json_safe({"b", "a", "c"}) == ["a", "b", "c"]


def test_keys_that_are_not_text_become_text() -> None:
    assert json_safe({date(2026, 11, 20): 1, 7: 2}) == {"2026-11-20": 1, "7": 2}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_values_postgres_refuses_become_text(value: float) -> None:
    """JSONB rejects NaN and infinity, which a warehouse average can produce."""
    assert json_safe(value) == str(value)


def test_an_object_json_does_not_know_becomes_its_text() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    assert json_safe({"x": Opaque()}) == {"x": "opaque"}


def test_the_input_is_not_changed() -> None:
    original = {"d": date(2026, 11, 20), "rows": [{"x": Decimal("1")}]}

    json_safe(original)

    assert original == {"d": date(2026, 11, 20), "rows": [{"x": Decimal("1")}]}
