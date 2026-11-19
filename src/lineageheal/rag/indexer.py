"""Building the knowledge index from the runbooks in the repository (FR-KB-01).

Indexing is safe to repeat: a runbook whose text and embedding model are unchanged is skipped
before anything is embedded, a changed one has its chunks replaced in one transaction, and a
runbook deleted from the repository is removed from the index. Decided incidents join the index
when reviews exist (LH-053); they go through the same store.
"""

from dataclasses import dataclass
from pathlib import Path

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.chunking import parse_runbook
from lineageheal.rag.embeddings import Embedder
from lineageheal.store.knowledge import (
    ChunkToIndex,
    DocumentToIndex,
    IndexOutcome,
    KnowledgeRepository,
)

RUNBOOK_SOURCE = "runbook"
LIVE_NAMESPACE = "live"


@dataclass(frozen=True)
class IndexReport:
    """What one indexing run did, by runbook."""

    outcomes: dict[str, IndexOutcome]
    removed: tuple[str, ...]


class RunbookIndexer:
    """Reads runbook files, embeds their chunks and stores them."""

    def __init__(self, repository: KnowledgeRepository, embedder: Embedder) -> None:
        """Create an indexer over a store and an embedder."""
        self._repository = repository
        self._embedder = embedder

    def index(self, runbooks_dir: Path, *, namespace: str = LIVE_NAMESPACE) -> IndexReport:
        """Index every `*.md` file in a folder.

        Raises:
            KnowledgeError: The folder holds no runbooks, or one does not follow the template.
                Nothing is removed from the index in that case, so a typo cannot empty it.
        """
        paths = sorted(runbooks_dir.glob("*.md"))
        if not paths:
            raise KnowledgeError(f"No runbooks (*.md) found in {runbooks_dir}")
        parsed = [parse_runbook(path.stem, path.read_text(encoding="utf-8")) for path in paths]

        outcomes: dict[str, IndexOutcome] = {}
        for runbook in parsed:
            if self._repository.is_current(
                namespace,
                RUNBOOK_SOURCE,
                runbook.slug,
                runbook.content_hash,
                self._embedder.model_name,
            ):
                outcomes[runbook.slug] = IndexOutcome.UNCHANGED
                continue
            vectors = self._embedder.embed_documents([chunk.text for chunk in runbook.chunks])
            outcomes[runbook.slug] = self._repository.store(
                DocumentToIndex(
                    namespace=namespace,
                    source=RUNBOOK_SOURCE,
                    slug=runbook.slug,
                    title=runbook.title,
                    fault_class=runbook.fault_class,
                    feeds=runbook.feeds,
                    content_hash=runbook.content_hash,
                    embedding_model=self._embedder.model_name,
                    chunks=[
                        ChunkToIndex(c.position, c.heading, c.text, vector)
                        for c, vector in zip(runbook.chunks, vectors, strict=True)
                    ],
                )
            )
        removed = self._repository.remove_missing(
            namespace, RUNBOOK_SOURCE, [runbook.slug for runbook in parsed]
        )
        return IndexReport(outcomes=outcomes, removed=tuple(removed))
