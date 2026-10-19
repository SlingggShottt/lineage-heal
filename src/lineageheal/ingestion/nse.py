"""NSE loaders: the CM-UDiFF bhavcopy, the security master and corporate actions.

Each loader downloads one trade date's data and stores it exactly as published. Typing,
renaming and schema checks happen later in dbt (decision D-02), so a loader only checks
that what it received is intact: a complete zip, a complete gzip or valid JSON. Source
details are in docs/design_architecture.md ("NSE findings").
"""

import gzip
import json
import zipfile
import zlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import date
from io import BytesIO

import structlog

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

log = structlog.get_logger(__name__)

ARCHIVE_URL = "https://nsearchives.nseindia.com/content/cm/{filename}"
CORPORATE_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions"
BHAVCOPY_URL = ARCHIVE_URL  # kept under its old name for callers and tests

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

_GZIP_CHUNK = 1024 * 1024


def bhavcopy_filename(trade_date: date) -> str:
    """Return NSE's UDiFF file name for a trade date, for example `..._20261016_F_0000.csv.zip`."""
    return f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv.zip"


def security_master_filename(trade_date: date) -> str:
    """Return NSE's security master file name, for example `NSE_CM_security_16102026.csv.gz`."""
    return f"NSE_CM_security_{trade_date:%d%m%Y}.csv.gz"


def corporate_actions_filename(trade_date: date) -> str:
    """Return the file name used to store a day's corporate actions response."""
    return f"corporate_actions_{trade_date:%Y%m%d}.json"


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


def _check_gzip(content: bytes, trade_date: date) -> None:
    """Raise `IngestionError` unless `content` is a complete gzip with something inside.

    The whole stream is read, in chunks, because gzip only verifies its checksum at the end.
    """
    size = 0
    try:
        with gzip.GzipFile(fileobj=BytesIO(content)) as stream:
            while chunk := stream.read(_GZIP_CHUNK):
                size += len(chunk)
    except (OSError, EOFError, zlib.error) as exc:
        raise IngestionError(
            f"NSE returned something that is not a complete gzip for {trade_date.isoformat()}"
        ) from exc
    if size == 0:
        raise IngestionError(f"NSE returned an empty gzip for {trade_date.isoformat()}")


def _parse_json_list(content: bytes, trade_date: date) -> list[object]:
    """Return the JSON array in `content`, or raise `IngestionError` if it is not one."""
    try:
        parsed = json.loads(content)
    except ValueError as exc:  # includes JSONDecodeError and bad UTF-8
        raise IngestionError(
            f"NSE returned something that is not JSON for {trade_date.isoformat()}"
        ) from exc
    if not isinstance(parsed, list):
        raise IngestionError(f"NSE returned JSON that is not a list for {trade_date.isoformat()}")
    return parsed


