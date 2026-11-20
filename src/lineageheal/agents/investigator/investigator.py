"""The Investigator: a lineage-guided search for an incident's root cause (FR-INV-01 to 06).

The search is a loop of small decisions. A model sees the evidence so far and the nodes still
worth testing, and either asks for one tool call or concludes; the registry runs the call. The
model never chooses what a tool is, only which registered one to call and with what arguments
(D-49), and it can only name nodes the graph has.

Every limit is enforced here, not requested of the model: a call budget with one call kept for
the final answer, a wall-clock limit, and a check of the conclusion against the graph and the
evidence. A search that runs out of budget is reported with what it found, never as a guess
dressed up as an answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace

import structlog

from lineageheal.agents.detector.classify import Verdict
from lineageheal.agents.detector.grouping import SignalGroup
from lineageheal.agents.investigator.models import (
    Budget,
    Conclusion,
    Evidence,
    Hypothesis,
    Investigation,
    InvestigationStatus,
    Step,
)
from lineageheal.agents.investigator.search import frontier, seed_calls
from lineageheal.agents.prompt_registry import Prompt, PromptRegistry, untrusted
from lineageheal.agents.tools import ToolRegistry
from lineageheal.lineage.service import LineageService
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import Completion, LLMError, LLMRequest, ModelTier

log = structlog.get_logger(__name__)

AGENT = "investigator"
# Evidence older than the newest is shown short, so twelve steps do not fill a prompt.
OLDER_EVIDENCE_CHARACTERS = 300
CONCLUSION_EVIDENCE_CHARACTERS = 900
# A conclusion that rests on fewer than two findings about its node is capped here.
THIN_EVIDENCE_CONFIDENCE = 0.6
NO_EVIDENCE_CONFIDENCE = 0.3
MIN_SUPPORTING_FINDINGS = 2


class Investigator:
    """Finds the root cause of one incident, within a budget."""

    def __init__(
        self,
        client: LLMClient,
        tools: ToolRegistry,
        lineage: LineageService,
        *,
        budget: Budget,
        prompts: PromptRegistry | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an investigator; `clock` is injectable so tests control the time limit."""
        self._client = client
        self._tools = tools
        self._lineage = lineage
        self._budget = budget
        self._prompts = prompts or PromptRegistry()
        self._clock = clock

    def investigate(self, group: SignalGroup, verdict: Verdict) -> Investigation:
        """Search for the root cause of an incident.

        Args:
            group: The incident's signals and what they touch.
            verdict: The Detector's fault class and summary, which the search may overrule.

        Returns:
            A concluded investigation, or a `budget_exhausted` one with the best hypothesis so
            far. A model that is unreachable counts as an exhausted budget: the evidence
            gathered is kept.

        Raises:
            PromptError: A prompt is missing or malformed, which is a bug in the project.
        """
        run = _Run(started=self._clock())
        for tool, arguments in seed_calls(group, verdict):
            self._call_tool(run, tool, arguments)

        step_prompt = self._prompts.get(AGENT, "step")
        while True:
            reason = self._out_of_budget(run)
            if reason is not None:
                return self._exhausted(run, reason)
            try:
                step = self._ask_step(run, step_prompt, group, verdict)
            except LLMError as error:
                log.warning("investigator_model_failed", error=type(error).__name__)
                return self._exhausted(run, f"the model could not be reached: {error}")
            self._apply(run, step)
            if self._is_done(run, step):
                break
            if step.action == "call_tool":
                self._run_step_tool(run, step)

        reason = self._out_of_time(run)
        if reason is not None:
            return self._exhausted(run, reason)
        try:
            conclusion = self._conclude(run, group, verdict)
        except LLMError as error:
            return self._exhausted(run, f"the model could not be reached: {error}")
        if conclusion is None:
            return self._exhausted(run, "the model gave no usable conclusion")
        return run.finish(
            InvestigationStatus.CONCLUDED, self._clock(), conclusion=conclusion, partial=None
        )

    # -- the search loop -------------------------------------------------------------------

    def _out_of_budget(self, run: _Run) -> str | None:
        # One call is kept for the final answer.
        if run.llm_calls >= self._budget.max_llm_calls - 1:
            return "the model call limit was reached"
        return self._out_of_time(run)

    def _out_of_time(self, run: _Run) -> str | None:
        if self._clock() - run.started >= self._budget.max_seconds:
            return "the time limit was reached"
        return None

    def _ask_step(self, run: _Run, prompt: Prompt, group: SignalGroup, verdict: Verdict) -> Step:
        nodes = frontier(self._lineage, group.root_node, run.refuted)
        request = LLMRequest(
            prompt=prompt.render(
                summary=verdict.summary,
                root_node=group.root_node or "none found",
                rule_class=verdict.fault_class,
                tools=json.dumps(self._tools.specifications(), separators=(",", ":")),
                frontier="\n".join(f"- {node} (distance {d})" for node, d in nodes) or "none",
                evidence=_evidence_for_step(run.evidence),
                calls_left=self._budget.max_llm_calls - run.llm_calls,
            ),
            tier=ModelTier.STANDARD,
        )
        result = self._client.complete_structured(request, Step)
        run.record_call(prompt, result.completions)
        return result.parsed

    def _apply(self, run: _Run, step: Step) -> None:
        """Record what the model said the latest evidence meant, ignoring what the graph denies."""
        known = set(self._lineage.node_ids)
        if step.leading_node is not None and step.leading_node in known:
            run.leading = Hypothesis(
                node=step.leading_node,
                fault_class="unknown",
                explanation=step.reason or "Leading node at the end of the search.",
                confidence=step.confidence,
            )
        elif step.leading_node is not None:
            run.notes.append(f"Ignored a leading node the graph does not have: {step.leading_node}")
        assessment = step.assessment
        if assessment is None:
            return
        index = next(
            (i for i, e in enumerate(run.evidence) if e.id == assessment.evidence_id), None
        )
        if index is None or assessment.node not in known:
            run.notes.append("Ignored an assessment of unknown evidence or an unknown node")
            return
        run.evidence[index] = replace(
            run.evidence[index], node=assessment.node, verdict=assessment.verdict
        )
        if assessment.verdict == "refutes":
            run.refuted.add(assessment.node)

    def _is_done(self, run: _Run, step: Step) -> bool:
        if step.action == "conclude":
            return True
        leader = run.leading
        return (
            leader is not None
            and leader.confidence >= self._budget.confidence_threshold
            and _supporting(run.evidence, leader.node) >= MIN_SUPPORTING_FINDINGS
        )

    def _run_step_tool(self, run: _Run, step: Step) -> None:
        if step.tool is None:
            run.notes.append("The model chose to call a tool but named none")
            return
        if any(e.tool == step.tool and e.arguments == step.arguments for e in run.evidence):
            run.notes.append(f"Skipped a repeat of {step.tool}")
            return
        self._call_tool(run, step.tool, step.arguments)

    def _call_tool(self, run: _Run, tool: str, arguments: dict[str, object]) -> None:
        result = self._tools.call(tool, arguments)
        run.evidence.append(
            Evidence(
                id=len(run.evidence) + 1,
                tool=tool,
                arguments=dict(arguments),
                ok=result.ok,
                content=result.content,
                payload=result.payload,
            )
        )

    # -- the conclusion --------------------------------------------------------------------

    def _conclude(self, run: _Run, group: SignalGroup, verdict: Verdict) -> Conclusion | None:
        prompt = self._prompts.get(AGENT, "conclude")
        problem = ""
        # The answer may be corrected once, which is the only way to exceed the call limit.
        for _ in range(2):
            request = LLMRequest(
                prompt=prompt.render(
                    summary=verdict.summary,
                    root_node=group.root_node or "none found",
                    rule_class=verdict.fault_class,
                    evidence=_evidence_for_conclusion(run.evidence),
                    nodes="\n".join(self._candidate_nodes(group)),
                )
                + (f"\n\nYour previous answer was rejected: {problem}" if problem else ""),
                tier=ModelTier.STANDARD,
            )
            result = self._client.complete_structured(request, Conclusion)
            run.record_call(prompt, result.completions)
            checked = self._check(run, result.parsed)
            if isinstance(checked, Conclusion):
                return checked
            problem = checked
        return None

    def _candidate_nodes(self, group: SignalGroup) -> list[str]:
        return [node for node, _ in frontier(self._lineage, group.root_node, set())]

    def _check(self, run: _Run, conclusion: Conclusion) -> Conclusion | str:
        """Hold the model's answer to the graph and the evidence, correcting what is safe to."""
        known = set(self._lineage.node_ids)
        if conclusion.root_cause_node not in known:
            return f"{conclusion.root_cause_node!r} is not a node in the lineage graph."
        valid_ids = {e.id for e in run.evidence}
        cited = [i for i in conclusion.evidence_ids if i in valid_ids]
        if len(cited) != len(conclusion.evidence_ids):
            run.notes.append("Dropped evidence ids that do not exist")
        confidence = conclusion.confidence
        if not cited:
            confidence = min(confidence, NO_EVIDENCE_CONFIDENCE)
            run.notes.append("Capped confidence: the conclusion cites no evidence")
        elif _supporting(run.evidence, conclusion.root_cause_node) < MIN_SUPPORTING_FINDINGS:
            confidence = min(confidence, THIN_EVIDENCE_CONFIDENCE)
            run.notes.append("Capped confidence: fewer than two findings support the node")
        location = conclusion.fix_location
        if conclusion.fault_class == "late_data" and location != "external":
            location = "external"
            run.notes.append("Set the fix location to external: no dbt change brings back a file")
        alternatives = [a for a in conclusion.alternatives if a.node in known]
        return conclusion.model_copy(
            update={
                "evidence_ids": cited,
                "confidence": confidence,
                "fix_location": location,
                "alternatives": alternatives,
            }
        )

    def _exhausted(self, run: _Run, reason: str) -> Investigation:
        run.notes.append(f"Stopped early: {reason}")
        return run.finish(
            InvestigationStatus.BUDGET_EXHAUSTED,
            self._clock(),
            conclusion=None,
            partial=run.leading,
        )


