"""FBIL reference rate loader.

FBIL publishes no file or API of its own, so the rates come from the RBI Reference Rate
Archive, a form on rbi.org.in that republishes them (decision D-81, source details in
docs/design_architecture.md). Asking for one date costs one POST, after one GET of the form
per run, and the answer is a download whose body is an HTML table. It is stored exactly as
published and staged as CSV for dbt.
"""

from datetime import date
from html.parser import HTMLParser

import httpx
import structlog

from lineageheal.core.errors import IngestionError, SourceBlockedError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import HtmlTableStager, parse_html_table
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult

log = structlog.get_logger(__name__)

ARCHIVE_URL = "https://www.rbi.org.in/scripts/ReferenceRateArchive.aspx"
ARCHIVE_ORIGIN = "https://www.rbi.org.in"

# The currencies the project uses. The form also offers AED, IDR and RUB.
_CURRENCY_BOXES = {"chkUSD": "on", "chkGBP": "on", "chkEURO": "on", "chkYEN": "on"}

# RBI's firewall answers a request it refuses with one of these. A refusal can last, so it is
# reported as a block rather than retried.
_BLOCKED_STATUSES = frozenset({403, 418})

# ASP.NET rejects a post that does not send back the page's own state.
_REQUIRED_STATE = ("__VIEWSTATE", "__EVENTVALIDATION")


def rates_filename(trade_date: date) -> str:
    """Return the file name for a date's rates, such as `reference_rates_2026-11-20.xls`.

    The server names every download `BankWise.xls`, which would not tell two dates apart. The
    `.xls` suffix is kept because the staging step keys on it (the body is an HTML table).
    """
    return f"reference_rates_{trade_date.isoformat()}.xls"


class _FormStateParser(HTMLParser):
    """Collects the hidden `__...` inputs of an ASP.NET page, which a post must send back."""

    def __init__(self) -> None:
        super().__init__()
        self.state: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        fields = dict(attrs)
        name = fields.get("name") or ""
        if fields.get("type") == "hidden" and name.startswith("__"):
            self.state[name] = fields.get("value") or ""


class FbilReferenceRatesLoader:
    """Loads FBIL's reference rates for a trade date into the raw zone."""

    source = "fbil"
    dataset = "reference_rates"

    def __init__(
        self, fetcher: PoliteFetcher, zone: RawZone, stager: HtmlTableStager | None = None
    ) -> None:
        """Create a loader that downloads with `fetcher` and stores into `zone`.

        Args:
            fetcher: The HTTP client to download with. Its cookies must persist between
                requests, because the archive's session starts with the first GET.
            zone: Where the file is stored, exactly as published.
            stager: If given, also writes each stored table as CSV into the staged area that
                dbt reads. Staging is repeated for an unchanged file, so re-running an ingest
                repairs a missing staged copy.
        """
        self._fetcher = fetcher
        self._zone = zone
        self._stager = stager
        # The form's hidden state serves every date of a run, so the form is read once.
        self._state: dict[str, str] | None = None

    def load(self, trade_date: date) -> IngestionResult:
        """Download and store the reference rates for one date.

        Args:
            trade_date: The date in Asia/Kolkata.

        Returns:
            The result: stored, unchanged (an identical file was already stored) or no data
            (the archive holds no rate for the date, as on a bank holiday, which is not an
            error). When a stager is configured, `staged_key` names the CSV copy.

        Raises:
            SourceBlockedError: RBI refused the request. Asking again would only add to it.
            SourceUnavailableError: RBI could not be reached or answered with another status.
            IngestionError: The archive answered with something that is not one whole table
                for one date, or the form no longer holds the state a post needs.
        """
        response = self._download(trade_date)
        content = response.content
        rows = self._check(content, trade_date)

        if len(rows) == 1:  # only the header row: nothing is published for the date
            log.info("no_data_published", source=self.source, trade_date=trade_date.isoformat())
            return IngestionResult(
                source=self.source,
                dataset=self.dataset,
                trade_date=trade_date,
                outcome=IngestionOutcome.NO_DATA,
                http_status=response.status_code,
                stored=None,
            )

        stored = self._zone.put_if_absent(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            filename=rates_filename(trade_date),
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

    def _download(self, trade_date: date) -> httpx.Response:
        stamp = f"{trade_date:%d/%m/%Y}"
        for _ in range(2):
            form = {
                **self._form_state(),
                **_CURRENCY_BOXES,
                "__EVENTTARGET": "lnkDownloadExcel",
                "__EVENTARGUMENT": "",
                "txtFromDate": stamp,
                "txtToDate": stamp,
            }
            response = self._fetcher.post(
                ARCHIVE_URL, form, headers={"Referer": ARCHIVE_URL, "Origin": ARCHIVE_ORIGIN}
            )
            _raise_for_status(response, f"rates for {trade_date.isoformat()}")
            if "attachment" in response.headers.get("content-disposition", "").lower():
                return response
            # The page itself came back, not a download: its state has gone stale. Read the
            # form again, once, before giving up.
            log.warning("archive_state_refreshed", trade_date=trade_date.isoformat())
            self._state = None
        raise IngestionError(
            f"The RBI archive answered with a page, not a download, for {trade_date.isoformat()}"
        )

    def _form_state(self) -> dict[str, str]:
        if self._state is None:
            response = self._fetcher.get(ARCHIVE_URL)
            _raise_for_status(response, "the form")
            parser = _FormStateParser()
            parser.feed(response.text)
            parser.close()
            missing = [name for name in _REQUIRED_STATE if name not in parser.state]
            if missing:
                raise IngestionError(
                    f"The RBI archive page has no {', '.join(missing)}, so it cannot be asked "
                    "for rates"
                )
            self._state = parser.state
        return self._state

    @staticmethod
    def _check(content: bytes, trade_date: date) -> list[list[str]]:
        """Return the table's rows, or raise if it is not one whole table for one date.

        Column names and values are deliberately not checked: a changed format must reach dbt,
        where schema drift is reported with its lineage.
        """
        try:
            rows = parse_html_table(content)
        except IngestionError as exc:
            raise IngestionError(
                f"The RBI archive's download for {trade_date.isoformat()} is not a usable "
                f"table: {exc}"
            ) from exc
        if len(rows) > 2:
            raise IngestionError(
                f"Asked the RBI archive for {trade_date.isoformat()} alone and got "
                f"{len(rows) - 1} rows of rates"
            )
        return rows


def _raise_for_status(response: httpx.Response, what: str) -> None:
    if response.status_code in _BLOCKED_STATUSES:
        raise SourceBlockedError(
            f"The RBI archive refused the request for {what} with HTTP {response.status_code}"
        )
    if response.status_code != 200:
        raise SourceUnavailableError(
            f"The RBI archive returned HTTP {response.status_code} for {what}"
        )
