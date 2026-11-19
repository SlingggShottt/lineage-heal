"""FR-KB-01: a runbook is split into one chunk per section, each carrying its context."""

from pathlib import Path

import pytest

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.chunking import parse_runbook

RUNBOOKS = Path(__file__).parents[3] / "docs" / "runbooks"

SAMPLE = """# A value goes missing

**Fault class:** nulls · **Feeds:** AMFI NAVAll

## Symptoms
A not_null test fails.

## Fix
Read the raw file first.
"""


def test_a_runbook_becomes_one_chunk_per_section() -> None:
    parsed = parse_runbook("nulls", SAMPLE)

    assert (parsed.slug, parsed.title) == ("nulls", "A value goes missing")
    assert (parsed.fault_class, parsed.feeds) == ("nulls", "AMFI NAVAll")
    assert [(c.position, c.heading) for c in parsed.chunks] == [(0, "Symptoms"), (1, "Fix")]


def test_each_chunk_repeats_the_title_and_heading_so_it_makes_sense_alone() -> None:
    chunk = parse_runbook("nulls", SAMPLE).chunks[1]

    assert chunk.text == "A value goes missing\nFix\n\nRead the raw file first."


def test_the_hash_changes_with_the_text_and_not_otherwise() -> None:
    first = parse_runbook("nulls", SAMPLE)

    assert parse_runbook("nulls", SAMPLE).content_hash == first.content_hash
    assert parse_runbook("nulls", SAMPLE + "More.\n").content_hash != first.content_hash


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("no title\n\n**Fault class:** nulls · **Feeds:** x\n## A\nbody", "'# title'"),
        ("# T\n\nnot a header line\n## A\nbody", "line 3"),
        ("# T\n\n**Fault class:** gremlins · **Feeds:** x\n## A\nbody", "unknown fault class"),
        ("# T\n\n**Fault class:** nulls · **Feeds:** x\nno sections", "no '## section'"),
        ("# T\n\n**Fault class:** nulls · **Feeds:** x\n## A\n\n## B\nbody", "empty sections: A"),
    ],
)
def test_a_runbook_off_the_template_is_refused_with_its_name(markdown: str, message: str) -> None:
    with pytest.raises(KnowledgeError, match=f"bad: .*{message}"):
        parse_runbook("bad", markdown)


@pytest.mark.parametrize("path", sorted(RUNBOOKS.glob("*.md")), ids=lambda p: p.name)
def test_every_shipped_runbook_parses_into_the_four_sections(path: Path) -> None:
    parsed = parse_runbook(path.stem, path.read_text())

    assert [c.heading for c in parsed.chunks] == ["Symptoms", "Diagnosis", "Fix", "Prevention"]
