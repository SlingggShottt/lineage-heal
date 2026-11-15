"""A client-side rate limiter, so free-tier quotas are respected before a provider has to refuse.

A token bucket refills continuously. With the default capacity of one, calls are spaced evenly
(60 / requests-per-minute seconds apart), which is the safest reading of a per-minute quota that
may be enforced over any sliding window. The clock and the sleep are injected so tests run
instantly and deterministically.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """Hands out permission to call a provider at no more than a steady rate."""

    def __init__(
        self,
        requests_per_minute: float,
        *,
        capacity: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a full bucket.

        Args:
            requests_per_minute: Sustained rate; must be positive.
            capacity: How many calls may go back to back after a quiet period.
            clock: Monotonic time in seconds.
            sleep: Blocks for the given seconds.
        """
        if requests_per_minute <= 0 or capacity < 1:
            raise ValueError("requests_per_minute and capacity must be positive")
        self._rate = requests_per_minute / 60.0
        self._capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self._capacity
        self._updated = clock()

    def acquire(self) -> float:
        """Wait until a call is allowed and return how long that took, in seconds."""
        waited = 0.0
        while True:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return waited
            pause = (1.0 - self._tokens) / self._rate
            self._sleep(pause)
            waited += pause
