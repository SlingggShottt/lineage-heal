"""The rule-based baseline (design 12.3): no model, no lineage graph, no tools.

It maps the most serious failing check to the nearest raw source of the failing model, using only
dbt's own model dependencies, and guesses the column by how much its name resembles the failing
column's. It decides where the fix belongs from the check type alone, and has one template: when
the newest file's header differs from the one before by exactly one renamed column, the model
reads the new name too. Beyond the manifest that is the only thing it reads, two header lines.
It is the "what a good rule of thumb gets you" line the LineageHeal agents must beat (PRD G4).
"""

from __future__ import annotations

import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from lineageheal.agents.detector.classify import rule_class
from lineageheal.agents.fixer.patch import (
    DEFAULT_LIMITS,
    Patch,
    PatchError,
    PatchLimits,
    build_patch,
)
from lineageheal.benchmark.models import Diagnosis, PatchRecord, Scenario
from lineageheal.benchmark.systems.common import (
    REPO_ROOT,
    first_signal,
    model_file,
    read_manifest,
)

TOP_N = 3
_NOT_RAW = {"raw_zone"}  # the meta source that lists every file is not a feed
_WORD = re.compile(r"[a-z0-9]+|[A-Z][a-z0-9]*")


class RuleBasedSystem:
    """Nearest upstream source plus a column-name guess, and a template for one known pattern."""

    name = "rule-based"

    def __init__(
        self, *, repo_root: Path = REPO_ROOT, limits: PatchLimits = DEFAULT_LIMITS
    ) -> None:
        """Create the baseline.

        Args:
            repo_root: The repository holding the dbt project, for the model file it patches.
            limits: The patch size limits, the same ones the Fixer is held to.
        """
        self._root = repo_root
        self._limits = limits

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
        patch = None if late else self._renamed_column_patch(scenario, manifest, node)
        return Diagnosis(
            root_causes=causes or (f"{node}.*",),
            fix_location="outside_dbt" if late else "dbt",
            patch=PatchRecord.of(patch[0]) if patch else None,
            patch_attempts=1 if patch else 0,
            seconds=time.perf_counter() - started,
            notes=f"nearest source {node} of {signal.model}" + (f"; {patch[1]}" if patch else ""),
        )

    def _renamed_column_patch(
        self, scenario: Scenario, manifest: dict[str, Any], node: str
    ) -> tuple[Patch, str] | None:
        """The one template this baseline has: a column the publisher renamed.

        The rule is the one an engineer would script: compare the newest file's header with the
        one before it, and if exactly one column disappeared and exactly one appeared, the model
        reads the new name as well as the old (older files keep the old layout). Anything else
        gets no patch.
        """
        _, feed, dataset = node.split(".", 2)
        headers = newest_headers(scenario.raw_zone, feed, dataset)
        if headers is None:
            return None
        previous, newest = headers
        removed, added = (
            [c for c in previous if c not in newest],
            [c for c in newest if c not in previous],
        )
        if len(removed) != 1 or len(added) != 1:
            return None
        readers = models_reading(manifest, feed, dataset)
        path = model_file(manifest, readers[0]) if len(readers) == 1 else None
        if path is None:
            return None
        old, new = removed[0], added[0]
        root = scenario.repo_root or self._root
        try:
            text = (root / path).read_text(encoding="utf-8")
            if f'"{old}"' not in text:
                return None
            patched = text.replace(f'"{old}"', f'coalesce("{new}", "{old}")')
            return build_patch(root, [(path, patched)], self._limits), f"{old} became {new}"
        except (OSError, PatchError):
            return None


def models_reading(manifest: dict[str, Any], feed: str, dataset: str) -> list[str]:
    """The models that read a raw source directly, by name, sorted."""
    uid = next(
        (
            u
            for u, n in manifest.get("sources", {}).items()
            if n.get("source_name") == f"raw_{feed}" and n.get("name") == dataset
        ),
        None,
    )
    return sorted(
        n["name"]
        for n in manifest.get("nodes", {}).values()
        if n.get("resource_type") == "model" and uid in n.get("depends_on", {}).get("nodes", [])
    )


def newest_headers(raw_zone: Path, feed: str, dataset: str) -> tuple[list[str], list[str]] | None:
    """The column names of the second newest and the newest stored file of a dataset.

    Archived feeds are read from their staged copy, which is the same file unzipped. None when
    fewer than two dates have a file.
    """
    area = "staged" if feed == "nse" else "raw"
    separator = ";" if feed == "amfi" else ","
    folders = sorted(
        (raw_zone / area / f"source={feed}" / f"dataset={dataset}").glob("trade_date=*")
    )
    headers: list[list[str]] = []
    for folder in folders:
        files = sorted(p for p in folder.iterdir() if p.is_file())
        if files:
            with files[-1].open(encoding="utf-8") as handle:
                headers.append(handle.readline().rstrip("\r\n").split(separator))
    return (headers[-2], headers[-1]) if len(headers) >= 2 else None


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
