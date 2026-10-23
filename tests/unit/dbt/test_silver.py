"""FR-WH-02: silver conforms bronze data and keeps the latest stored version of each day."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lineageheal.benchmark.faults import AMFI_FAULTS

if TYPE_CHECKING:
    from conftest import Workspace

REPO_ROOT = Path(__file__).parents[3]
AMFI_REPORT = (
    REPO_ROOT / "tests" / "fixtures" / "amfi" / "navall_history_2026-10-15_sample.txt"
).read_bytes()
NSE_DIR = REPO_ROOT / "tests" / "fixtures" / "nse"

VERSIONS = "brz_meta__raw_file_versions"
BRONZE_AMFI = "brz_amfi__navall"
SILVER_AMFI = "slv_amfi__nav"
AMFI_CHAIN = (VERSIONS, BRONZE_AMFI, SILVER_AMFI)

EARLY = 1_700_000_000.0  # arrival times in seconds since 1970; only their order matters
LATE = EARLY + 3600


def amfi_report(rows: list[tuple[str, str]], day: str = "15-Oct-2026") -> bytes:
    """A small AMFI report whose rows have the given (plan, option) text."""
    header = (
        "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
        "Net Asset Value;Date"
    )
    lines = [header, "", "Open Ended Schemes ( Equity Scheme - Large Cap Fund )", ""]
    lines += [
        f"{code};Fund {code};{plan};{option};INF{code:06d}01;;10.5;{day}"
        for code, (plan, option) in enumerate(rows, start=1)
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


# --- conforming --------------------------------------------------------------------------


def test_healthy_amfi_reports_build_through_silver_with_every_test_passing(
    workspace: Workspace,
) -> None:
    workspace.store_amfi(AMFI_REPORT)

    result = workspace.build(*AMFI_CHAIN)

    assert result.success
    assert result.failed() == set()
    (count,) = result.query("select count(*) from silver.slv_amfi__nav")[0]
    assert count == 10


def test_silver_nav_rows_carry_typed_values_and_conformed_plan_and_option(
    workspace: Workspace,
) -> None:
    workspace.store_amfi(AMFI_REPORT)

    result = workspace.build(*AMFI_CHAIN)

    rows = result.query(
        "select scheme_code, plan, option_type, nav_inr from silver.slv_amfi__nav "
        "where scheme_code in (148921, 148920, 148918) order by scheme_code"
    )
    assert [(code, plan, option) for code, plan, option, _ in rows] == [
        (148918, "regular", "growth"),
        (148920, "direct", "idcw"),
        (148921, "direct", "growth"),
    ]


def test_plan_and_option_text_maps_to_a_small_vocabulary_and_unclear_values_stay_null(
    workspace: Workspace,
) -> None:
    cases = [
        ("Direct Plan", "Growth Option"),  # 1
        ("Regular Plan", "GROWTH"),  # 2
        ("Direct Plan", "Cumulative"),  # 3
        ("Direct Plan", "Monthly IDCW"),  # 4
        ("Regular Plan", "IDCW (Income Distribution CUM Capital Withdrawal)"),  # 5
        ("Direct Plan", "Payout/Reinvestment"),  # 6
        ("Direct Plan", "Bonus Option"),  # 7
        ("", ""),  # 8: neither published
        ("Institutional Plan", "Unclaimed Redemption Less than 3 years"),  # 9: not recognised
    ]
    workspace.store_amfi(amfi_report(cases))

    result = workspace.build(*AMFI_CHAIN)

    assert result.success
    assert result.query(
        "select scheme_code, plan, option_type from silver.slv_amfi__nav order by scheme_code"
    ) == [
        (1, "direct", "growth"),
        (2, "regular", "growth"),
        (3, "direct", "growth"),
        (4, "direct", "idcw"),
        (5, "regular", "idcw"),
        (6, "direct", "idcw"),
        (7, "direct", "bonus"),
        (8, None, None),
        (9, None, None),
    ]


# --- version selection -------------------------------------------------------------------


def blank_plan(report: bytes) -> bytes:
    """The republication AMFI really made: a scheme's plan went blank in one version."""
    return report.replace(b";Direct Plan;GROWTH;INF209KB1Y49;", b";;GROWTH;INF209KB1Y49;")


