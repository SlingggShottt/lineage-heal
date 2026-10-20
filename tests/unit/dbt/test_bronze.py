"""FR-WH-01 and FR-WH-04: bronze models read stored files and their tests catch faults.

Each test stores a file in a temporary raw zone, runs `dbt build` for one model and inspects
which models and tests passed (the shared machinery is in conftest.py). Damaged files are
often stored as a second version of the same trade date, the way a bad republication would
arrive.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lineageheal.benchmark.faults import AMFI_FAULTS

if TYPE_CHECKING:
    from conftest import Workspace

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

MASTER_MODEL = "brz_nse__security_master"
ACTIONS_MODEL = "brz_nse__corporate_actions"
REFERENCE_DATE = date(2026, 10, 16)
MASTER_FIXTURE = NSE_DIR / "security_master_sample.csv"
ACTIONS_FIXTURE = NSE_DIR / "corporate_actions_sample.json"


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


# --- NSE security master -----------------------------------------------------------------

MASTER_UNIQUE_ID_TEST = "unique_combination_brz_nse__security_master_instrument_id___source_file"
MASTER_UNIQUE_SYMBOL_TEST = (
    "unique_combination_brz_nse__security_master_symbol__series___source_file"
)


def healthy_master() -> bytes:
    return MASTER_FIXTURE.read_bytes()


def edit_master(content: bytes, symbol: str, edit: Callable[[dict[str, str]], None]) -> bytes:
    """Change one instrument's row (by symbol, series EQ) and write the CSV back."""
    reader = csv.DictReader(content.decode("utf-8").splitlines())
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
    for row in rows:
        if row["TckrSymb"] == symbol and row["SctySrs"] == "EQ":
            edit(row)
    out = _StringLines()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.text().encode("utf-8")