class _Run:
    """The mutable state of one investigation."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.evidence: list[Evidence] = []
        self.refuted: set[str] = set()
        self.leading: Hypothesis | None = None
        self.llm_calls = 0
        self.notes: list[str] = []
        self.prompt_ids: list[str] = []
        self.model_ids: dict[str, str] = {}

    def record_call(self, prompt: Prompt, completions: tuple[Completion, ...]) -> None:
        # A reply that failed validation is asked for again, and that is a second call.
        self.llm_calls += len(completions)
        if prompt.id not in self.prompt_ids:
            self.prompt_ids.append(prompt.id)
        self.model_ids[prompt.id] = completions[-1].model

    def finish(
        self,
        status: InvestigationStatus,
        now: float,
        *,
        conclusion: Conclusion | None,
        partial: Hypothesis | None,
    ) -> Investigation:
        return Investigation(
            status=status,
            conclusion=conclusion,
            partial=partial,
            evidence=self.evidence,
            llm_calls=self.llm_calls,
            elapsed_seconds=round(now - self.started, 3),
            prompt_ids=self.prompt_ids,
            model_ids=self.model_ids,
            notes=self.notes,
        )


def _supporting(evidence: list[Evidence], node: str) -> int:
    return sum(1 for e in evidence if e.node == node and e.verdict == "supports" and e.ok)


ARGUMENT_CHARACTERS = 300


def _label(e: Evidence) -> str:
    """The evidence's number, tool and verdict, plus its arguments as data.

    The arguments can carry feed text (the seed's search query holds the incident summary) and
    a model's own arguments are untrusted too, so they are delimited like any other data.
    """
    where = f" about {e.node}: {e.verdict}" if e.node and e.verdict else ""
    arguments = json.dumps(e.arguments, separators=(",", ":"), default=str)
    return f"#{e.id} {e.tool}{where}\n{untrusted(arguments, 'arguments', ARGUMENT_CHARACTERS)}"


def _short(e: Evidence, limit: int) -> str:
    body = json.dumps(e.payload, default=str, ensure_ascii=False, separators=(",", ":"))
    return f"{_label(e)}\n{untrusted(body, e.tool, limit)}"


def _evidence_for_step(evidence: list[Evidence]) -> str:
    if not evidence:
        return "none yet"
    *older, newest = evidence
    parts = [_short(e, OLDER_EVIDENCE_CHARACTERS) for e in older]
    parts.append(f"{_label(newest)}\n{newest.content}")
    return "\n".join(parts)


def _evidence_for_conclusion(evidence: list[Evidence]) -> str:
    return "\n".join(_short(e, CONCLUSION_EVIDENCE_CHARACTERS) for e in evidence) or "none"