@pytest.mark.parametrize(
    ("original_arrives", "republished_arrives", "expected_plan"),
    [
        pytest.param(EARLY, LATE, None, id="the republished version arrives last and wins"),
        pytest.param(LATE, EARLY, "direct", id="the original arrives last and wins"),
    ],
)
def test_the_version_that_arrived_last_is_the_one_silver_keeps(
    workspace: Workspace,
    original_arrives: float,
    republished_arrives: float,
    expected_plan: str | None,
) -> None:
    """Arrival time decides, not the file's content, size or name."""
    workspace.store_amfi(AMFI_REPORT, arrived_at=original_arrives)
    workspace.store_amfi(blank_plan(AMFI_REPORT), arrived_at=republished_arrives)

    result = workspace.build(*AMFI_CHAIN)

    assert result.success
    assert result.query("select plan from silver.slv_amfi__nav where scheme_code = 148921") == [
        (expected_plan,)
    ]


def test_bronze_keeps_every_version_while_silver_keeps_one_row_per_scheme(
    workspace: Workspace,
) -> None:
    workspace.store_amfi(AMFI_REPORT, arrived_at=EARLY)
    workspace.store_amfi(blank_plan(AMFI_REPORT), arrived_at=LATE)

    result = workspace.build(*AMFI_CHAIN)

    (bronze,) = result.query("select count(*) from bronze.brz_amfi__navall")[0]
    (silver,) = result.query("select count(*) from silver.slv_amfi__nav")[0]
    (versions,) = result.query(
        "select count(*) from bronze.brz_meta__raw_file_versions where source = 'amfi'"
    )[0]
    assert (bronze, silver, versions) == (20, 10, 2)


def test_versions_of_different_days_are_chosen_independently(workspace: Workspace) -> None:
    day_one, day_two = date(2026, 10, 15), date(2026, 10, 16)
    workspace.store_amfi(amfi_report([("Direct Plan", "Growth")], "15-Oct-2026"), arrived_at=EARLY)
    workspace.store_amfi(amfi_report([("Regular Plan", "Growth")], "15-Oct-2026"), arrived_at=LATE)
    workspace.store_amfi(
        amfi_report([("Direct Plan", "IDCW")], "16-Oct-2026"), trade_date=day_two, arrived_at=EARLY
    )

    result = workspace.build(*AMFI_CHAIN)

    assert result.success
    assert result.query(
        "select nav_date, plan, option_type from silver.slv_amfi__nav order by nav_date"
    ) == [(day_one, "regular", "growth"), (day_two, "direct", "idcw")]


def test_a_bad_republication_stops_the_build_before_silver_runs(workspace: Workspace) -> None:
    """A failed bronze test gates what follows, so bad data does not reach silver."""
    workspace.store_amfi(AMFI_REPORT, arrived_at=EARLY)
    workspace.store_amfi(AMFI_FAULTS["blank-nav"].apply(AMFI_REPORT), arrived_at=LATE)

    result = workspace.build(*AMFI_CHAIN)

    assert not result.success
    assert result.failed() == {"not_null_brz_amfi__navall_nav_inr"}
    assert result.statuses[SILVER_AMFI] == "skipped"


def test_a_day_missing_from_the_versions_model_is_not_silently_dropped(
    workspace: Workspace,
) -> None:
    """If the versions model is stale, duplicates appear and fail loudly rather than vanish."""
    workspace.store_amfi(AMFI_REPORT, arrived_at=EARLY)
    assert workspace.build(*AMFI_CHAIN).success

    day_two = date(2026, 10, 16)  # new day, two versions, and the versions model is not rebuilt
    workspace.store_amfi(
        amfi_report([("Direct Plan", "Growth")], "16-Oct-2026"), trade_date=day_two
    )
    workspace.store_amfi(
        amfi_report([("Regular Plan", "Growth")], "16-Oct-2026"), trade_date=day_two
    )
    result = workspace.build(BRONZE_AMFI, SILVER_AMFI)

    assert not result.success
    assert result.failed() == {"unique_combination_slv_amfi__nav_scheme_code__nav_date"}


# --- the versions model ------------------------------------------------------------------


