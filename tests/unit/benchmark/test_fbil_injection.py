"""FR-BEN-02 for FBIL: the archive's HTML table is damaged in place and still stages cleanly."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from lineageheal.benchmark.html_table import HtmlTable
from lineageheal.benchmark.injection import apply_fault
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import parse_html_table

FIXTURES = Path(__file__).parents[2] / "fixtures"
CORPUS = Path(__file__).parents[3] / "benchmark" / "cases"
# The archive's own markup: a header row and one row per date, as FBIL publishes it.
SAMPLE = (FIXTURES / "fbil" / "reference_rates_2026-11-16_2026-11-20_sample.xls").read_bytes()
USD = "USD (INR / 1 USD)"


def case(injection: dict[str, Any], *, seed: int = 1, fault_class: str = "nulls") -> FaultCase:
    return FaultCase.model_validate(
        {
            "id": "fbil-test-001",
            "class": fault_class,
            "source": "fbil",
            "dataset": "reference_rates",
            "trade_date": "2026-11-20",
            "split": "test",
            "seed": seed,
            "injection": injection,
            "expected": {
                "root_cause": f"raw.fbil.reference_rates.{USD}",
                "fix_location": "outside_dbt" if fault_class == "late_data" else "dbt",
            },
        }
    )


def rows(content: bytes) -> list[list[str]]:
    return parse_html_table(content)


def test_the_table_reports_its_header_and_data_rows() -> None:
    table = HtmlTable(SAMPLE)

    assert table.fields(0)[:2] == ["Date", USD]
    assert table.rows() == [1, 2, 3, 4, 5]
    assert table.column("EUR (INR / 1 EUR)") == 3


def test_blank_value_empties_one_cell_and_changes_nothing_else() -> None:
    damaged = apply_fault(case({"type": "blank_value", "column": USD}), SAMPLE)

    assert damaged is not None
    before, after = rows(SAMPLE), rows(damaged)
    changed = [
        (r, c)
        for r in range(len(before))
        for c in range(len(before[0]))
        if before[r][c] != after[r][c]
    ]
    assert len(changed) == 1
    r, c = changed[0]
    assert (c, after[r][c]) == (1, "")
    assert r >= 1
    assert damaged.decode().count("<tr") == SAMPLE.decode().count("<tr")  # the markup is intact


def test_rename_column_changes_only_the_header_cell() -> None:
    damaged = apply_fault(
        case(
            {"type": "rename_column", "column": USD, "new_name": "USD"}, fault_class="schema_drift"
        ),
        SAMPLE,
    )

    assert damaged is not None
    assert rows(damaged)[0][1] == "USD"
    assert rows(damaged)[1:] == rows(SAMPLE)[1:]
    assert SAMPLE.decode().replace(USD, "USD", 1) == damaged.decode()


def test_duplicate_row_repeats_a_data_row_and_the_table_still_parses() -> None:
    damaged = apply_fault(case({"type": "duplicate_row"}, fault_class="duplicates"), SAMPLE)

    assert damaged is not None
    after = rows(damaged)
    assert len(after) == len(rows(SAMPLE)) + 1
    assert any(after[i] == after[i + 1] for i in range(1, len(after) - 1))


def test_stale_date_changes_the_date_of_every_row() -> None:
    damaged = apply_fault(
        case(
            {"type": "stale_date", "column": "Date", "value": "13/11/2026"}, fault_class="late_data"
        ),
        SAMPLE,
    )

    assert damaged is not None
    assert {row[0] for row in rows(damaged)[1:]} == {"13/11/2026"}


def test_scale_values_multiplies_a_currency_and_leaves_the_others() -> None:
    damaged = apply_fault(
        case({"type": "scale_values", "column": USD, "factor": 100}, fault_class="unit_change"),
        SAMPLE,
    )

    assert damaged is not None
    before, after = rows(SAMPLE), rows(damaged)
    assert [Decimal(r[1]) for r in after[1:]] == [Decimal(r[1]) * 100 for r in before[1:]]
    assert [r[2:] for r in after] == [r[2:] for r in before]


def test_the_same_specification_gives_the_same_bytes_and_a_withheld_file_gives_none() -> None:
    blank = case({"type": "blank_value", "column": USD}, seed=9)

    assert apply_fault(blank, SAMPLE) == apply_fault(blank, SAMPLE)
    assert apply_fault(case({"type": "withhold_file"}, fault_class="late_data"), SAMPLE) is None


def test_a_missing_column_or_a_document_with_no_table_is_an_error() -> None:
    with pytest.raises(BenchmarkError, match="has no 'Nope' column"):
        apply_fault(case({"type": "blank_value", "column": "Nope"}), SAMPLE)
    with pytest.raises(BenchmarkError, match="no table rows"):
        apply_fault(case({"type": "blank_value", "column": USD}), b"<html>blocked</html>")


def test_every_fbil_specification_in_the_corpus_damages_the_sample_and_it_still_stages() -> None:
    fbil = [c for c in load_cases(CORPUS) if c.source == "fbil"]

    assert len(fbil) == 28
    for example in fbil:
        damaged = apply_fault(example, SAMPLE)
        assert damaged is not None, example.id
        assert damaged != SAMPLE, example.id
        assert parse_html_table(damaged), example.id  # the stager accepts what the injector wrote


# -- the sandbox copies FBIL files too ---------------------------------------------------------


def no_dbt(project: Path, data: Path, raw: Path, target: Path, timeout: float) -> tuple[int, str]:
    return 1, "fake dbt"


def test_a_scenario_holds_fbil_history_and_the_damaged_day_is_staged_for_dbt(
    tmp_path: Path,
) -> None:
    source = LocalObjectStore(tmp_path / "src")
    zone = RawZone(source)
    day = date(2026, 11, 20)
    for offset in range(12):
        d = day - timedelta(days=offset)
        zone.put_if_absent(
            source="fbil",
            dataset="reference_rates",
            trade_date=d,
            filename=f"reference_rates_{d.isoformat()}.xls",
            content=SAMPLE + f"<!-- {offset} -->".encode(),
        )
    (tmp_path / "scratch").mkdir()
    builder = ScenarioBuilder(source, tmp_path, tmp_path / "scratch", dbt_build=no_dbt)

    scenario = builder.build(case({"type": "blank_value", "column": USD}))

    staged = sorted(
        (scenario.raw_zone / "staged/source=fbil/dataset=reference_rates").glob("*/*.csv")
    )
    assert len(staged) == 12
    damaged_day = scenario.raw_zone / f"staged/source=fbil/dataset=reference_rates/trade_date={day}"
    assert len(list(damaged_day.glob("*.csv"))) == 1
