"""The shape of an Investigator tool, and the registry that runs them safely (FR-INV-02).

The model never chooses what a tool is, only which of the registered tools to call and with what
arguments, and both are checked here before anything runs. Every tool reads and none writes.
What comes back is bounded and delimited (NFR-SEC-04): rows are capped by each tool, the whole
output is cut at a fixed size, and it is wrapped in a `<data>` tag the content cannot close, so
a scheme name or a log line that says "ignore your instructions" stays data.

A tool that cannot answer (an unknown node, no database) returns an error result instead of
raising, so the agent can read it and try something else; a bug in a tool still raises.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from lineageheal.agents.prompt_registry import untrusted
from lineageheal.core.errors import LineageHealError
from lineageheal.core.signals import RunSummary
from lineageheal.ingestion.calendar import DEFAULT_SEED
from lineageheal.lineage.service import LineageService
from lineageheal.rag.retriever import Retriever
from lineageheal.store.ingestion_log import IngestionLogRepository

# Rows a tool may return. Enough to see a pattern, too few to flood a prompt.
MAX_ROWS = 20
# The whole output of one call, after delimiting; a tool that stays within its row limits never
# reaches it, so hitting it is a sign the tool needs a tighter bound.
MAX_OUTPUT_CHARACTERS = 6000
# A single text field (SQL, a log message) inside an output.
MAX_FIELD_CHARACTERS = 1500

Payload = dict[str, Any]


class ToolError(LineageHealError):
    """A tool could not answer; the message is meant for the agent to read and act on."""


class ToolArguments(BaseModel):
    """Base for a tool's arguments. Unknown fields are refused, never ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ToolContext:
    """Everything the tools may read for one investigation, all of it read-only.

    Optional parts are `None` when the run has no database or index; a tool that needs one says
    so instead of failing.
    """

    lineage: LineageService
    manifest: Mapping[str, Any]
    summary: RunSummary
    ingestion_log: IngestionLogRepository | None = None
    retriever: Retriever | None = None
    calendar_seed: Path = DEFAULT_SEED


@dataclass(frozen=True)
class Tool[A: ToolArguments]:
    """A named, described, typed read-only operation."""

    name: str
    description: str
    arguments: type[A]
    handler: Callable[[ToolContext, A], Payload]


@dataclass(frozen=True)
class ToolResult:
    """What one call produced.

    `content` is what the model reads: bounded and delimited. `payload` is the same facts as
    data, for storing as evidence.
    """

    tool: str
    ok: bool
    content: str
    payload: Payload = field(default_factory=dict)


class ToolRegistry:
    """The tools an investigation may call, bound to the data they read."""

    def __init__(self, context: ToolContext, tools: Sequence[Tool[Any]]) -> None:
        """Register tools by name; two tools with one name are a programming error."""
        self._context = context
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Two tools are named {tool.name!r}")
            self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        """The registered tool names, in registration order."""
        return tuple(self._tools)

    def specifications(self) -> list[dict[str, Any]]:
        """Each tool as a name, a description and a JSON schema, for presenting to a model."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.arguments.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Run one tool with arguments the model supplied.

        Never raises for a bad call: an unknown tool, invalid arguments or a tool that cannot
        answer all come back as an error result the agent can read.
        """
        tool = self._tools.get(name)
        if tool is None:
            return _error("tool_error", f"No tool named that. Available: {', '.join(self._tools)}")
        try:
            parsed = tool.arguments.model_validate(dict(arguments))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
                for error in exc.errors(include_input=False, include_url=False)
            )
            return _error(tool.name, f"Invalid arguments: {problems}")
        try:
            payload = tool.handler(self._context, parsed)
        except LineageHealError as exc:
            return _error(tool.name, clip(str(exc), 500))
        text = json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))
        return ToolResult(
            tool.name, True, untrusted(text, tool.name, MAX_OUTPUT_CHARACTERS), payload
        )


def _error(tag: str, message: str) -> ToolResult:
    payload = {"error": message}
    return ToolResult(
        tag, False, untrusted(json.dumps(payload), tag, MAX_OUTPUT_CHARACTERS), payload
    )


def clip(text: str, limit: int = MAX_FIELD_CHARACTERS) -> str:
    """Cut a text field to `limit` characters, saying so, so a long value cannot take over."""
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def limited[T](items: Sequence[T], limit: int = MAX_ROWS) -> tuple[list[T], int]:
    """The first `limit` items and how many were left out."""
    return list(items[:limit]), max(len(items) - limit, 0)
