"""The interface every model provider implements, so the client never knows which one it has."""

from __future__ import annotations

from typing import Protocol

from lineageheal.llm.types import Completion, LLMRequest


class Provider(Protocol):
    """One model service. Implementations turn HTTP outcomes into the layer's errors.

    A 429 becomes `RateLimitedError`, a 5xx or a timeout `ServerError`, and any other rejection
    `BadRequestError`, so the client can decide between waiting, falling back and giving up
    without knowing a provider's wire format. Error messages must never contain credentials.
    """

    name: str

    def complete(self, request: LLMRequest) -> Completion:
        """Send one request and return the answer, or raise an `LLMError`."""
        ...
