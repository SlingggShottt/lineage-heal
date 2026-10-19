"""AMFI mutual fund NAV loader.

Downloads AMFI's NAV history report for one date and stores it unchanged in the raw
zone. The report, not the live NAVAll.txt snapshot, is used because it can be fetched
for any past date and holds exactly one NAV date (decision D-19, source details in
docs/design_architecture.md).
"""

from datetime import date

import structlog

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

log = structlog.get_logger(__name__)

AMFI_REPORT_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"

# For a date with no NAVs AMFI answers HTTP 200 with an HTML page carrying this message.
_NO_DATA_MARKER = b"No data found on the basis of selected parameters"

# Fixed English month names: strftime("%b") would follow the machine's locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_amfi_date(value: date) -> str:
    """Format a date the way AMFI expects it in queries, for example "05-Oct-2026"."""
    return f"{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year}"


def report_filename(trade_date: date) -> str:
    """Return AMFI's file name for a date's report, such as `NAVHistoryReport151026.txt`."""
    return f"NAVHistoryReport{trade_date:%d%m%y}.txt"


class AmfiNavLoader:
    """Loads the AMFI NAV report for a trade date into the raw zone."""

    source = "amfi"
    dataset = "navall"

    def __init__(self, fetcher: PoliteFetcher, zone: RawZone) -> None:
        """Create a loader that downloads with `fetcher` and stores into `zone`."""
        self._fetcher = fetcher
        self._zone = zone

    def load(self, trade_date: date) -> IngestionResult:
        """Download and store the NAV report for one date.

        Args:
            trade_date: The NAV date in Asia/Kolkata.

        Returns:
            The result: stored, unchanged (an identical file was already stored), or
            no data (AMFI published nothing for the date, which is not an error).

        Raises:
            SourceUnavailableError: AMFI could not be reached or answered with an
                error status.
            IngestionError: AMFI answered with a page that is neither a NAV report
                nor its "no data" page.
        """
        stamp = format_amfi_date(trade_date)
        response = self._fetcher.get(AMFI_REPORT_URL, params={"frmdt": stamp, "todt": stamp})
        if response.status_code != 200:
            raise SourceUnavailableError(
                f"AMFI returned HTTP {response.status_code} for {trade_date.isoformat()}"
            )

        body = response.content
        if not body.strip():
            return self._no_data(trade_date, response.status_code)
        if "html" in response.headers.get("content-type", "").lower() or body.lstrip().startswith(
            b"<"
        ):
            if _NO_DATA_MARKER in body:
                return self._no_data(trade_date, response.status_code)
            raise IngestionError(
                f"AMFI returned an unexpected HTML page for {trade_date.isoformat()}"
            )

        stored = self._zone.put_if_absent(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            filename=report_filename(trade_date),
            content=body,
        )
        outcome = IngestionOutcome.STORED if stored.created else IngestionOutcome.UNCHANGED
        return IngestionResult(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            outcome=outcome,
            http_status=response.status_code,
            stored=stored,
        )

    def _no_data(self, trade_date: date, http_status: int) -> IngestionResult:
        log.info("no_data_published", source=self.source, trade_date=trade_date.isoformat())
        return IngestionResult(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            outcome=IngestionOutcome.NO_DATA,
            http_status=http_status,
            stored=None,
        )
