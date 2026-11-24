"""What both baselines read from a scenario: its manifest and its most serious signal.

Neither baseline sees the lineage graph, the raw files or any tool (design 12.3). They get what
an engineer would have in front of them after a failed run: the failing check and the dbt
project's own description of itself.
"""

from __future__ import annotations

import re
from typing import Any

from lineageheal.benchmark.models import Scenario
from lineageheal.core.errors import LineageHealError
from lineageheal.core.signals import Signal
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.orchestration.run_artifacts import load_manifest

# `raw.<source>.<dataset>.<column>` is the form a root cause must take to be scored.
NODE_FORM = re.compile(r"^raw\.[a-z0-9_]+\.[a-z0-9_]+\.(\*|[^.]+)$")


def first_signal(scenario: Scenario) -> Signal | None:
    """The signal a person would look at first: the summary lists errors before warnings."""
    signals = scenario.summary.signals
    return signals[0] if signals else None


def read_manifest(scenario: Scenario) -> dict[str, Any] | None:
    """The sandbox run's dbt manifest, or None when dbt wrote none (it failed before that)."""
    try:
        return load_manifest(LocalObjectStore(scenario.raw_zone), scenario.run_id)
    except LineageHealError:
        return None
