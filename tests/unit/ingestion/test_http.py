"""FR-ING-10: retries with backoff and a minimum interval between requests."""

import httpx
import pytest
import respx

from lineageheal.core.errors import SourceUnavailableError
from lineageheal.ingestion.http import USER_AGENT, PoliteFetcher

URL = "https://example.test/report"


class FakeTime:
    """A clock that only moves when something sleeps, so tests never block."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


def make_fetcher(
    fake_time: FakeTime, *, min_interval: float = 0.0, max_attempts: int = 4
) -> PoliteFetcher:
    return PoliteFetcher(
        httpx.Client(),
        min_interval_seconds=min_interval,
        max_attempts=max_attempts,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )


@respx.mock
def test_a_successful_response_is_returned_without_waiting(fake_time: FakeTime) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"data"))

    response = make_fetcher(fake_time).get(URL)

    assert response.content == b"data"
    assert fake_time.sleeps == []


@respx.mock
def test_transient_statuses_are_retried_with_exponential_backoff(fake_time: FakeTime) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, content=b"ok")]
    )

    response = make_fetcher(fake_time).get(URL)

    assert response.content == b"ok"
    assert route.call_count == 3
    assert fake_time.sleeps == [2, 4]


@respx.mock
def test_timeouts_are_retried(fake_time: FakeTime) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(200, content=b"ok")]
    )

    response = make_fetcher(fake_time).get(URL)

    assert response.content == b"ok"
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_the_configured_attempts_and_says_why(fake_time: FakeTime) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(SourceUnavailableError, match=r"4 attempts: HTTP 503"):
        make_fetcher(fake_time, max_attempts=4).get(URL)

    assert route.call_count == 4


@respx.mock
def test_exhausted_network_errors_name_the_error_type(fake_time: FakeTime) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))

    with pytest.raises(SourceUnavailableError, match="ConnectTimeout"):
        make_fetcher(fake_time, max_attempts=2).get(URL)


@respx.mock
def test_other_statuses_are_returned_not_retried(fake_time: FakeTime) -> None:
    """A 404 is the source's real answer; whether it is an error is the loader's call."""
    route = respx.get(URL).mock(return_value=httpx.Response(404))

    response = make_fetcher(fake_time).get(URL)

    assert response.status_code == 404
    assert route.call_count == 1


@respx.mock
def test_requests_are_spaced_by_the_minimum_interval(fake_time: FakeTime) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200))
    fetcher = make_fetcher(fake_time, min_interval=3.0)

    fetcher.get(URL)
    fetcher.get(URL)
    fetcher.get(URL)

    assert fake_time.sleeps == [3.0, 3.0]


@respx.mock
def test_no_waiting_when_the_interval_has_already_passed(fake_time: FakeTime) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200))
    fetcher = make_fetcher(fake_time, min_interval=3.0)

    fetcher.get(URL)
    fake_time.now += 10
    fetcher.get(URL)

    assert fake_time.sleeps == []


@respx.mock
def test_extra_headers_replace_the_defaults_of_the_same_name() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200))

    with PoliteFetcher.create(
        min_interval_seconds=0, headers={"User-Agent": "Mozilla/5.0 test", "Accept": "*/*"}
    ) as fetcher:
        fetcher.get(URL)

    headers = route.calls.last.request.headers
    assert headers["user-agent"] == "Mozilla/5.0 test"
    assert headers["accept"] == "*/*"


@respx.mock
def test_real_client_identifies_the_project_and_sets_explicit_timeouts() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200))

    with PoliteFetcher.create(min_interval_seconds=0) as fetcher:
        fetcher.get(URL)

    request = route.calls.last.request
    assert request.headers["user-agent"] == USER_AGENT
    assert "lineageheal" in USER_AGENT
    assert request.extensions["timeout"]["connect"] == 10.0
    assert request.extensions["timeout"]["read"] == 60.0