def test_every_raw_file_gets_a_row_with_its_identity_and_arrival_time(
    workspace: Workspace,
) -> None:
    amfi = workspace.store_amfi(AMFI_REPORT, arrived_at=EARLY)
    workspace.store_nse((NSE_DIR / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes())
    workspace.store_security_master((NSE_DIR / "security_master_sample.csv").read_bytes())
    workspace.store_corporate_actions((NSE_DIR / "corporate_actions_sample.json").read_bytes())

    result = workspace.build(VERSIONS)

    assert result.success
    rows = result.query(
        "select source, dataset, trade_date, version, size_bytes, arrived_at "
        "from bronze.brz_meta__raw_file_versions order by source, dataset"
    )
    assert [(r[0], r[1]) for r in rows] == [
        ("amfi", "navall"),
        ("nse", "cm_bhavcopy"),
        ("nse", "corporate_actions"),
        ("nse", "security_master"),
    ]
    amfi_row = rows[0]
    assert amfi_row[2] == date(2026, 10, 15)
    assert amfi_row[3] == amfi.sha256[:16]
    assert amfi_row[4] == amfi.size_bytes
    assert amfi_row[5].timestamp() == EARLY  # type: ignore[attr-defined]


def test_the_unzipped_staged_copies_are_not_counted_as_raw_versions(
    workspace: Workspace,
) -> None:
    workspace.store_nse((NSE_DIR / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes())

    result = workspace.build(VERSIONS)

    assert result.query("select count(*) from bronze.brz_meta__raw_file_versions") == [(1,)]


# --- NSE: shared helpers -----------------------------------------------------------------

BHAVCOPY_CSV = (NSE_DIR / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()
MASTER_CSV = (NSE_DIR / "security_master_sample.csv").read_bytes()
ACTIONS = json.loads((NSE_DIR / "corporate_actions_sample.json").read_bytes())

EOD_CHAIN = (VERSIONS, "brz_nse__cm_bhavcopy", "slv_nse__equity_eod")
SECURITIES_CHAIN = (VERSIONS, "brz_nse__security_master", "slv_nse__securities")
ACTIONS_CHAIN = (VERSIONS, "brz_nse__corporate_actions", "slv_nse__corporate_actions")
CROSS_FEED_CHAIN = (
    VERSIONS,
    "brz_nse__security_master",
    "brz_nse__corporate_actions",
    "slv_nse__securities",
    "slv_nse__corporate_actions",
)
PARSED = "assert_slv_nse__corporate_actions_are_fully_parsed"
WORDING = "assert_slv_nse__corporate_actions_wording_is_recognised"
PAR_CHECK = "assert_par_value_matches_corporate_action_face_value"


def master_with(par_paise: dict[str, str]) -> bytes:
    """The security master sample plus one EQ instrument per symbol, with the given par value.

    Each new row is a copy of RELIANCE's with its own symbol and id, so it is a realistic row.
    """
    reader = csv.DictReader(MASTER_CSV.decode("utf-8").splitlines())
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)
    template = next(row for row in rows if row["TckrSymb"] == "RELIANCE")
    for offset, (symbol, par) in enumerate(par_paise.items(), start=900_001):
        rows.append(
            {**template, "TckrSymb": symbol, "FinInstrmId": str(offset), "ParVal": par,
             "ISIN": f"INE{offset}", "FinInstrmNm": symbol}
        )  # fmt: skip
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def actions_json(records: list[dict[str, object]]) -> bytes:
    return json.dumps(records).encode("utf-8")


def action(symbol: str, subject: str, **fields: object) -> dict[str, object]:
    base = dict(ACTIONS[1])  # a real dividend record, used as a template
    base.update(symbol=symbol, subject=subject, exDate="16-Oct-2026", recDate="16-Oct-2026")
    base.update(fields)
    return base


# --- NSE corporate actions: the description parser ----------------------------------------

# (description exactly as NSE words it, expected type, cash, old face value, new face value,
#  ratio new, ratio held, rights premium). Every shape below appears in real data.
PARSER_CASES = [
    ("Dividend - Rs 5 Per Share", "dividend", "5", None, None, None, None, None),
    ("Dividend - Re 1 Per Share", "dividend", "1", None, None, None, None, None),
    ("Interim Dividend - Rs 5.50 Per Share", "dividend", "5.5", None, None, None, None, None),
    ("Interim Dividend - Re 0.25 Per Share", "dividend", "0.25", None, None, None, None, None),
    ("Dividend - Rs 8 Per Sh", "dividend", "8", None, None, None, None, None),
    ("Dividend - Rs  8 Per Share", "dividend", "8", None, None, None, None, None),  # two spaces
    ("Dividend - Rs8 Per Share", "dividend", "8", None, None, None, None, None),  # no space
    ("Dividend - Rs. 8 Per Share", "dividend", "8", None, None, None, None, None),  # with a dot
    ("Special Dividend - Rs 6 Per Share", "dividend", "6", None, None, None, None, None),
    (
        "Dividend - Rs 5 Per Share/Special Dividend - Rs 2 Per Share",
        "dividend",
        "7",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Dividend - Rs 5 Per Share & Special Dividend Rs 2 Per Share",
        "dividend",
        "7",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Interim Dividend - Re 0.75 Per Share & Special Dividend Rs 1.25 Per Share",
        "dividend",
        "2",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Distribution - Rs 3.187 Per Unit Consist Of Rs 1.5 Per Unit As Interest/ "
        "Re 1 Per Unit As Other Income",
        "distribution",
        "3.187",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "Distribution - Rs. 2 Per Unit As Interest",
        "distribution",
        "2",
        None,
        None,
        None,
        None,
        None,
    ),
    ("Bonus 1:1", "bonus", None, None, None, 1, 1, None),
    ("Bonus 3:2", "bonus", None, None, None, 3, 2, None),
    ("Rights 2:21 @ Premium Rs 748/-", "rights", None, None, None, 2, 21, "748"),
    ("Rights 1:9 @ Premium 91", "rights", None, None, None, 1, 9, "91"),  # no currency
    ("Rights 3:20 @ Premium Re. 150/-", "rights", None, None, None, 3, 20, "150"),
    (
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share",
        "split",
        None,
        "10",
        "5",
        None,
        None,
        None,
    ),
    (
        "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share",
        "split",
        None,
        "5",
        "1",
        None,
        None,
        None,
    ),
    ("Buy Back", "buyback", None, None, None, None, None, None),
    ("Demerger", "demerger", None, None, None, None, None, None),
    ("Interest Payment", "interest", None, None, None, None, None, None),
    (
        "Scheme Of Arrangement - Bonus Ncrps 3:1",
        "other",
        None,
        None,
        None,
        None,
        None,
        None,
    ),  # not a plain bonus
    (
        "Rights - 7 Ccps And 7 Warrants:40",
        "other",
        None,
        None,
        None,
        None,
        None,
        None,
    ),  # not a ratio issue
]


def test_the_parser_understands_every_real_wording_shape(workspace: Workspace) -> None:
    records = [action(f"S{i:02d}", case[0]) for i, case in enumerate(PARSER_CASES)]
    workspace.store_corporate_actions(actions_json(records))

    result = workspace.build(*ACTIONS_CHAIN)

    assert result.success
    assert result.failed() == set()
    rows = result.query(
        "select symbol, action_type, cash_amount_inr, split_old_face_value_inr, "
        "split_new_face_value_inr, ratio_new, ratio_held, rights_premium_inr "
        "from silver.slv_nse__corporate_actions order by symbol"
    )
    expected = []
    for i, (_, kind, cash, old, new, r_new, r_held, premium) in enumerate(PARSER_CASES):
        dec = lambda v: None if v is None else Decimal(v)  # noqa: E731
        expected.append(
            (f"S{i:02d}", kind, dec(cash), dec(old), dec(new), r_new, r_held, dec(premium))
        )
    assert rows == expected


def test_wording_the_parser_does_not_recognise_is_a_warning_not_a_failure(
    workspace: Workspace,
) -> None:
    workspace.store_corporate_actions(
        actions_json([action("ODD", "Scheme Of Arrangement - Bonus Ncrps 3:1")])
    )

    result = workspace.build(*ACTIONS_CHAIN)

    assert result.success
    assert result.statuses[WORDING] == "warn"


def test_old_unrecognised_wording_does_not_warn_on_every_build_for_ever(
    workspace: Workspace,
) -> None:
    """Only the latest three ex-dates are looked at; real 'other' actions stay in the history."""
    odd = action("ODD", "Scheme Of Arrangement - Bonus Ncrps 3:1", exDate="10-Oct-2026")
    workspace.store_corporate_actions(actions_json([odd]), ex_date=date(2026, 10, 10))
    for day in (13, 14, 15):
        later = action("DIV", "Dividend - Rs 5 Per Share", exDate=f"{day}-Oct-2026")
        workspace.store_corporate_actions(actions_json([later]), ex_date=date(2026, 10, day))

    result = workspace.build(*ACTIONS_CHAIN)

    assert result.success
    assert result.statuses[WORDING] == "pass"


def test_unrecognised_wording_in_a_recent_action_still_warns(workspace: Workspace) -> None:
    for day in (12, 13, 14):
        ok = action("DIV", "Dividend - Rs 5 Per Share", exDate=f"{day}-Oct-2026")
        workspace.store_corporate_actions(actions_json([ok]), ex_date=date(2026, 10, day))
    odd = action("ODD", "Scheme Of Arrangement - Bonus Ncrps 3:1", exDate="15-Oct-2026")
    workspace.store_corporate_actions(actions_json([odd]), ex_date=date(2026, 10, 15))

    assert workspace.build(*ACTIONS_CHAIN).statuses[WORDING] == "warn"


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("Dividend - Per Share", id="a dividend without an amount"),
        pytest.param(
            "Face Value Split (Sub-Division) - From Rs 10/- Per Share",
            id="a split without its new value",
        ),
        pytest.param(
            "Face Value Split (Sub-Division) - From Rs 5/- To Rs 10/-",
            id="a split that raises face value",
        ),
    ],
)
def test_a_recognised_type_with_missing_numbers_fails_loudly(
    workspace: Workspace, subject: str
) -> None:
    """If NSE rewords its descriptions so the numbers can no longer be read, this fires."""
    workspace.store_corporate_actions(actions_json([action("X", subject)]))

    result = workspace.build(*ACTIONS_CHAIN)

    assert result.failed() == {PARSED}


