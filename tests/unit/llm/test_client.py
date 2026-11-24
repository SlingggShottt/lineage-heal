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
