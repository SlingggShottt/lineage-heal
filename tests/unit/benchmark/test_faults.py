"""FR-BEN-02: each injector damages a report in exactly one controlled way."""

from pathlib import Path

import pytest

from lineageheal.benchmark.faults import (
    AMFI_FAULTS,
    WRONG_DATE,
    blank_nav,
    duplicate_row,
    negative_nav,
    rename_nav_column,
    wrong_date,
)
from lineageheal.core.errors import BenchmarkError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "amfi"
REPORT = (FIXTURES / "navall_history_2026-10-15_sample.txt").read_bytes()
LIVE_LAYOUT_REPORT = (FIXTURES / "navall_live_sample.txt").read_bytes()
FIRST_ROW = (
    "148921;Aditya Birla Sun Life Multi-Cap Fund-Direct Growth;"
    "Direct Plan;GROWTH;INF209KB1Y49;;21.86;15-Oct-2026"
)


def lines(content: bytes) -> list[str]:
    return content.decode("utf-8").split("\r\n")


def changed_line_numbers(before: bytes, after: bytes) -> list[int]:
    """Indexes of lines that differ; requires both to have the same number of lines."""
    old, new = lines(before), lines(after)
    assert len(old) == len(new)
    return [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]


def test_the_five_documented_faults_are_registered() -> None:
    assert set(AMFI_FAULTS) == {
        "blank-nav",
        "negative-nav",
        "duplicate-row",
        "wrong-date",
        "rename-column",
    }


def test_every_registered_fault_changes_the_report() -> None:
    for spec in AMFI_FAULTS.values():
        assert spec.apply(REPORT) != REPORT, spec.name


def test_blank_nav_empties_only_the_first_rows_nav() -> None:
    damaged = blank_nav(REPORT)

    assert changed_line_numbers(REPORT, damaged) == [6]
    assert lines(damaged)[6] == FIRST_ROW.replace(";21.86;", ";;")


def test_negative_nav_flips_only_the_first_rows_sign() -> None:
    damaged = negative_nav(REPORT)

    assert changed_line_numbers(REPORT, damaged) == [6]
    assert lines(damaged)[6] == FIRST_ROW.replace(";21.86;", ";-21.86;")


def test_duplicate_row_repeats_the_first_data_row_next_to_itself() -> None:
    damaged = duplicate_row(REPORT)

    original, new = lines(REPORT), lines(damaged)
    assert len(new) == len(original) + 1
    assert new[6] == new[7] == FIRST_ROW
    assert new[8:] == original[7:]


def test_wrong_date_changes_every_data_row_and_nothing_else() -> None:
    damaged = wrong_date(REPORT)

    old, new = lines(REPORT), lines(damaged)
    data_rows = [i for i, line in enumerate(old) if i > 0 and line.split(";")[0].isdigit()]
    assert changed_line_numbers(REPORT, damaged) == data_rows
    assert all(new[i].endswith(f";{WRONG_DATE}") for i in data_rows)


def test_rename_column_changes_only_the_header() -> None:
    damaged = rename_nav_column(REPORT)

    assert changed_line_numbers(REPORT, damaged) == [0]
    assert lines(damaged)[0] == (
        "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;"
        "ISIN Div Reinvestment;NAV;Date"
    )


def test_untouched_bytes_stay_exactly_as_published() -> None:
    """CRLF endings and non-ASCII text must survive, or the fault is not the only change."""
    damaged = blank_nav(LIVE_LAYOUT_REPORT)

    assert damaged.count(b"\r\n") == LIVE_LAYOUT_REPORT.count(b"\r\n")
    assert b"Children\xe2\x80\x99s Fund" in damaged  # a curly apostrophe, as UTF-8
    assert b" \r\n" in damaged


def test_injectors_work_on_the_live_layout_too() -> None:
    """The layouts order columns differently, so columns must be found by name."""
    damaged = blank_nav(LIVE_LAYOUT_REPORT)

    changed = changed_line_numbers(LIVE_LAYOUT_REPORT, damaged)
    assert len(changed) == 1
    assert lines(damaged)[changed[0]].split(";")[6] == ""


@pytest.mark.parametrize("fault", [blank_nav, negative_nav, duplicate_row, wrong_date])
def test_a_report_without_data_rows_cannot_be_damaged(fault: object) -> None:
    header_only = b"Scheme Code;NAV Name;Plan;Option;ISIN A;ISIN B;Net Asset Value;Date\r\n"

    with pytest.raises(BenchmarkError, match="no data rows"):
        fault(header_only)  # type: ignore[operator]


def test_a_report_without_the_nav_column_cannot_be_damaged() -> None:
    with pytest.raises(BenchmarkError, match="Net Asset Value"):
        blank_nav(b"Scheme Code;Name\r\n1;Fund\r\n")
