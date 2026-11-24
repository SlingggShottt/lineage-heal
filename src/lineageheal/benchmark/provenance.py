"""What a benchmark run is, recorded so it can be reproduced (NFR-REPRO-01).

The manifest names the dependency lockfile hash, the model ids, the prompt versions, the seeds
and the corpus the run used. A run resumed later keeps its original manifest, so results from
two different code states are never mixed under one run id without it being visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lineageheal.agents.prompt_registry import PromptRegistry
from lineageheal.benchmark.results import lockfile_hash
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.settings import Settings


def build_manifest(
    settings: Settings,
    repo_root: Path,
    run_id: str,
    cases: Sequence[FaultCase],
    systems: Sequence[str],
) -> dict[str, Any]:
    """Describe a run: code, models, prompts, seeds and the faults it covers."""
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC),
        "lockfile_sha256": lockfile_hash(repo_root / "uv.lock"),
        "models": {
            "gemini": settings.gemini_model,
            "gemini_light": settings.gemini_light_model,
            "groq": settings.groq_model,
            "embedding": settings.embedding_model,
        },
        "temperature": 0,
        "prompts": PromptRegistry().ids(),
        "systems": list(systems),
        "cases": [c.id for c in cases],
        "seeds": {c.id: c.seed for c in cases},
    }
