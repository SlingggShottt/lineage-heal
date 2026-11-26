"""The Groq provider: the OpenAI-compatible `chat/completions` endpoint over plain httpx (D-41).

Groq is the fallback behind Gemini. Like `GeminiProvider` it makes one attempt per call and
leaves retries and fallback to `LLMClient`. It serves both tiers with one model, since the free
tier offers one strong model rather than a strong and a light one.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import SecretStr

from lineageheal.llm.types import (
    BadRequestError,
    Completion,
    LLMRequest,
    RateLimitedError,
    ServerError,
)

_BASE_URL = "https://api.groq.com/openai/v1"
# Groq answers 400 with this code when the model's output was not valid JSON. Another attempt
# can succeed, so it is treated as a transient failure rather than a rejected request.
_INVALID_JSON_CODE = "json_validate_failed"


class GroqProvider:
    """Calls one Groq-hosted model for every tier."""

    name = "groq"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str,
        client: httpx.Client | None = None,
        base_url: str = _BASE_URL,
        timeout_seconds: float = 90.0,
    ) -> None:
        """Create a provider.

        Args:
            api_key: The Groq key, sent as a bearer token and scrubbed from error messages.
            model: Model ID used for every request.
            client: An httpx client; tests pass one backed by respx. When omitted, a client with
                explicit timeouts is created.
            base_url: The API root, overridable for tests.
            timeout_seconds: How long one request may wait for the provider to answer or send
                more of its answer, when no client is given. A provider that goes quiet then
                counts as failed instead of holding up the investigation.
        """
        self._key = api_key
        self._model = model
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        )
        self._base_url = base_url.rstrip("/")

    def complete(self, request: LLMRequest) -> Completion:
        """Send one request and return the answer, or raise an `LLMError`."""
        started = time.monotonic()
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json=self._payload(request),
            )
        except httpx.TimeoutException as exc:
            raise ServerError("groq timed out") from exc
        except httpx.TransportError as exc:
            raise ServerError(f"groq could not be reached ({type(exc).__name__})") from exc

        if response.status_code == 429:
            raise RateLimitedError("groq quota reached", retry_after=_retry_after(response))
        if response.status_code >= 500:
            raise ServerError(f"groq failed with status {response.status_code}")
        if response.status_code >= 400:
            code, message = _error(response)
            if code == _INVALID_JSON_CODE:
                raise ServerError("groq returned output that was not valid JSON")
            raise BadRequestError(
                f"groq rejected the request ({response.status_code}): {self._scrub(message)}"
            )
        return self._completion(response, time.monotonic() - started)

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            payload["max_completion_tokens"] = request.max_output_tokens
        if request.json_schema is not None:
            # JSON mode rather than a schema-enforcing mode: it works on every Groq model, and
            # the client has already put the schema in the system prompt (JSON mode requires
            # the word JSON there) and validates the reply against it.
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _completion(self, response: httpx.Response, latency: float) -> Completion:
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ServerError("groq returned a body that is not JSON") from exc

        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        if not text:
            reason = choices[0].get("finish_reason", "unknown") if choices else "no choices"
            raise ServerError(f"groq returned no text (finish reason {reason})")

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            provider=self.name,
            model=str(body.get("model") or self._model),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_seconds=latency,
        )

    def _scrub(self, message: str) -> str:
        return message.replace(self._key.get_secret_value(), "[redacted]")


def _error(response: httpx.Response) -> tuple[str | None, str]:
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return None, "no error detail"
    return error.get("code"), str(error.get("message") or "no error detail")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None
