"""Propose, verify, revise: the loop that turns an incident into a sandbox-checked patch.

The Fixer drafts a patch and the sandbox judges it. A patch that fails goes back to the Fixer
with the failure output, up to a fixed number of attempts (FR-FIX-03). Every attempt is kept so
a reviewer can see what was tried when none passed (FR-FIX-06).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import structlog

from lineageheal.agents.fixer.fixer import FixerResult, FixerStatus
from lineageheal.agents.fixer.patch import Patch
from lineageheal.agents.fixer.sandbox import SandboxError, SandboxResult
from lineageheal.store.incidents import StoredIncident

log = structlog.get_logger()


class PatchProposer(Protocol):
    """What the loop needs from the Fixer."""

    def propose(self, incident: StoredIncident, feedback: str | None = None) -> FixerResult:
        """A checked patch for the incident, or why there is none."""


class PatchVerifier(Protocol):
    """What the loop needs from the sandbox."""

    def verify(self, patch: Patch, model: str) -> SandboxResult:
        """Judge a patch by building the model and what depends on it."""


class VerificationStatus(StrEnum):
    """How the loop ended."""

    VERIFIED = "verified"  # a patch passed the sandbox
    UNVERIFIED = "unverified"  # patches were drafted but none passed
    NO_PATCH = "no_patch"  # the Fixer produced nothing to verify
    ERROR = "error"  # the sandbox itself could not run


@dataclass(frozen=True)
class Attempt:
    """One drafted patch and the sandbox's verdict on it."""

    patch: Patch
    result: SandboxResult


@dataclass
class VerificationOutcome:
    """What came of an incident's patch attempts."""

    status: VerificationStatus
    reason: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def verified(self) -> Attempt | None:
        """The passing attempt, if there is one."""
        return next((a for a in self.attempts if a.result.passed), None)


def model_of(incident: StoredIncident) -> str | None:
    """What to build to test a fix for an incident: dbt's name for the root cause's table.

    A column of a dbt model gives that model. A raw column (`raw.<feed>.<dataset>.<column>`) is
    not a model, so it gives the source, whose downstream is what a fix would change: dbt selects
    that as `source:raw_<feed>.<dataset>`.
    """
    return model_for_node((incident.root_cause or {}).get("node"))


def model_for_node(node: object) -> str | None:
    """`model_of` for a bare root cause node, which the benchmark has without an incident."""
    if not isinstance(node, str) or "." not in node:
        return None
    if node.startswith("raw."):
        parts = node.split(".", 3)
        return f"source:raw_{parts[1]}.{parts[2]}" if len(parts) >= 3 else None
    return node.rsplit(".", 1)[0]


def verify_incident(
    fixer: PatchProposer,
    sandbox: PatchVerifier,
    incident: StoredIncident,
    *,
    max_attempts: int = 2,
) -> VerificationOutcome:
    """Draft a patch for an incident and verify it, revising after a failure.

    Args:
        fixer: Drafts patches.
        sandbox: Judges them.
        incident: An incident the Investigator placed in dbt.
        max_attempts: Patches to try in the sandbox, counting the first.

    Returns:
        The outcome, with every sandboxed attempt. A sandbox that cannot run is reported as
        `ERROR` and keeps the attempts made so far; it is not retried, because the same fault
        would recur and say nothing about the patch.
    """
    model = model_of(incident)
    if model is None:
        return VerificationOutcome(VerificationStatus.NO_PATCH, "the root cause names no model")
    outcome = VerificationOutcome(VerificationStatus.NO_PATCH)
    feedback: str | None = None
    for number in range(1, max_attempts + 1):
        drafted = fixer.propose(incident, feedback)
        outcome.llm_calls += drafted.llm_calls
        outcome.input_tokens += drafted.input_tokens
        outcome.output_tokens += drafted.output_tokens
        if drafted.status is not FixerStatus.PATCHED or drafted.patch is None:
            outcome.reason = drafted.reason or drafted.status.value
            outcome.status = (
                VerificationStatus.UNVERIFIED if outcome.attempts else VerificationStatus.NO_PATCH
            )
            return outcome
        try:
            result = sandbox.verify(drafted.patch, model)
        except SandboxError as error:
            log.error("sandbox_failed", incident_id=incident.id, error=str(error))
            outcome.status = VerificationStatus.ERROR
            outcome.reason = str(error)
            return outcome
        outcome.attempts.append(Attempt(drafted.patch, result))
        log.info("sandbox_verdict", incident_id=incident.id, attempt=number, passed=result.passed)
        if result.passed:
            outcome.status = VerificationStatus.VERIFIED
            outcome.reason = result.reason
            return outcome
        outcome.status = VerificationStatus.UNVERIFIED
        outcome.reason = result.reason
        feedback = result.feedback()
    return outcome
