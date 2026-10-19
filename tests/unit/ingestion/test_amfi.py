"""FR-ING-03: the AMFI NAV report is stored unchanged, once per distinct content."""

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.amfi import AMFI_REPORT_URL, AmfiNavLoader, format_amfi_date
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.types import IngestionOutcome

TRADE_DATE = date(2026, 10, 15)
FIXTURES = Path(__file__).parents[2] / "fixtures" / "amfi"
REPORT = (FIXTURES / "navall_history_2026-10-15_sample.txt").read_bytes()
NO_DATA_PAGE = (FIXTURES / "navall_no_data_response.html").read_bytes()


def text_response(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "text/plain"})


def html_response(content: bytes) -> httpx.Response:
    return httpx.Response(
        200, content=content, headers={"content-type": "text/html; charset=utf-8"}
    )


@pytest.fixture
def zone(tmp_path: Path) -> RawZone:
    return RawZone(LocalObjectStore(tmp_path))


@pytest.fixture
def loader(zone: RawZone) -> AmfiNavLoader:
    fetcher = PoliteFetcher(httpx.Client(), min_interval_seconds=0, sleep=lambda _: None)
    return AmfiNavLoader(fetcher, zone)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 5), "05-Oct-2026"),
        (date(2024, 1, 2), "02-Jan-2024"),
        (date(2025, 12, 31), "31-Dec-2025"),
    ],
)
def test_dates_are_formatted_the_way_amfi_expects(value: date, expected: str) -> None:
    assert format_amfi_date(value) == expected


@respx.mock
def test_the_report_is_requested_for_exactly_one_date(loader: AmfiNavLoader) -> None:
    route = respx.get(AMFI_REPORT_URL).mock(return_value=text_response(REPORT))

    loader.load(TRADE_DATE)

    params = route.calls.last.request.url.params
    assert params["frmdt"] == "15-Oct-2026"
    assert params["todt"] == "15-Oct-2026"


@respx.mock
def test_a_new_report_is_stored_exactly_as_published(loader: AmfiNavLoader, zone: RawZone) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=text_response(REPORT))

    result = loader.load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert result.stored is not None
    assert result.stored.key.startswith("raw/source=amfi/dataset=navall/trade_date=2026-10-15/")
    assert result.stored.key.endswith("__NAVHistoryReport151026.txt")
    assert zone.read(result.stored.key) == REPORT
    assert result.http_status == 200


@respx.mock
def test_loading_the_same_report_again_stores_nothing_new(loader: AmfiNavLoader) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=text_response(REPORT))

    first = loader.load(TRADE_DATE)
    second = loader.load(TRADE_DATE)

    assert second.outcome is IngestionOutcome.UNCHANGED
    assert second.stored is not None
    assert first.stored is not None
    assert second.stored.key == first.stored.key


@respx.mock
def test_a_revised_report_becomes_a_new_object(loader: AmfiNavLoader, zone: RawZone) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        side_effect=[text_response(REPORT), text_response(REPORT + b"extra;row\r\n")]
    )

    first = loader.load(TRADE_DATE)
    second = loader.load(TRADE_DATE)

    assert second.outcome is IngestionOutcome.STORED
    assert first.stored is not None
    assert second.stored is not None
    assert second.stored.key != first.stored.key
    assert zone.read(first.stored.key) == REPORT


@respx.mock
def test_the_no_data_page_is_a_normal_outcome_and_stores_nothing(
    loader: AmfiNavLoader, tmp_path: Path
) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=html_response(NO_DATA_PAGE))

    result = loader.load(date(2027, 12, 31))

    assert result.outcome is IngestionOutcome.NO_DATA
    assert result.stored is None
    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_an_empty_response_counts_as_no_data(loader: AmfiNavLoader) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=text_response(b"  \r\n"))

    assert loader.load(TRADE_DATE).outcome is IngestionOutcome.NO_DATA


@respx.mock
def test_an_unrecognised_html_page_is_an_error_not_a_stored_file(
    loader: AmfiNavLoader, tmp_path: Path
) -> None:
    respx.get(AMFI_REPORT_URL).mock(
        return_value=html_response(b"<html><body>Service temporarily unavailable</body></html>")
    )

    with pytest.raises(IngestionError, match="unexpected HTML"):
        loader.load(TRADE_DATE)

    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_an_error_status_is_reported_as_the_source_being_unavailable(
    loader: AmfiNavLoader,
) -> None:
    respx.get(AMFI_REPORT_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(SourceUnavailableError, match="HTTP 403"):
        loader.load(TRADE_DATE)
