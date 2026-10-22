"""FR-ORC-01: the dbt project appears in Dagster as assets and checks, joined to the raw assets."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import dagster as dg
import pytest

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import get_settings
from lineageheal.orchestration import dbt_assets
from lineageheal.orchestration.assets import raw_asset_key, raw_assets
from lineageheal.orchestration.dbt_assets import (
    ALL_RAW_ASSETS,
    RAW_ZONE_FILES,
    dbt_project,
    lineageheal_dbt_assets,
)
from lineageheal.orchestration.definitions import defs

MANIFEST = json.loads(dbt_project.manifest_path.read_text())
MODELS = {n["name"]: n for n in MANIFEST["nodes"].values() if n["resource_type"] == "model"}


@pytest.fixture(scope="module")
def graph() -> dg.AssetGraph:  # type: ignore[type-arg]
    return defs.resolve_asset_graph()


def model_key(name: str) -> dg.AssetKey:
    return dg.AssetKey([MODELS[name]["schema"], name])


# --- the graph ---------------------------------------------------------------------------


def test_every_dbt_model_is_an_asset_beside_the_raw_assets(graph: dg.AssetGraph) -> None:  # type: ignore[type-arg]
    assert set(graph.get_all_asset_keys()) == {model_key(n) for n in MODELS} | set(ALL_RAW_ASSETS)
    assert len(MODELS) >= 12


def test_models_are_grouped_by_layer_and_raw_files_have_their_own_group(
    graph: dg.AssetGraph,  # type: ignore[type-arg]
) -> None:
    for name, node in MODELS.items():
        assert graph.get(model_key(name)).group_name == node["schema"]
    assert {graph.get(key).group_name for key in ALL_RAW_ASSETS} == {"raw"}
    assert {n["schema"] for n in MODELS.values()} == {"bronze", "silver", "gold"}


def test_a_model_depends_on_the_raw_assets_of_the_sources_it_reads(
    graph: dg.AssetGraph,  # type: ignore[type-arg]
) -> None:
    """The dbt source names and the raw asset keys were chosen to agree (LH-030)."""
    checked = 0
    for name, node in MODELS.items():
        sources = [
            MANIFEST["sources"][dep]
            for dep in node["depends_on"]["nodes"]
            if dep in MANIFEST["sources"]
        ]
        expected = {
            dg.AssetKey([s["source_name"], s["name"]])
            for s in sources
            if s["source_name"] != "raw_zone"
        }
        parents = set(graph.get(model_key(name)).parent_keys)
        assert expected <= parents, name
        checked += len(expected)
    assert checked == len(raw_assets)  # each of the four raw datasets is read by a model


def test_the_all_files_source_stands_for_every_raw_dataset(graph: dg.AssetGraph) -> None:  # type: ignore[type-arg]
    assert RAW_ZONE_FILES not in set(graph.get_all_asset_keys())
    versions = graph.get(model_key("brz_meta__raw_file_versions"))
    assert set(ALL_RAW_ASSETS) <= set(versions.parent_keys)


def test_a_model_depends_on_the_models_it_selects_from(graph: dg.AssetGraph) -> None:  # type: ignore[type-arg]
    parents = set(graph.get(model_key("gld_market_daily_summary")).parent_keys)

    assert {model_key("gld_equity_daily_prices"), model_key("gld_mf_nav_daily")} <= parents


def test_the_whole_table_is_rebuilt_from_all_the_days_of_raw_files(
    graph: dg.AssetGraph,  # type: ignore[type-arg]
) -> None:
    """Dbt models are not partitioned; they read every stored day."""
    key = model_key("brz_amfi__navall")

    assert not graph.get(key).is_partitioned
    mapping = graph.get_partition_mapping(key, raw_asset_key("amfi", "navall"))
    assert isinstance(mapping, dg.AllPartitionMapping)


# --- checks and pools --------------------------------------------------------------------


def test_dbt_tests_become_asset_checks_on_the_models_they_test() -> None:
    checks = {(spec.asset_key, spec.name) for spec in lineageheal_dbt_assets.check_specs}

    assert len(checks) > 100
    assert (model_key("slv_amfi__nav"), "not_null_slv_amfi__nav_nav_inr") in checks
    assert (
        model_key("gld_equity_daily_prices"),
        "gld_equity_daily_prices_no_change_of_scale",
    ) in checks


def test_dbt_runs_share_one_pool_because_duckdb_has_a_single_writer() -> None:
    assert lineageheal_dbt_assets.op.pool == "warehouse"


# --- finding the manifest ----------------------------------------------------------------


def make_project(root: Path) -> Path:
    (root / "models").mkdir(parents=True)
    (root / "target").mkdir()
    (root / "dbt_project.yml").write_text("name: x\n")
    (root / "models" / "a.sql").write_text("select 1\n")
    return root / "target" / "manifest.json"


def age(path: Path, seconds: float) -> None:
    moment = time.time() + seconds
    os.utime(path, (moment, moment))


def test_a_missing_manifest_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = make_project(tmp_path)
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)

    assert dbt_assets._manifest_is_stale(manifest)


def test_a_manifest_newer_than_every_project_file_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_project(tmp_path)
    manifest.write_text("{}")
    age(manifest, +100)
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)

    assert not dbt_assets._manifest_is_stale(manifest)


def test_editing_a_model_makes_the_manifest_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_project(tmp_path)
    manifest.write_text("{}")
    age(manifest, +100)
    age(tmp_path / "models" / "a.sql", +200)
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)

    assert dbt_assets._manifest_is_stale(manifest)


def test_files_dbt_generates_do_not_count_as_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_project(tmp_path)
    manifest.write_text("{}")
    age(manifest, +100)
    compiled = tmp_path / "target" / "compiled" / "x.sql"
    compiled.parent.mkdir()
    compiled.write_text("select 1")
    age(compiled, +300)
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)

    assert not dbt_assets._manifest_is_stale(manifest)


def test_a_missing_dbt_project_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)

    with pytest.raises(ConfigurationError, match="No dbt project"):
        dbt_assets._prepared_project()


# --- the paths dbt is given ----------------------------------------------------------------


def test_a_relative_raw_zone_is_resolved_where_the_loaders_resolve_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dbt starts in dbt/, so it must be given the folder the loaders (which start here) use."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAW_ZONE_DIR", "data/raw")
    monkeypatch.setattr(dbt_assets, "DATA_DIR", tmp_path / "db")
    get_settings.cache_clear()

    assert dbt_assets.dbt_environment()["RAW_ZONE_DIR"] == str(tmp_path / "data" / "raw")


def test_the_default_raw_zone_is_also_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dbt_assets, "DATA_DIR", tmp_path / "db")
    get_settings.cache_clear()

    assert dbt_assets.dbt_environment()["RAW_ZONE_DIR"] == str(tmp_path / ".data" / "raw")


def test_the_database_folder_is_absolute_and_created_because_duckdb_will_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "a" / "b"
    monkeypatch.setattr(dbt_assets, "DATA_DIR", folder)

    assert dbt_assets.dbt_environment()["DBT_DATA_DIR"] == str(folder)
    assert folder.is_dir()


def test_the_database_folder_sits_at_the_repository_root_beside_dbt() -> None:
    assert dbt_assets.DBT_DIR.parent / ".data" == dbt_assets.DATA_DIR
