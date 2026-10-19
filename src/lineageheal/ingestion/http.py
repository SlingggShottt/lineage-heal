"""A polite HTTP client for public data sources.

Source sites are shared public infrastructure, so every request goes through one
place that identifies the project, keeps a minimum gap between requests and retries
transient failures with exponential backoff (FR-ING-10).
"""

import time
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Self

import httpx
import structlog
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from lineageheal import __version__
from lineageheal.core.errors import SourceUnavailableError

log = structlog.get_logger(__name__)

USER_AGENT = f"lineageheal/{__version__} (+https://github.com/SlingggShottt/lineage-heal)"

# Statuses that usually clear up on their own; anything else is the source's real answer.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class _TransientStatusError(Exception):
    """Internal signal that a response was a retryable status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _is_transient(error: BaseException) -> bool:
    return isinstance(error, httpx.TransportError | _TransientStatusError)


class PoliteFetcher:
    """GET requests with a minimum interval, a project User-Agent and retries."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        min_interval_seconds: float = 3.0,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap an httpx client.

        Args:
            client: The underlying client. Use `create` to get one with sensible defaults.
            min_interval_seconds: Minimum gap between the start of two requests.
            max_attempts: Attempts per request, including the first.
            sleep: Waits for a number of seconds. Replaced in tests so they never block.
            clock: Monotonic time in seconds. Replaced in tests.
        """
        self._client = client
        self._min_interval = min_interval_seconds
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    @classmethod
    def create(cls, *, min_interval_seconds: float = 3.0) -> Self:
        """Build a fetcher with a real client: identified, with explicit timeouts."""
        client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
        )
        return cls(client, min_interval_seconds=min_interval_seconds)

    def __enter__(self) -> Self:
        """Return the fetcher for use in a `with` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying client."""
        self.close()

    def close(self) -> None:
        """Close the underlying client."""
        self._client.close()

    def get(self, url: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        """Fetch a URL, retrying transient failures.

        Responses with a non-retryable status (including 404) are returned for the
        caller to interpret, because what a missing file means depends on the source.

        Args:
            url: The URL to fetch.
            params: Query parameters.

        Returns:
            The response.

        Raises:
            SourceUnavailableError: The request still failed after all attempts.
        """
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception(_is_transient),
            sleep=self._sleep,
            before_sleep=lambda state: log.warning(
                "request_retry_scheduled", url=url, attempt=state.attempt_number
            ),
            reraise=True,
        )
        try:
            return retrying(self._attempt, url, params)
        except (httpx.TransportError, _TransientStatusError) as exc:
            reason = (
                f"HTTP {exc.status_code}"
                if isinstance(exc, _TransientStatusError)
                else (type(exc).__name__)
            )
            raise SourceUnavailableError(
                f"GET {url} failed after {self._max_attempts} attempts: {reason}"
            ) from exc

    def _attempt(self, url: str, params: Mapping[str, str] | None) -> httpx.Response:
        self._wait_for_turn()
        response = self._client.get(url, params=params)
        if response.status_code in _TRANSIENT_STATUSES:
            raise _TransientStatusError(response.status_code)
        return response

    def _wait_for_turn(self) -> None:
        if self._last_request_at is not None:
            remaining = self._min_interval - (self._clock() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()
