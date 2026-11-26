"""D-81: the pre-change project reads the old layout cleanly and breaks on the new one."""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from lineageheal.benchmark.heldout.legacy import LEGACY_COLUMNS, to_legacy_zip
from lineageheal.benchmark.heldout.project import build_prechange_repo
from lineageheal.benchmark.scenario import run_dbt_build
from lineageheal.benchmark.zip_io import write_single_csv
from lineageheal.core.errors import BenchmarkError
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager

REPO = Path(__file__).parents[4]
UDIFF = (REPO / "tests/fixtures/nse/cm_bhavcopy_udiff_2024-07-05_sample.csv").read_text()
LAST_OLD_DAY = date(2024, 7, 5)


@pytest.fixture(scope="module")
def prechange(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_prechange_repo(REPO, tmp_path_factory.mktemp("prechange") / "repo")


def test_the_repository_is_a_committed_copy_with_the_old_bronze_model(prechange: Path) -> None:
    model = (prechange / "dbt/models/bronze/brz_nse__cm_bhavcopy.sql").read_text()
    log = subprocess.run(  # noqa: S603
        ["git", "-C", str(prechange), "log", "--format=%s"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert 'trim("SYMBOL")' in model
    assert "TckrSymb" not in model
    assert "July 2024 format change" in log
    assert (prechange / "dbt/macros/latest_version_rows.sql").is_file()
    assert not (prechange / "dbt/target").exists()  # build output is never copied


def test_the_source_declares_the_old_columns_and_keeps_its_location(prechange: Path) -> None:
    document = yaml.safe_load((prechange / "dbt/models/bronze/_bronze__sources.yml").read_text())
    nse = next(s for s in document["sources"] if s["name"] == "raw_nse")
    table = next(t for t in nse["tables"] if t["name"] == "cm_bhavcopy")

    assert [c["name"] for c in table["columns"]] == list(LEGACY_COLUMNS)
    assert "staged/source=nse/dataset=cm_bhavcopy" in table["meta"]["external_location"]
    assert 'env_var("RAW_ZONE_DIR"' in table["meta"]["external_location"]


def test_the_real_project_is_left_alone(prechange: Path) -> None:
    real = (REPO / "dbt/models/bronze/brz_nse__cm_bhavcopy.sql").read_text()

    assert "TckrSymb" in real


def test_an_existing_destination_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="already exists"):
        build_prechange_repo(REPO, tmp_path)


def build(prechange: Path, root: Path, *, with_new_format_day: bool) -> dict[str, str]:
    """Build the project on 12 old-format days, plus a UDiFF day as NSE published it after."""
    store = LocalObjectStore(root / "raw")
    zone, stager = RawZone(store), ZipStager(store)
    weekdays = [LAST_OLD_DAY - timedelta(days=i) for i in range(20)]
    for day in [d for d in weekdays if d.weekday() < 5][:12]:
        csv = UDIFF.replace("2024-07-05", day.isoformat()).encode()
        name, archive = to_legacy_zip(write_single_csv("u.csv", csv), day)
        stager.stage(
            zone.put_if_absent(
                source="nse", dataset="cm_bhavcopy", trade_date=day, filename=name, content=archive
            ).key
        )
    if with_new_format_day:
        day = date(2024, 7, 8)
        archive = write_single_csv("new.csv", UDIFF.replace("2024-07-05", "2024-07-08").encode())
        stager.stage(
            zone.put_if_absent(
                source="nse",
                dataset="cm_bhavcopy",
                trade_date=day,
                filename="BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip",
                content=archive,
            ).key
        )
    run_dbt_build(prechange / "dbt", root / "data", root / "raw", root / "target", 600)
    results = json.loads((root / "target/run_results.json").read_text())["results"]
    return {
        r["unique_id"]: r["status"]
        for r in results
        if "brz_nse__cm_bhavcopy" in r["unique_id"] or "slv_nse__equity_eod" in r["unique_id"]
    }


def test_a_clean_old_format_history_builds_and_passes_every_nse_price_check(
    prechange: Path, tmp_path: Path
) -> None:
    statuses = build(prechange, tmp_path, with_new_format_day=False)

    assert statuses
    assert set(statuses.values()) <= {"pass", "success", "warn"}


def test_the_first_new_format_file_breaks_the_old_pipeline_as_it_did_in_july_2024(
    prechange: Path, tmp_path: Path
) -> None:
    statuses = build(prechange, tmp_path, with_new_format_day=True)

    failed = {uid.split(".")[2] for uid, s in statuses.items() if s == "fail"}
    assert "not_null_brz_nse__cm_bhavcopy_symbol" in failed
    assert "not_null_brz_nse__cm_bhavcopy_close_price_inr" in failed
