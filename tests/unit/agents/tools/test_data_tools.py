"""FR-INV-02: raw schema, profile, sample and corporate action tools."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from lineageheal.agents.tools import ToolContext, ToolRegistry, build_registry
from lineageheal.agents.tools.data_access import RawFileReader, Warehouse
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

DAY = date(2026, 10, 15)
NEXT = date(2026, 10, 16)


@pytest.fixture
def zone(tmp_path: Path) -> RawZone:
    zone = RawZone(LocalObjectStore(tmp_path / "raw"))
    zone.put_if_absent(
        source="amfi", dataset="navall", trade_date=DAY, filename="a.txt",
        content=b"Scheme Code;NAV Name;Net Asset Value\n",
    )  # fmt: skip
    zone.put_if_absent(
        source="amfi", dataset="navall", trade_date=NEXT, filename="a.txt",
        content=b"Scheme Code;Scheme Name;Net Asset Value\n",
    )  # fmt: skip
    return zone


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    path = tmp_path / "w.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("create schema silver")
    connection.execute(
        "create table silver.slv_amfi__nav"
        " (nav_date date, scheme_code bigint, nav_inr decimal(18,4))"
    )
    connection.execute(
        "insert into silver.slv_amfi__nav values (date '2026-10-15', 1, 10.0),"
        " (date '2026-10-15', 2, 20.0), (date '2026-10-15', 3, null),"
        " (date '2026-10-16', 1, 1000.0), (date '2026-10-16', 2, 2000.0)"
    )
    connection.execute(
        "create table silver.slv_nse__corporate_actions (ex_date date, symbol varchar,"
        " series varchar, action_type varchar, subject varchar, cash_amount_inr decimal(18,4),"
        " other varchar)"
    )
    connection.execute(
        "insert into silver.slv_nse__corporate_actions values"
        " (date '2026-10-16', 'ABC', 'EQ', 'split', 'Face Value Split', null, 'x'),"
        " (date '2026-12-01', 'ABC', 'EQ', 'dividend', 'Dividend - Rs 5', 5, 'x'),"
        " (date '2026-10-16', 'OTHER', 'EQ', 'bonus', 'Bonus 1:1', null, 'x')"
    )
    connection.close()
    return Warehouse(path)


@pytest.fixture
def data_registry(context: ToolContext, zone: RawZone, warehouse: Warehouse) -> ToolRegistry:
    return build_registry(replace(context, raw_files=RawFileReader(zone), warehouse=warehouse))


def test_the_raw_schema_lists_the_published_columns(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "get_raw_schema", {"source": "amfi", "dataset": "navall", "trade_date": "2026-10-15"}
    )

    assert result.ok
    assert result.payload["columns"] == ["Scheme Code", "NAV Name", "Net Asset Value"]


def test_a_renamed_column_shows_as_removed_and_added(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "compare_schemas",
        {"source": "amfi", "dataset": "navall", "date_a": "2026-10-15", "date_b": "2026-10-16"},
    )

    assert result.payload["removed"] == ["NAV Name"]
    assert result.payload["added"] == ["Scheme Name"]
    assert result.payload["identical"] is False
    assert result.payload["reordered"] is False


def test_the_profile_counts_nulls_and_keeps_decimals_exact(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "profile_column",
        {"model": "slv_amfi__nav", "column": "nav_inr", "trade_date": "2026-10-15"},
    )

    assert result.payload["rows"] == 3
    assert result.payload["nulls"] == 1
    assert result.payload["null_share"] == pytest.approx(1 / 3, abs=1e-6)
    assert result.payload["min"] == "10.0000"
    assert result.payload["max"] == "20.0000"
    assert result.payload["type"] == "DECIMAL(18,4)"


def test_comparing_profiles_shows_a_hundredfold_unit_change(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "compare_profiles",
        {
            "model": "slv_amfi__nav",
            "column": "nav_inr",
            "date_a": "2026-10-15",
            "date_b": "2026-10-16",
        },
    )

    change = result.payload["change"]
    assert change["mean_ratio"] == pytest.approx(100)
    assert change["max_ratio"] == pytest.approx(100)
    assert change["rows_ratio"] == pytest.approx(2 / 3, abs=1e-6)


def test_a_column_that_is_not_numeric_has_no_mean(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "profile_column", {"model": "slv_nse__corporate_actions", "column": "symbol"}
    )

    assert result.payload["mean"] is None
    assert result.payload["distinct"] == 2


def test_sampling_is_capped_filtered_and_limited_to_the_columns_asked_for(
    data_registry: ToolRegistry,
) -> None:
    result = data_registry.call(
        "sample_rows",
        {
            "model": "slv_amfi__nav",
            "columns": ["scheme_code"],
            "match_column": "scheme_code",
            "match_value": "1",
        },
    )

    assert result.payload["rows"] == [{"scheme_code": 1}, {"scheme_code": 1}]
    assert result.payload["limit"] == 10


def test_a_match_column_needs_a_value(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "sample_rows", {"model": "slv_amfi__nav", "match_column": "scheme_code"}
    )

    assert not result.ok
    assert "go together" in result.payload["error"]


def test_corporate_actions_near_a_date_are_listed_for_that_symbol_only(
    data_registry: ToolRegistry,
) -> None:
    result = data_registry.call(
        "get_corporate_actions", {"symbol": "ABC", "trade_date": "2026-10-15"}
    )

    (action,) = result.payload["actions"]
    assert action["action_type"] == "split"
    assert action["ex_date"] == date(2026, 10, 16)
    assert "other" not in action


def test_a_model_name_with_sql_is_refused_before_it_runs(data_registry: ToolRegistry) -> None:
    result = data_registry.call(
        "profile_column", {"model": "slv_amfi__nav; drop table x", "column": "nav_inr"}
    )

    assert not result.ok
    assert "model" in result.payload["error"]


def test_unknown_models_and_columns_come_back_as_errors(data_registry: ToolRegistry) -> None:
    missing = data_registry.call("profile_column", {"model": "nope", "column": "x"})
    bad_column = data_registry.call("profile_column", {"model": "slv_amfi__nav", "column": "nope"})

    assert not missing.ok
    assert "no table named" in missing.payload["error"]
    assert "Columns: nav_date" in bad_column.payload["error"]


def test_a_day_filter_needs_a_date_column(
    data_registry: ToolRegistry, warehouse: Warehouse, tmp_path: Path
) -> None:
    connection = duckdb.connect(str(tmp_path / "w.duckdb"))
    connection.execute("create table silver.plain (x int)")
    connection.close()

    result = data_registry.call(
        "profile_column", {"model": "plain", "column": "x", "trade_date": "2026-10-15"}
    )

    assert "no date column" in result.payload["error"]


def test_tools_without_data_say_so(context: ToolContext) -> None:
    registry = build_registry(context)

    raw = registry.call(
        "get_raw_schema", {"source": "amfi", "dataset": "navall", "trade_date": "2026-10-15"}
    )
    table = registry.call("profile_column", {"model": "m", "column": "c"})

    assert "No raw zone" in raw.payload["error"]
    assert "No warehouse" in table.payload["error"]


def test_every_data_tool_is_registered_with_a_strict_schema(data_registry: ToolRegistry) -> None:
    names = {
        "get_raw_schema",
        "compare_schemas",
        "profile_column",
        "compare_profiles",
        "sample_rows",
        "get_corporate_actions",
    }

    assert names <= set(data_registry.names)
    for spec in data_registry.specifications():
        if spec["name"] in names:
            assert spec["parameters"]["additionalProperties"] is False
    assert json.dumps(data_registry.specifications())
