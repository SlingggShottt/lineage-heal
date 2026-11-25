"""FR-ING-04: FBIL's reference rates are stored unchanged, one date per request (D-81)."""

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from lineageheal.core.errors import IngestionError, SourceBlockedError, SourceUnavailableError
from lineageheal.ingestion.fbil import (
    ARCHIVE_ORIGIN,
    ARCHIVE_URL,
    FbilReferenceRatesLoader,
    rates_filename,
)
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import HtmlTableStager
from lineageheal.ingestion.types import IngestionOutcome

TRADE_DATE = date(2026, 11, 20)
SAMPLE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "fbil"
    / "reference_rates_2026-11-16_2026-11-20_sample.xls"
).read_bytes()
ATTACHMENT = {
    "content-type": "application/vnd.ms-excel",
    "content-disposition": 'attachment;filename="BankWise.xls"',
}


def table(days: int) -> bytes:
    """The real sample cut to its header and the first `days` days of rates (newest first)."""
    end = 0
    for _ in range(days + 1):
        end = SAMPLE.index(b"</tr>", end) + len(b"</tr>")
    return SAMPLE[:end] + b"\r\n</table>"


def form_page(viewstate: str = "vs1") -> httpx.Response:
    html = (
        '<form method="post" action="./ReferenceRateArchive.aspx" id="frm">'
        f'<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="{viewstate}" />'
        '<input type="hidden" name="__VIEWSTATEGENERATOR" value="6A117BB1" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="ev1" />'
        '<input type="hidden" name="notes" value="not page state" />'
        '<input name="txtFromDate" type="text" value="typed" />'
        "</form>"
    )
    return httpx.Response(
        200, content=html.encode(), headers={"set-cookie": "ASP.NET_SessionId=abc; path=/"}
    )


def download(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers=ATTACHMENT)


def plain_page() -> httpx.Response:
    """The form page itself, which is what comes back when the page state has gone stale."""
    return httpx.Response(200, content=b"<html><form></form></html>")


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path)


def make_loader(store: LocalObjectStore, *, staged: bool = True) -> FbilReferenceRatesLoader:
    fetcher = PoliteFetcher(httpx.Client(), min_interval_seconds=0, sleep=lambda _: None)
    return FbilReferenceRatesLoader(
        fetcher, RawZone(store), HtmlTableStager(store) if staged else None
    )


def keys(store: LocalObjectStore, day: date = TRADE_DATE) -> list[str]:
    return RawZone(store).stored_keys(source="fbil", dataset="reference_rates", trade_date=day)


def test_the_loader_names_its_source_and_dataset() -> None:
    assert (FbilReferenceRatesLoader.source, FbilReferenceRatesLoader.dataset) == (
        "fbil",
        "reference_rates",
    )


def test_the_file_name_carries_the_date_and_keeps_the_xls_suffix_staging_needs() -> None:
    assert rates_filename(TRADE_DATE) == "reference_rates_2026-11-20.xls"


@respx.mock
def test_a_date_is_stored_unchanged_and_staged_as_csv(store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))

    result = make_loader(store).load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert result.http_status == 200
    assert result.stored is not None
    assert result.stored.key.startswith(
        "raw/source=fbil/dataset=reference_rates/trade_date=2026-11-20/"
    )
    assert result.stored.key.endswith("__reference_rates_2026-11-20.xls")
    assert store.get(result.stored.key) == table(1)
    assert result.staged_key is not None
    lines = store.get(result.staged_key).decode().splitlines()
    assert len(lines) == 2
    assert lines[1] == "20/11/2026,92.9699,121.9337,102.1504,58.4300"


@respx.mock
def test_the_form_is_posted_with_one_date_the_page_state_and_the_four_currencies(
    store: LocalObjectStore,
) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    post = respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))

    make_loader(store).load(TRADE_DATE)

    request = post.calls.last.request
    form = parse_qs(request.content.decode(), keep_blank_values=True)
    assert form["__VIEWSTATE"] == ["vs1"]
    assert form["__EVENTVALIDATION"] == ["ev1"]
    assert form["__VIEWSTATEGENERATOR"] == ["6A117BB1"]
    assert form["__EVENTTARGET"] == ["lnkDownloadExcel"]
    assert form["__EVENTARGUMENT"] == [""]
    assert form["txtFromDate"] == form["txtToDate"] == ["20/11/2026"]
    assert {name for name in form if name.startswith("chk")} == {
        "chkUSD",
        "chkGBP",
        "chkEURO",
        "chkYEN",
    }
    assert "notes" not in form  # a hidden field that is not page state is not sent back
    assert "btnSubmit" not in form
    assert request.headers["referer"] == ARCHIVE_URL
    assert request.headers["origin"] == ARCHIVE_ORIGIN
    assert request.headers["cookie"] == "ASP.NET_SessionId=abc"


@respx.mock
def test_the_form_is_read_once_however_many_dates_a_run_asks_for(store: LocalObjectStore) -> None:
    get = respx.get(ARCHIVE_URL).mock(return_value=form_page())
    post = respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))
    loader = make_loader(store)

    loader.load(date(2026, 11, 20))
    loader.load(date(2026, 11, 19))

    assert get.call_count == 1
    assert post.call_count == 2


@respx.mock
def test_a_day_with_only_a_header_row_is_no_data_and_stores_nothing(
    store: LocalObjectStore,
) -> None:
    """A bank holiday comes back as an empty table, not an error page (measured, D-81)."""
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(table(0)))

    result = make_loader(store).load(date(2026, 11, 21))

    assert result.outcome is IngestionOutcome.NO_DATA
    assert result.stored is None
    assert result.staged_key is None
    assert keys(store, date(2026, 11, 21)) == []


