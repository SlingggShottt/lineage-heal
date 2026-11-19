"""FR-KB-01: runbooks follow the template, so the indexer can chunk them by heading."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lineageheal.store.models import FAULT_CLASSES

RUNBOOKS = Path(__file__).parents[2] / "docs" / "runbooks"
HEADINGS = ["## Symptoms", "## Diagnosis", "## Fix", "## Prevention"]
FIRST_FIVE = {"schema_drift", "nulls", "duplicates", "unit_change", "late_data"}
CLASS_LINE = re.compile(r"^\*\*Fault class:\*\* (?P<cls>[a-z_]+) · \*\*Feeds:\*\* (?P<feeds>.+)$")


def runbooks() -> list[Path]:
    return sorted(RUNBOOKS.glob("*.md"))


def test_every_fault_class_has_a_runbook() -> None:
    covered = set()
    for path in runbooks():
        match = CLASS_LINE.match(path.read_text().splitlines()[2])
        assert match, path.name
        covered.add(match["cls"])

    assert covered >= FIRST_FIVE


@pytest.mark.parametrize("path", runbooks(), ids=lambda p: p.name)
def test_a_runbook_has_the_template_shape(path: Path) -> None:
    lines = path.read_text().splitlines()

    assert lines[0].startswith("# ")
    assert lines[1] == ""
    match = CLASS_LINE.match(lines[2])
    assert match
    assert match["cls"] in FAULT_CLASSES
    assert [line for line in lines if line.startswith("## ")] == HEADINGS


@pytest.mark.parametrize("path", runbooks(), ids=lambda p: p.name)
def test_every_section_has_content(path: Path) -> None:
    sections = re.split(r"^## .+$", path.read_text(), flags=re.MULTILINE)[1:]

    assert len(sections) == len(HEADINGS)
    assert all(section.strip() for section in sections)


def test_the_file_name_is_a_readable_slug() -> None:
    assert [p.stem for p in runbooks()] == sorted(
        ["schema-drift", "nulls", "duplicates", "unit-change", "late-data"]
    )
