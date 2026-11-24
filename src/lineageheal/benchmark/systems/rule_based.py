"""The rule-based baseline (design 12.3): no model, no lineage graph, no tools.

It maps the most serious failing check to the nearest raw source of the failing model, using only
dbt's own model dependencies, and guesses the column by how much its name resembles the failing
column's. It decides where the fix belongs from the check type alone. It is the "what a good
rule of thumb gets you" line the LineageHeal agents must beat (PRD goal G4).
"""

from __future__ import annotations

import re
import time
from collections import deque
from typing import Any

from lineageheal.agents.detector.classify import rule_class
from lineageheal.benchmark.models import Diagnosis, Scenario
from lineageheal.benchmark.systems.common import first_signal, read_manifest

TOP_N = 3
_NOT_RAW = {"raw_zone"}  # the meta source that lists every file is not a feed
_WORD = re.compile(r"[a-z0-9]+|[A-Z][a-z0-9]*")


class RuleBasedSystem:
    """Nearest upstream source plus a column-name guess."""

    name = "rule-based"

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Name the likely raw column and where the fix belongs, from the manifest alone."""
        started = time.perf_counter()
        signal = first_signal(scenario)
        manifest = read_manifest(scenario)
        if signal is None or signal.model is None or manifest is None:
            return Diagnosis(
                seconds=time.perf_counter() - started,
                notes="nothing to go on: no failing model or no manifest",
            )
        late = rule_class((signal,))[0] == "late_data"
        found = nearest_source(manifest, signal.model)
        if found is None:
            return Diagnosis(
                fix_location="outside_dbt" if late else "dbt",
                seconds=time.perf_counter() - started,
                notes=f"{signal.model} has no raw source upstream",
            )
        node, columns = found
        causes = (
            (f"{node}.*",)
            if signal.column is None
            else tuple(f"{node}.{c}" for c in rank_columns(signal.column, columns)[:TOP_N])
        )
        return Diagnosis(
            root_causes=causes or (f"{node}.*",),
            fix_location="outside_dbt" if late else "dbt",
            seconds=time.perf_counter() - started,
            notes=f"nearest source {node} of {signal.model}",
        )


def nearest_source(manifest: dict[str, Any], model: str) -> tuple[str, list[str]] | None:
    """The raw source closest upstream of a model, as `raw.<source>.<dataset>` and its columns.

    Distance is the number of model hops; ties go to the alphabetically first source so the
    answer is stable.
    """
    nodes: dict[str, Any] = {**manifest.get("nodes", {}), **manifest.get("sources", {})}
    start = next(
        (
            uid
            for uid, n in nodes.items()
            if n.get("resource_type") == "model" and n["name"] == model
        ),
        None,
    )
    if start is None:
        return None
    best: dict[int, list[str]] = {}
    seen = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        uid, depth = queue.popleft()
        for parent in nodes[uid].get("depends_on", {}).get("nodes", []):
            if parent in seen or parent not in nodes:
                continue
            seen.add(parent)
            if nodes[parent].get("resource_type") == "source":
                if nodes[parent]["source_name"] not in _NOT_RAW:
                    best.setdefault(depth, []).append(parent)
            else:
                queue.append((parent, depth + 1))
    if not best:
        return None
    source = nodes[sorted(best[min(best)])[0]]
    feed = source["source_name"].removeprefix("raw_")
    return f"raw.{feed}.{source['name']}", list(source.get("columns", {}))


def rank_columns(column: str, candidates: list[str]) -> list[str]:
    """Candidate raw columns, the most similar to `column` first; ties keep declared order."""
    wanted = _tokens(column)
    scored = [(_similarity(wanted, _tokens(c)), -i, c) for i, c in enumerate(candidates)]
    return [c for _, _, c in sorted(scored, reverse=True)]


def _tokens(name: str) -> set[str]:
    """Lowercase words of a name, plus the initials of a multi-word name (Net Asset Value: nav)."""
    words = [w.lower() for w in _WORD.findall(name.replace("_", " "))]
    tokens = set(words)
    if len(words) > 1:
        tokens.add("".join(w[0] for w in words))
    return tokens


def _similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
