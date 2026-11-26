"""FR-BEN-01 and FR-BEN-07: the committed corpus is about 100 valid, balanced, split cases."""

from datetime import date
from pathlib import Path

import pytest

from lineageheal.benchmark.corpus import FAULT_CLASSES, validate_corpus
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.calendar import DEFAULT_SEED, read_seed

CORPUS = Path(__file__).parents[3] / "benchmark" / "cases"
CALENDAR = read_seed(DEFAULT_SEED)
JULY_2024 = (date(2024, 7, 1), date(2024, 7, 15))


@pytest.fixture(scope="module")
def cases() -> list[FaultCase]:
    return load_cases(CORPUS)


def test_the_committed_corpus_is_valid(cases: list[FaultCase]) -> None:
    summary = validate_corpus(cases, CALENDAR)

    assert summary.total == 98
    assert summary.by_split == {"seed": 28, "test": 70}
    assert summary.by_source == {"amfi": 35, "nse": 35, "fbil": 28}


def test_every_feed_covers_every_class_it_can_be_tested_on_with_two_seed_cases_each(
    cases: list[FaultCase],
) -> None:
    by_feed = {
        feed: {c.fault_class for c in cases if c.source == feed} for feed in ("amfi", "nse", "fbil")
    }

    assert by_feed["amfi"] == by_feed["nse"] == set(FAULT_CLASSES)
    # FBIL has no FX plausibility check (LH-087) or calendar (LH-108), so no system could see a
    # scale change or a missing file there.
    assert by_feed["fbil"] == set(FAULT_CLASSES) - {"unit_change"}
    for feed, classes in by_feed.items():
        for fault_class in classes:
            of = [c for c in cases if c.source == feed and c.fault_class == fault_class]
            assert [c.split for c in of].count("seed") == 2, (feed, fault_class)
            assert [c.split for c in of].count("test") == 5, (feed, fault_class)


def test_every_date_is_from_2024_and_clear_of_the_held_out_july_window(
    cases: list[FaultCase],
) -> None:
    assert all(c.trade_date >= date(2024, 1, 1) for c in cases)
    assert not [c.id for c in cases if JULY_2024[0] <= c.trade_date <= JULY_2024[1]]


def test_late_data_withholds_files_only_where_a_missing_file_can_be_seen(
    cases: list[FaultCase],
) -> None:
    late = [c for c in cases if c.fault_class == "late_data"]

    assert {c.source for c in late if c.injection.type == "withhold_file"} == {"amfi", "nse"}
    assert {c.injection.type for c in late if c.source == "fbil"} == {"stale_date"}


def test_the_same_corpus_is_what_the_generator_writes(cases: list[FaultCase]) -> None:
    import importlib.util

    path = Path(__file__).parents[3] / "scripts" / "make_corpus.py"
    spec = importlib.util.spec_from_file_location("make_corpus", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.build()

    assert {c.id for c in cases} == set(generated)
    assert {c.id: c.trade_date for c in cases} == {k: v["trade_date"] for k, v in generated.items()}


def test_a_corpus_that_is_too_small_or_missing_a_class_is_refused(cases: list[FaultCase]) -> None:
    few = [c for c in cases if c.fault_class != "nulls"][:50]

    with pytest.raises(BenchmarkError, match=r"50 cases.*no nulls case in the seed split"):
        validate_corpus(few, CALENDAR)


def test_a_case_on_a_holiday_or_a_repeated_fault_is_refused(cases: list[FaultCase]) -> None:
    nse = next(c for c in cases if c.source == "nse")
    holiday = nse.model_copy(update={"id": "nse-holiday-001", "trade_date": date(2024, 1, 26)})
    clone = cases[0].model_copy(update={"id": "amfi-clone-001"})

    with pytest.raises(BenchmarkError, match="nse-holiday-001: nse publishes no file"):
        validate_corpus([*cases, holiday], CALENDAR)
    with pytest.raises(BenchmarkError, match="amfi-clone-001: same fault on the same file as"):
        validate_corpus([*cases, clone], CALENDAR)
