"""Suggested changes for root causes outside the dbt project (FR-FIX-05, D-14).

A source that moved or a loader that broke cannot be fixed by a dbt patch, and the Fixer never
opens a pull request for code outside `dbt/`. Instead it drafts advice for an engineer: what to
change, where, how to check it, and what is uncertain. The model sees the diagnosis and the
evidence but no repository files, so it can advise and cannot write a change that would be run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from lineageheal.agents.prompt_registry import PromptRegistry, untrusted
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import LLMError, LLMRequest, ModelTier
from lineageheal.store.incidents import StoredIncident

log = structlog.get_logger(__name__)

AGENT = "fixer"
PURPOSE = "suggest"
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARACTERS = 500


class Suggestion(BaseModel):
    """A drafted change for a person to apply."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120, description="One line naming the change.")
    target: str = Field(
        min_length=1,
        max_length=300,
        description="The component or setting to change, in general terms.",
    )
    change: str = Field(min_length=1, max_length=1500, description="What to change and why.")
    draft: str = Field(default="", max_length=1500, description="An illustrative sketch, if any.")
    verify: str = Field(min_length=1, max_length=600, description="How to confirm it worked.")
    risks: str = Field(default="", max_length=600, description="What could go wrong or is unknown.")


class SuggestionStatus(StrEnum):
    """What the Fixer did for an incident whose cause is outside dbt."""

    DRAFTED = "drafted"
    NOT_APPLICABLE = "not_applicable"  # the cause is not a concluded one outside dbt
    UNAVAILABLE = "unavailable"  # the model could not be reached


@dataclass(slots=True)
class SuggestionResult:
    """The outcome of one draft, with what it cost."""

    status: SuggestionStatus
    reason: str = ""
    suggestion: Suggestion | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    model_ids: dict[str, str] = field(default_factory=dict)


class Suggester:
    """Drafts suggestions for incidents the Investigator placed outside dbt."""

    def __init__(self, client: LLMClient, *, prompts: PromptRegistry | None = None) -> None:
        """Create a suggester.

        Args:
            client: The LLM client.
            prompts: The prompt registry; the project's own when omitted.
        """
        self._client = client
        self._prompts = prompts or PromptRegistry()

    def draft(
        self, incident: StoredIncident, evidence: list[dict[str, Any]] | None = None
    ) -> SuggestionResult:
        """Draft a suggestion for an incident.

        Args:
            incident: An incident the Investigator concluded on.
            evidence: The investigation's evidence rows (tool, arguments, output).

        Returns:
            A result whose status says whether a suggestion came out.

        Raises:
            PromptError: The prompt is missing or malformed, which is a bug in the project.
        """
        cause = incident.root_cause or {}
        if cause.get("partial", True) or not cause.get("node"):
            return SuggestionResult(
                SuggestionStatus.NOT_APPLICABLE, "the incident has no concluded cause"
            )
        if cause.get("fix_location") != "external":
            return SuggestionResult(
                SuggestionStatus.NOT_APPLICABLE, "the root cause can be fixed inside dbt"
            )
        prompt = self._prompts.get(AGENT, PURPOSE)
        request = LLMRequest(
            prompt=prompt.render(
                summary=incident.summary,
                root_node=cause["node"],
                explanation=cause.get("explanation", ""),
                fix_reason=cause.get("fix_reason", ""),
                evidence=_evidence_text(evidence or []),
            ),
            tier=ModelTier.STANDARD,
        )
        result = SuggestionResult(SuggestionStatus.UNAVAILABLE)
        try:
            answer = self._client.complete_structured(request, Suggestion)
        except LLMError as error:
            log.warning("fixer_suggestion_failed", error=type(error).__name__)
            result.reason = f"the model could not be reached: {error}"
            return result
        result.llm_calls = len(answer.completions)
        result.input_tokens = sum(c.input_tokens for c in answer.completions)
        result.output_tokens = sum(c.output_tokens for c in answer.completions)
        result.prompt_hashes[prompt.id] = prompt.sha256
        result.model_ids[prompt.id] = answer.completions[-1].model
        result.status = SuggestionStatus.DRAFTED
        result.suggestion = answer.parsed
        return result


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "(none recorded)"
    blocks = []
    for number, item in enumerate(evidence[:MAX_EVIDENCE_ITEMS], start=1):
        arguments = json.dumps(item.get("arguments", {}), default=str, sort_keys=True)
        output = json.dumps(item.get("output", {}), default=str, sort_keys=True)
        text = f"{item.get('tool', 'tool')} {arguments}\n{output}"
        blocks.append(untrusted(text, f"evidence_{number}", MAX_EVIDENCE_CHARACTERS))
    return "\n".join(blocks)