def test_the_same_actions_in_a_different_order_change_nothing(workspace: Workspace) -> None:
    """NSE reorders its response between requests; silver must not care."""
    workspace.store_corporate_actions(actions_json(ACTIONS), arrived_at=EARLY)
    workspace.store_corporate_actions(actions_json(list(reversed(ACTIONS))), arrived_at=LATE)

    result = workspace.build(*ACTIONS_CHAIN)

    assert result.success
    assert result.failed() == set()
    assert result.query("select count(*) from silver.slv_nse__corporate_actions") == [(4,)]
    assert result.query("select count(*) from bronze.brz_nse__corporate_actions") == [(8,)]


# --- the paise-versus-rupees cross-feed check ---------------------------------------------

# Matching instruments for the sample actions: BANSALWIRE (split), HINDCOMPOS, CRAMC, HCLTECH
# (dividends) have face values 1, 5, 10 and 2 rupees, which are 100, 500, 1000 and 200 paise.
MATCHING_PAR = {"BANSALWIRE": "100", "HINDCOMPOS": "500", "CRAMC": "1000", "HCLTECH": "200"}


def test_matching_par_values_and_face_values_pass_the_cross_feed_check(
    workspace: Workspace,
) -> None:
    workspace.store_security_master(master_with(MATCHING_PAR))
    workspace.store_corporate_actions(actions_json(ACTIONS))

    result = workspace.build(*CROSS_FEED_CHAIN)

    assert result.success
    assert result.statuses[PAR_CHECK] == "pass"