class _StringLines:
    """A write target for csv that collects the text."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, text: str) -> int:
        self._parts.append(text)
        return len(text)

    def text(self) -> str:
        return "".join(self._parts)


def sets(**values: str) -> Callable[[dict[str, str]], None]:
    def edit(row: dict[str, str]) -> None:
        row.update(values)

    return edit


def test_untouched_master_survives_the_edit_helper() -> None:
    """Guards the helper: with no change it must reproduce the published bytes."""
    original = healthy_master()

    assert edit_master(original, "RELIANCE", sets()) == original


def test_a_healthy_security_master_builds_and_every_test_passes(workspace: Workspace) -> None:
    workspace.store_security_master(healthy_master())

    result = workspace.build(MASTER_MODEL)

    assert result.success
    assert result.failed() == set()


def test_security_master_rows_are_typed_and_dates_are_decoded(workspace: Workspace) -> None:
    workspace.store_security_master(healthy_master())

    result = workspace.build(MASTER_MODEL)

    (count,) = result.query("select count(*) from bronze.brz_nse__security_master")[0]
    assert count == 7
    row = result.query(
        "select instrument_id, series, instrument_name, isin, board_lot_qty, par_value_paise, "
        "issued_capital_shares, issue_date, listing_date "
        "from bronze.brz_nse__security_master where symbol = 'RELIANCE'"
    )[0]
    assert row == (
        2885,
        "EQ",
        "RELIANCE INDUSTRIES LTD",
        "INE002A01018",
        1,
        Decimal("1000.00"),
        Decimal("13532538722"),
        date(1980, 1, 1) + timedelta(days=467424000 // 86400),
        date(1995, 11, 29),
    )


def test_an_unset_listing_date_is_null_not_the_1980_epoch(workspace: Workspace) -> None:
    workspace.store_security_master(edit_master(healthy_master(), "TCS", sets(ListgDt="0")))

    result = workspace.build(MASTER_MODEL)

    assert result.success
    assert result.query(
        "select listing_date from bronze.brz_nse__security_master where symbol = 'TCS'"
    ) == [(None,)]


@pytest.mark.parametrize(
    ("symbol", "edit", "expected_failures"),
    [
        pytest.param(
            "RELIANCE",
            sets(TckrSymb=""),
            {"not_null_brz_nse__security_master_symbol"},
            id="blank symbol",
        ),
        pytest.param(
            "RELIANCE",
            sets(FinInstrmId="not-a-number"),
            {"not_null_brz_nse__security_master_instrument_id"},
            id="unreadable instrument id",
        ),
        pytest.param(
            "RELIANCE",
            sets(NewBrdLotQty="-1"),
            {"non_negative_brz_nse__security_master_board_lot_qty"},
            id="negative lot size",
        ),
        pytest.param(
            "RELIANCE",
            sets(ParVal=""),
            {"not_null_brz_nse__security_master_par_value_paise"},
            id="missing par value",
        ),
    ],
)
def test_each_security_master_fault_is_caught_by_the_expected_check(
    workspace: Workspace,
    symbol: str,
    edit: Callable[[dict[str, str]], None],
    expected_failures: set[str],
) -> None:
    workspace.store_security_master(edit_master(healthy_master(), symbol, edit))

    result = workspace.build(MASTER_MODEL)

    assert not result.success
    assert result.failed() == expected_failures


def test_a_duplicated_instrument_is_caught_by_both_uniqueness_checks(
    workspace: Workspace,
) -> None:
    lines = healthy_master().decode("utf-8").splitlines()
    reliance = next(line for line in lines if ",RELIANCE,EQ," in line)
    workspace.store_security_master(("\n".join([*lines, reliance]) + "\n").encode("utf-8"))

    result = workspace.build(MASTER_MODEL)

    assert result.failed() == {MASTER_UNIQUE_ID_TEST, MASTER_UNIQUE_SYMBOL_TEST}


def test_a_renamed_security_master_column_breaks_the_model(workspace: Workspace) -> None:
    workspace.store_security_master(healthy_master().replace(b",TckrSymb,", b",Ticker,", 1))

    result = workspace.build(MASTER_MODEL)

    assert result.statuses[MASTER_MODEL] == "error"
    assert result.statuses["not_null_brz_nse__security_master_symbol"] == "skipped"


def test_an_added_security_master_column_is_tolerated(workspace: Workspace) -> None:
    header, *rows = healthy_master().rstrip(b"\n").split(b"\n")
    widened = b"\n".join([header + b",Extra", *(row + b",x" for row in rows)]) + b"\n"
    workspace.store_security_master(widened)

    assert workspace.build(MASTER_MODEL).success


def test_par_value_switching_from_paise_to_rupees_is_invisible_to_bronze(
    workspace: Workspace,
) -> None:
    """A known limit, written down on purpose: the values stay valid numbers.

    NSE publishes par value in paise (1000 means Rs 10). A silent switch to rupees would
    shrink every value by 100 and still pass every bronze check. Catching it needs corporate
    actions' face value (in rupees) as a reference, which is a cross-feed check for silver
    (LH-028) and a unit-change fault for the benchmark.
    """
    workspace.store_security_master(edit_master(healthy_master(), "RELIANCE", sets(ParVal="10")))

    result = workspace.build(MASTER_MODEL)

    assert result.success


# --- NSE corporate actions -----------------------------------------------------------------


def healthy_actions() -> list[dict[str, object]]:
    return json.loads(ACTIONS_FIXTURE.read_bytes())  # type: ignore[no-any-return]


def as_json(records: list[dict[str, object]]) -> bytes:
    return json.dumps(records).encode("utf-8")


def with_first(**changes: object) -> list[dict[str, object]]:
    records = healthy_actions()
    records[0].update(changes)
    return records


def test_healthy_corporate_actions_build_and_every_test_passes(workspace: Workspace) -> None:
    workspace.store_corporate_actions(as_json(healthy_actions()))

    result = workspace.build(ACTIONS_MODEL)

    assert result.success
    assert result.failed() == set()


def test_corporate_actions_are_typed_and_placeholders_become_null(workspace: Workspace) -> None:
    workspace.store_corporate_actions(as_json(healthy_actions()))

    result = workspace.build(ACTIONS_MODEL)

    (count,) = result.query("select count(*) from bronze.brz_nse__corporate_actions")[0]
    assert count == 4
    row = result.query(
        "select series, company_name, ex_date, record_date, book_closure_start_date, "
        "no_delivery_end_date, face_value_inr, indicator, subject like 'Face Value Split%' "
        "from bronze.brz_nse__corporate_actions where symbol = 'BANSALWIRE'"
    )[0]
    assert row == (
        "EQ",
        "Bansal Wire Industries Limited",
        date(2026, 10, 16),
        date(2026, 10, 16),
        None,
        None,
        Decimal("1.0000"),
        None,
        True,
    )


@pytest.mark.parametrize(
    ("records", "expected_failures"),
    [
        pytest.param(
            with_first(symbol=None),
            {"not_null_brz_nse__corporate_actions_symbol"},
            id="missing symbol",
        ),
        pytest.param(
            with_first(subject=None),
            {"not_null_brz_nse__corporate_actions_subject"},
            id="missing description",
        ),
        pytest.param(
            with_first(exDate="15-Oct-2026"),
            {"assert_brz_nse__corporate_actions_ex_dates_match_their_file"},
            id="ex-date for another day",
        ),
        pytest.param(
            with_first(exDate="2026-10-16"),
            {"not_null_brz_nse__corporate_actions_ex_date"},
            id="unreadable ex-date",
        ),
        pytest.param(
            with_first(faceVal="-1"),
            {"non_negative_brz_nse__corporate_actions_face_value_inr"},
            id="negative face value",
        ),
        pytest.param(
            [*healthy_actions(), healthy_actions()[0]],
            {
                "unique_combination_brz_nse__corporate_actions_"
                "symbol__series__subject__ex_date___source_file"
            },
            id="duplicated record",
        ),
    ],
)
def test_each_corporate_actions_fault_is_caught_by_the_expected_check(
    workspace: Workspace,
    records: list[dict[str, object]],
    expected_failures: set[str],
) -> None:
    workspace.store_corporate_actions(as_json(records))

    result = workspace.build(ACTIONS_MODEL)

    assert not result.success
    assert result.failed() == expected_failures


def rename_key(records: list[dict[str, object]], old: str, new: str) -> list[dict[str, object]]:
    return [{(new if key == old else key): value for key, value in r.items()} for r in records]


def test_a_renamed_corporate_actions_key_breaks_the_model(workspace: Workspace) -> None:
    workspace.store_corporate_actions(as_json(rename_key(healthy_actions(), "exDate", "exDt")))

    result = workspace.build(ACTIONS_MODEL)

    assert result.statuses[ACTIONS_MODEL] == "error"
    assert result.statuses["not_null_brz_nse__corporate_actions_ex_date"] == "skipped"


def test_a_change_of_letter_case_in_a_key_is_not_schema_drift(workspace: Workspace) -> None:
    """DuckDB matches column names without regard to case, so exDate and exdate are the same."""
    workspace.store_corporate_actions(as_json(rename_key(healthy_actions(), "exDate", "exdate")))

    assert workspace.build(ACTIONS_MODEL).success


def test_an_added_corporate_actions_key_is_tolerated(workspace: Workspace) -> None:
    records = [{**record, "extra": "x"} for record in healthy_actions()]
    workspace.store_corporate_actions(as_json(records))

    assert workspace.build(ACTIONS_MODEL).success
