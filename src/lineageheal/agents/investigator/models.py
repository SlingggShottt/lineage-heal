"""The Investigator's typed inputs and outputs (FR-INV-04, FR-INV-05).

`Step` and `Conclusion` are what a model is asked to return, so each field is a decision a
reviewer can read. `Evidence` and `Investigation` are what the Investigator records, in a form
the store can keep later without reshaping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from lineageheal.agents.detector.classify import FaultClassName
from lineageheal.core.settings import Settings

Verdict = Literal["supports", "refutes", "unclear"]
FixLocation = Literal["dbt", "external"]
MAX_ALTERNATIVES = 3


@dataclass(frozen=True, slots=True)
class Budget:
    """How much one investigation may spend (FR-INV-05)."""

    max_llm_calls: int
    max_seconds: float
    confidence_threshold: float

    @classmethod
    def from_settings(cls, settings: Settings) -> Budget:
        """The limits configured for this deployment."""
        return cls(
            settings.investigator_max_llm_calls,
            settings.investigator_max_seconds,
            settings.investigator_confidence_threshold,
        )


class Assessment(BaseModel):
    """What the latest evidence says about one node."""

    evidence_id: int
    node: str = Field(max_length=300)
    verdict: Verdict


class Step(BaseModel):
    """One decision in the search: what the last evidence meant, and what to do next."""

    assessment: Assessment | None = Field(
        default=None, description="What the most recent evidence says about a node; null at first."
    )
    leading_node: str | None = Field(
        default=None, max_length=300, description="The node you currently think is the root cause."
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are of the leading node.")
    action: Literal["call_tool", "conclude"]
    tool: str | None = Field(default=None, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=400)


class Hypothesis(BaseModel):
    """A possible root cause, with how likely it is."""

    node: str = Field(max_length=300)
    fault_class: FaultClassName
    explanation: str = Field(min_length=1, max_length=400)
    confidence: float = Field(ge=0, le=1)


class Conclusion(BaseModel):
    """The Investigator's answer (FR-INV-04, FR-INV-06)."""

    root_cause_node: str = Field(max_length=300)
    fault_class: FaultClassName
    explanation: str = Field(min_length=1, max_length=800)
    evidence_ids: list[int] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1)
    alternatives: list[Hypothesis] = Field(default_factory=list, max_length=MAX_ALTERNATIVES)
    fix_location: FixLocation
    fix_reason: str = Field(min_length=1, max_length=400)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One tool result, numbered so the model can cite it."""

    id: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    content: str  # bounded and delimited by the registry; safe to show a model
    payload: dict[str, Any]
    node: str | None = None
    verdict: Verdict | None = None


class InvestigationStatus(StrEnum):
    """How an investigation ended."""

    CONCLUDED = "concluded"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(slots=True)
class Investigation:
    """Everything an investigation produced, including what a stopped one found so far."""

    status: InvestigationStatus
    conclusion: Conclusion | None
    partial: Hypothesis | None  # the best guess when the budget ran out
    evidence: list[Evidence]
    llm_calls: int
    elapsed_seconds: float
    prompt_ids: list[str] = field(default_factory=list)
    model_ids: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # corrections made to the model's answer
