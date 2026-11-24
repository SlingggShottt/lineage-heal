"""Checks on the benchmark corpus as a whole (FR-BEN-01, FR-BEN-07).

`FaultCase` validates one specification. What can only be wrong across all of them lives here: a
class with no seed cases, two cases that damage the same file the same way, or a date on which the
feed publishes nothing, which would make the fault untestable. Reporting is limited to the test
split, so the split sizes are checked too.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.calendar import CalendarEntry, expectation

# FR-BEN-01 asks for about 100; the band allows a case to be added or dropped without a rewrite.
MIN_CASES = 90
MAX_CASES = 110
FAULT_CLASSES = ("schema_drift", "nulls", "duplicates", "unit_change", "late_data")


@dataclass(frozen=True)
class CorpusSummary:
    """Counts of a corpus, for `bench list` and reports."""

    total: int
    by_split: dict[str, int]
    by_class_and_split: dict[tuple[str, str], int]
    by_source: dict[str, int]


def summarise(cases: Sequence[FaultCase]) -> CorpusSummary:
    """Count cases by split, class and source."""
    return CorpusSummary(
        total=len(cases),
        by_split=dict(Counter(c.split for c in cases)),
        by_class_and_split=dict(Counter((c.fault_class, c.split) for c in cases)),
        by_source=dict(Counter(c.source for c in cases)),
    )


def validate_corpus(cases: Sequence[FaultCase], calendar: Sequence[CalendarEntry]) -> CorpusSummary:
    """Check the corpus as a whole and return its summary.

    Args:
        cases: Every specification in the corpus.
        calendar: The NSE calendar exceptions, to tell a publishing day from a holiday.

    Raises:
        BenchmarkError: The corpus is the wrong size, a class is missing from a split, a case
            has no file to damage on its date, or two cases are the same fault.
    """
    problems: list[str] = []
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        problems.append(f"{len(cases)} cases; about 100 are required ({MIN_CASES}-{MAX_CASES})")
    summary = summarise(cases)
    for fault_class in FAULT_CLASSES:
        for split in ("seed", "test"):
            if summary.by_class_and_split.get((fault_class, split), 0) == 0:
                problems.append(f"no {fault_class} case in the {split} split")
    seen: dict[tuple[object, ...], str] = {}
    for case in cases:
        _, expected = expectation(case.source, case.trade_date, calendar)
        if case.source in ("amfi", "nse") and not expected:
            problems.append(f"{case.id}: {case.source} publishes no file on {case.trade_date}")
        key = (case.source, case.dataset, case.trade_date, case.injection.model_dump_json())
        if key in seen:
            problems.append(f"{case.id}: same fault on the same file as {seen[key]}")
        seen[key] = case.id
    if problems:
        raise BenchmarkError("Corpus problems: " + "; ".join(problems))
    return summary
