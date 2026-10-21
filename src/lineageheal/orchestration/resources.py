"""The resource that lets assets download a feed without knowing how loaders are built."""

from datetime import date

from dagster import ConfigurableResource

from lineageheal.core.settings import get_settings
from lineageheal.ingestion.log import open_recorder
from lineageheal.ingestion.runner import load_and_record, open_loader
from lineageheal.ingestion.types import IngestionResult


class IngestionResource(ConfigurableResource):  # type: ignore[type-arg]
    """Downloads, stores and logs one trade date of one dataset, as the command line does.

    It reads the project settings (raw zone, request spacing, optional ingestion log) at run
    time, so an asset has nothing to configure. Tests replace it with a fake that never
    touches the network.
    """

    def run(self, source: str, dataset: str, trade_date: date) -> IngestionResult:
        """Ingest one trade date and log the attempt, whether it succeeds or fails.

        Raises:
            LineageHealError: The download or the log write failed.
        """
        settings = get_settings()
        recorder = open_recorder(settings)
        try:
            with open_loader(settings, source, dataset) as loader:
                return load_and_record(loader, recorder, trade_date)
        finally:
            recorder.close()
