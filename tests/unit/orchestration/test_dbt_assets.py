"""FR-ORC-01: the dbt project appears in Dagster as assets and checks, joined to the raw assets."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import dagster as dg
import pytest

from lineageheal.core.errors import ConfigurationError, StorageError
from lineageheal.core.settings import get_settings
from lineageheal.core.signals import SignalKind
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.orchestration import dbt_assets
from lineageheal.orchestration.assets import raw_asset_key, raw_assets
from lineageheal.orchestration.dbt_assets import (
    ALL_RAW_ASSETS,
    RAW_ZONE_FILES,
    dbt_project,
    lineageheal_dbt_assets,
)
from lineageheal.orchestration.definitions import defs
from lineageheal.orchestration.run_artifacts import load_run_summary

MANIFEST = json.loads(dbt_project.manifest_path.read_text())
MODELS = {n["name"]: n for n in MANIFEST["nodes"].values() if n["resource_type"] == "model"}
SEEDS = {n["name"]: n for n in MANIFEST["nodes"].values() if n["resource_type"] == "seed"}


@pytest.fixture(scope="module")
def graph() -> dg.AssetGraph:  # type: ignore[type-arg]
    return defs.resolve_asset_graph()


def model_key(name: str) -> dg.AssetKey:
    return dg.AssetKey([MODELS[name]["schema"], name])


def seed_key(name: str) -> dg.AssetKey:
    return dg.AssetKey([SEEDS[name]["schema"], name])


# --- the graph ---------------------------------------------------------------------------


def test_every_dbt_model_and_seed_is_an_asset_beside_the_raw_assets(
    graph: dg.AssetGraph,  # type: ignore[type-arg]
) -> None:
    assert set(graph.get_all_asset_keys()) == (
        {model_key(n) for n in MODELS} | {seed_key(n) for n in SEEDS} | set(ALL_RAW_ASSETS)
    )
    assert len(MODELS) >= 12
    assert "seed_calendar_nse" in SEEDS


def test_the_calendar_seed_is_a_bronze_asset_that_reads_no_raw_file(
    graph: dg.AssetGraph,  # type: ignore[type-arg]
) -> None:
    """It is reference data kept in the repository, not something a loader downloads."""
    node = graph.get(seed_key("seed_calendar_nse"))

    assert node.group_name == "bronze"
    assert set(node.parent_keys) == set()


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


# --- parsing on demand ---------------------------------------------------------------------


class FakePreparer:
    def __init__(self) -> None:
        self.prepared = 0

    def prepare(self, project: object) -> None:
        self.prepared += 1


class FakeProject:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.preparer = FakePreparer()


@pytest.mark.parametrize(("manifest_age", "expected_parses"), [(None, 1), (+100, 0), (-100, 1)])
def test_the_project_is_parsed_only_when_the_manifest_is_missing_or_older_than_an_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_age: float | None,
    expected_parses: int,
) -> None:
    manifest = make_project(tmp_path)
    if manifest_age is not None:
        manifest.write_text("{}")
        age(manifest, manifest_age)
    fake = FakeProject(manifest)
    monkeypatch.setattr(dbt_assets, "DBT_DIR", tmp_path)
    monkeypatch.setattr(dbt_assets, "DbtProject", lambda **kwargs: fake)

    assert dbt_assets._prepared_project() is fake
    assert fake.preparer.prepared == expected_parses


# --- keeping a run's artifacts -------------------------------------------------------------


class FakeLog:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))


class FakeInstance:
    def __init__(self) -> None:
        self.tags: dict[str, dict[str, str]] = {}

    def add_run_tags(self, run_id: str, tags: dict[str, str]) -> None:
        self.tags[run_id] = tags


class FakeContext:
    run_id = "5b1f2c3e-1111-4222-8333-444455556666"

    def __init__(self) -> None:
        self.log = FakeLog()
        self.instance = FakeInstance()


class FakeInvocation:
    def __init__(self, target_path: Path, error: Exception | None = None) -> None:
        self.target_path = target_path
        self._error = error

    def get_error(self) -> Exception | None:
        return self._error


@pytest.fixture
def zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalObjectStore:
    store = LocalObjectStore(tmp_path / "zone")
    monkeypatch.setattr(dbt_assets, "open_store", lambda settings: store)
    return store


def keep(context: FakeContext, invocation: FakeInvocation, *, raise_errors: bool) -> None:
    dbt_assets._keep_run_artifacts(context, invocation, raise_errors=raise_errors)  # type: ignore[arg-type]


def test_a_run_with_no_results_is_recorded_with_dbts_own_error_and_tagged(
    tmp_path: Path, zone: LocalObjectStore
) -> None:
    context = FakeContext()
    invocation = FakeInvocation(
        tmp_path / "nothing", error=RuntimeError("Cannot open the database")
    )

    keep(context, invocation, raise_errors=False)

    summary = load_run_summary(zone, context.run_id)
    assert [s.kind for s in summary.signals] == [SignalKind.RUN_ERROR]
    assert "Cannot open the database" in summary.signals[0].message
    assert context.instance.tags[context.run_id] == {
        "lineageheal/signals": "1",
        "lineageheal/has_failures": "true",
    }
    assert [level for level, _ in context.log.messages] == ["info"]


def test_a_problem_keeping_artifacts_does_not_hide_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: LocalObjectStore
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise StorageError("the bucket is unreachable")

    monkeypatch.setattr(dbt_assets, "record_dbt_run", refuse)
    context = FakeContext()

    keep(context, FakeInvocation(tmp_path), raise_errors=False)  # the run already failed

    assert [level for level, _ in context.log.messages] == ["error"]
    assert "the bucket is unreachable" in context.log.messages[0][1]
    assert context.instance.tags == {}


def test_a_problem_keeping_artifacts_after_a_successful_run_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zone: LocalObjectStore
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise StorageError("the bucket is unreachable")

    monkeypatch.setattr(dbt_assets, "record_dbt_run", refuse)

    with pytest.raises(StorageError, match="unreachable"):
        keep(FakeContext(), FakeInvocation(tmp_path), raise_errors=True)
