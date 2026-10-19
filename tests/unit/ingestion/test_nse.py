"""FR-ING-01: the NSE CM-UDiFF bhavcopy zip is stored unchanged and checked for integrity."""

import gzip
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import respx

from lineageheal.core.errors import IngestionError, SourceUnavailableError
from lineageheal.ingestion.http import PoliteFetcher
from lineageheal.ingestion.nse import (
    CORPORATE_ACTIONS_URL,
    NSE_BROWSER_HEADERS,
    NseBhavcopyLoader,
    NseCorporateActionsLoader,
    NseSecurityMasterLoader,
    bhavcopy_filename,
    corporate_actions_filename,
    security_master_filename,
)
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager
from lineageheal.ingestion.types import IngestionOutcome, Loader

TRADE_DATE = date(2024, 7, 5)
URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv.zip"
CSV = (
    Path(__file__).parents[2] / "fixtures" / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv"
).read_bytes()


def make_zip(csv_bytes: bytes = CSV, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv", csv_bytes)
    return buffer.getvalue()


def zip_response(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "application/zip"})


@pytest.fixture
def zone(tmp_path: Path) -> RawZone:
    return RawZone(LocalObjectStore(tmp_path))


@pytest.fixture
def loader(zone: RawZone) -> NseBhavcopyLoader:
    fetcher = PoliteFetcher(httpx.Client(), min_interval_seconds=0, sleep=lambda _: None)
    return NseBhavcopyLoader(fetcher, zone)


def test_file_names_follow_nses_udiff_pattern() -> None:
    assert bhavcopy_filename(date(2026, 10, 16)) == "BhavCopy_NSE_CM_0_0_0_20261016_F_0000.csv.zip"
    assert bhavcopy_filename(date(2024, 1, 2)) == "BhavCopy_NSE_CM_0_0_0_20240102_F_0000.csv.zip"


def test_browser_headers_look_like_a_browser() -> None:
    """NSE resets connections from clients that do not, so this guards the fix."""
    assert NSE_BROWSER_HEADERS["User-Agent"].startswith("Mozilla/5.0")
    assert "Accept" in NSE_BROWSER_HEADERS
    assert "Accept-Language" in NSE_BROWSER_HEADERS


@respx.mock
def test_the_zip_is_stored_exactly_as_published(loader: NseBhavcopyLoader, zone: RawZone) -> None:
    published = make_zip()
    respx.get(URL).mock(return_value=zip_response(published))

    result = loader.load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.STORED
    assert result.stored is not None
    assert result.stored.key.startswith("raw/source=nse/dataset=cm_bhavcopy/trade_date=2024-07-05/")
    assert result.stored.key.endswith("__BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv.zip")
    assert zone.read(result.stored.key) == published
    with zipfile.ZipFile(BytesIO(zone.read(result.stored.key))) as archive:
        assert archive.read(archive.namelist()[0]) == CSV


@respx.mock
def test_loading_the_same_file_again_stores_nothing_new(loader: NseBhavcopyLoader) -> None:
    respx.get(URL).mock(return_value=zip_response(make_zip()))

    first = loader.load(TRADE_DATE)
    second = loader.load(TRADE_DATE)

    assert second.outcome is IngestionOutcome.UNCHANGED
    assert first.stored is not None
    assert second.stored is not None
    assert second.stored.key == first.stored.key


@respx.mock
def test_a_republished_file_becomes_a_new_object(loader: NseBhavcopyLoader, zone: RawZone) -> None:
    original = make_zip()
    revised = make_zip(CSV + b"EXTRA,ROW\n")
    respx.get(URL).mock(side_effect=[zip_response(original), zip_response(revised)])

    first = loader.load(TRADE_DATE)
    second = loader.load(TRADE_DATE)

    assert first.stored is not None
    assert second.stored is not None
    assert second.outcome is IngestionOutcome.STORED
    assert second.stored.key != first.stored.key
    assert zone.read(first.stored.key) == original


