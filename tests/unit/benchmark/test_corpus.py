"""FR-BEN-01 and FR-BEN-07: the committed corpus is about 100 valid, balanced, split cases."""

from datetime import date
from pathlib import Path

import pytest

from lineageheal.benchmark.corpus import FAULT_CLASSES, summarise, validate_corpus
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

    assert summary.total == 100
    assert summary.by_split == {"seed": 30, "test": 70}
    assert summary.by_source == {"amfi": 50, "nse": 50}


def test_every_class_has_the_same_number_of_cases_in_each_split(cases: list[FaultCase]) -> None:
    summary = summarise(cases)

    for fault_class in FAULT_CLASSES:
        assert summary.by_class_and_split[(fault_class, "seed")] == 6
        assert summary.by_class_and_split[(fault_class, "test")] == 14


def test_every_date_is_from_2024_and_clear_of_the_held_out_july_window(
    cases: list[FaultCase],
) -> None:
    assert all(c.trade_date >= date(2024, 1, 1) for c in cases)
    assert not [c.id for c in cases if JULY_2024[0] <= c.trade_date <= JULY_2024[1]]


def test_late_data_is_half_withheld_and_half_stale(cases: list[FaultCase]) -> None:
    kinds = [c.injection.type for c in cases if c.fault_class == "late_data"]

    assert kinds.count("withhold_file") == kinds.count("stale_date") == 10


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
