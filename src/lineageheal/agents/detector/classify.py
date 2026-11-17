"""Deciding what kind of fault a group of signals is (FR-DET-04).

Rules come first and always produce an answer, so detection never depends on a model being
reachable. One LLM call may then refine the class, give a confidence and write the summary. If
that call fails, the rules' answer stands and the incident records that rules made it.

The rules' class, not the model's, goes into the incident's fingerprint: the same signals always
give the same rule class, so a re-run finds the same incident even if the model answers
differently next time (FR-DET-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from lineageheal.agents.detector.grouping import SignalGroup
from lineageheal.agents.prompt_registry import PromptRegistry
from lineageheal.core.signals import Signal, SignalKind
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import LLMError, LLMRequest, ModelTier

log = structlog.get_logger(__name__)

FaultClassName = Literal[
    "schema_drift", "nulls", "duplicates", "unit_change", "late_data", "unknown"
]

# What each dbt check suggests on its own, and how sure that makes the rules. Anything not
# listed is `unknown`. A check cannot tell nulls from schema drift (a renamed column makes every
# value null), which is one reason a model reads the numbers afterwards.
_BY_CHECK: dict[str, tuple[FaultClassName, float]] = {
    "not_null": ("nulls", 0.6),
    "unique": ("duplicates", 0.7),
    "unique_combination": ("duplicates", 0.7),
    "file_present_when_expected": ("late_data", 0.7),
    "fresh_within": ("late_data", 0.7),
    "distribution_anomaly": ("unit_change", 0.5),
    "unexplained_price_move": ("unit_change", 0.6),
    "implausible_nav_move": ("unit_change", 0.6),
}
_BY_KIND: dict[SignalKind, tuple[FaultClassName, float]] = {
    SignalKind.INGESTION_ERROR: ("late_data", 0.8),
}
_UNKNOWN: tuple[FaultClassName, float] = ("unknown", 0.2)

PROMPT_AGENT = "detector"
PROMPT_PURPOSE = "classify"


class Classification(BaseModel):
    """What the model is asked to return."""

    fault_class: FaultClassName
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class Verdict:
    """A fault class with how sure we are and who decided."""

    fault_class: FaultClassName
    confidence: float
    summary: str
    classified_by: str  # "rules", or "<prompt id>@<model>"


def rule_class(signals: tuple[Signal, ...]) -> tuple[FaultClassName, float]:
    """The class the checks themselves suggest, and a confidence for it.

    Each signal votes with the class and confidence its check implies; the class with the most
    total weight wins, and ties go to the alphabetically first class so the answer is stable.
    """
    weight: dict[FaultClassName, float] = {}
    best: dict[FaultClassName, float] = {}
    for signal in signals:
        vote = _BY_KIND.get(signal.kind) or _BY_CHECK.get(signal.check_type) or _UNKNOWN
        weight[vote[0]] = weight.get(vote[0], 0.0) + vote[1]
        best[vote[0]] = max(best.get(vote[0], 0.0), vote[1])
    winner = min(weight, key=lambda c: (-weight[c], c))
    return winner, best[winner]


def describe(group: SignalGroup) -> str:
    """A plain summary built from the signals alone, used when no model writes one."""
    first = group.signals[0]
    where = ".".join(p for p in (first.model, first.column) if p) or first.name
    extra = len(group.signals) - 1
    more = f" and {extra} more signal{'s' if extra > 1 else ''}" if extra else ""
    rows = f" ({first.failures} rows)" if first.failures else ""
    return f"{first.check_type} on {where}{rows}{more}"


def _signal_lines(group: SignalGroup) -> str:
    lines = []
    for s in group.signals:
        where = ".".join(p for p in (s.model, s.column) if p) or s.name
        message = s.message.splitlines()[0] if s.message else ""
        lines.append(f"{s.check_type} | {where} | {s.failures if s.failures else '-'} | {message}")
    return "\n".join(lines)


class Classifier:
    """Rules, refined by one model call when a client is available."""

    def __init__(self, client: LLMClient | None = None, prompts: PromptRegistry | None = None):
        """Create a classifier; without a client every verdict comes from the rules."""
        self._client = client
        self._prompts = prompts or PromptRegistry()

    def classify(self, group: SignalGroup) -> Verdict:
        """Return a verdict for the group, never failing because a model did.

        Raises:
            PromptError: The classification prompt is missing or malformed. That is a bug in the
                project, not a runtime condition, so it is not swallowed.
        """
        fault, confidence = rule_class(group.signals)
        fallback = Verdict(fault, confidence, describe(group), "rules")
        if self._client is None:
            return fallback
        prompt = self._prompts.get(PROMPT_AGENT, PROMPT_PURPOSE)
        request = LLMRequest(
            prompt=prompt.render(
                root_node=group.root_node or "none found",
                rule_class=fault,
                signals=_signal_lines(group),
            ),
            tier=ModelTier.LIGHT,
        )
        try:
            result = self._client.complete_structured(request, Classification)
        except LLMError as error:
            log.warning("detector_llm_failed", error=type(error).__name__, using="rules")
            return fallback
        answer = result.parsed
        model = result.completions[-1].model
        return Verdict(
            answer.fault_class,
            answer.confidence,
            answer.summary,
            f"{prompt.id}@{model}"[:128],
        )
