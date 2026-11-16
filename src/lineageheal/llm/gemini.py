"""The Gemini provider: Google AI Studio's `generateContent` endpoint over plain httpx (D-41).

The provider makes exactly one attempt per call. Retries, backoff and fallback belong to
`LLMClient`, so a second retry loop here would multiply waits and burn the free-tier quota.
Its job is to translate: a request into Gemini's wire format, and every HTTP outcome into one
of the layer's errors.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from pydantic import SecretStr

from lineageheal.llm.types import (
    BadRequestError,
    Completion,
    LLMRequest,
    ModelTier,
    RateLimitedError,
    ServerError,
)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Gemini states its suggested wait in the error body as a duration string such as "12s" or "0.5s".
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GeminiProvider:
    """Calls Gemini models, choosing the model by the request's tier."""

    name = "gemini"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        standard_model: str,
        light_model: str,
        client: httpx.Client | None = None,
        base_url: str = _BASE_URL,
    ) -> None:
        """Create a provider.

        Args:
            api_key: The AI Studio key. Sent in a header, never in the URL, so it cannot end up
                in a logged request line.
            standard_model: Model ID for investigation and patch writing.
            light_model: Model ID for classification and summaries.
            client: An httpx client; tests pass one backed by respx. When omitted, a client with
                explicit timeouts is created.
            base_url: The API root, overridable for tests.
        """
        self._key = api_key
        self._models = {ModelTier.STANDARD: standard_model, ModelTier.LIGHT: light_model}
        self._client = client or httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
        self._base_url = base_url.rstrip("/")

    def complete(self, request: LLMRequest) -> Completion:
        """Send one request and return the answer, or raise an `LLMError`."""
        model = self._models[request.tier]
        started = time.monotonic()
        try:
            response = self._client.post(
                f"{self._base_url}/models/{model}:generateContent",
                headers={"x-goog-api-key": self._key.get_secret_value()},
                json=_payload(request),
            )
        except httpx.TimeoutException as exc:
            raise ServerError("gemini timed out") from exc
        except httpx.TransportError as exc:
            # The exception type is enough to act on; its text can carry the request URL.
            raise ServerError(f"gemini could not be reached ({type(exc).__name__})") from exc

        if response.status_code == 429:
            raise RateLimitedError("gemini quota reached", retry_after=_retry_after(response))
        if response.status_code >= 500:
            raise ServerError(f"gemini failed with status {response.status_code}")
        if response.status_code >= 400:
            raise BadRequestError(
                f"gemini rejected the request ({response.status_code}): "
                f"{self._scrub(_error_message(response))}"
            )
        return self._completion(response, model, time.monotonic() - started)

    def _completion(self, response: httpx.Response, model: str, latency: float) -> Completion:
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ServerError("gemini returned a body that is not JSON") from exc

        text = _answer_text(body)
        if text is None:
            # A blocked or empty answer is treated as transient so the client can try the
            # fallback provider, which may answer the same prompt.
            raise ServerError(f"gemini returned no text ({_finish_reason(body)})")

        usage = body.get("usageMetadata") or {}
        return Completion(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            # Thinking tokens are billed against the quota like answer tokens.
            output_tokens=int(usage.get("candidatesTokenCount", 0))
            + int(usage.get("thoughtsTokenCount", 0)),
            latency_seconds=latency,
        )

    def _scrub(self, message: str) -> str:
        return message.replace(self._key.get_secret_value(), "[redacted]")


def _payload(request: LLMRequest) -> dict[str, Any]:
    config: dict[str, Any] = {"temperature": request.temperature}
    if request.max_output_tokens is not None:
        config["maxOutputTokens"] = request.max_output_tokens
    if request.json_schema is not None:
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = request.json_schema

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
        "generationConfig": config,
    }
    if request.system:
        payload["systemInstruction"] = {"parts": [{"text": request.system}]}
    return payload


def _answer_text(body: dict[str, Any]) -> str | None:
    candidates = body.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    # Parts flagged as thoughts are the model's reasoning summary, not part of the answer.
    texts = [p["text"] for p in parts if "text" in p and not p.get("thought")]
    return "".join(texts) if texts else None


def _finish_reason(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if candidates and candidates[0].get("finishReason"):
        return f"finish reason {candidates[0]['finishReason']}"
    feedback = body.get("promptFeedback") or {}
    return f"block reason {feedback.get('blockReason', 'unknown')}"


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return "no error detail"
    return str(error.get("message") or error.get("status") or "no error detail")


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header is not None:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        details = response.json().get("error", {}).get("details", [])
    except (ValueError, AttributeError):
        return None
    for detail in details:
        match = _DURATION.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None
