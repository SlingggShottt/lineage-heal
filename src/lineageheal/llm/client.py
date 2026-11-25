"""The one entry point agents use to call a model (IF-SW-05, NFR-REL-03).

The client puts a rate limiter in front of each provider, retries transient failures with
backoff, and moves to the next provider when one keeps failing or is out of quota. A request the
provider rejects outright is raised at once, since another attempt would fail the same way.
Structured output is validated against a Pydantic model, with one correction round if the model
returns something that does not fit.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import structlog
from pydantic import BaseModel, ValidationError
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

from lineageheal.llm.limiter import TokenBucket
from lineageheal.llm.provider import Provider
from lineageheal.llm.types import (
    Completion,
    LLMError,
    LLMRequest,
    RateLimitedError,
    ServerError,
    StructuredCompletion,
    StructuredOutputError,
)

log = structlog.get_logger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(slots=True)
class Usage:
    """What a client has used so far: successful calls and the tokens they were billed for."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A provider and the limiter that keeps calls to it within its quota."""

    provider: Provider
    limiter: TokenBucket


class LLMClient:
    """Calls models through an ordered list of endpoints: the first is preferred."""

    def __init__(
        self,
        endpoints: Sequence[Endpoint],
        *,
        attempts: int = 3,
        base_delay_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a client.

        Args:
            endpoints: Providers in order of preference; at least one.
            attempts: Tries per provider for a transient failure before moving on.
            base_delay_seconds: First backoff; each further try doubles it.
            sleep: Blocks for the given seconds (injected so tests do not wait).
        """
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        self._endpoints = tuple(endpoints)
        self._attempts = attempts
        self._base_delay = base_delay_seconds
        self._sleep = sleep
        # Cumulative; the benchmark takes the difference around one fault to cost its systems.
        self.usage = Usage()

    def complete(self, request: LLMRequest) -> Completion:
        """Get an answer from the first provider that gives one.

        Raises:
            BadRequestError: A provider rejected the request itself.
            LLMError: Every provider failed; the last provider's error is raised.
        """
        last: LLMError | None = None
        for position, endpoint in enumerate(self._endpoints):
            has_next = position < len(self._endpoints) - 1
            try:
                completion = self._call(endpoint, request, has_next=has_next)
            except (RateLimitedError, ServerError) as error:
                log.warning(
                    "llm_provider_failed",
                    provider=endpoint.provider.name,
                    error=type(error).__name__,
                    falling_back=has_next,
                )
                last = error
                continue
            self.usage.calls += 1
            self.usage.input_tokens += completion.input_tokens
            self.usage.output_tokens += completion.output_tokens
            log.info(
                "llm_call",
                provider=completion.provider,
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                latency_seconds=round(completion.latency_seconds, 3),
                fell_back=position > 0,
            )
            return completion
        raise last or LLMError("no provider answered")

    def complete_structured[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> StructuredCompletion[T]:
        """Get an answer that validates as `schema`, asking once more if it does not.

        Raises:
            StructuredOutputError: The correction did not produce valid JSON either.
        """
        json_schema = schema.model_json_schema()
        instruction = (
            "Reply with only a JSON object that matches this JSON Schema, with no other text:\n"
            + json.dumps(json_schema)
        )
        system = f"{request.system}\n\n{instruction}" if request.system else instruction
        asked = replace(request, system=system, json_schema=json_schema)

        first = self.complete(asked)
        problem = _validation_problem(first.text, schema)
        if isinstance(problem, BaseModel):
            return StructuredCompletion(problem, (first,))
        retry = replace(
            asked,
            prompt=f"{request.prompt}\n\nYour previous reply was not valid: {problem} "
            "Reply again with only the corrected JSON.",
        )
        second = self.complete(retry)
        outcome = _validation_problem(second.text, schema)
        if isinstance(outcome, BaseModel):
            return StructuredCompletion(outcome, (first, second))
        raise StructuredOutputError(f"the reply did not fit {schema.__name__}: {outcome}")

    def _call(self, endpoint: Endpoint, request: LLMRequest, *, has_next: bool) -> Completion:
        def should_retry(error: BaseException) -> bool:
            # With another provider to go to, waiting out a quota is wasted time.
            if isinstance(error, RateLimitedError):
                return not has_next
            return isinstance(error, ServerError)

        retrying = Retrying(
            stop=stop_after_attempt(self._attempts),
            retry=retry_if_exception(should_retry),
            wait=self._wait,
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                endpoint.limiter.acquire()
                return endpoint.provider.complete(request)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, state: RetryCallState) -> float:
        error = state.outcome.exception() if state.outcome else None
        if isinstance(error, RateLimitedError) and error.retry_after is not None:
            return error.retry_after
        return float(self._base_delay * 2 ** (state.attempt_number - 1))


def _validation_problem[T: BaseModel](text: str, schema: type[T]) -> T | str:
    """The parsed model, or a short description of why `text` does not fit."""
    fenced = _FENCE.match(text)
    body = fenced.group(1) if fenced else text.strip()
    try:
        return schema.model_validate_json(body)
    except ValidationError as error:
        issues = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}" for e in error.errors()
        )
        return f"{issues}."
