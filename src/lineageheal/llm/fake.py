"""A provider that replays scripted answers, so agent and client tests never call a real model.

Each call takes the next item of the script: a string is returned as the model's answer, an
exception is raised as the provider's failure. Every request is kept, so a test can check what
an agent actually asked.
"""

from __future__ import annotations

from collections.abc import Sequence

from lineageheal.llm.types import Completion, LLMError, LLMRequest


class FakeProvider:
    """Replays a script of answers and failures in order."""

    def __init__(self, name: str, script: Sequence[str | LLMError]) -> None:
        """Create a provider; `name` appears on every completion it returns."""
        self.name = name
        self.requests: list[LLMRequest] = []
        self._script = list(script)

    def complete(self, request: LLMRequest) -> Completion:
        """Return or raise the next scripted item."""
        self.requests.append(request)
        if not self._script:
            raise LLMError(f"the fake provider {self.name!r} has no scripted answer left")
        item = self._script.pop(0)
        if isinstance(item, LLMError):
            raise item
        return Completion(
            text=item,
            provider=self.name,
            model=f"{self.name}-{request.tier.value}",
            input_tokens=len(request.prompt.split()),
            output_tokens=len(item.split()),
            latency_seconds=0.0,
        )
