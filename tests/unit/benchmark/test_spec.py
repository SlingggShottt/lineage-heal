"""FR-BEN-01: a fault specification refuses a label that does not match what it does."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from lineageheal.benchmark.spec import CLASS_OF_INJECTION, FaultCase


def case(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "amfi-nulls-001",
        "class": "nulls",
        "source": "amfi",
        "dataset": "navall",
        "trade_date": date(2026, 10, 15),
        "split": "test",
        "seed": 2,
        "injection": {"type": "blank_value", "column": "Net Asset Value"},
        "expected": {"root_cause": "raw.amfi.navall.Net Asset Value", "fix_location": "dbt"},
    }
    base.update(overrides)
    return base


def test_a_valid_specification_loads_by_the_documented_field_names() -> None:
    parsed = FaultCase.model_validate(case())

    assert parsed.fault_class == "nulls"
    assert parsed.injection.type == "blank_value"
    assert parsed.version == 1


def test_every_injection_type_has_a_class() -> None:
    assert set(CLASS_OF_INJECTION) == {
        "rename_column",
        "blank_value",
        "duplicate_row",
        "scale_values",
        "stale_date",
        "withhold_file",
    }
    assert set(CLASS_OF_INJECTION.values()) == {
        "schema_drift",
        "nulls",
        "duplicates",
        "unit_change",
        "late_data",
    }


def test_a_class_that_does_not_match_the_damage_is_refused() -> None:
    with pytest.raises(ValidationError, match="blank_value is a nulls fault, not schema_drift"):
        FaultCase.model_validate(case(**{"class": "schema_drift"}))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "Bad Id"}, "lowercase words joined by hyphens"),
        ({"trade_date": date(2023, 12, 29)}, "2024-01-01 or later"),
        ({"seed": -1}, "greater than or equal to 0"),
        ({"split": "validation"}, "seed"),
        ({"source": "bse"}, "amfi"),
        ({"surprise": 1}, "Extra inputs are not permitted"),
        ({"version": 2}, "1"),
        (
            {"expected": {"root_cause": "amfi.navall.nav", "fix_location": "dbt"}},
            "raw.<source>.<dataset>.<column>",
        ),
        (
            {"expected": {"root_cause": "raw.nse.cm_bhavcopy.ClsPric", "fix_location": "dbt"}},
            "root_cause must be in raw.amfi.navall",
        ),
    ],
)
def test_an_invalid_specification_is_refused_with_the_reason(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        FaultCase.model_validate(case(**overrides))


def test_a_misspelt_injection_parameter_is_not_silently_ignored() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FaultCase.model_validate(
            case(injection={"type": "blank_value", "column": "Net Asset Value", "colum": "x"})
        )


def test_late_data_can_only_be_fixed_outside_dbt() -> None:
    with pytest.raises(ValidationError, match="late_data fault can only be fixed outside_dbt"):
        FaultCase.model_validate(
            case(
                **{"class": "late_data"},
                injection={"type": "withhold_file"},
                expected={"root_cause": "raw.amfi.navall.*", "fix_location": "dbt"},
            )
        )


def test_a_scale_factor_must_actually_change_the_values() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        FaultCase.model_validate(
            case(
                **{"class": "unit_change"},
                injection={"type": "scale_values", "column": "Net Asset Value", "factor": 1},
            )
        )