def test_a_par_value_published_in_rupees_instead_of_paise_is_caught_by_silver(
    workspace: Workspace,
) -> None:
    """Bronze cannot see this (the values are still valid numbers); the cross-feed check can."""
    in_rupees = {**MATCHING_PAR, "HCLTECH": "2"}  # Rs 2 written as 2, not 200
    workspace.store_security_master(master_with(in_rupees))
    workspace.store_corporate_actions(actions_json(ACTIONS))

    result = workspace.build(*CROSS_FEED_CHAIN)

    assert not result.success
    assert result.failed() == {PAR_CHECK}
    assert result.statuses["brz_nse__security_master"] == "success"  # bronze really is blind to it


def test_a_face_value_published_in_paise_instead_of_rupees_is_caught_too(
    workspace: Workspace,
) -> None:
    records = [dict(r) for r in ACTIONS]
    next(r for r in records if r["symbol"] == "HCLTECH")["faceVal"] = "200"
    workspace.store_security_master(master_with(MATCHING_PAR))
    workspace.store_corporate_actions(actions_json(records))

    assert workspace.build(*CROSS_FEED_CHAIN).failed() == {PAR_CHECK}


def test_a_split_is_not_expected_to_match_because_par_value_moves(workspace: Workspace) -> None:
    """On a split day the master may already show the new par value, or still the old one."""
    pre_split = {**MATCHING_PAR, "BANSALWIRE": "500"}  # Rs 5 before the split to Re 1
    workspace.store_security_master(master_with(pre_split))
    workspace.store_corporate_actions(actions_json(ACTIONS))

    assert workspace.build(*CROSS_FEED_CHAIN).success


