"""The plain-LLM baseline (design 12.3): the same model, the failing check and one model's SQL.

No lineage, no tools, no retrieval. It shows what a language model alone, given what an engineer
would paste into a chat, can do, so that LineageHeal's added structure is measured against it.
Calls go through the same client as LineageHeal, so models, retries, fallback and temperature
are identical (FR-BEN-03).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from lineageheal.agents.fixer.models import PatchProposal
from lineageheal.agents.fixer.patch import (
    DEFAULT_LIMITS,
    Patch,
    PatchError,
    PatchLimits,
    build_patch,
)
from lineageheal.agents.prompt_registry import PromptRegistry
from lineageheal.benchmark.models import Diagnosis, PatchRecord, Scenario
from lineageheal.benchmark.systems.common import REPO_ROOT, first_signal, model_file, read_manifest
from lineageheal.core.signals import Signal
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import Completion, LLMRequest, ModelTier

PROMPT_AGENT = "baseline"
PROMPT_PURPOSE = "plain"
PATCH_PURPOSE = "patch"
TOP_N = 3


class PlainAnswer(BaseModel):
    """What the model is asked to return."""

    root_causes: list[str] = Field(min_length=1)
    fix_location: Literal["dbt", "outside_dbt"]
    reason: str = Field(min_length=1, max_length=600)


class PlainLLMSystem:
    """One structured model call per fault."""

    name = "plain-llm"

    def __init__(
        self,
        client: LLMClient,
        prompts: PromptRegistry | None = None,
        *,
        repo_root: Path = REPO_ROOT,
        limits: PatchLimits = DEFAULT_LIMITS,
    ) -> None:
        """Create the baseline on a client, which owns the model, retries and rate limits.

        Args:
            client: The shared client.
            prompts: The prompt registry; the project's own when omitted.
            repo_root: The repository holding the dbt project, for the file it may edit.
            limits: The patch size limits, the same ones the Fixer is held to.
        """
        self._client = client
        registry = prompts or PromptRegistry()
        self._prompt = registry.get(PROMPT_AGENT, PROMPT_PURPOSE)
        self._patch_prompt = registry.get(PROMPT_AGENT, PATCH_PURPOSE)
        self._root = repo_root
        self._limits = limits

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
        completions = list(result.completions)
        patch, note = None, ""
        if answer.fix_location == "dbt":
            patch, extra, note = self._draft_patch(scenario, signal, answer)
            completions += extra
        return Diagnosis(
            root_causes=tuple(answer.root_causes[:TOP_N]),
            fix_location=answer.fix_location,
            patch=PatchRecord.of(patch) if patch else None,
            patch_attempts=1 if patch else 0,
            seconds=time.perf_counter() - started,
            llm_calls=len(completions),
            input_tokens=sum(c.input_tokens for c in completions),
            output_tokens=sum(c.output_tokens for c in completions),
            notes=f"{self._prompt.id}: {answer.reason}" + (f"; {note}" if note else ""),
        )

    def _draft_patch(
        self, scenario: Scenario, signal: Signal, answer: PlainAnswer
    ) -> tuple[Patch | None, tuple[Completion, ...], str]:
        """One attempt to fix the failing model's file, which is the only file it is shown."""
        manifest = read_manifest(scenario)
        path = model_file(manifest, signal.model) if manifest else None
        if path is None:
            return None, (), "no patch: the failing model's file is unknown"
        root = scenario.repo_root or self._root
        try:
            content = (root / path).read_text(encoding="utf-8")
        except OSError:
            return None, (), f"no patch: {path} cannot be read"
        if len(content) > self._limits.max_file_characters:
            return None, (), f"no patch: {path} is too long to show whole"
        request = LLMRequest(
            prompt=self._patch_prompt.render(
                signal=_describe(signal),
                root_cause=answer.root_causes[0],
                reason=answer.reason,
                path=path,
                content=content,
            ),
            tier=ModelTier.STANDARD,
        )
        result = self._client.complete_structured(request, PatchProposal)
        proposal = result.parsed
        if proposal.declined_reason or not proposal.edits:
            return None, result.completions, f"no patch: {proposal.declined_reason or 'no edits'}"
        edits = [(e.path, e.new_content) for e in proposal.edits]
        try:
            if any(p != path for p, _ in edits):
                raise PatchError("the model changed a file it was not shown")
            return build_patch(root, edits, self._limits), result.completions, ""
        except PatchError as exc:
            return None, result.completions, f"no patch: {exc}"


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
