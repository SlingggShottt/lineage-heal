"""The types the benchmark runner passes between its parts (FR-BEN-03, FR-BEN-06).

A `Scenario` is one fault built into a sandbox: the damaged data, the dbt run on it and the
signals it raised. Every system under test receives the same scenario, so they are compared on
identical conditions. What a system answers is a `Diagnosis`, and the runner stores each answer
with its timings as a `CaseResult`, one file per (fault, system).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from lineageheal.agents.fixer.patch import Patch
from lineageheal.benchmark.spec import FaultCase, FixLocation
from lineageheal.core.signals import RunSummary


@dataclass(frozen=True)
class Scenario:
    """One fault built into a sandbox, ready for a system to diagnose."""

    case: FaultCase
    root: Path  # the scratch folder holding everything below; removed after the case
    raw_zone: Path  # raw/, staged/ and runs/<run_id>/ of this sandbox
    warehouse: Path  # the DuckDB file dbt built
    run_id: str
    summary: RunSummary
    dbt_exit_code: int
    build_seconds: float

    @property
    def detected(self) -> bool:
        """Whether the pipeline noticed anything wrong at all."""
        return bool(self.summary.signals)


class PatchRecord(BaseModel):
    """A drafted dbt patch, as stored: the unified diff and what it touches."""

    model_config = ConfigDict(frozen=True)

    diff: str
    files: tuple[str, ...]
    changed_lines: int = Field(ge=0)

    @classmethod
    def of(cls, patch: Patch) -> PatchRecord:
        """The record of a checked patch."""
        return cls(diff=patch.diff, files=patch.files, changed_lines=patch.changed_lines)

    def to_patch(self) -> Patch:
        """The patch again, for the sandbox, which applies the diff (new texts are not kept)."""
        return Patch(self.diff, self.files, self.changed_lines)


class Diagnosis(BaseModel):
    """What a system concluded about a scenario."""

    model_config = ConfigDict(frozen=True)

    # Graph nodes (`raw.<source>.<dataset>.<column>`), best first, so top-1 and top-3 accuracy
    # can be read off the same list.
    root_causes: tuple[str, ...] = ()
    fix_location: FixLocation | None = None
    patch: PatchRecord | None = None
    # The sandbox's verdict on the patch; None until it is verified, or when there is no patch.
    sandbox_passed: bool | None = None
    sandbox_reason: str = ""
    patch_attempts: int = Field(default=0, ge=0)  # patches drafted, counting revisions
    # `seconds` is what the system itself spent (calls, tools, drafting); time in the sandbox is
    # kept apart so a report can show both and sum them for detection-to-fix time.
    seconds: float = Field(default=0.0, ge=0)
    sandbox_seconds: float = Field(default=0.0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    notes: str = ""


class System(Protocol):
    """A system the benchmark can evaluate: LineageHeal or a baseline."""

    name: str

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Diagnose the scenario's failure, using only what the scenario contains."""
        ...


class CaseResult(BaseModel):
    """One system's outcome on one fault, as stored."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    case_id: str
    system: str
    split: Literal["seed", "test"]
    fault_class: str
    status: Literal["completed", "errored", "scenario_failed"]
    detected: bool = False
    signal_count: int = 0
    diagnosis: Diagnosis | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime
    scenario_seconds: float = 0.0
