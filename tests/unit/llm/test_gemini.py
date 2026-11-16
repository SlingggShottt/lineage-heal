"""IF-SW-05, NFR-REL-03: the Gemini provider's wire format and error mapping, with respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from lineageheal.llm.gemini import GeminiProvider
from lineageheal.llm.types import (
    BadRequestError,
    LLMRequest,
    ModelTier,
    RateLimitedError,
    ServerError,
)

KEY = "test-key-do-not-leak"
BASE = "https://gemini.test/v1beta"
STANDARD_URL = f"{BASE}/models/big:generateContent"
LIGHT_URL = f"{BASE}/models/small:generateContent"


def provider() -> GeminiProvider:
    return GeminiProvider(SecretStr(KEY), standard_model="big", light_model="small", base_url=BASE)


def answer(text: str = "ok", **usage: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": usage or {"promptTokenCount": 7, "candidatesTokenCount": 3},
        },
    )


@respx.mock
def test_an_answer_carries_text_model_and_token_counts() -> None:
    respx.post(STANDARD_URL).mock(return_value=answer("Nulls upstream."))

    result = provider().complete(LLMRequest(prompt="why?"))

    assert (result.text, result.provider, result.model) == ("Nulls upstream.", "gemini", "big")
    assert (result.input_tokens, result.output_tokens) == (7, 3)


@respx.mock
def test_thinking_tokens_count_as_output_and_thought_parts_are_not_the_answer() -> None:
    respx.post(STANDARD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "private reasoning", "thought": True},
                                {"text": "final"},
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 2,
                    "thoughtsTokenCount": 40,
                },
            },
        )
    )

    result = provider().complete(LLMRequest(prompt="why?"))

    assert result.text == "final"
    assert result.output_tokens == 42


@respx.mock
def test_the_tier_picks_the_model() -> None:
    light = respx.post(LIGHT_URL).mock(return_value=answer())

    result = provider().complete(LLMRequest(prompt="classify", tier=ModelTier.LIGHT))

    assert light.called
    assert result.model == "small"


@respx.mock
def test_the_key_travels_in_a_header_and_never_in_the_url() -> None:
    route = respx.post(STANDARD_URL).mock(return_value=answer())

    provider().complete(LLMRequest(prompt="why?"))

    sent = route.calls.last.request
    assert sent.headers["x-goog-api-key"] == KEY
    assert KEY not in str(sent.url)


@respx.mock
def test_request_fields_map_to_gemini_names() -> None:
    route = respx.post(STANDARD_URL).mock(return_value=answer())
    schema = {"type": "object", "properties": {"cause": {"type": "string"}}}

    provider().complete(
        LLMRequest(
            prompt="why?",
            system="Be brief.",
            temperature=0.2,
            max_output_tokens=100,
            json_schema=schema,
        )
    )

    body = json.loads(route.calls.last.request.content)
    assert body["contents"][0]["parts"][0]["text"] == "why?"
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 100,
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }


@respx.mock
def test_plain_requests_leave_out_the_optional_fields() -> None:
    route = respx.post(STANDARD_URL).mock(return_value=answer())

    provider().complete(LLMRequest(prompt="why?"))

    body = json.loads(route.calls.last.request.content)
    assert "systemInstruction" not in body
    assert body["generationConfig"] == {"temperature": 0.0}


@respx.mock
def test_a_rate_limit_reports_the_wait_from_the_header() -> None:
    respx.post(STANDARD_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "12"}))

    with pytest.raises(RateLimitedError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert caught.value.retry_after == 12.0


@respx.mock
def test_a_rate_limit_reports_the_wait_from_the_error_body() -> None:
    body = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.Help"},
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "0.5s",
                },
            ]
        }
    }
    respx.post(STANDARD_URL).mock(return_value=httpx.Response(429, json=body))

    with pytest.raises(RateLimitedError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert caught.value.retry_after == 0.5


@respx.mock
def test_a_rate_limit_without_a_stated_wait_has_none() -> None:
    respx.post(STANDARD_URL).mock(return_value=httpx.Response(429, text="slow down"))

    with pytest.raises(RateLimitedError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert caught.value.retry_after is None


@respx.mock
@pytest.mark.parametrize("status", [500, 503])
def test_a_server_failure_is_transient(status: int) -> None:
    respx.post(STANDARD_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(ServerError):
        provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_timeout_and_a_dropped_connection_are_transient() -> None:
    route = respx.post(STANDARD_URL)
    route.side_effect = [httpx.ReadTimeout("slow"), httpx.ConnectError("no route")]

    for _ in range(2):
        with pytest.raises(ServerError):
            provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_rejected_request_is_not_retryable_and_never_shows_the_key() -> None:
    respx.post(STANDARD_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": f"API key not valid: {KEY}", "status": "INVALID"}}
        )
    )

    with pytest.raises(BadRequestError) as caught:
        provider().complete(LLMRequest(prompt="why?"))

    assert "400" in str(caught.value)
    assert KEY not in str(caught.value)


@respx.mock
def test_a_blocked_answer_is_transient_so_the_fallback_can_try() -> None:
    respx.post(STANDARD_URL).mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )

    with pytest.raises(ServerError, match="SAFETY"):
        provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_body_that_is_not_json_is_transient() -> None:
    respx.post(STANDARD_URL).mock(return_value=httpx.Response(200, text="<html>"))

    with pytest.raises(ServerError):
        provider().complete(LLMRequest(prompt="why?"))


@respx.mock
def test_a_cap_spent_on_thinking_is_a_transient_failure_that_names_the_reason() -> None:
    respx.post(STANDARD_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}],
                "usageMetadata": {"promptTokenCount": 8, "thoughtsTokenCount": 64},
            },
        )
    )

    with pytest.raises(ServerError, match="MAX_TOKENS"):
        provider().complete(LLMRequest(prompt="why?", max_output_tokens=64))
