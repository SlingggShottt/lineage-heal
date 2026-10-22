"""The dbt project as Dagster assets: every model is an asset and every dbt test a check.

Dagster reads the dbt manifest, so its asset graph is dbt's graph, joined at the top to the
raw assets: each dbt source already has the key of a raw asset (see assets.py), so bronze
models depend on the raw files they read without any mapping. One run of the asset group is
one `dbt build`, which builds a model, tests it, and skips what is downstream of a failed test.
"""

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

import dagster as dg
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import get_settings
from lineageheal.ingestion.runner import DATASETS
from lineageheal.orchestration.assets import raw_asset_key

# The repository root holds src/ and dbt/ side by side, in a checkout and in the container.
DBT_DIR = Path(__file__).resolve().parents[3] / "dbt"

# Where the dev and bench DuckDB files live (DBT_DATA_DIR in dbt/profiles.yml).
DATA_DIR = DBT_DIR.parent / ".data"

# TODO(LH-033): the scheduled pipeline builds against MotherDuck (prod); dev is the local default.
DBT_TARGET = "dev"

# Directories dbt writes into; they change on every run and say nothing about the project.
_GENERATED = {"target", "logs", "dbt_packages"}

# `brz_meta__raw_file_versions` reads the modification time of every raw file through this
# one dbt source, so it stands for all the raw datasets rather than for one.
RAW_ZONE_FILES = dg.AssetKey(["raw_zone", "files"])
ALL_RAW_ASSETS = [
    raw_asset_key(source, dataset) for source, datasets in DATASETS.items() for dataset in datasets
]


class LineageHealDbtTranslator(DagsterDbtTranslator):
    """Groups models by lakehouse layer and ties the all-files source to the raw assets."""

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        """Group a model by its schema, which is its layer: bronze, silver or gold."""
        schema = dbt_resource_props.get("schema")
        return str(schema) if schema else super().get_group_name(dbt_resource_props)

    def get_asset_spec(
        self, manifest: Mapping[str, Any], unique_id: str, project: DbtProject | None
    ) -> dg.AssetSpec:
        """Replace a dependency on the all-files source by one on each raw asset."""
        spec = super().get_asset_spec(manifest, unique_id, project)
        if not any(dep.asset_key == RAW_ZONE_FILES for dep in spec.deps):
            return spec
        deps = [dep for dep in spec.deps if dep.asset_key != RAW_ZONE_FILES]
        deps += [dg.AssetDep(key) for key in ALL_RAW_ASSETS]
        return spec.replace_attributes(deps=deps)


def dbt_environment() -> dict[str, str]:
    """The paths dbt reads, made absolute for a dbt that starts in a different directory.

    dagster-dbt always starts dbt inside the dbt/ folder, while the settings and the profile
    write their defaults (`.data/raw`, `.data/dev.duckdb`) relative to the repository root, where
    the loaders and the command line run. Left alone, dbt would look for the raw files, and
    create its database, under dbt/.data. The raw zone is resolved the way the loaders resolve
    it (from the settings), so both sides always mean the same folder. The database folder is
    created if missing, because DuckDB will not create the folder it writes into.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "RAW_ZONE_DIR": str(get_settings().raw_zone_dir.resolve()),
        "DBT_DATA_DIR": str(DATA_DIR),
    }


def _newest_project_file_time() -> float:
    """The latest modification time of any model, test, macro or configuration file."""
    return max(
        path.stat().st_mtime
        for pattern in ("*.sql", "*.yml", "*.yaml")
        for path in DBT_DIR.rglob(pattern)
        if not _GENERATED.intersection(path.relative_to(DBT_DIR).parts)
    )


def _manifest_is_stale(manifest_path: Path) -> bool:
    return not manifest_path.exists() or manifest_path.stat().st_mtime < _newest_project_file_time()


def _prepared_project() -> DbtProject:
    """Return the dbt project with an up-to-date manifest, parsing it first if needed.

    Dagster builds the asset graph from `manifest.json`, which dbt writes. A fresh checkout, a
    container and CI have none, and after an edit to a model it is out of date, so the project
    is parsed whenever the manifest is missing or older than any project file. Parsing reads
    the files only; it does not touch the warehouse.

    Raises:
        ConfigurationError: There is no dbt project beside the source code.
    """
    if not (DBT_DIR / "dbt_project.yml").exists():
        raise ConfigurationError(f"No dbt project at {DBT_DIR}; run from a source checkout.")
    project = DbtProject(project_dir=DBT_DIR, target=DBT_TARGET)
    if _manifest_is_stale(project.manifest_path):
        project.preparer.prepare(project)
    return project


dbt_project = _prepared_project()
dbt_resource = DbtCliResource(project_dir=dbt_project)


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=LineageHealDbtTranslator(),
    # DuckDB allows one writer at a time, so dbt runs share a pool limited to one step
    # (dagster_home/dagster.yaml), the same mechanism that keeps ingestion polite.
    pool="warehouse",
)
def lineageheal_dbt_assets(context: dg.AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    """Build every dbt model and run its tests. A failed test fails the run."""
    # dagster-dbt hands dbt a copy of this process's environment when it starts it, so the
    # paths are set for that moment only and the process's own environment is left as it was.
    with mock.patch.dict(os.environ, dbt_environment()):
        invocation = dbt.cli(["build"], context=context)
    yield from invocation.stream()