# --- NSE securities ----------------------------------------------------------------------


def test_securities_are_a_daily_snapshot_with_par_value_in_rupees(workspace: Workspace) -> None:
    workspace.store_security_master(MASTER_CSV)

    result = workspace.build(*SECURITIES_CHAIN)

    assert result.success
    assert result.failed() == set()
    row = result.query(
        "select as_of_date, instrument_id, series, par_value_inr, board_lot_qty "
        "from silver.slv_nse__securities where symbol = 'RELIANCE'"
    )[0]
    assert row == (date(2026, 10, 16), 2885, "EQ", Decimal("10.00"), 1)  # 1000 paise = Rs 10
    assert result.query("select count(*) from silver.slv_nse__securities") == [(7,)]


def test_the_latest_security_master_of_a_day_is_the_one_kept(workspace: Workspace) -> None:
    workspace.store_security_master(master_with({"NEWCO": "100"}), arrived_at=EARLY)
    workspace.store_security_master(master_with({"NEWCO": "200"}), arrived_at=LATE)

    result = workspace.build(*SECURITIES_CHAIN)

    assert result.success
    assert result.query(
        "select par_value_inr from silver.slv_nse__securities where symbol = 'NEWCO'"
    ) == [(Decimal("2.00"),)]
    assert result.query("select count(*) from bronze.brz_nse__security_master") == [(16,)]


# --- NSE end-of-day prices ---------------------------------------------------------------


def test_equity_eod_has_one_row_per_instrument_with_the_change_from_previous_close(
    workspace: Workspace,
) -> None:
    workspace.store_nse(BHAVCOPY_CSV)

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert result.failed() == set()
    (price_date, close, previous, change) = result.query(
        "select price_date, close_price_inr, prev_close_price_inr, change_pct "
        "from silver.slv_nse__equity_eod where symbol = 'RELIANCE'"
    )[0]
    expected = (Decimal("3177.25") / Decimal("3108.05") - 1) * 100
    assert price_date == date(2024, 7, 5)
    assert (close, previous) == (Decimal("3177.2500"), Decimal("3108.0500"))
    assert abs(change - expected) < Decimal("0.0002")
    assert result.query("select count(*) from silver.slv_nse__equity_eod") == [(7,)]


def test_a_zero_previous_close_gives_no_change_instead_of_an_error(workspace: Workspace) -> None:
    workspace.store_nse(BHAVCOPY_CSV.replace(b"3108.05", b"0.00"))

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert result.query(
        "select change_pct from silver.slv_nse__equity_eod where symbol = 'RELIANCE'"
    ) == [(None,)]


def test_the_latest_bhavcopy_of_a_day_is_kept_and_its_version_is_found_in_the_staged_path(
    workspace: Workspace,
) -> None:
    workspace.store_nse(BHAVCOPY_CSV, arrived_at=EARLY)
    revised = workspace.store_nse(BHAVCOPY_CSV.replace(b"3177.25", b"3180.00"), arrived_at=LATE)

    result = workspace.build(*EOD_CHAIN)

    assert result.success
    assert result.query(
        "select close_price_inr, _file_version from silver.slv_nse__equity_eod "
        "where symbol = 'RELIANCE'"
    ) == [(Decimal("3180.0000"), revised.sha256[:16])]
    assert result.query("select count(*) from bronze.brz_nse__cm_bhavcopy") == [(14,)]
