"""The limiter spaces calls to stay inside a free-tier quota (NFR-REL-03 depends on it)."""

import pytest

from lineageheal.llm.limiter import TokenBucket


class Clock:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def bucket(clock: Clock, rpm: float = 30, capacity: int = 1) -> TokenBucket:
    return TokenBucket(rpm, capacity=capacity, clock=clock, sleep=clock.sleep)


def test_the_first_call_goes_straight_through() -> None:
    clock = Clock()

    assert bucket(clock).acquire() == 0
    assert clock.slept == []


def test_calls_are_spaced_by_the_rate() -> None:
    clock = Clock()
    limiter = bucket(clock, rpm=30)  # one every 2 seconds

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert clock.slept == pytest.approx([2.0, 2.0])


def test_a_quiet_period_refills_the_bucket() -> None:
    clock = Clock()
    limiter = bucket(clock, rpm=30)
    limiter.acquire()
    clock.now += 10  # long enough for more than one token

    assert limiter.acquire() == 0


def test_capacity_allows_a_burst_but_never_more() -> None:
    clock = Clock()
    limiter = bucket(clock, rpm=60, capacity=3)
    clock.now += 600

    waits = [limiter.acquire() for _ in range(4)]

    assert waits[:3] == [0, 0, 0]
    assert waits[3] == pytest.approx(1.0)


@pytest.mark.parametrize(("rpm", "capacity"), [(0, 1), (-5, 1), (10, 0)])
def test_a_nonsense_rate_or_capacity_is_refused(rpm: float, capacity: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(rpm, capacity=capacity)