@respx.mock
def test_a_404_means_no_file_and_stores_nothing(loader: NseBhavcopyLoader, tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404, text="<html>Not Found</html>"))

    result = loader.load(TRADE_DATE)

    assert result.outcome is IngestionOutcome.NO_DATA
    assert result.http_status == 404
    assert result.stored is None
    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_other_error_statuses_mean_the_source_is_unavailable(loader: NseBhavcopyLoader) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403))

    with pytest.raises(SourceUnavailableError, match="HTTP 403"):
        loader.load(TRADE_DATE)


@respx.mock
def test_a_html_page_served_as_success_is_rejected(
    loader: NseBhavcopyLoader, tmp_path: Path
) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, html="<html>Access Denied</html>"))

    with pytest.raises(IngestionError, match="not a valid zip"):
        loader.load(TRADE_DATE)

    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_a_truncated_download_is_rejected(loader: NseBhavcopyLoader, tmp_path: Path) -> None:
    published = make_zip()
    respx.get(URL).mock(return_value=zip_response(published[: len(published) // 2]))

    with pytest.raises(IngestionError, match="not a valid zip"):
        loader.load(TRADE_DATE)

    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_a_zip_with_damaged_contents_is_rejected(loader: NseBhavcopyLoader, tmp_path: Path) -> None:
    published = bytearray(make_zip(compression=zipfile.ZIP_STORED))
    published[published.index(b"RELIANCE")] ^= 0xFF  # change a content byte, breaking the CRC
    respx.get(URL).mock(return_value=zip_response(bytes(published)))

    with pytest.raises(IngestionError, match="damaged or empty"):
        loader.load(TRADE_DATE)

    assert list(tmp_path.rglob("*.*")) == []


@pytest.fixture
def staging_loader(tmp_path: Path) -> NseBhavcopyLoader:
    store = LocalObjectStore(tmp_path)
    fetcher = PoliteFetcher(httpx.Client(), min_interval_seconds=0, sleep=lambda _: None)
    return NseBhavcopyLoader(fetcher, RawZone(store), ZipStager(store))


@respx.mock
def test_a_stored_file_is_also_unzipped_into_the_staged_area(
    staging_loader: NseBhavcopyLoader, tmp_path: Path
) -> None:
    respx.get(URL).mock(return_value=zip_response(make_zip()))

    result = staging_loader.load(TRADE_DATE)

    assert result.staged_key is not None
    assert result.staged_key.startswith("staged/source=nse/dataset=cm_bhavcopy/")
    assert result.staged_key.endswith(".csv")
    assert (tmp_path / result.staged_key).read_bytes() == CSV


@respx.mock
def test_an_unchanged_file_repairs_a_missing_staged_copy(
    staging_loader: NseBhavcopyLoader, tmp_path: Path
) -> None:
    respx.get(URL).mock(return_value=zip_response(make_zip()))
    first = staging_loader.load(TRADE_DATE)
    assert first.staged_key is not None
    (tmp_path / first.staged_key).unlink()

    second = staging_loader.load(TRADE_DATE)

    assert second.outcome is IngestionOutcome.UNCHANGED
    assert (tmp_path / first.staged_key).read_bytes() == CSV


@respx.mock
def test_a_day_without_a_file_stages_nothing(
    staging_loader: NseBhavcopyLoader, tmp_path: Path
) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))

    result = staging_loader.load(TRADE_DATE)

    assert result.staged_key is None
    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_without_a_stager_nothing_is_staged(loader: NseBhavcopyLoader) -> None:
    respx.get(URL).mock(return_value=zip_response(make_zip()))

    assert loader.load(TRADE_DATE).staged_key is None


# --- security master and corporate actions -----------------------------------------------

NSE_FIXTURES = Path(__file__).parents[2] / "fixtures" / "nse"
MASTER_CSV = (NSE_FIXTURES / "security_master_sample.csv").read_bytes()
ACTIONS_JSON = (NSE_FIXTURES / "corporate_actions_sample.json").read_bytes()
REFERENCE_DATE = date(2026, 10, 16)
MASTER_URL = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_16102026.csv.gz"


@dataclass(frozen=True)
class ReferenceCase:
    """One of the two reference datasets, with a valid response to serve for it."""

    loader_class: Callable[[PoliteFetcher, RawZone], Loader]
    dataset: str
    url: str
    body: bytes
    filename: str

    def altered_body(self) -> bytes:
        """A different but still valid response, as a republication would be."""
        if self.dataset == "security_master":
            return gzip.compress(MASTER_CSV + b"extra,row\n")
        return json.dumps([*json.loads(ACTIONS_JSON), {"symbol": "NEW"}]).encode()


MASTER = ReferenceCase(
    NseSecurityMasterLoader,
    "security_master",
    MASTER_URL,
    gzip.compress(MASTER_CSV),
    "NSE_CM_security_16102026.csv.gz",
)
ACTIONS = ReferenceCase(
    NseCorporateActionsLoader,
    "corporate_actions",
    CORPORATE_ACTIONS_URL,
    ACTIONS_JSON,
    "corporate_actions_20261016.json",
)


def make_reference_loader(case: ReferenceCase, zone: RawZone) -> Loader:
    fetcher = PoliteFetcher(httpx.Client(), min_interval_seconds=0, sleep=lambda _: None)
    return case.loader_class(fetcher, zone)


@pytest.mark.parametrize("case", [MASTER, ACTIONS], ids=lambda c: c.dataset)
class TestReferenceLoaders:
    @respx.mock
    def test_the_response_is_stored_exactly_as_published(
        self, case: ReferenceCase, zone: RawZone
    ) -> None:
        respx.get(case.url).mock(return_value=httpx.Response(200, content=case.body))

        result = make_reference_loader(case, zone).load(REFERENCE_DATE)

        assert result.outcome is IngestionOutcome.STORED
        assert result.stored is not None
        assert result.stored.key.startswith(
            f"raw/source=nse/dataset={case.dataset}/trade_date=2026-10-16/"
        )
        assert result.stored.key.endswith(f"__{case.filename}")
        assert zone.read(result.stored.key) == case.body
        assert result.staged_key is None

    @respx.mock
    def test_loading_the_same_response_again_stores_nothing_new(
        self, case: ReferenceCase, zone: RawZone
    ) -> None:
        respx.get(case.url).mock(return_value=httpx.Response(200, content=case.body))
        loader = make_reference_loader(case, zone)

        loader.load(REFERENCE_DATE)
        second = loader.load(REFERENCE_DATE)

        assert second.outcome is IngestionOutcome.UNCHANGED

    @respx.mock
    def test_a_changed_response_becomes_a_new_object(
        self, case: ReferenceCase, zone: RawZone
    ) -> None:
        respx.get(case.url).mock(
            side_effect=[
                httpx.Response(200, content=case.body),
                httpx.Response(200, content=case.altered_body()),
            ]
        )
        loader = make_reference_loader(case, zone)

        first = loader.load(REFERENCE_DATE)
        second = loader.load(REFERENCE_DATE)

        assert first.stored is not None
        assert second.stored is not None
        assert second.outcome is IngestionOutcome.STORED
        assert second.stored.key != first.stored.key
        assert zone.read(first.stored.key) == case.body

    @respx.mock
    def test_a_404_means_no_file(self, case: ReferenceCase, zone: RawZone, tmp_path: Path) -> None:
        respx.get(case.url).mock(return_value=httpx.Response(404))

        result = make_reference_loader(case, zone).load(REFERENCE_DATE)

        assert result.outcome is IngestionOutcome.NO_DATA
        assert result.http_status == 404
        assert list(tmp_path.rglob("*.*")) == []

    @respx.mock
    def test_other_error_statuses_mean_the_source_is_unavailable(
        self, case: ReferenceCase, zone: RawZone
    ) -> None:
        respx.get(case.url).mock(return_value=httpx.Response(403))

        with pytest.raises(SourceUnavailableError, match=f"HTTP 403 for {case.dataset}"):
            make_reference_loader(case, zone).load(REFERENCE_DATE)

    @respx.mock
    def test_an_error_page_served_as_success_is_rejected_and_not_stored(
        self, case: ReferenceCase, zone: RawZone, tmp_path: Path
    ) -> None:
        respx.get(case.url).mock(return_value=httpx.Response(200, html="<html>Denied</html>"))

        with pytest.raises(IngestionError):
            make_reference_loader(case, zone).load(REFERENCE_DATE)

        assert list(tmp_path.rglob("*.*")) == []


def test_reference_file_names_follow_the_published_patterns() -> None:
    assert security_master_filename(date(2026, 10, 16)) == "NSE_CM_security_16102026.csv.gz"
    assert security_master_filename(date(2024, 1, 2)) == "NSE_CM_security_02012024.csv.gz"
    assert corporate_actions_filename(date(2026, 10, 16)) == "corporate_actions_20261016.json"


@respx.mock
def test_the_security_master_gzip_holds_the_published_csv(zone: RawZone) -> None:
    respx.get(MASTER_URL).mock(return_value=httpx.Response(200, content=MASTER.body))

    result = make_reference_loader(MASTER, zone).load(REFERENCE_DATE)

    assert result.stored is not None
    assert gzip.decompress(zone.read(result.stored.key)) == MASTER_CSV


@respx.mock
def test_a_truncated_security_master_is_rejected(zone: RawZone, tmp_path: Path) -> None:
    respx.get(MASTER_URL).mock(
        return_value=httpx.Response(200, content=MASTER.body[: len(MASTER.body) // 2])
    )

    with pytest.raises(IngestionError, match="not a complete gzip"):
        make_reference_loader(MASTER, zone).load(REFERENCE_DATE)

    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_a_security_master_with_a_failed_checksum_is_rejected(zone: RawZone) -> None:
    damaged = bytearray(MASTER.body)
    damaged[-5] ^= 0xFF  # inside the CRC that gzip keeps at the end of the stream
    respx.get(MASTER_URL).mock(return_value=httpx.Response(200, content=bytes(damaged)))

    with pytest.raises(IngestionError, match="not a complete gzip"):
        make_reference_loader(MASTER, zone).load(REFERENCE_DATE)


@respx.mock
def test_an_empty_security_master_is_rejected(zone: RawZone) -> None:
    respx.get(MASTER_URL).mock(return_value=httpx.Response(200, content=gzip.compress(b"")))

    with pytest.raises(IngestionError, match="empty gzip"):
        make_reference_loader(MASTER, zone).load(REFERENCE_DATE)


@respx.mock
def test_corporate_actions_are_requested_for_one_ex_date(zone: RawZone) -> None:
    route = respx.get(CORPORATE_ACTIONS_URL).mock(
        return_value=httpx.Response(200, content=ACTIONS.body)
    )

    make_reference_loader(ACTIONS, zone).load(REFERENCE_DATE)

    params = route.calls.last.request.url.params
    assert params["index"] == "equities"
    assert params["from_date"] == "16-10-2026"
    assert params["to_date"] == "16-10-2026"


@respx.mock
def test_a_day_with_no_corporate_actions_is_no_data_not_a_stored_empty_file(
    zone: RawZone, tmp_path: Path
) -> None:
    respx.get(CORPORATE_ACTIONS_URL).mock(return_value=httpx.Response(200, content=b"[]"))

    result = make_reference_loader(ACTIONS, zone).load(REFERENCE_DATE)

    assert result.outcome is IngestionOutcome.NO_DATA
    assert result.http_status == 200
    assert list(tmp_path.rglob("*.*")) == []


@respx.mock
def test_corporate_actions_json_that_is_not_a_list_is_rejected(zone: RawZone) -> None:
    respx.get(CORPORATE_ACTIONS_URL).mock(
        return_value=httpx.Response(200, content=b'{"error": "blocked"}')
    )

    with pytest.raises(IngestionError, match="not a list"):
        make_reference_loader(ACTIONS, zone).load(REFERENCE_DATE)


@respx.mock
def test_corporate_actions_that_are_not_json_are_rejected(zone: RawZone) -> None:
    respx.get(CORPORATE_ACTIONS_URL).mock(return_value=httpx.Response(200, content=b"\xff\xfe"))

    with pytest.raises(IngestionError, match="not JSON"):
        make_reference_loader(ACTIONS, zone).load(REFERENCE_DATE)
