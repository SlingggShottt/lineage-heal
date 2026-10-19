"""Guards that sample source files keep the exact bytes the publishers serve.

Hooks, editors and git can all "tidy" a text file. For fixtures that would hide the
very quirks (CRLF endings, stray spaces) the loaders and bronze models must cope with.
"""

from pathlib import Path

import pytest

AMFI_FIXTURES = sorted((Path(__file__).parents[1] / "fixtures" / "amfi").glob("*.txt"))


def test_amfi_fixtures_are_present() -> None:
    assert len(AMFI_FIXTURES) == 2


@pytest.mark.parametrize("path", AMFI_FIXTURES, ids=lambda p: p.name)
def test_amfi_fixtures_keep_crlf_line_endings(path: Path) -> None:
    raw = path.read_bytes()

    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


@pytest.mark.parametrize("path", AMFI_FIXTURES, ids=lambda p: p.name)
def test_amfi_fixtures_start_with_an_eight_column_header(path: Path) -> None:
    header = path.read_bytes().split(b"\r\n", maxsplit=1)[0].decode("utf-8")

    assert header.startswith("Scheme Code;")
    assert header.count(";") == 7


def test_live_sample_keeps_its_space_only_separator_lines() -> None:
    live = next(p for p in AMFI_FIXTURES if "live" in p.name)

    assert b" \r\n" in live.read_bytes()
