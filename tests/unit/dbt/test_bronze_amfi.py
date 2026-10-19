"""FR-WH-01 and FR-WH-04: AMFI bronze reads stored reports and its tests catch faults.

Each test stores a report in a temporary raw zone, runs `dbt build` against a temporary
DuckDB file, and inspects which models and tests passed. Damaged files are stored as a
second version of the same trade date, the way a bad republication would arrive.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import yaml
from dbt.cli.main import dbtRunner

from lineageheal.benchmark.faults import AMFI_FAULTS
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone

REPO_ROOT = Path(__file__).parents[3]
DBT_DIR = REPO_ROOT / "dbt"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "amfi" / "navall_history_2026-10-15_sample.txt"
TRADE_DATE = date(2026, 10, 15)
NAV_TEST = "not_null_brz_amfi__navall_nav_inr"


@dataclass
class BuildResult:
    success: bool
    statuses: dict[str, str]  # node name -> pass, fail, success, error or skipped
    database: Path

    def query(self, sql: str) -> list[tuple[object, ...]]:
        # dbt keeps its own connection to the file open in this process, and DuckDB only
        # allows a second one with identical settings, so this cannot be read-only.
        connection = duckdb.connect(str(self.database))
        try:
            return connection.sql(sql).fetchall()
        finally:
            connection.close()

    def failed(self) -> set[str]:
        return {name for name, status in self.statuses.items() if status in ("fail", "error")}


@dataclass
class Workspace:
    zone: RawZone
    build: Callable[[], BuildResult]

    def store(self, content: bytes) -> None:
        self.zone.put_if_absent(
            source="amfi",
            dataset="navall",
            trade_date=TRADE_DATE,
            filename="NAVHistoryReport151026.txt",
            content=content,
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    raw_dir = tmp_path / "raw_zone"
    monkeypatch.setenv("RAW_ZONE_DIR", str(raw_dir))
    monkeypatch.chdir(REPO_ROOT)

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    database = tmp_path / "test.duckdb"
    profile = {
        "lineageheal": {
            "target": "ci",
            "outputs": {"ci": {"type": "duckdb", "path": str(database), "threads": 1}},
        }
    }
    (profiles_dir / "profiles.yml").write_text(yaml.safe_dump(profile))

    def build() -> BuildResult:
        result = dbtRunner().invoke(
            [
                "build",
                "--project-dir", str(DBT_DIR),
                "--profiles-dir", str(profiles_dir),
                "--target-path", str(tmp_path / "target"),
                "--log-path", str(tmp_path / "logs"),
                "--no-version-check",
                "--quiet",
            ]
        )  # fmt: skip
        statuses = {
            r.node.name: str(r.status.value)
            for r in (result.result.results if result.result else [])  # type: ignore[union-attr]
        }
        return BuildResult(result.success, statuses, database)

    return Workspace(RawZone(LocalObjectStore(raw_dir)), build)


def healthy_report() -> bytes:
    return FIXTURE.read_bytes()


def data_row_count(report: bytes) -> int:
    """Count lines with eight fields that start with a scheme code (not the header)."""
    return sum(
        1
        for line in report.split(b"\r\n")
        if line.count(b";") == 7 and line.split(b";")[0].isdigit()
    )


def test_a_healthy_report_builds_and_every_test_passes(workspace: Workspace) -> None:
    workspace.store(healthy_report())

    result = workspace.build()

    assert result.success
    assert result.failed() == set()
    assert result.statuses["brz_amfi__navall"] == "success"


def test_only_data_rows_are_kept_and_they_are_typed(workspace: Workspace) -> None:
    report = healthy_report()
    workspace.store(report)

    result = workspace.build()

    (count,) = result.query("select count(*) from bronze.brz_amfi__navall")[0]
    assert count == data_row_count(report)
    row = result.query(
        "select scheme_code, plan_name, isin_div_reinvestment, nav_inr, nav_date "
        "from bronze.brz_amfi__navall where scheme_code = 148921"
    )[0]
    assert row == (148921, "Direct Plan", None, Decimal("21.8600"), date(2026, 10, 15))


# What each injectable fault should trip when it is the only version of the report.
EXPECTED_FAILURES = {
    "blank-nav": {NAV_TEST},
    "negative-nav": {"non_negative_brz_amfi__navall_nav_inr"},
    "duplicate-row": {"unique_combination_brz_amfi__navall_scheme_code__nav_date___source_file"},
    "wrong-date": {"assert_brz_amfi__navall_dates_match_their_report"},
    "rename-column": {"brz_amfi__navall"},  # the model itself fails; its tests are skipped
}


def test_every_injectable_fault_has_an_expected_check() -> None:
    assert set(EXPECTED_FAILURES) == set(AMFI_FAULTS)


@pytest.mark.parametrize("fault", sorted(AMFI_FAULTS))
def test_each_injectable_fault_is_caught_by_the_expected_check(
    workspace: Workspace, fault: str
) -> None:
    workspace.store(AMFI_FAULTS[fault].apply(healthy_report()))

    result = workspace.build()

    assert not result.success
    assert result.failed() == EXPECTED_FAILURES[fault]


def test_a_bad_republication_is_caught_while_the_good_version_stays(
    workspace: Workspace,
) -> None:
    report = healthy_report()
    workspace.store(report)
    workspace.store(AMFI_FAULTS["blank-nav"].apply(report))

    result = workspace.build()

    assert result.failed() == {NAV_TEST}
    (rows, missing) = result.query(
        "select count(*), count(*) filter (where nav_inr is null) from bronze.brz_amfi__navall"
    )[0]
    assert rows == 2 * data_row_count(report)
    assert missing == 1


def test_a_renamed_column_in_a_republished_report_shows_up_as_missing_navs(
    workspace: Workspace,
) -> None:
    """Files merge by column name, so the drift appears as null NAVs, not a failed model.

    With a healthy version present the model still builds; the renamed column is just
    empty for the damaged version's rows.
    """
    report = healthy_report()
    workspace.store(report)
    workspace.store(AMFI_FAULTS["rename-column"].apply(report))

    result = workspace.build()

    assert result.statuses["brz_amfi__navall"] == "success"
    assert result.failed() == {NAV_TEST}


def test_a_zero_nav_is_not_a_fault(workspace: Workspace) -> None:
    """AMFI publishes zero NAVs for segregated portfolios on ordinary days."""
    workspace.store(healthy_report().replace(b";21.86;", b";0;"))

    assert workspace.build().success


def test_an_unparseable_date_is_caught_as_a_missing_value(workspace: Workspace) -> None:
    workspace.store(healthy_report().replace(b"15-Oct-2026", b"2026-10-15"))

    result = workspace.build()

    assert "not_null_brz_amfi__navall_nav_date" in result.failed()


def test_a_renamed_column_breaks_the_model_and_skips_what_depends_on_it(
    workspace: Workspace,
) -> None:
    """Schema drift surfaces in dbt: the model fails, so its tests cannot vouch for the data."""
    workspace.store(AMFI_FAULTS["rename-column"].apply(healthy_report()))

    result = workspace.build()

    assert result.statuses["brz_amfi__navall"] == "error"
    assert result.statuses[NAV_TEST] == "skipped"


def test_an_added_column_is_tolerated(workspace: Workspace) -> None:
    workspace.store(healthy_report().replace(b";Date\r\n", b";Date;Extra\r\n", 1))

    assert workspace.build().success
