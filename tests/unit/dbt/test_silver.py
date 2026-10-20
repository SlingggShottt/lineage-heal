"""FR-WH-02: silver conforms bronze data and keeps the latest stored version of each day."""

from __future__ import annotations

from datetime import date
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
