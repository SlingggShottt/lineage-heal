"""FR-ORC-01: materialising the dbt assets in Dagster builds the models and runs their checks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import dagster as dg
import duckdb
import pytest

from lineageheal.benchmark.faults import blank_nav
from lineageheal.core.settings import get_settings
from lineageheal.core.signals import SignalKind
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.orchestration import dbt_assets
from lineageheal.orchestration.dbt_assets import dbt_resource, lineageheal_dbt_assets
from lineageheal.orchestration.run_artifacts import load_run_summary, run_prefix

if TYPE_CHECKING:
    from conftest import Workspace

REPO_ROOT = Path(__file__).parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def store_fixtures(workspace: Workspace, *, damage_amfi: bool = False) -> None:
    amfi = (FIXTURES / "amfi" / "navall_history_2026-10-15_sample.txt").read_bytes()
    workspace.store_amfi(blank_nav(amfi) if damage_amfi else amfi)
    workspace.store_nse((FIXTURES / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes())
    workspace.store_security_master((FIXTURES / "nse" / "security_master_sample.csv").read_bytes())
    workspace.store_corporate_actions(
        (FIXTURES / "nse" / "corporate_actions_sample.json").read_bytes()
    )


@pytest.fixture(autouse=True)
def _dev_target_in_a_temporary_folder_with_a_relative_raw_zone(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the project's own resource (the dev target) the way `make dagster` does.

    The database goes to a temporary folder instead of .data, and the raw zone is given as a
    path relative to the repository root, which is what a default .env contains. dbt starts in
    dbt/, so it only finds the files if the asset passes it absolute paths.
    """
    monkeypatch.setattr(dbt_assets, "DATA_DIR", tmp_path / "data")
    monkeypatch.setenv("RAW_ZONE_DIR", os.path.relpath(workspace.raw_dir, REPO_ROOT))
    get_settings.cache_clear()


@pytest.fixture
def instance() -> Iterator[dg.DagsterInstance]:
    with dg.DagsterInstance.ephemeral() as instance:
        yield instance


def materialize(instance: dg.DagsterInstance, *, raise_on_error: bool) -> dg.ExecuteInProcessResult:
    return dg.materialize(
        [lineageheal_dbt_assets],
        resources={"dbt": dbt_resource},
        instance=instance,
        raise_on_error=raise_on_error,
    )


def built(result: dg.ExecuteInProcessResult) -> set[str]:
    return {
        event.step_materialization_data.materialization.asset_key.to_user_string()
        for event in result.get_asset_materialization_events()
    }


def test_a_healthy_run_builds_every_model_and_every_check_passes(
    workspace: Workspace, tmp_path: Path, instance: dg.DagsterInstance
) -> None:
    store_fixtures(workspace)

    result = materialize(instance, raise_on_error=True)

    assert result.success
    # The tables are in the database dbt was pointed at, and nothing was written under dbt/.
    connection = duckdb.connect(str(tmp_path / "data" / "dev.duckdb"), read_only=True)
    try:
        assert connection.sql("select count(*) from silver.slv_nse__equity_eod").fetchall() == [
            (7,)
        ]
    finally:
        connection.close()
    assert not (REPO_ROOT / "dbt" / ".data").exists()
    # The paths were given to dbt alone: this process's environment is as it was.
    assert os.environ["RAW_ZONE_DIR"] == os.path.relpath(workspace.raw_dir, REPO_ROOT)
    assert "DBT_DATA_DIR" not in os.environ
    # The run's artifacts were kept, and it raised no signals.
    store = LocalObjectStore(workspace.raw_dir)
    prefix = run_prefix(result.run_id)
    assert store.exists(f"{prefix}/manifest.json")
    assert store.exists(f"{prefix}/run_results.json")
    summary = load_run_summary(store, result.run_id)
    assert summary.signals == ()
    assert summary.counts["success"] == len(lineageheal_dbt_assets.keys)
    assert run_tags(instance, result)["lineageheal/signals"] == "0"
    assert run_tags(instance, result)["lineageheal/has_failures"] == "false"
    assert built(result) == {key.to_user_string() for key in lineageheal_dbt_assets.keys}
    evaluations = result.get_asset_check_evaluations()
    assert len(evaluations) > 100
    assert [e.check_name for e in evaluations if not e.passed] == []


def test_a_damaged_report_fails_the_run_and_keeps_its_data_out_of_the_models_after_it(
    workspace: Workspace, instance: dg.DagsterInstance
) -> None:
    store_fixtures(workspace, damage_amfi=True)

    result = materialize(instance, raise_on_error=False)

    assert not result.success
    failed = {e.check_name for e in result.get_asset_check_evaluations() if not e.passed}
    assert failed == {"not_null_brz_amfi__navall_nav_inr"}
    models = built(result)
    # The AMFI side stops at the failed check; the NSE side, which does not read it, still builds.
    assert "silver/slv_amfi__nav" not in models
    assert "gold/gld_mf_nav_daily" not in models
    assert {"silver/slv_nse__equity_eod", "gold/gld_equity_daily_prices"} <= models
    # The failed run's artifacts were kept, and the signal names what failed.
    summary = load_run_summary(LocalObjectStore(workspace.raw_dir), result.run_id)
    (signal,) = summary.signals
    assert signal.kind is SignalKind.TEST_FAILED
    assert (signal.model, signal.column, signal.check_type) == (
        "brz_amfi__navall",
        "nav_inr",
        "not_null",
    )
    assert signal.run_id == result.run_id
    assert "slv_amfi__nav" in summary.skipped_models
    assert run_tags(instance, result)["lineageheal/signals"] == "1"
    assert run_tags(instance, result)["lineageheal/has_failures"] == "true"


# --- the run's artifacts and signals -------------------------------------------------------


def run_tags(instance: dg.DagsterInstance, result: dg.ExecuteInProcessResult) -> dict[str, str]:
    run = instance.get_run_by_id(result.run_id)
    assert run is not None
    return dict(run.tags)


def test_a_run_where_dbt_cannot_start_still_records_why(
    workspace: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    instance: dg.DagsterInstance,
) -> None:
    """No run_results.json exists then, but the Detector must still hear that the run failed."""
    store_fixtures(workspace)
    broken = tmp_path / "broken"
    (broken / "dev.duckdb").mkdir(parents=True)  # a folder where the database file should be
    monkeypatch.setattr(dbt_assets, "DATA_DIR", broken)

    result = materialize(instance, raise_on_error=False)

    assert not result.success
    summary = load_run_summary(LocalObjectStore(workspace.raw_dir), result.run_id)
    (signal,) = summary.signals
    assert signal.kind is SignalKind.RUN_ERROR
    assert summary.counts == {}
    assert signal.message
    assert run_tags(instance, result)["lineageheal/has_failures"] == "true"
