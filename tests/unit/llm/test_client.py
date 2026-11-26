"""NFR-REL-03: retries, fallback and structured output in the LLM client, with fake providers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from lineageheal.llm.client import Endpoint, LLMClient
from lineageheal.llm.fake import FakeProvider
from lineageheal.llm.limiter import TokenBucket
from lineageheal.llm.types import (
    BadRequestError,
    LLMRequest,
    ModelTier,
    RateLimitedError,
    ServerError,
    StructuredOutputError,
)

REQUEST = LLMRequest(prompt="Why did the NAV test fail?")


class Verdict(BaseModel):
    root_cause: str
    confidence: float


class Waits:
    """Records backoff sleeps instead of sleeping."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def client(*providers: FakeProvider, waits: Waits | None = None, attempts: int = 3) -> LLMClient:
    limiter = lambda: TokenBucket(6000, capacity=100)  # noqa: E731 - never the bottleneck here
    return LLMClient(
        [Endpoint(p, limiter()) for p in providers],
        attempts=attempts,
        base_delay_seconds=2.0,
        sleep=waits or Waits(),
    )


def test_an_answer_carries_what_produced_it() -> None:
    answer = client(FakeProvider("gemini", ["Nulls upstream."])).complete(REQUEST)

    assert (answer.text, answer.provider, answer.model) == (
        "Nulls upstream.",
        "gemini",
        "gemini-standard",
    )
    assert answer.output_tokens == 2


def test_the_tier_reaches_the_provider() -> None:
    provider = FakeProvider("gemini", ["ok"])

    client(provider).complete(LLMRequest(prompt="x", tier=ModelTier.LIGHT))

    assert provider.requests[0].tier is ModelTier.LIGHT


def test_a_server_error_is_retried_with_growing_backoff() -> None:
    waits = Waits()
    provider = FakeProvider("gemini", [ServerError("503"), ServerError("503"), "recovered"])

    answer = client(provider, waits=waits).complete(REQUEST)

    assert answer.text == "recovered"
    assert waits.seconds == [2.0, 4.0]


def test_a_provider_that_keeps_failing_hands_over_to_the_next() -> None:
    primary = FakeProvider("gemini", [ServerError("down")] * 3)
    secondary = FakeProvider("groq", ["from the fallback"])

    answer = client(primary, secondary).complete(REQUEST)

    assert (answer.provider, answer.text) == ("groq", "from the fallback")
    assert len(primary.requests) == 3


def test_a_rate_limit_falls_back_at_once_instead_of_waiting() -> None:
    waits = Waits()
    primary = FakeProvider("gemini", [RateLimitedError("quota", retry_after=60)])
    secondary = FakeProvider("groq", ["ok"])

    answer = client(primary, secondary, waits=waits).complete(REQUEST)

    assert answer.provider == "groq"
    assert waits.seconds == []
    assert len(primary.requests) == 1


def test_a_rate_limit_with_nowhere_to_go_waits_as_long_as_the_provider_asks() -> None:
    waits = Waits()
    provider = FakeProvider("gemini", [RateLimitedError("quota", retry_after=30), "ok"])

    client(provider, waits=waits).complete(REQUEST)

    assert waits.seconds == [30]


def test_when_every_provider_fails_the_last_error_surfaces() -> None:
    primary = FakeProvider("gemini", [ServerError("a")] * 3)
    secondary = FakeProvider("groq", [ServerError("b")] * 3)

    with pytest.raises(ServerError, match="b"):
        client(primary, secondary).complete(REQUEST)


def test_a_rejected_request_is_not_retried_or_sent_elsewhere() -> None:
    primary = FakeProvider("gemini", [BadRequestError("invalid key")])
    secondary = FakeProvider("groq", ["unused"])

    with pytest.raises(BadRequestError):
        client(primary, secondary).complete(REQUEST)

    assert secondary.requests == []


def test_structured_output_is_parsed_and_the_schema_is_sent() -> None:
    provider = FakeProvider("gemini", ['{"root_cause": "paise", "confidence": 0.9}'])

    result = client(provider).complete_structured(REQUEST, Verdict)

    assert result.parsed == Verdict(root_cause="paise", confidence=0.9)
    sent = provider.requests[0]
    assert sent.json_schema is not None
    assert "root_cause" in (sent.system or "")


def test_a_json_fence_around_the_answer_is_accepted() -> None:
    provider = FakeProvider("gemini", ['```json\n{"root_cause": "x", "confidence": 1}\n```'])

    assert client(provider).complete_structured(REQUEST, Verdict).parsed.root_cause == "x"


def test_an_invalid_answer_is_corrected_once_and_both_calls_are_counted() -> None:
    provider = FakeProvider(
        "gemini", ['{"root_cause": "x"}', '{"root_cause": "x", "confidence": 0.5}']
    )

    result = client(provider).complete_structured(REQUEST, Verdict)

    assert result.parsed.confidence == 0.5
    assert len(result.completions) == 2
    assert "confidence" in provider.requests[1].prompt  # the correction names the problem


