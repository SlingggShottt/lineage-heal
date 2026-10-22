"""The Dagster entry point: `dagster dev -m lineageheal.orchestration.definitions`."""

import dagster as dg

from lineageheal.orchestration.assets import raw_assets
from lineageheal.orchestration.dbt_assets import dbt_resource, lineageheal_dbt_assets
from lineageheal.orchestration.resources import IngestionResource

defs = dg.Definitions(
    assets=[*raw_assets, lineageheal_dbt_assets],
    resources={"ingestion": IngestionResource(), "dbt": dbt_resource},
)
