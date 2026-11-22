"""The Fixer: proposes a checked patch for an incident whose root cause is in dbt (FR-FIX-01).

It shows a model the dbt files nearest the root cause and asks for the complete new content of
those it changes. A model can only change a file it was shown, and every path and size is
checked by `build_patch` before the result is returned, so what comes out is something the
sandbox may safely apply. An answer the checks refuse is sent back once with the reason. Running
the patch is the sandbox's job (LH-053), and storing it is `FixRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from lineageheal.agents.fixer.models import PatchProposal
from lineageheal.agents.fixer.patch import (
    DEFAULT_LIMITS,
    MODELS_DIR,
    Patch,
    PatchError,
    PatchLimits,
    build_patch,
)
from lineageheal.agents.prompt_registry import PromptRegistry, untrusted
from lineageheal.lineage.service import LineageService, UnknownNodeError
from lineageheal.llm.client import LLMClient
from lineageheal.llm.types import LLMError, LLMRequest, ModelTier
from lineageheal.store.incidents import StoredIncident

log = structlog.get_logger(__name__)

AGENT = "fixer"
PURPOSE = "patch"
MAX_CONTEXT_FILES = 6
FILE_TAG = "file"


class FixerStatus(StrEnum):
    """What the Fixer did for an incident."""

    PATCHED = "patched"
    NOT_APPLICABLE = "not_applicable"  # no dbt root cause to fix
    DECLINED = "declined"  # the model said the files shown cannot fix it
    REJECTED = "rejected"  # every answer failed the checks
    UNAVAILABLE = "unavailable"  # the model could not be reached


@dataclass(slots=True)
class FixerResult:
    """The outcome of one proposal, with what it cost."""

    status: FixerStatus
    reason: str = ""
    patch: Patch | None = None
    rationale: str = ""
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    model_ids: dict[str, str] = field(default_factory=dict)


class Fixer:
    """Proposes patches for incidents whose root cause the Investigator placed in dbt."""

    def __init__(
        self,
        client: LLMClient,
        lineage: LineageService,
        project_root: Path,
        *,
        limits: PatchLimits = DEFAULT_LIMITS,
        max_llm_calls: int = 2,
        prompts: PromptRegistry | None = None,
    ) -> None:
        """Create a fixer.

        Args:
            client: The LLM client.
            lineage: The lineage graph the incident was diagnosed against.
            project_root: The repository root; patch paths are relative to it.
            limits: The patch size limits.
            max_llm_calls: The first answer plus corrections for an answer the checks refuse.
            prompts: The prompt registry; the project's own when omitted.
        """
        self._client = client
        self._lineage = lineage
        self._root = project_root
        self._limits = limits
        self._max_calls = max_llm_calls
        self._prompts = prompts or PromptRegistry()

    def propose(self, incident: StoredIncident, feedback: str | None = None) -> FixerResult:
        """Propose a patch for an incident.

        Args:
            incident: An incident the Investigator has concluded on.
            feedback: Why an earlier patch failed in the sandbox, for a revised attempt.

        Returns:
            A result whose status says whether a checked patch came out.

        Raises:
            PromptError: The prompt is missing or malformed, which is a bug in the project.
        """
        cause = incident.root_cause or {}
        if cause.get("partial", True) or not cause.get("node"):
            return FixerResult(FixerStatus.NOT_APPLICABLE, "the incident has no concluded cause")
        if cause.get("fix_location") != "dbt":
            return FixerResult(
                FixerStatus.NOT_APPLICABLE, "the root cause is outside the dbt project"
            )
        shown = self._files_to_show(incident, cause["node"])
        if not shown:
            return FixerResult(FixerStatus.NOT_APPLICABLE, "no dbt file could be found to change")

        prompt = self._prompts.get(AGENT, PURPOSE)
        result = FixerResult(FixerStatus.REJECTED)
        problem = feedback
        for _ in range(self._max_calls):
            request = LLMRequest(
                prompt=prompt.render(
                    summary=incident.summary,
                    root_node=cause["node"],
                    explanation=cause.get("explanation") or "",
                    fix_reason=cause.get("fix_reason") or "",
                    files="\n".join(
                        f"{path}\n{untrusted(text, FILE_TAG, len(text))}"
                        for path, text in shown.items()
                    ),
                    feedback=_feedback(problem),
                    max_files=self._limits.max_files,
                    max_lines=self._limits.max_changed_lines,
                ),
                tier=ModelTier.STANDARD,
            )
            try:
                answer = self._client.complete_structured(request, PatchProposal)
            except LLMError as error:
                log.warning("fixer_model_failed", error=type(error).__name__)
                result.status = FixerStatus.UNAVAILABLE
                result.reason = f"the model could not be reached: {error}"
                return result
            result.llm_calls += len(answer.completions)
            result.input_tokens += sum(c.input_tokens for c in answer.completions)
            result.output_tokens += sum(c.output_tokens for c in answer.completions)
            result.prompt_hashes[prompt.id] = prompt.sha256
            result.model_ids[prompt.id] = answer.completions[-1].model
            proposal = answer.parsed
            if proposal.declined_reason and not proposal.edits:
                result.status = FixerStatus.DECLINED
                result.reason = proposal.declined_reason
                return result
            try:
                result.patch = self._check(proposal, shown)
            except PatchError as error:
                problem = f"your previous answer was rejected: {error}"
                result.reason = str(error)
                continue
            result.status = FixerStatus.PATCHED
            result.rationale = proposal.rationale
            result.reason = ""
            return result
        return result

    def _check(self, proposal: PatchProposal, shown: dict[str, str]) -> Patch:
        edits = [(edit.path, edit.new_content) for edit in proposal.edits]
        for path, _ in edits:
            if path not in shown:
                raise PatchError(f"{path!r} was not one of the files shown")
        return build_patch(self._root, edits, self._limits)

    def _files_to_show(self, incident: StoredIncident, root_node: str) -> dict[str, str]:
        """The dbt files nearest the root cause: its own model, then the ones it feeds.

        A file longer than the limit is left out rather than shown cut short, because the model
        returns whole files and would otherwise delete the part it never saw.
        """
        affected = set(incident.affected.get("models", ()))
        tables = [_table_of(root_node)]
        try:
            tables += [_table_of(r.node) for r in self._lineage.downstream(root_node).reached]
        except UnknownNodeError:  # show only what the root cause itself names
            log.warning("fixer_root_node_not_in_graph", node=root_node)
        wanted: list[str] = []
        for table in tables:
            if table not in wanted and (table == tables[0] or table in affected):
                wanted.append(table)

        models = self._root / MODELS_DIR
        sql_files = {p.stem: p for p in sorted(models.rglob("*.sql"))} if models.is_dir() else {}
        paths: list[Path] = []
        for table in wanted:
            sql = sql_files.get(table)
            if sql is not None:
                paths.append(sql)
                if len(paths) == 1:
                    paths += sorted(sql.parent.glob("*.yml"))
            if len(paths) >= MAX_CONTEXT_FILES:
                break
        shown: dict[str, str] = {}
        for path in paths[:MAX_CONTEXT_FILES]:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if len(text) <= self._limits.max_file_characters:
                shown[path.relative_to(self._root).as_posix()] = text
        return shown


def _table_of(node: str) -> str:
    return node.rsplit(".", 1)[0]


def _feedback(problem: str | None) -> str:
    if not problem:
        return ""
    return f"\nA previous attempt failed: {untrusted(problem, 'feedback', 1500)}\nFix that.\n"
