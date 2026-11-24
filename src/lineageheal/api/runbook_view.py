"""Reading runbooks for the dashboard (FR-UI-08).

Files are parsed with the knowledge index's own parser, so the screen and the index never
disagree about a runbook's title, class or sections. A slug is only ever looked up in the folder's
listing, never joined to a path, so no request can name a file outside it.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lineageheal.api.health_schemas import Runbook, RunbookSection, RunbookSummary
from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.chunking import ParsedRunbook, parse_runbook

log = structlog.get_logger()


def _parsed(folder: Path) -> dict[str, tuple[ParsedRunbook, str]]:
    found: dict[str, tuple[ParsedRunbook, str]] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            found[path.stem] = (parse_runbook(path.stem, text), text)
        except KnowledgeError as exc:
            # One malformed file must not hide the others; `lineageheal index` reports it.
            log.warning("runbook_skipped", slug=path.stem, error=str(exc))
    return found


def list_runbooks(folder: Path) -> list[RunbookSummary]:
    """Every runbook that follows the template, by slug."""
    return [
        RunbookSummary(slug=slug, title=r.title, fault_class=r.fault_class, feeds=r.feeds)
        for slug, (r, _text) in _parsed(folder).items()
    ]


def read_runbook(folder: Path, slug: str) -> Runbook | None:
    """One runbook with its markdown and sections, or None if there is no such runbook."""
    entry = _parsed(folder).get(slug)
    if entry is None:
        return None
    runbook, text = entry
    return Runbook(
        slug=slug,
        title=runbook.title,
        fault_class=runbook.fault_class,
        feeds=runbook.feeds,
        markdown=text,
        sections=[
            RunbookSection(heading=c.heading, text=c.text.split("\n\n", 1)[1])
            for c in runbook.chunks
        ],
    )
