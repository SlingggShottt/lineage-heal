"""FR-BEN-02: injection is deterministic and changes only what the specification says."""

from pathlib import Path
from typing import Any

import pytest

from lineageheal.benchmark.faults import AMFI_FAULTS
from lineageheal.benchmark.injection import apply_fault
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError

FIXTURES = Path(__file__).parents[2] / "fixtures"
SPECS = FIXTURES / "benchmark"
REPORT = (FIXTURES / "amfi" / "navall_history_2026-10-15_sample.txt").read_bytes()
LIVE_LAYOUT = (FIXTURES / "amfi" / "navall_live_sample.txt").read_bytes()
NAV = "Net Asset Value"


def lines(content: bytes) -> list[str]:
    return content.decode("utf-8").split("\r\n")


def changed(before: bytes, after: bytes) -> list[int]:
    old, new = lines(before), lines(after)
    assert len(old) == len(new)
    return [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]


def case(injection: dict[str, Any], *, seed: int = 1, fault_class: str = "nulls") -> FaultCase:
    return FaultCase.model_validate(
        {
            "id": "amfi-test-001",
            "class": fault_class,
            "source": "amfi",
            "dataset": "navall",
            "trade_date": "2026-10-15",
            "split": "test",
            "seed": seed,
            "injection": injection,
            "expected": {
                "root_cause": f"raw.amfi.navall.{NAV}",
                "fix_location": "outside_dbt" if fault_class == "late_data" else "dbt",
            },
        }
    )


def blank(seed: int) -> FaultCase:
    return case({"type": "blank_value", "column": NAV}, seed=seed)


def test_the_same_specification_always_gives_the_same_bytes() -> None:
    assert apply_fault(blank(7), REPORT) == apply_fault(blank(7), REPORT)


def test_different_seeds_damage_different_rows() -> None:
    rows = {tuple(changed(REPORT, apply_fault(blank(seed), REPORT) or b"")) for seed in range(20)}

    assert len(rows) > 1


def test_blank_value_empties_one_data_rows_value_and_nothing_else() -> None:
    damaged = apply_fault(blank(3), REPORT)

    assert damaged is not None
    [row] = changed(REPORT, damaged)
    original = lines(REPORT)[row].split(";")
    after = lines(damaged)[row].split(";")
    assert after[6] == ""
    assert original[6] != ""
    assert after[:6] + after[7:] == original[:6] + original[7:]
    assert original[0].isdigit()  # a data row, never a heading or blank line


def test_duplicate_row_repeats_a_data_row_next_to_itself() -> None:
    damaged = apply_fault(case({"type": "duplicate_row"}, fault_class="duplicates"), REPORT)

    assert damaged is not None
    old, new = lines(REPORT), lines(damaged)
    assert len(new) == len(old) + 1
    [at] = [i for i in range(len(old)) if new[i] == new[i + 1] and old[i].split(";")[0].isdigit()]
    assert new[: at + 1] == old[: at + 1]
    assert new[at + 2 :] == old[at + 1 :]


def test_rename_column_changes_only_the_header() -> None:
    damaged = apply_fault(
        case(
            {"type": "rename_column", "column": NAV, "new_name": "NAV"}, fault_class="schema_drift"
        ),
        REPORT,
    )

    assert damaged is not None
    assert changed(REPORT, damaged) == [0]
    assert lines(damaged)[0].split(";")[6] == "NAV"


def test_scale_values_multiplies_every_number_and_keeps_its_decimals() -> None:
    damaged = apply_fault(
        case({"type": "scale_values", "column": NAV, "factor": 100}, fault_class="unit_change"),
        REPORT,
    )

    assert damaged is not None
    old, new = lines(REPORT), lines(damaged)
    assert new[6].split(";")[6] == "2186.00"  # 21.86 x 100
    assert new[6].split(";")[:6] == old[6].split(";")[:6]
    assert [i for i in changed(REPORT, damaged) if not old[i].split(";")[0].isdigit()] == []


def test_scale_values_leaves_a_non_numeric_value_alone() -> None:
    report = (
        b"Scheme Code;NAV Name;Plan;Option;A;B;Net Asset Value;Date\r\n"
        b"1;Fund;P;O;;;N.A.;15-Oct-2026\r\n"
        b"2;Fund;P;O;;;10.5;15-Oct-2026\r\n"
    )

    damaged = apply_fault(
        case({"type": "scale_values", "column": NAV, "factor": 10}, fault_class="unit_change"),
        report,
    )

    assert damaged is not None
    assert lines(damaged)[1].split(";")[6] == "N.A."
    assert lines(damaged)[2].split(";")[6] == "105.0"


def test_stale_date_changes_every_data_rows_date() -> None:
    damaged = apply_fault(
        case(
            {"type": "stale_date", "column": "Date", "value": "01-Jan-2020"},
            fault_class="late_data",
        ),
        REPORT,
    )

    assert damaged is not None
    rows = changed(REPORT, damaged)
    assert rows
    assert all(lines(damaged)[i].endswith(";01-Jan-2020") for i in rows)


def test_withholding_a_file_returns_nothing_to_store() -> None:
    assert apply_fault(case({"type": "withhold_file"}, fault_class="late_data"), REPORT) is None


def test_untouched_bytes_stay_exactly_as_published() -> None:
    damaged = apply_fault(blank(1), LIVE_LAYOUT)

    assert damaged is not None
    assert damaged.count(b"\r\n") == LIVE_LAYOUT.count(b"\r\n")
    assert b"Children\xe2\x80\x99s Fund" in damaged


def test_the_live_layout_is_handled_because_columns_are_found_by_name() -> None:
    damaged = apply_fault(blank(1), LIVE_LAYOUT)

    assert damaged is not None
    [row] = changed(LIVE_LAYOUT, damaged)
    assert lines(damaged)[row].split(";")[6] == ""


def test_a_missing_column_or_missing_rows_is_an_error_naming_the_fault() -> None:
    with pytest.raises(BenchmarkError, match="Net Asset Value"):
        apply_fault(blank(1), b"Scheme Code;Name\r\n1;Fund\r\n")
    header_only = b"Scheme Code;NAV Name;Plan;Option;A;B;Net Asset Value;Date\r\n"
    with pytest.raises(BenchmarkError, match="amfi-test-001: the file has no data rows"):
        apply_fault(blank(1), header_only)


def test_a_dataset_without_an_injector_is_refused_not_guessed() -> None:
    nse = FaultCase.model_validate(
        {
            "id": "nse-nulls-001",
            "class": "nulls",
            "source": "nse",
            "dataset": "security_master",
            "trade_date": "2026-10-15",
            "split": "test",
            "seed": 1,
            "injection": {"type": "blank_value", "column": "ClsPric"},
            "expected": {"root_cause": "raw.nse.security_master.ClsPric", "fix_location": "dbt"},
        }
    )

    with pytest.raises(BenchmarkError, match="no injector for nse/security_master"):
        apply_fault(nse, b"x")


def test_every_example_specification_damages_the_sample_report() -> None:
    cases = load_cases(SPECS)

    assert {c.fault_class for c in cases} == {
        "schema_drift",
        "nulls",
        "duplicates",
        "unit_change",
        "late_data",
    }
    for example in (c for c in cases if c.source == "amfi"):
        damaged = apply_fault(example, REPORT)
        assert damaged != REPORT, example.id


def test_the_demo_presets_still_exist_beside_the_specification_engine() -> None:
    assert set(AMFI_FAULTS) >= {"blank-nav", "rename-column"}
