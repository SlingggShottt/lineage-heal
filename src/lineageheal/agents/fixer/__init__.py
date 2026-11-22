"""The Fixer: turns a dbt root cause into a checked patch (FR-FIX-01)."""

from lineageheal.agents.fixer.fixer import Fixer, FixerResult, FixerStatus
from lineageheal.agents.fixer.models import FileEdit, PatchProposal
from lineageheal.agents.fixer.patch import Patch, PatchError, PatchLimits, build_patch, check_path

__all__ = [
    "FileEdit",
    "Fixer",
    "FixerResult",
    "FixerStatus",
    "Patch",
    "PatchError",
    "PatchLimits",
    "PatchProposal",
    "build_patch",
    "check_path",
]