class _NseFileLoader(ABC):
    """The flow shared by NSE datasets that publish one file per trade date.

    A 404 is reported as `no_data`. It is the normal answer for weekends and market
    holidays, but also what NSE returns before a file is published or when one is
    genuinely missing; telling those apart needs the exchange calendar.
    """

    source = "nse"
    dataset: str

    def __init__(
        self, fetcher: PoliteFetcher, zone: RawZone, stager: ZipStager | None = None
    ) -> None:
        """Create a loader that downloads with `fetcher` and stores into `zone`.

        Args:
            fetcher: The HTTP client to download with.
            zone: Where the file is stored, exactly as published.
            stager: If given, also unzips each stored file into the staged area that
                dbt reads. Staging is repeated for an unchanged file, so re-running an
                ingest repairs a missing staged copy.
        """
        self._fetcher = fetcher
        self._zone = zone
        self._stager = stager

    @abstractmethod
    def _url(self, trade_date: date) -> str:
        """Return the URL to download for a trade date."""

    def _params(self, trade_date: date) -> Mapping[str, str] | None:
        return None

    @abstractmethod
    def _filename(self, trade_date: date) -> str:
        """Return the file name to store the download under."""

    @abstractmethod
    def _check(self, content: bytes, trade_date: date) -> None:
        """Raise `IngestionError` if the downloaded content is damaged."""

    def _is_empty(self, content: bytes, trade_date: date) -> bool:
        """Whether a valid response means the source has nothing for the date."""
        return False

    def load(self, trade_date: date) -> IngestionResult:
        """Download and store one trade date's file.

        Args:
            trade_date: The trade date in Asia/Kolkata.

        Returns:
            The result: stored, unchanged (an identical file was already stored) or
            no data. When a stager is configured, `staged_key` names the unzipped copy.

        Raises:
            SourceUnavailableError: NSE could not be reached or answered with a status
                other than 200 or 404.
            IngestionError: NSE answered 200 with damaged or unexpected content.
        """
        response = self._fetcher.get(self._url(trade_date), self._params(trade_date))

        if response.status_code == 404:
            log.info(
                "no_file_published",
                source=self.source,
                dataset=self.dataset,
                trade_date=trade_date.isoformat(),
            )
            return self._no_data(trade_date, 404)
        if response.status_code != 200:
            raise SourceUnavailableError(
                f"NSE returned HTTP {response.status_code} for {self.dataset} "
                f"on {trade_date.isoformat()}"
            )

        content = response.content
        self._check(content, trade_date)
        if self._is_empty(content, trade_date):
            log.info(
                "nothing_published",
                source=self.source,
                dataset=self.dataset,
                trade_date=trade_date.isoformat(),
            )
            return self._no_data(trade_date, response.status_code)

        stored = self._zone.put_if_absent(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            filename=self._filename(trade_date),
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

    def _no_data(self, trade_date: date, http_status: int) -> IngestionResult:
        return IngestionResult(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            outcome=IngestionOutcome.NO_DATA,
            http_status=http_status,
            stored=None,
        )


class NseBhavcopyLoader(_NseFileLoader):
    """Loads the NSE CM-UDiFF bhavcopy zip for a trade date into the raw zone."""

    dataset = "cm_bhavcopy"

    def _url(self, trade_date: date) -> str:
        return ARCHIVE_URL.format(filename=bhavcopy_filename(trade_date))

    def _filename(self, trade_date: date) -> str:
        return bhavcopy_filename(trade_date)

    def _check(self, content: bytes, trade_date: date) -> None:
        _check_zip(content, trade_date)


class NseSecurityMasterLoader(_NseFileLoader):
    """Loads NSE's security master (a gzipped CSV of every listed instrument) for a date.

    NSE serves no file for older dates, so a 404 here can mean the date is before the
    master's history begins as well as a holiday. DuckDB reads gzip natively, so no
    staging is needed.
    """

    dataset = "security_master"

    def _url(self, trade_date: date) -> str:
        return ARCHIVE_URL.format(filename=security_master_filename(trade_date))

    def _filename(self, trade_date: date) -> str:
        return security_master_filename(trade_date)

    def _check(self, content: bytes, trade_date: date) -> None:
        _check_gzip(content, trade_date)


class NseCorporateActionsLoader(_NseFileLoader):
    """Loads the corporate actions whose ex-date is the given date, as NSE's JSON response.

    The API filters by ex-date, so one day's request returns that day's splits, bonuses,
    dividends and so on, and `[]` when there are none. An empty list is "nothing to
    store", reported as `no_data`.
    """

    dataset = "corporate_actions"

    def _url(self, trade_date: date) -> str:
        return CORPORATE_ACTIONS_URL

    def _params(self, trade_date: date) -> Mapping[str, str]:
        day = f"{trade_date:%d-%m-%Y}"
        return {"index": "equities", "from_date": day, "to_date": day}

    def _filename(self, trade_date: date) -> str:
        return corporate_actions_filename(trade_date)

    def _check(self, content: bytes, trade_date: date) -> None:
        _parse_json_list(content, trade_date)

    def _is_empty(self, content: bytes, trade_date: date) -> bool:
        return _parse_json_list(content, trade_date) == []
