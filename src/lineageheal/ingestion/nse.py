"""NSE CM-UDiFF bhavcopy loader.

Downloads NSE's end-of-day equity file for one trade date and stores the zip exactly as
published. Typing, renaming and schema checks happen later in dbt (decision D-02), so
this loader only checks that what it received is an intact zip. Source details are in
docs/design_architecture.md ("NSE findings").
"""

import zipfile
from datetime import date
from io import BytesIO

import structlog

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

log = structlog.get_logger(__name__)

BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/cm/{filename}"

# NSE's edge resets connections from clients that do not look like a browser, including
# one that identifies itself honestly, so NSE requests send ordinary browser headers.
# Requests stay as polite as for any other source: spaced out and never in a tight loop.
NSE_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def bhavcopy_filename(trade_date: date) -> str:
    """Return NSE's UDiFF file name for a trade date, for example `..._20261016_F_0000.csv.zip`."""
    return f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"


def _check_zip(content: bytes, trade_date: date) -> None:
    """Raise `IngestionError` unless `content` is a complete, uncorrupted zip."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            damaged = archive.testzip()
            empty = not archive.namelist()
    except zipfile.BadZipFile as exc:
        raise IngestionError(
            f"NSE returned something that is not a valid zip for {trade_date.isoformat()}"
        ) from exc
    if damaged is not None or empty:
        raise IngestionError(f"NSE returned a damaged or empty zip for {trade_date.isoformat()}")


class NseBhavcopyLoader:
    """Loads the NSE CM-UDiFF bhavcopy for a trade date into the raw zone."""

    source = "nse"
    dataset = "cm_bhavcopy"

    def __init__(
        self, fetcher: PoliteFetcher, zone: RawZone, stager: ZipStager | None = None
    ) -> None:
        """Create a loader that downloads with `fetcher` and stores into `zone`.

        Args:
            fetcher: The HTTP client to download with.
            zone: Where the zip is stored, exactly as published.
            stager: If given, also unzips each stored file into the staged area that
                dbt reads. Staging is repeated for an unchanged file, so re-running an
                ingest repairs a missing staged copy.
        """
        self._fetcher = fetcher
        self._zone = zone
        self._stager = stager

    def load(self, trade_date: date) -> IngestionResult:
        """Download and store the bhavcopy for one trade date.

        A 404 is reported as `no_data`. It is the normal answer for weekends and market
        holidays, but also what NSE returns before a file is published or when one is
        genuinely missing; telling those apart needs the exchange calendar.

        Args:
            trade_date: The trade date in Asia/Kolkata.

        Returns:
            The result: stored, unchanged (an identical file was already stored) or
            no data. When a stager is configured, `staged_key` names the unzipped copy.

        Raises:
            SourceUnavailableError: NSE could not be reached or answered with a status
                other than 200 or 404.
            IngestionError: NSE answered 200 with something that is not an intact zip.
        """
        filename = bhavcopy_filename(trade_date)
        response = self._fetcher.get(BHAVCOPY_URL.format(filename=filename))

        if response.status_code == 404:
            log.info("no_file_published", source=self.source, trade_date=trade_date.isoformat())
            return IngestionResult(
                source=self.source,
                dataset=self.dataset,
                trade_date=trade_date,
                outcome=IngestionOutcome.NO_DATA,
                http_status=404,
                stored=None,
            )
        if response.status_code != 200:
            raise SourceUnavailableError(
                f"NSE returned HTTP {response.status_code} for {trade_date.isoformat()}"
            )

        content = response.content
        _check_zip(content, trade_date)
        stored = self._zone.put_if_absent(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            filename=filename,
            content=content,
        )
        outcome = IngestionOutcome.STORED if stored.created else IngestionOutcome.UNCHANGED
        staged_key = self._stager.stage(stored.key) if self._stager else None
        return IngestionResult(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            outcome=outcome,
            http_status=response.status_code,
            stored=stored,
            staged_key=staged_key,
        )
