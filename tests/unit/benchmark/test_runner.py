"""FR-BEN-03 and FR-BEN-06: identical scenarios, isolated data, per-fault resumable results."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from lineageheal.benchmark.models import Diagnosis, Scenario
from lineageheal.benchmark.results import ResultStore
from lineageheal.benchmark.runner import BenchmarkRunner, NoneSystem
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

FIXTURES = Path(__file__).parents[2] / "fixtures"
REPORT = (FIXTURES / "amfi" / "navall_history_2026-10-15_sample.txt").read_bytes()
FAULT_DAY = date(2026, 10, 15)


def amfi_case(
    case_id: str = "amfi-nulls-001", injection: dict[str, Any] | None = None
) -> FaultCase:
    late = injection is not None and injection["type"] == "withhold_file"
    return FaultCase.model_validate(
        {
            "id": case_id,
            "class": "late_data" if late else "nulls",
            "source": "amfi",
            "dataset": "navall",
            "trade_date": FAULT_DAY.isoformat(),
            "split": "test",
            "seed": 1,
            "injection": injection or {"type": "blank_value", "column": "Net Asset Value"},
            "expected": {
                "root_cause": "raw.amfi.navall.Net Asset Value"
                if not late
                else "raw.amfi.navall.*",
                "fix_location": "outside_dbt" if late else "dbt",
            },
        }
    )


def seed_source(root: Path, days: int = 12) -> LocalObjectStore:
    store = LocalObjectStore(root)
    zone = RawZone(store)
    for offset in range(days):
        zone.put_if_absent(
            source="amfi",
            dataset="navall",
            trade_date=FAULT_DAY - timedelta(days=offset),
            filename="NAVAll.txt",
            content=REPORT + f"\r\n# day {offset}".encode(),
        )
    return store


def no_dbt(project: Path, data: Path, raw: Path, target: Path, timeout: float) -> tuple[int, str]:
    return 1, "fake dbt wrote nothing"


class Recorder:
    """A system that notes what it was shown."""

    name = "recorder"

    def __init__(self) -> None:
        self.seen: list[Scenario] = []
        self.damaged: list[bytes] = []

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        self.seen.append(scenario)
        key = next(
            (
                scenario.raw_zone
                / "raw"
                / "source=amfi"
                / "dataset=navall"
                / f"trade_date={FAULT_DAY}"
            ).glob("*")
        )
        self.damaged.append(key.read_bytes())
        return Diagnosis(root_causes=("raw.amfi.navall.Net Asset Value",), llm_calls=2)


class Crasher:
    name = "crasher"

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        raise RuntimeError("boom")


def runner(
    tmp_path: Path, systems: list[Any], source: LocalObjectStore | None = None
) -> tuple[BenchmarkRunner, ResultStore]:
    store = ResultStore(tmp_path / "results", "run1")
    builder = ScenarioBuilder(
        source=source or seed_source(tmp_path / "source"),
        repo_root=tmp_path,
        scratch_root=tmp_path / "scratch",
        dbt_build=no_dbt,
    )
    (tmp_path / "scratch").mkdir(exist_ok=True)
    return BenchmarkRunner(builder, store, systems), store


def test_every_system_sees_the_same_scenario_with_the_fault_injected(tmp_path: Path) -> None:
    first, second = Recorder(), Recorder()
    second.name = "second"
    run, store = runner(tmp_path, [first, second])

    report = run.run([amfi_case()], {"run_id": "run1"})

    assert report.ran == 2
    assert first.seen[0] is second.seen[0]
    assert first.damaged[0] != REPORT + b"\r\n# day 0"
    assert [r.system for r in store.load_all()] == ["recorder", "second"]


def test_the_source_raw_zone_is_never_changed(tmp_path: Path) -> None:
    source = seed_source(tmp_path / "source")
    before = sorted(p.read_bytes() for p in (tmp_path / "source").rglob("*") if p.is_file())
    run, _ = runner(tmp_path, [Recorder()], source)

    run.run([amfi_case()], {"run_id": "run1"})

    after = sorted(p.read_bytes() for p in (tmp_path / "source").rglob("*") if p.is_file())
    assert before == after


def test_the_scenario_holds_the_history_but_the_damaged_day_replaces_the_clean_one(
    tmp_path: Path,
) -> None:
    system = Recorder()
    run, _ = runner(tmp_path, [system])
    run.run([amfi_case()], {"run_id": "run1"})
    # scenarios are removed after use; rebuild one to look inside
    builder = ScenarioBuilder(
        seed_source(tmp_path / "s2"), tmp_path, tmp_path / "scratch2", dbt_build=no_dbt
    )
    (tmp_path / "scratch2").mkdir()

    scenario = builder.build(amfi_case("amfi-nulls-002"))

    folders = sorted(
        p.name for p in (scenario.raw_zone / "raw/source=amfi/dataset=navall").iterdir()
    )
    assert len(folders) == 12
    day = scenario.raw_zone / f"raw/source=amfi/dataset=navall/trade_date={FAULT_DAY}"
    assert len(list(day.iterdir())) == 1


def test_a_withheld_file_leaves_the_day_empty(tmp_path: Path) -> None:
    builder = ScenarioBuilder(
        seed_source(tmp_path / "s"), tmp_path, tmp_path / "scratch", dbt_build=no_dbt
    )
    (tmp_path / "scratch").mkdir()

    scenario = builder.build(amfi_case("amfi-late-data-001", {"type": "withhold_file"}))

    assert not (
        scenario.raw_zone / f"raw/source=amfi/dataset=navall/trade_date={FAULT_DAY}"
    ).exists()


def test_dbt_writing_nothing_is_a_run_error_signal(tmp_path: Path) -> None:
    run, store = runner(tmp_path, [NoneSystem()])

    run.run([amfi_case()], {"run_id": "run1"})

    [result] = store.load_all()
    assert result.status == "completed"
    assert result.detected
    assert result.signal_count == 1


def test_a_finished_pair_is_skipped_when_the_run_is_repeated(tmp_path: Path) -> None:
    system = Recorder()
    run, _ = runner(tmp_path, [system])
    run.run([amfi_case()], {"run_id": "run1"})

    again = run.run([amfi_case()], {"run_id": "run1"})

    assert (again.ran, again.skipped) == (0, 1)
    assert len(system.seen) == 1


def test_a_crashing_system_is_stored_and_the_others_still_run(tmp_path: Path) -> None:
    good = Recorder()
    run, store = runner(tmp_path, [Crasher(), good])

    report = run.run([amfi_case()], {"run_id": "run1"})

    assert (report.errored, report.ran) == (1, 1)
    assert store.load("amfi-nulls-001", "crasher").error == "boom"
    assert store.load("amfi-nulls-001", "recorder").status == "completed"


def test_missing_history_is_a_failed_scenario_naming_what_to_backfill(tmp_path: Path) -> None:
    run, store = runner(tmp_path, [Recorder()], seed_source(tmp_path / "thin", days=3))

    report = run.run([amfi_case(), amfi_case("amfi-nulls-002")], {"run_id": "run1"})

    assert report.scenario_failed == 2
    result = store.load("amfi-nulls-001", "recorder")
    assert result.status == "scenario_failed"
    assert result.error is not None
    assert "backfill that feed" in result.error
    assert result.diagnosis is None


def test_sandboxes_are_removed_after_use_unless_kept(tmp_path: Path) -> None:
    run, _ = runner(tmp_path, [Recorder()])

    run.run([amfi_case()], {"run_id": "run1"})

    assert list((tmp_path / "scratch").iterdir()) == []


def test_the_manifest_is_written_once_and_a_resume_keeps_the_original(tmp_path: Path) -> None:
    run, store = runner(tmp_path, [Recorder()])

    run.run([amfi_case()], {"run_id": "run1", "lockfile_sha256": "aaa"})
    run.run([amfi_case()], {"run_id": "run1", "lockfile_sha256": "bbb"})

    assert store.read_manifest()["lockfile_sha256"] == "aaa"


def test_unsafe_run_ids_and_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="Not a usable run id"):
        ResultStore(tmp_path, "../x")
    store = ResultStore(tmp_path, "ok")
    with pytest.raises(BenchmarkError, match="Not a usable system"):
        store.done("amfi-nulls-001", "a/b")


def test_duplicate_system_names_and_an_empty_list_are_refused(tmp_path: Path) -> None:
    builder = ScenarioBuilder(seed_source(tmp_path / "s"), tmp_path, tmp_path, dbt_build=no_dbt)
    store = ResultStore(tmp_path / "r", "x")

    with pytest.raises(BenchmarkError, match="At least one"):
        BenchmarkRunner(builder, store, [])
    with pytest.raises(BenchmarkError, match="unique"):
        BenchmarkRunner(builder, store, [NoneSystem(), NoneSystem()])


def test_an_nse_bhavcopy_fault_is_injected_into_the_zip_and_staged(tmp_path: Path) -> None:
    from lineageheal.benchmark.zip_io import write_single_csv

    csv = (FIXTURES / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()
    store = LocalObjectStore(tmp_path / "src")
    zone = RawZone(store)
    for offset in range(12):
        day = FAULT_DAY - timedelta(days=offset)
        zone.put_if_absent(
            source="nse",
            dataset="cm_bhavcopy",
            trade_date=day,
            filename=f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip",
            content=write_single_csv(f"BhavCopy_{day:%Y%m%d}.csv", csv),
        )
    case = FaultCase.model_validate(
        {
            "id": "nse-nulls-001",
            "class": "nulls",
            "source": "nse",
            "dataset": "cm_bhavcopy",
            "trade_date": FAULT_DAY.isoformat(),
            "split": "test",
            "seed": 1,
            "injection": {"type": "blank_value", "column": "ClsPric"},
            "expected": {"root_cause": "raw.nse.cm_bhavcopy.ClsPric", "fix_location": "dbt"},
        }
    )
    (tmp_path / "scratch").mkdir()

    scenario = ScenarioBuilder(store, tmp_path, tmp_path / "scratch", dbt_build=no_dbt).build(case)

    staged = list(
        (scenario.raw_zone / f"staged/source=nse/dataset=cm_bhavcopy/trade_date={FAULT_DAY}").glob(
            "*.csv"
        )
    )
    assert len(staged) == 1
    assert staged[0].name.endswith("20261015_F_0000.csv")
    assert staged[0].read_bytes() != csv
