"""The Dagster entry point: `dagster dev -m lineageheal.orchestration.definitions`."""

import dagster as dg

from lineageheal.orchestration.assets import raw_assets
from lineageheal.orchestration.resources import IngestionResource

defs = dg.Definitions(assets=raw_assets, resources={"ingestion": IngestionResource()})
