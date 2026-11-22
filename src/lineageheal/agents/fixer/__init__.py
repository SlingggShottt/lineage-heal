"""The Fixer: turns a dbt root cause into a patch checked in a sandbox (FR-FIX-01, FR-FIX-02)."""

from lineageheal.agents.fixer.fixer import Fixer, FixerResult, FixerStatus
from lineageheal.agents.fixer.models import FileEdit, PatchProposal
from lineageheal.agents.fixer.patch import Patch, PatchError, PatchLimits, build_patch, check_path
from lineageheal.agents.fixer.sandbox import Sandbox, SandboxError, SandboxResult, compare
from lineageheal.agents.fixer.suggestion import Suggester, Suggestion, SuggestionStatus
from lineageheal.agents.fixer.verify import (
    VerificationOutcome,
    VerificationStatus,
    verify_incident,
)

__all__ = [
    "FileEdit",
    "Fixer",
    "FixerResult",
    "FixerStatus",
    "Patch",
    "PatchError",
    "PatchLimits",
    "PatchProposal",
    "Sandbox",
    "SandboxError",
    "SandboxResult",
    "Suggester",
    "Suggestion",
    "SuggestionStatus",
    "VerificationOutcome",
    "VerificationStatus",
    "build_patch",
    "check_path",
    "compare",
    "verify_incident",
]
