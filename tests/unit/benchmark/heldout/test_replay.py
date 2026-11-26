"""FR-BEN-05: the held-out cases build on the pre-change project with old-layout history."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lineageheal.benchmark.heldout.legacy import LEGACY_COLUMNS
from lineageheal.benchmark.loader import load_cases
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.benchmark.zip_io import read_single_csv, write_single_csv
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

REPO = Path(__file__).parents[4]
HELDOUT = REPO / "benchmark" / "heldout"
UDIFF = (REPO / "tests/fixtures/nse/cm_bhavcopy_udiff_2024-07-05_sample.csv").read_text()
AMFI = (REPO / "tests/fixtures/amfi/navall_history_2026-10-15_sample.txt").read_bytes()
CHANGE_DAY = date(2024, 7, 8)


def no_dbt(project: Path, data: Path, raw: Path, target: Path, timeout: float) -> tuple[int, str]:
    return 1, "fake dbt"


def cases() -> dict[str, FaultCase]:
    return {c.id: c for c in load_cases(HELDOUT)}


def udiff_zip(day: date) -> bytes:
    return write_single_csv("u.csv", UDIFF.replace("2024-07-05", day.isoformat()).encode())


def source_zone(root: Path) -> LocalObjectStore:
    """Twelve weekdays of UDiFF files before the change, the change day's, and AMFI's for it."""
    store = LocalObjectStore(root)
    zone = RawZone(store)
    days = [CHANGE_DAY - timedelta(days=i) for i in range(1, 20)]
    for day in [*[d for d in days if d.weekday() < 5][:12], CHANGE_DAY]:
        zone.put_if_absent(
            source="nse",
            dataset="cm_bhavcopy",
            trade_date=day,
            filename=f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip",
            content=udiff_zip(day),
        )
    # AMFI publishes every day, so its file for the change day is what shows NSE's is missing.
    zone.put_if_absent(
        source="amfi", dataset="navall", trade_date=CHANGE_DAY, filename="NAVAll.txt", content=AMFI
    )
    return store


def test_the_held_out_cases_are_valid_and_kept_apart_from_the_corpus() -> None:
    found = cases()

    assert set(found) == {"nse-heldout-001", "nse-heldout-002"}
    assert {c.split for c in found.values()} == {"heldout"}
    assert found["nse-heldout-001"].expected.fix_location == "outside_dbt"
    assert found["nse-heldout-002"].expected.fix_location == "dbt"


def test_the_layout_case_accepts_each_of_the_twelve_columns_that_vanish() -> None:
    accepted = cases()["nse-heldout-002"].expected.accepted

    assert accepted == {f"raw.nse.cm_bhavcopy.{c}" for c in LEGACY_COLUMNS if c != "ISIN"}
    assert len(accepted) == 12


def test_history_is_in_the_old_layout_and_the_change_day_is_the_real_new_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "scratch").mkdir()
    builder = ScenarioBuilder(
        source_zone(tmp_path / "src"), REPO, tmp_path / "scratch", dbt_build=no_dbt
    )

    scenario = builder.build(cases()["nse-heldout-002"])

    folder = scenario.raw_zone / "raw/source=nse/dataset=cm_bhavcopy"
    before = sorted(folder.glob("trade_date=2024-07-0[1-5]/*"))
    assert before
    for path in before:
        assert path.name.endswith("bhav.csv.zip")  # NSE's old archive name
        name, content = read_single_csv(path.read_bytes())
        assert content.decode().startswith("SYMBOL,SERIES,OPEN")
        assert name.startswith("cm")
    [new] = (folder / f"trade_date={CHANGE_DAY}").iterdir()
    assert "BhavCopy_NSE_CM_0_0_0_20240708" in new.name
    assert read_single_csv(new.read_bytes())[1].decode().startswith("TradDt,BizDt")


def test_the_file_naming_case_leaves_the_change_day_without_a_bhavcopy(tmp_path: Path) -> None:
    (tmp_path / "scratch").mkdir()
    builder = ScenarioBuilder(
        source_zone(tmp_path / "src"), REPO, tmp_path / "scratch", dbt_build=no_dbt
    )

    scenario = builder.build(cases()["nse-heldout-001"])

    day = scenario.raw_zone / f"raw/source=nse/dataset=cm_bhavcopy/trade_date={CHANGE_DAY}"
    assert not day.exists()
    assert (scenario.raw_zone / f"raw/source=amfi/dataset=navall/trade_date={CHANGE_DAY}").exists()


def test_a_replay_runs_on_the_pre_change_repository_built_once(tmp_path: Path) -> None:
    (tmp_path / "scratch").mkdir()
    builder = ScenarioBuilder(
        source_zone(tmp_path / "src"), REPO, tmp_path / "scratch", dbt_build=no_dbt
    )

    first = builder.build(cases()["nse-heldout-001"])
    second = builder.build(cases()["nse-heldout-002"])

    assert first.repo_root == second.repo_root
    assert first.repo_root is not None
    assert first.repo_root != REPO
    bronze = (first.repo_root / "dbt/models/bronze/brz_nse__cm_bhavcopy.sql").read_text()
    assert 'trim("SYMBOL")' in bronze


def test_an_ordinary_case_still_runs_on_the_real_repository(tmp_path: Path) -> None:
    zone = RawZone(source_zone(tmp_path / "src"))
    del zone
    (tmp_path / "scratch").mkdir()
    ordinary = FaultCase.model_validate(
        {
            "id": "nse-nulls-001",
            "class": "nulls",
            "source": "nse",
            "dataset": "cm_bhavcopy",
            "trade_date": CHANGE_DAY.isoformat(),
            "split": "test",
            "seed": 1,
            "injection": {"type": "blank_value", "column": "ClsPric"},
            "expected": {"root_cause": "raw.nse.cm_bhavcopy.ClsPric", "fix_location": "dbt"},
        }
    )
    builder = ScenarioBuilder(
        source_zone(tmp_path / "src2"), REPO, tmp_path / "scratch", dbt_build=no_dbt
    )

    assert builder.build(ordinary).repo_root == REPO


# -- real dbt: both halves of July 2024 are visible to the pipeline ----------------------------


@pytest.fixture(scope="module")
def built(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, list[tuple[str, str | None, str | None]]]:
    root = tmp_path_factory.mktemp("replay")
    (root / "scratch").mkdir()
    builder = ScenarioBuilder(source_zone(root / "src"), REPO, root / "scratch")
    out = {}
    for case_id, case in cases().items():
        scenario = builder.build(case)
        out[case_id] = [(s.check_type, s.model, s.column) for s in scenario.summary.signals]
        assert json.loads(
            (scenario.raw_zone / "runs" / scenario.run_id / "signals.json").read_text()
        )
    return out


def test_the_real_new_file_breaks_the_old_project_on_its_symbol_and_prices(
    built: dict[str, list[tuple[str, str | None, str | None]]],
) -> None:
    signals = built["nse-heldout-002"]

    assert ("not_null", "brz_nse__cm_bhavcopy", "symbol") in signals
    assert ("not_null", "brz_nse__cm_bhavcopy", "close_price_inr") in signals


def test_a_missing_change_day_file_is_flagged_as_expected_but_absent(
    built: dict[str, list[tuple[str, str | None, str | None]]],
) -> None:
    assert any(check == "file_present_when_expected" for check, _, _ in built["nse-heldout-001"])
    # and the same day with the file present does not raise it, so the signal is the absence
    assert not any(
        check == "file_present_when_expected" for check, _, _ in built["nse-heldout-002"]
    )
