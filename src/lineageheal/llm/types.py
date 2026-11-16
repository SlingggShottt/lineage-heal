"""What goes into and comes out of the LLM layer, and how a call can fail.

Agents describe what they need (a prompt and a tier) and never name a provider or a model: the
layer picks both from configuration. Every answer carries the model, provider, token counts and
latency, because NFR-REPRO-01 and the benchmark need to know what produced each result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from lineageheal.core.errors import LineageHealError


class ModelTier(StrEnum):
    """Which kind of model a step needs, so cheap steps do not spend the strong model's quota."""

    STANDARD = "standard"  # investigation and patch writing
    LIGHT = "light"  # classification and summaries


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One call to a model. Temperature 0 by default keeps a replay as repeatable as it can be."""

    prompt: str
    system: str | None = None
    tier: ModelTier = ModelTier.STANDARD
    temperature: float = 0.0
    # Gemini counts its thinking tokens against this cap, so a small value can leave no room for
    # the answer: the call then fails with no text. Leave it unset unless an answer must be short.
    max_output_tokens: int | None = None
    # Set for structured output. A provider with native JSON-schema support can use it;
    # the others rely on the instruction the client adds to the system prompt.
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    """A model's answer and what it cost."""

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class StructuredCompletion[T: BaseModel]:
    """A validated answer and every completion it took (for token accounting)."""

    parsed: T
    completions: tuple[Completion, ...]


class LLMError(LineageHealError):
    """Base class for failures of the LLM layer."""


class RateLimitedError(LLMError):
    """The provider refused the call because a quota was reached; it may say when to come back."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        """Keep the provider's suggested wait, in seconds, when it gave one."""
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(LLMError):
    """The provider failed or timed out; trying again or another provider may work."""


class BadRequestError(LLMError):
    """The provider rejected the request itself (bad key, bad parameters). Retrying cannot help."""


class StructuredOutputError(LLMError):
    """The model did not return JSON that fits the requested schema, even after a correction."""
