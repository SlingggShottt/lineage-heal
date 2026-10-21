"""The raw assets: one per dataset, each partitioned by trade date.

An asset's key is the name of the dbt source that reads its files (`raw_nse/cm_bhavcopy`
and so on), so the dbt models can later be linked to them without a mapping (LH-031).
Materialising a partition downloads that trade date through the same loader the command
line uses. A date the source published nothing for (a holiday) is a success whose metadata
says `no_data`: it is not an error (CLAUDE.md, Data).
"""

from datetime import date

import dagster as dg

from lineageheal.ingestion.runner import DATASETS
from lineageheal.ingestion.types import IngestionResult
from lineageheal.orchestration.partitions import daily_partitions
from lineageheal.orchestration.resources import IngestionResource

DESCRIPTIONS = {
    ("amfi", "navall"): "AMFI mutual fund NAV report for one date, stored as published.",
    (
        "nse",
        "cm_bhavcopy",
    ): "NSE CM-UDiFF end-of-day prices for one date (a zip), stored as published.",
    (
        "nse",
        "security_master",
    ): "NSE security master (instrument list) for one date, stored as published.",
    (
        "nse",
        "corporate_actions",
    ): "NSE corporate actions with the partition date as ex-date, stored as published.",
}


def raw_asset_key(source: str, dataset: str) -> dg.AssetKey:
    """The asset key for a dataset: the name of the dbt source that reads it."""
    return dg.AssetKey([f"raw_{source}", dataset])


def _metadata(result: IngestionResult) -> dict[str, dg.MetadataValue]:
    metadata: dict[str, dg.MetadataValue] = {
        "outcome": dg.MetadataValue.text(result.outcome.value),
        "http_status": dg.MetadataValue.int(result.http_status),
    }
    if result.stored is not None:
        metadata["object_key"] = dg.MetadataValue.text(result.stored.key)
        metadata["size_bytes"] = dg.MetadataValue.int(result.stored.size_bytes)
        metadata["sha256"] = dg.MetadataValue.text(result.stored.sha256)
    return metadata


def _build_raw_asset(source: str, dataset: str) -> dg.AssetsDefinition:
    @dg.asset(
        key=raw_asset_key(source, dataset),
        description=DESCRIPTIONS[(source, dataset)],
        group_name="raw",
        partitions_def=daily_partitions,
        # Pools are limited to one step at a time in dagster_home/dagster.yaml, so the
        # sources are fetched one request at a time, as politely as from the command line.
        pool="ingestion",
        kinds={"python"},
    )
    def raw_file(
        context: dg.AssetExecutionContext, ingestion: IngestionResource
    ) -> dg.MaterializeResult[None]:
        result = ingestion.run(source, dataset, date.fromisoformat(context.partition_key))
        context.log.info(f"{source}/{dataset} {context.partition_key}: {result.outcome.value}")
        return dg.MaterializeResult(
            metadata=_metadata(result),
            # The file's hash is its version: re-ingesting an identical file leaves
            # everything downstream of it up to date instead of marking it stale.
            data_version=dg.DataVersion(result.stored.sha256) if result.stored else None,
        )

    return raw_file


raw_assets = [
    _build_raw_asset(source, dataset)
    for source, datasets in DATASETS.items()
    for dataset in datasets
]
