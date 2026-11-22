"""What a model is asked to return when it proposes a fix.

The model returns the complete new content of each file it changes rather than a unified diff:
models often get a diff's line numbers and context wrong, and a malformed diff would waste a
sandbox attempt. The diff is computed from the files instead (D-54).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileEdit(BaseModel):
    """One file the fix changes."""

    path: str = Field(
        max_length=300, description="Repository-relative path of a file you were shown."
    )
    new_content: str = Field(description="The complete new content of the file.")


class PatchProposal(BaseModel):
    """A proposed fix, or a reason the model could not make one."""

    edits: list[FileEdit] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=800, description="What the change does and why.")
    declined_reason: str | None = Field(
        default=None,
        max_length=400,
        description="Set instead of edits when the files shown cannot fix the problem.",
    )
