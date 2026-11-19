"""Splitting a runbook into chunks that can be retrieved on their own (FR-KB-01).

A runbook is short and has a fixed shape (docs/style_guide.md 8.2), so the natural unit is one
section: Symptoms, Diagnosis, Fix or Prevention. Each chunk repeats the runbook's title and the
section name, because "Check the file hash" means little to a search without knowing which
fault it belongs to.
"""

import hashlib
import re
from dataclasses import dataclass

from lineageheal.core.errors import KnowledgeError
from lineageheal.store.models import FAULT_CLASSES

_CLASS_LINE = re.compile(r"^\*\*Fault class:\*\* (?P<cls>[a-z_]+) · \*\*Feeds:\*\* (?P<feeds>.+)$")
_SECTION = re.compile(r"^## (?P<heading>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document."""

    position: int
    heading: str
    text: str  # what is embedded and searched: title, heading and body together


@dataclass(frozen=True)
class ParsedRunbook:
    """A runbook split into its metadata and chunks."""

    slug: str
    title: str
    fault_class: str
    feeds: str
    content_hash: str
    chunks: tuple[Chunk, ...]


def parse_runbook(slug: str, markdown: str) -> ParsedRunbook:
    """Split a runbook written in the template into chunks, one per section.

    Args:
        slug: The runbook's file name without its extension; it identifies the document.
        markdown: The file's text.

    Returns:
        The runbook with its title, fault class, feeds and chunks.

    Raises:
        KnowledgeError: The runbook does not follow the template, so indexing it would put
            something unretrievable in the index.
    """
    lines = markdown.splitlines()
    if len(lines) < 3 or not lines[0].startswith("# "):
        raise KnowledgeError(f"{slug}: a runbook starts with a '# title' line")
    title = lines[0][2:].strip()
    header = _CLASS_LINE.match(lines[2])
    if header is None:
        raise KnowledgeError(f"{slug}: line 3 must be '**Fault class:** <class> · **Feeds:** ...'")
    fault_class = header["cls"]
    if fault_class not in FAULT_CLASSES:
        raise KnowledgeError(f"{slug}: unknown fault class {fault_class!r}")

    body = "\n".join(lines[3:])
    headings = [m["heading"].strip() for m in _SECTION.finditer(body)]
    bodies = [part.strip() for part in _SECTION.split(body)[2::2]]
    if not headings:
        raise KnowledgeError(f"{slug}: no '## section' headings")
    empty = [heading for heading, text in zip(headings, bodies, strict=True) if not text]
    if empty:
        raise KnowledgeError(f"{slug}: empty sections: {', '.join(empty)}")

    chunks = tuple(
        Chunk(position=i, heading=heading, text=f"{title}\n{heading}\n\n{text}")
        for i, (heading, text) in enumerate(zip(headings, bodies, strict=True))
    )
    return ParsedRunbook(
        slug=slug,
        title=title,
        fault_class=fault_class,
        feeds=header["feeds"].strip(),
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        chunks=chunks,
    )
