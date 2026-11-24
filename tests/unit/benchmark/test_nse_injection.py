"""FR-BEN-02 for NSE bhavcopy: a zip is opened, one CSV is damaged and a valid zip comes back."""

from pathlib import Path
from typing import Any

import pytest

from lineageheal.benchmark.csv_report import split_fields
from lineageheal.benchmark.injection import apply_fault
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.spec import FaultCase
from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.core.errors import BenchmarkError

FIXTURES = Path(__file__).parents[2] / "fixtures"
CSV = (FIXTURES / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()
NAME = "BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv"
ZIP = write_single_csv(NAME, CSV)
CLOSE = "ClsPric"


def case(injection: dict[str, Any], *, seed: int = 1, fault_class: str = "nulls") -> FaultCase:
    return FaultCase.model_validate(
        {
            "id": "nse-test-001",
            "class": fault_class,
            "source": "nse",
            "dataset": "cm_bhavcopy",
            "trade_date": "2024-07-05",
            "split": "test",
            "seed": seed,
            "injection": injection,
            "expected": {
                "root_cause": f"raw.nse.cm_bhavcopy.{CLOSE}",
                "fix_location": "outside_dbt" if fault_class == "late_data" else "dbt",
            },
        }
    )


def damaged_lines(injection: dict[str, Any], **kwargs: Any) -> tuple[str, list[str], list[str]]:
    result = apply_fault(case(injection, **kwargs), ZIP)
    assert result is not None
    name, content = read_single_csv(result)
    return name, CSV.decode().split("\n"), content.decode().split("\n")


def test_the_entry_keeps_its_published_name_and_the_zip_is_deterministic() -> None:
    injection = {"type": "blank_value", "column": CLOSE}

    name, _, _ = damaged_lines(injection)

    assert name == NAME
    assert apply_fault(case(injection), ZIP) == apply_fault(case(injection), ZIP)


def test_blank_value_empties_one_close_price_and_nothing_else() -> None:
    _, old, new = damaged_lines({"type": "blank_value", "column": CLOSE})

    [row] = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    index = split_fields(old[0]).index(CLOSE)
    assert split_fields(new[row])[index] == ""
    assert split_fields(old[row])[index] != ""
    assert len(split_fields(new[row])) == len(split_fields(old[row]))


def test_duplicate_row_repeats_a_whole_data_line() -> None:
    _, old, new = damaged_lines({"type": "duplicate_row"}, fault_class="duplicates")

    assert len(new) == len(old) + 1
    assert any(new[i] == new[i + 1] and new[i] for i in range(len(new) - 1))


def test_rename_column_changes_only_the_header() -> None:
    _, old, new = damaged_lines(
        {"type": "rename_column", "column": CLOSE, "new_name": "ClosePrice"},
        fault_class="schema_drift",
    )

    assert new[1:] == old[1:]
    assert "ClosePrice" in split_fields(new[0])
    assert CLOSE not in split_fields(new[0])


def test_scale_values_multiplies_prices_and_leaves_empty_ones() -> None:
    _, old, new = damaged_lines(
        {"type": "scale_values", "column": CLOSE, "factor": 100}, fault_class="unit_change"
    )

    index = split_fields(old[0]).index(CLOSE)
    assert split_fields(old[1])[index] == "73.49"
    assert split_fields(new[1])[index] == "7349.00"
    assert split_fields(new[1])[:index] == split_fields(old[1])[:index]


def test_stale_date_changes_the_trade_date_of_every_row() -> None:
    _, old, new = damaged_lines(
        {"type": "stale_date", "column": "TradDt", "value": "2020-01-01"},
        fault_class="late_data",
    )

    index = split_fields(old[0]).index("TradDt")
    rows = [line for line in new[1:] if line]
    assert rows
    assert all(split_fields(line)[index] == "2020-01-01" for line in rows)


def test_withholding_a_zip_returns_nothing_even_for_unreadable_bytes() -> None:
    assert apply_fault(case({"type": "withhold_file"}, fault_class="late_data"), b"x") is None


def test_crlf_files_and_quoted_commas_are_kept_as_published() -> None:
    csv = b'A,B,C\r\n1,"x, y",3.5\r\n'
    zipped = write_single_csv("f.csv", csv)

    result = apply_fault(
        case({"type": "scale_values", "column": "C", "factor": 10}, fault_class="unit_change"),
        zipped,
    )

    assert result is not None
    assert read_single_csv(result)[1] == b'A,B,C\r\n1,"x, y",35.0\r\n'


def test_a_missing_column_or_a_corrupt_zip_is_an_error() -> None:
    with pytest.raises(BenchmarkError, match="has no 'ClsPric' column"):
        apply_fault(
            case({"type": "blank_value", "column": CLOSE}), write_single_csv("f", b"A\n1\n")
        )
    with pytest.raises(BenchmarkError, match="not a valid zip"):
        apply_fault(case({"type": "blank_value", "column": CLOSE}), b"nope")


def test_every_nse_example_specification_damages_the_sample() -> None:
    nse = [c for c in load_cases(FIXTURES / "benchmark") if c.source == "nse"]

    assert {c.fault_class for c in nse} == {
        "schema_drift",
        "nulls",
        "duplicates",
        "unit_change",
        "late_data",
    }
    for example in nse:
        damaged = apply_fault(example, ZIP)
        assert damaged != ZIP, example.id
