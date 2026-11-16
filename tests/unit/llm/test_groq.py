"""IF-SW-05, NFR-REL-03: the Groq provider's wire format and error mapping, with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from lineageheal.llm.groq import GroqProvider
from lineageheal.llm.types import (
    BadRequestError,
    LLMRequest,
    ModelTier,
    RateLimitedError,
    ServerError,
)

KEY = "test-key-do-not-leak"
BASE = "https://groq.test/openai/v1"
URL = f"{BASE}/chat/completions"


def provider() -> GroqProvider:
    return GroqProvider(SecretStr(KEY), model="oss", base_url=BASE)


def answer(text: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "oss",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        },
    )


@respx.mock
def test_an_answer_carries_text_model_and_token_counts() -> None:
    respx.post(URL).mock(return_value=answer("Nulls upstream."))

    result = provider().complete(LLMRequest(prompt="why?"))

    assert (result.text, result.provider, result.model) == ("Nulls upstream.", "groq", "oss")
    assert (result.input_tokens, result.output_tokens) == (9, 4)


@respx.mock
def test_both_tiers_use_the_one_model() -> None:
    route = respx.post(URL).mock(return_value=answer())

    provider().complete(LLMRequest(prompt="a", tier=ModelTier.LIGHT))
    provider().complete(LLMRequest(prompt="b", tier=ModelTier.STANDARD))

    models = {json.loads(call.request.content)["model"] for call in route.calls}
    assert models == {"oss"}


@respx.mock
def test_the_key_travels_as_a_bearer_token_and_never_in_the_url() -> None:
    route = respx.post(URL).mock(return_value=answer())

    provider().complete(LLMRequest(prompt="why?"))

    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(sent.url)


@respx.mock
def test_request_fields_map_to_chat_completion_names() -> None:
    route = respx.post(URL).mock(return_value=answer())

    provider().complete(
        LLMRequest(
            prompt="why?",
            system="Be brief. Reply in JSON.",
            temperature=0.2,
            max_output_tokens=100,
            json_schema={"type": "object"},
        )
    )

    body = json.loads(route.calls.last.request.content)
    assert body["messages"] == [
        {"role": "system", "content": "Be brief. Reply in JSON."},
        {"role": "user", "content": "why?"},
    ]
    assert body["temperature"] == 0.2
    assert body["max_completion_tokens"] == 100
    assert body["response_format"] == {"type": "json_object"}


@respx.mock
def test_plain_requests_leave_out_the_optional_fields() -> None:
    route = respx.post(URL).mock(return_value=answer())

    provider().complete(LLMRequest(prompt="why?"))

    body = json.loads(route.calls.last.request.content)
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert "response_format" not in body
    assert "max_completion_tokens" not in body


@respx.mock
def test_a_rate_limit_reports_the_wait() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"retry-after": "7"}))

    with pytest.raises(RateLimitedError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert caught.value.retry_after == 7.0


@respx.mock
def test_a_rate_limit_without_a_stated_wait_has_none() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429))

    with pytest.raises(RateLimitedError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert caught.value.retry_after is None


@respx.mock
def test_a_server_failure_is_transient() -> None:
    respx.post(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ServerError):
        provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_timeout_and_a_dropped_connection_are_transient() -> None:
    route = respx.post(URL)
    route.side_effect = [httpx.ReadTimeout("slow"), httpx.ConnectError("no route")]

    for _ in range(2):
        with pytest.raises(ServerError):
            provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_rejected_request_is_not_retryable_and_never_shows_the_key() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            401, json={"error": {"message": f"invalid key {KEY}", "code": "invalid_api_key"}}
        )
    )

    with pytest.raises(BadRequestError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert "401" in str(caught.value)
    assert KEY not in str(caught.value)


@respx.mock
def test_invalid_json_output_is_transient_not_a_rejected_request() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "failed", "code": "json_validate_failed"}}
        )
    )

    with pytest.raises(ServerError):
        provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_an_empty_answer_is_transient() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )
    )

    with pytest.raises(ServerError, match="length"):
        provider().complete(LLMRequest(prompt="why?"))
