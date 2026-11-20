"""The Investigator's read-only tools (FR-INV-02) and the registry that runs them."""

from lineageheal.agents.tools.base import (
    Tool,
    ToolArguments,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
)
from lineageheal.agents.tools.run_tools import RUN_TOOLS
from lineageheal.agents.tools.source_tools import SOURCE_TOOLS


def build_registry(context: ToolContext) -> ToolRegistry:
    """The tools available for one investigation, reading from `context`."""
    return ToolRegistry(context, (*RUN_TOOLS, *SOURCE_TOOLS))


__all__ = [
    "Tool",
    "ToolArguments",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
]
