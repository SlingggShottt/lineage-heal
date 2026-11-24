"""The plain-LLM baseline (design 12.3): the same model, the failing check and one model's SQL.

No lineage, no tools, no retrieval. It shows what a language model alone, given what an engineer
would paste into a chat, can do, so that LineageHeal's added structure is measured against it.
Calls go through the same client as LineageHeal, so models, retries, fallback and temperature
are identical (FR-BEN-03).
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from lineageheal.agents.prompt_registry import PromptRegistry
from lineageheal.benchmark.models import Diagnosis, Scenario
from lineageheal.benchmark.systems.common import first_signal, read_manifest
from lineageheal.core.signals import Signal
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import LLMRequest, ModelTier

PROMPT_AGENT = "baseline"
PROMPT_PURPOSE = "plain"
TOP_N = 3


class PlainAnswer(BaseModel):
    """What the model is asked to return."""

    root_causes: list[str] = Field(min_length=1)
    fix_location: Literal["dbt", "outside_dbt"]
    reason: str = Field(min_length=1, max_length=600)


class PlainLLMSystem:
    """One structured model call per fault."""

    name = "plain-llm"

    def __init__(self, client: LLMClient, prompts: PromptRegistry | None = None) -> None:
        """Create the baseline on a client, which owns the model, retries and rate limits."""
        self._client = client
        self._prompt = (prompts or PromptRegistry()).get(PROMPT_AGENT, PROMPT_PURPOSE)

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Ask the model for the root cause of the first failing check.

        Raises:
            LLMError: The client could not get a valid answer; the runner records it as this
                system's error for the fault.
        """
        started = time.perf_counter()
        signal = first_signal(scenario)
        if signal is None:
            return Diagnosis(seconds=time.perf_counter() - started, notes="no failing check")
        request = LLMRequest(
            prompt=self._prompt.render(
                signal=_describe(signal),
                model_name=signal.model or "unknown",
                model_sql=_model_sql(scenario, signal.model),
            ),
            tier=ModelTier.STANDARD,
        )
        result = self._client.complete_structured(request, PlainAnswer)
        answer = result.parsed
        return Diagnosis(
            root_causes=tuple(answer.root_causes[:TOP_N]),
            fix_location=answer.fix_location,
            seconds=time.perf_counter() - started,
            llm_calls=len(result.completions),
            input_tokens=sum(c.input_tokens for c in result.completions),
            output_tokens=sum(c.output_tokens for c in result.completions),
            notes=f"{self._prompt.id}: {answer.reason}",
        )


def _describe(signal: Signal) -> str:
    where = ".".join(p for p in (signal.model, signal.column) if p) or signal.name
    failures = signal.failures if signal.failures is not None else "unknown"
    return (
        f"check: {signal.check_type} ({signal.name})\n"
        f"on: {where}\n"
        f"rows failed: {failures}\n"
        f"message: {signal.message}"
    )


def _model_sql(scenario: Scenario, model: str | None) -> str:
    """The model's SQL as dbt records it, or a note when the run left no manifest."""
    manifest = read_manifest(scenario)
    if manifest is None or model is None:
        return "(not available)"
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") == "model" and node.get("name") == model:
            return str(node.get("raw_code") or "(empty)")
    return "(not available)"
