"""FR-WH-01 and FR-WH-04: bronze models read stored files and their tests catch faults.

Each test stores a file in a temporary raw zone, runs `dbt build` for one model against a
temporary DuckDB file, and inspects which models and tests passed. Damaged files are often
stored as a second version of the same trade date, the way a bad republication would arrive.
"""

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import duckdb
import pytest
import yaml
from dbt.cli.main import dbtRunner

from lineageheal.benchmark.faults import AMFI_FAULTS
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.ingestion.staging import ZipStager

REPO_ROOT = Path(__file__).parents[3]
DBT_DIR = REPO_ROOT / "dbt"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "amfi" / "navall_history_2026-10-15_sample.txt"
AMFI_MODEL = "brz_amfi__navall"
NSE_MODEL = "brz_nse__cm_bhavcopy"
TRADE_DATE = date(2026, 10, 15)
NAV_TEST = "not_null_brz_amfi__navall_nav_inr"

NSE_DIR = REPO_ROOT / "tests" / "fixtures" / "nse"
NSE_FIXTURE = NSE_DIR / "cm_bhavcopy_udiff_2024-07-05_sample.csv"
NSE_OLD_FORMAT_FIXTURE = NSE_DIR / "cm_bhavcopy_old_2024-07-05_sample.csv"
NSE_TRADE_DATE = date(2024, 7, 5)
NSE_FILENAME = "BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv"


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
    stager: ZipStager
    build: Callable[[str], BuildResult]  # builds one model and its tests

    def store_amfi(self, content: bytes) -> None:
        self.zone.put_if_absent(
            source="amfi",
            dataset="navall",
            trade_date=TRADE_DATE,
            filename="NAVHistoryReport151026.txt",
            content=content,
        )

    def store_nse(self, csv_content: bytes) -> None:
        """Store a bhavcopy zip in the raw zone and stage it, as the loader does."""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(NSE_FILENAME, csv_content)
        stored = self.zone.put_if_absent(
            source="nse",
            dataset="cm_bhavcopy",
            trade_date=NSE_TRADE_DATE,
            filename=f"{NSE_FILENAME}.zip",
            content=buffer.getvalue(),
        )
        self.stager.stage(stored.key)


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

    def build(model: str) -> BuildResult:
        result = dbtRunner().invoke(
            [
                "build",
                "--select", model,
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

    store = LocalObjectStore(raw_dir)
    return Workspace(RawZone(store), ZipStager(store), build)


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
    workspace.store_amfi(healthy_report())

    result = workspace.build(AMFI_MODEL)

    assert result.success
    assert result.failed() == set()
    assert result.statuses["brz_amfi__navall"] == "success"


def test_only_data_rows_are_kept_and_they_are_typed(workspace: Workspace) -> None:
    report = healthy_report()
    workspace.store_amfi(report)

    result = workspace.build(AMFI_MODEL)

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
    workspace.store_amfi(AMFI_FAULTS[fault].apply(healthy_report()))

    result = workspace.build(AMFI_MODEL)

    assert not result.success
    assert result.failed() == EXPECTED_FAILURES[fault]


def test_a_bad_republication_is_caught_while_the_good_version_stays(
    workspace: Workspace,
) -> None:
    report = healthy_report()
    workspace.store_amfi(report)
    workspace.store_amfi(AMFI_FAULTS["blank-nav"].apply(report))

    result = workspace.build(AMFI_MODEL)

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
    workspace.store_amfi(report)
    workspace.store_amfi(AMFI_FAULTS["rename-column"].apply(report))

    result = workspace.build(AMFI_MODEL)

    assert result.statuses["brz_amfi__navall"] == "success"
    assert result.failed() == {NAV_TEST}


def test_a_zero_nav_is_not_a_fault(workspace: Workspace) -> None:
    """AMFI publishes zero NAVs for segregated portfolios on ordinary days."""
    workspace.store_amfi(healthy_report().replace(b";21.86;", b";0;"))

    assert workspace.build(AMFI_MODEL).success


def test_an_unparseable_date_is_caught_as_a_missing_value(workspace: Workspace) -> None:
    workspace.store_amfi(healthy_report().replace(b"15-Oct-2026", b"2026-10-15"))

    result = workspace.build(AMFI_MODEL)

    assert "not_null_brz_amfi__navall_nav_date" in result.failed()


def test_a_renamed_column_breaks_the_model_and_skips_what_depends_on_it(
    workspace: Workspace,
) -> None:
    """Schema drift surfaces in dbt: the model fails, so its tests cannot vouch for the data."""
    workspace.store_amfi(AMFI_FAULTS["rename-column"].apply(healthy_report()))

    result = workspace.build(AMFI_MODEL)

    assert result.statuses["brz_amfi__navall"] == "error"
    assert result.statuses[NAV_TEST] == "skipped"


def test_an_added_column_is_tolerated(workspace: Workspace) -> None:
    workspace.store_amfi(healthy_report().replace(b";Date\r\n", b";Date;Extra\r\n", 1))

    assert workspace.build(AMFI_MODEL).success


# --- NSE bhavcopy -------------------------------------------------------------------------

NSE_RANGE_TEST = "assert_brz_nse__cm_bhavcopy_prices_within_day_range"
NSE_DATE_TEST = "assert_brz_nse__cm_bhavcopy_dates_match_their_file"
NSE_UNIQUE_TEST = "unique_combination_brz_nse__cm_bhavcopy_trade_date__symbol__series___source_file"
NSE_CLOSE_NULL_TEST = "not_null_brz_nse__cm_bhavcopy_close_price_inr"
NSE_CLOSE_NEGATIVE_TEST = "non_negative_brz_nse__cm_bhavcopy_close_price_inr"


def healthy_bhavcopy() -> bytes:
    return NSE_FIXTURE.read_bytes()


def reliance_row(csv_content: bytes) -> bytes:
    return next(line for line in csv_content.split(b"\n") if b",RELIANCE,EQ," in line)


def test_a_healthy_bhavcopy_builds_and_every_test_passes(workspace: Workspace) -> None:
    workspace.store_nse(healthy_bhavcopy())

    result = workspace.build(NSE_MODEL)

    assert result.success
    assert result.failed() == set()
    assert result.statuses[NSE_MODEL] == "success"


def test_bhavcopy_rows_are_typed_and_renamed(workspace: Workspace) -> None:
    workspace.store_nse(healthy_bhavcopy())

    result = workspace.build(NSE_MODEL)

    (count,) = result.query("select count(*) from bronze.brz_nse__cm_bhavcopy")[0]
    assert count == 7
    row = result.query(
        "select instrument_id, series, isin, instrument_name, open_price_inr, high_price_inr, "
        "low_price_inr, close_price_inr, last_price_inr, prev_close_price_inr, "
        "settlement_price_inr, volume, turnover_inr, trades, trade_date "
        "from bronze.brz_nse__cm_bhavcopy where symbol = 'RELIANCE'"
    )[0]
    assert row == (
        2885,
        "EQ",
        "INE002A01018",
        "RELIANCE INDUSTRIES LTD",
        Decimal("3107.6500"),
        Decimal("3197.0000"),
        Decimal("3096.0000"),
        Decimal("3177.2500"),
        Decimal("3189.9000"),
        Decimal("3108.0500"),
        Decimal("3177.6500"),
        6134855,
        Decimal("19323716027.90"),
        261494,
        date(2024, 7, 5),
    )


@pytest.mark.parametrize(
    ("damage", "expected_failures"),
    [
        pytest.param(
            lambda c: c.replace(b"3197.00,3096.00,3177.25,", b"3197.00,3096.00,,"),
            {NSE_CLOSE_NULL_TEST},
            id="blank close price",
        ),
        pytest.param(
            lambda c: c.replace(b"3096.00,3177.25,", b"3096.00,-3177.25,"),
            {NSE_CLOSE_NEGATIVE_TEST, NSE_RANGE_TEST},
            id="negative close price",
        ),
        pytest.param(
            lambda c: c.replace(b"3197.00,3096.00,", b"3096.00,3197.00,"),
            {NSE_RANGE_TEST},
            id="high below low",
        ),
        pytest.param(
            lambda c: c.replace(reliance_row(c) + b"\n", (reliance_row(c) + b"\n") * 2),
            {NSE_UNIQUE_TEST},
            id="duplicated row",
        ),
        pytest.param(
            lambda c: c.replace(b"\n2024-07-05,", b"\n2024-07-04,"),
            {NSE_DATE_TEST},
            id="file for the wrong day",
        ),
        pytest.param(
            lambda c: c.replace(b"\n2024-07-05,", b"\n05-JUL-2024,"),
            {"not_null_brz_nse__cm_bhavcopy_trade_date"},
            id="old date format",
        ),
    ],
)
def test_each_bhavcopy_fault_is_caught_by_the_expected_check(
    workspace: Workspace, damage: Callable[[bytes], bytes], expected_failures: set[str]
) -> None:
    workspace.store_nse(damage(healthy_bhavcopy()))

    result = workspace.build(NSE_MODEL)

    assert not result.success
    assert result.failed() == expected_failures


def test_a_renamed_price_column_breaks_the_model(workspace: Workspace) -> None:
    """The schema-drift case from the benchmark spec: ClsPric renamed to ClosePrice."""
    workspace.store_nse(healthy_bhavcopy().replace(b",ClsPric,", b",ClosePrice,"))

    result = workspace.build(NSE_MODEL)

    assert result.statuses[NSE_MODEL] == "error"
    assert NSE_CLOSE_NULL_TEST not in result.failed()
    assert result.statuses[NSE_CLOSE_NULL_TEST] == "skipped"


def test_the_pre_july_2024_format_is_rejected(workspace: Workspace) -> None:
    """NSE's real old-format file, stored where UDiFF is expected, breaks the model."""
    workspace.store_nse(NSE_OLD_FORMAT_FIXTURE.read_bytes())

    result = workspace.build(NSE_MODEL)

    assert not result.success
    assert result.statuses[NSE_MODEL] == "error"


def test_the_old_format_next_to_a_healthy_file_shows_up_as_missing_values(
    workspace: Workspace,
) -> None:
    workspace.store_nse(healthy_bhavcopy())
    workspace.store_nse(NSE_OLD_FORMAT_FIXTURE.read_bytes())

    result = workspace.build(NSE_MODEL)

    assert not result.success
    assert "not_null_brz_nse__cm_bhavcopy_symbol" in result.failed()


def test_an_added_bhavcopy_column_is_tolerated(workspace: Workspace) -> None:
    header, *rows = healthy_bhavcopy().rstrip(b"\n").split(b"\n")
    widened = b"\n".join([header + b",Extra", *(row + b",x" for row in rows)]) + b"\n"
    workspace.store_nse(widened)

    assert workspace.build(NSE_MODEL).success
