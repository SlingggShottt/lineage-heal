"""Building a feed's loader and running one logged ingestion.

Shared by the command line and the Dagster assets, so both download, store and log a trade
date in exactly the same way (same headers, same raw zone, same ingestion-log entry).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from lineageheal.core.errors import ConfigurationError, LineageHealError
from lineageheal.core.settings import Settings
from lineageheal.ingestion.amfi import AmfiNavLoader
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.log import (
    AttemptRecorder,
    attempt_from_failure,
    attempt_from_result,
)
from lineageheal.ingestion.nse import (
    NSE_BROWSER_HEADERS,
    NseBhavcopyLoader,
    NseCorporateActionsLoader,
    NseSecurityMasterLoader,
)
from lineageheal.ingestion.raw_zone import RawZone, open_store
from lineageheal.ingestion.staging import ZipStager
from lineageheal.ingestion.types import IngestionResult, Loader

# FR-ING-08: the earliest trade date the project ingests (NSE's UDiFF format begins in 2024).
EARLIEST_BACKFILL = date(2024, 1, 1)

# The datasets each source offers; the first is the default.
DATASETS: dict[str, tuple[str, ...]] = {
    "amfi": (AmfiNavLoader.dataset,),
    "nse": (
        NseBhavcopyLoader.dataset,
        NseSecurityMasterLoader.dataset,
        NseCorporateActionsLoader.dataset,
    ),
}


@contextmanager
def open_loader(settings: Settings, source: str, dataset: str) -> Iterator[Loader]:
    """Build the loader for a source's dataset on the configured raw zone, closing it afterwards.

    Raises:
        ConfigurationError: There is no loader for this source and dataset.
    """
    store = open_store(settings)
    zone = RawZone(store)
    headers = NSE_BROWSER_HEADERS if source == "nse" else None
    with PoliteFetcher.create(
        min_interval_seconds=settings.ingest_min_interval_seconds, headers=headers
    ) as fetcher:
        match (source, dataset):
            case ("amfi", AmfiNavLoader.dataset):
                yield AmfiNavLoader(fetcher, zone)
            case ("nse", NseBhavcopyLoader.dataset):
                yield NseBhavcopyLoader(fetcher, zone, ZipStager(store))
            case ("nse", NseSecurityMasterLoader.dataset):
                yield NseSecurityMasterLoader(fetcher, zone)
            case ("nse", NseCorporateActionsLoader.dataset):
                yield NseCorporateActionsLoader(fetcher, zone)
            case _:
                raise ConfigurationError(f"No loader for {source}/{dataset}")


def load_and_record(loader: Loader, recorder: AttemptRecorder, day: date) -> IngestionResult:
    """Run one ingestion and log the attempt, whether it succeeds or fails.

    Raises:
        LineageHealError: The loader failed. The failure is logged first, then re-raised.
    """
    started_at = datetime.now(UTC)
    try:
        result = loader.load(day)
    except LineageHealError as exc:
        recorder.record(
            attempt_from_failure(
                source=loader.source,
                dataset=loader.dataset,
                trade_date=day,
                error=exc,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        )
        raise
    recorder.record(
        attempt_from_result(result, started_at=started_at, finished_at=datetime.now(UTC))
    )
    return result