def test_an_answer_that_stays_invalid_is_an_error() -> None:
    provider = FakeProvider("gemini", ["not json", "still not json"])

    with pytest.raises(StructuredOutputError, match="Verdict"):
        client(provider).complete_structured(REQUEST, Verdict)


def test_a_client_needs_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        LLMClient([])


def test_an_exhausted_fake_says_so() -> None:
    from lineageheal.llm.types import LLMError

    with pytest.raises(LLMError, match="no scripted answer"):
        FakeProvider("gemini", []).complete(REQUEST)


def test_usage_counts_each_answered_call_and_its_tokens_but_not_failures() -> None:
    llm = client(FakeProvider("a", [ServerError("down"), "one two three"]), attempts=3)

    llm.complete(REQUEST)

    assert llm.usage.calls == 1  # the failed attempt was not billed
    assert llm.usage.input_tokens == len(REQUEST.prompt.split())
    assert llm.usage.output_tokens == 3


# -- an outage should cost seconds, not minutes (LH-114) ---------------------------------------


class Clock:
    """A clock the tests move by hand; sleeping advances it, so waits are visible as time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def timed(
    *providers: FakeProvider,
    clock: Clock,
    attempts: int = 3,
    deadline: float | None = None,
    cooldown: float = 0.0,
) -> LLMClient:
    return LLMClient(
        [Endpoint(p, TokenBucket(6000, capacity=100)) for p in providers],
        attempts=attempts,
        base_delay_seconds=2.0,
        sleep=clock.sleep,
        call_deadline_seconds=deadline,
        cooldown_seconds=cooldown,
        clock=clock,
    )


def test_a_provider_is_not_retried_past_the_deadline_of_one_call() -> None:
    clock = Clock()
    slow = FakeProvider("slow", [ServerError("down")] * 5)
    fine = FakeProvider("fine", ["ok"])

    answer = timed(slow, fine, clock=clock, attempts=5, deadline=5.0).complete(REQUEST)

    assert answer.text == "ok"
    # Waits are 2 then 4 seconds: a third attempt would start after 6, past the deadline of 5.
    assert len(slow.requests) == 2
    assert clock.now == 2.0


def test_without_a_deadline_every_attempt_is_made() -> None:
    clock = Clock()
    slow = FakeProvider("slow", [ServerError("down")] * 5)

    with pytest.raises(ServerError):
        timed(slow, clock=clock, attempts=4).complete(REQUEST)

    assert len(slow.requests) == 4


def test_a_provider_that_failed_all_its_attempts_is_passed_over_for_the_cooldown() -> None:
    clock = Clock()
    flaky = FakeProvider("flaky", [ServerError("down")] * 3 + ["back"])
    steady = FakeProvider("steady", ["one", "two", "three"])
    llm = timed(flaky, steady, clock=clock, cooldown=60.0)

    first = llm.complete(REQUEST)
    clock.now += 10
    second = llm.complete(REQUEST)
    clock.now += 100
    third = llm.complete(REQUEST)

    assert (first.provider, second.provider, third.provider) == ("steady", "steady", "flaky")
    assert len(flaky.requests) == 4  # three failures, then nothing while cooling, then one try


def test_a_success_ends_the_cooldown_so_the_provider_is_preferred_again() -> None:
    clock = Clock()
    flaky = FakeProvider("flaky", [ServerError("down")] * 3 + ["back", "again"])
    steady = FakeProvider("steady", ["one", "two"])
    llm = timed(flaky, steady, clock=clock, cooldown=30.0)

    llm.complete(REQUEST)
    clock.now += 31
    back = llm.complete(REQUEST)
    again = llm.complete(REQUEST)  # no waiting: the success cleared the cooldown

    assert (back.provider, again.provider) == ("flaky", "flaky")


def test_when_every_provider_is_cooling_they_are_all_still_tried() -> None:
    clock = Clock()
    a = FakeProvider("a", [ServerError("down")] * 3 + ["a back"])
    b = FakeProvider("b", [ServerError("down")] * 3 + ["b back"])
    llm = timed(a, b, clock=clock, cooldown=60.0)

    with pytest.raises(ServerError):
        llm.complete(REQUEST)
    clock.now += 5  # still inside both cooldowns
    answer = llm.complete(REQUEST)

    assert answer.text == "a back"  # the outage ended and was noticed, in the original order


def test_a_rejected_request_does_not_start_a_cooldown() -> None:
    clock = Clock()
    picky = FakeProvider("picky", [BadRequestError("no"), "fine"])
    other = FakeProvider("other", ["other"])
    llm = timed(picky, other, clock=clock, cooldown=60.0)

    with pytest.raises(BadRequestError):
        llm.complete(REQUEST)

    assert llm.complete(REQUEST).provider == "picky"