@respx.mock
def test_an_identical_download_is_unchanged_and_a_corrected_one_is_a_new_version(
    store: LocalObjectStore,
) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(
        side_effect=[
            download(table(1)),
            download(table(1)),
            download(table(1).replace(b"92.9699", b"92.9700")),
        ]
    )
    loader = make_loader(store)

    outcomes = [loader.load(TRADE_DATE).outcome for _ in range(3)]

    assert outcomes == [
        IngestionOutcome.STORED,
        IngestionOutcome.UNCHANGED,
        IngestionOutcome.STORED,
    ]
    assert len(keys(store)) == 2  # the first version is left untouched


@respx.mock
def test_loading_again_repairs_a_missing_staged_copy(
    store: LocalObjectStore, tmp_path: Path
) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))
    loader = make_loader(store)
    first = loader.load(TRADE_DATE)
    assert first.staged_key is not None
    (tmp_path / first.staged_key).unlink()

    second = loader.load(TRADE_DATE)

    assert second.outcome is IngestionOutcome.UNCHANGED
    assert second.staged_key == first.staged_key
    assert store.exists(first.staged_key)


@respx.mock
def test_without_a_stager_the_file_is_stored_and_nothing_is_staged(
    store: LocalObjectStore,
) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))

    result = make_loader(store, staged=False).load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert result.staged_key is None


@respx.mock
def test_a_page_instead_of_a_download_makes_the_loader_read_the_form_again_once(
    store: LocalObjectStore,
) -> None:
    get = respx.get(ARCHIVE_URL).mock(side_effect=[form_page("vs1"), form_page("vs2")])
    post = respx.post(ARCHIVE_URL).mock(side_effect=[plain_page(), download(table(1))])

    result = make_loader(store).load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert get.call_count == 2
    states = [parse_qs(call.request.content.decode())["__VIEWSTATE"] for call in post.calls]
    assert states == [["vs1"], ["vs2"]]


@respx.mock
def test_a_second_page_in_a_row_is_an_error_not_a_loop(store: LocalObjectStore) -> None:
    get = respx.get(ARCHIVE_URL).mock(return_value=form_page())
    post = respx.post(ARCHIVE_URL).mock(return_value=plain_page())

    with pytest.raises(IngestionError, match="a page, not a download, for 2026-11-20"):
        make_loader(store).load(TRADE_DATE)

    assert (get.call_count, post.call_count) == (2, 2)
    assert keys(store) == []


@pytest.mark.parametrize("status", [403, 418])
@respx.mock
def test_a_refusal_when_asking_for_rates_is_a_block(status: int, store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    post = respx.post(ARCHIVE_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(SourceBlockedError, match=f"rates for 2026-11-20 with HTTP {status}"):
        make_loader(store).load(TRADE_DATE)

    assert post.call_count == 1  # a block is not retried


@pytest.mark.parametrize("status", [403, 418])
@respx.mock
def test_a_refusal_when_reading_the_form_is_a_block(status: int, store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(status))
    post = respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))

    with pytest.raises(SourceBlockedError, match=f"the form with HTTP {status}"):
        make_loader(store).load(TRADE_DATE)

    assert post.call_count == 0


@respx.mock
def test_another_status_is_unavailable_but_not_a_block(store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(SourceUnavailableError, match="HTTP 404") as raised:
        make_loader(store).load(TRADE_DATE)

    assert not isinstance(raised.value, SourceBlockedError)


@respx.mock
def test_a_form_without_page_state_cannot_be_asked_for_rates(store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=plain_page())
    post = respx.post(ARCHIVE_URL).mock(return_value=download(table(1)))

    with pytest.raises(IngestionError, match="no __VIEWSTATE, __EVENTVALIDATION"):
        make_loader(store).load(TRADE_DATE)

    assert post.call_count == 0


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"<html>Unauthorised Access</html>", "Expected one table, found 0"),
        (table(1)[:-10], "cut off"),
        (
            b"<table><tr><td>Date</td><td>USD</td></tr><tr><td>20/11/2026</td></tr></table>",
            "different numbers of cells",
        ),
    ],
    ids=["a page with no table", "a cut-off table", "ragged rows"],
)
@respx.mock
def test_a_download_that_is_not_one_whole_table_is_refused_and_not_stored(
    content: bytes, reason: str, store: LocalObjectStore
) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(content))

    with pytest.raises(IngestionError, match=rf"for 2026-11-20 is not a usable table: .*{reason}"):
        make_loader(store).load(TRADE_DATE)

    assert keys(store) == []


@respx.mock
def test_several_days_of_rates_for_a_one_day_request_are_refused(store: LocalObjectStore) -> None:
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(table(5)))

    with pytest.raises(IngestionError, match="alone and got 5 rows of rates"):
        make_loader(store).load(TRADE_DATE)

    assert keys(store) == []


@respx.mock
def test_a_renamed_column_or_a_new_date_format_is_stored_for_dbt_to_report(
    store: LocalObjectStore,
) -> None:
    """Schema drift must reach dbt, where lineage can trace it, so the loader does not judge it."""
    drifted = (
        b"<table><tr><td>RateDate</td><td>US Dollar</td></tr>"
        b"<tr><td>2026-11-20</td><td>92.9699</td></tr></table>"
    )
    respx.get(ARCHIVE_URL).mock(return_value=form_page())
    respx.post(ARCHIVE_URL).mock(return_value=download(drifted))

    result = make_loader(store).load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert result.stored is not None
    assert store.get(result.stored.key) == drifted
