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
        "real_file",
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


def replay(**overrides: Any) -> dict[str, Any]:
    base = case(
        id="nse-heldout-009",
        source="nse",
        dataset="cm_bhavcopy",
        trade_date=date(2024, 7, 8),
        split="heldout",
        pipeline="pre_july_2024",
        injection={"type": "real_file"},
        expected={"root_cause": "raw.nse.cm_bhavcopy.SYMBOL", "fix_location": "dbt"},
    )
    base["class"] = "schema_drift"
    base.update(overrides)
    return base


def test_a_replay_of_the_real_file_is_a_valid_held_out_schema_drift_case() -> None:
    parsed = FaultCase.model_validate(replay())

    assert parsed.pipeline == "pre_july_2024"
    assert parsed.split == "heldout"
    assert parsed.fault_class == "schema_drift"


def test_a_replay_is_only_the_nse_bhavcopy_of_the_change_day() -> None:
    with pytest.raises(ValidationError, match=r"NSE bhavcopy on 2024-07-08"):
        FaultCase.model_validate(replay(trade_date=date(2024, 7, 9)))
    with pytest.raises(ValidationError, match=r"NSE bhavcopy on 2024-07-08"):
        FaultCase.model_validate(
            replay(
                source="amfi",
                dataset="navall",
                expected={"root_cause": "raw.amfi.navall.Date", "fix_location": "dbt"},
            )
        )


def test_the_real_file_needs_a_replay_and_so_does_a_held_out_case() -> None:
    with pytest.raises(ValidationError, match="needs a replay"):
        FaultCase.model_validate(replay(pipeline="current", split="test"))
    with pytest.raises(ValidationError, match="held-out case is a replay"):
        FaultCase.model_validate(case(split="heldout", pipeline="current"))


def test_other_acceptable_root_causes_are_checked_like_the_main_one() -> None:
    expected = {
        "root_cause": "raw.nse.cm_bhavcopy.SYMBOL",
        "fix_location": "dbt",
        "also_accept": ["raw.nse.cm_bhavcopy.CLOSE"],
    }

    parsed = FaultCase.model_validate(replay(expected=expected))

    assert parsed.expected.accepted == {
        "raw.nse.cm_bhavcopy.SYMBOL",
        "raw.nse.cm_bhavcopy.CLOSE",
    }
    with pytest.raises(ValidationError, match="must look like"):
        FaultCase.model_validate(replay(expected={**expected, "also_accept": ["CLOSE"]}))
    with pytest.raises(ValidationError, match=r"must be in raw\.nse\.cm_bhavcopy"):
        FaultCase.model_validate(
            replay(expected={**expected, "also_accept": ["raw.amfi.navall.Date"]})
        )
