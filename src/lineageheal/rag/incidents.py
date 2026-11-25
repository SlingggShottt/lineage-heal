"""Indexing resolved incidents so later investigations can retrieve them (FR-KB-01, FR-KB-03).

An incident document says what the failing checks looked like and what the confirmed root cause
and fix turned out to be, because a new failure is described by its symptoms and the useful
answer is the cause that was found last time. One incident is one chunk: it is short, and split
up its symptoms would be separated from the answer. The live system will add decided incidents
here from reviews (LH-097); the benchmark adds the seed split's faults the same way, which is
the only way a benchmark run may learn from an incident (FR-BEN-07).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from lineageheal.rag.embeddings import Embedder
from lineageheal.store.knowledge import (
    ChunkToIndex,
    DocumentToIndex,
    IndexOutcome,
    KnowledgeRepository,
)

INCIDENT_SOURCE = "incident"
HEADING = "Resolved incident"


@dataclass(frozen=True)
class ResolvedIncident:
    """A past incident with its confirmed outcome."""

    slug: str  # identifies the document; a benchmark seed case uses its case id
    fault_class: str
    feeds: str  # for example "amfi" or "nse"
    symptoms: tuple[str, ...]  # one line per failing check
    root_cause: str  # a graph node, `raw.<source>.<dataset>.<column>`
    fix_location: str  # `dbt` or `outside_dbt`
    fix_summary: str

    def text(self) -> str:
        """What is embedded and searched: the symptoms first, then the answer."""
        symptoms = "\n".join(f"- {line}" for line in self.symptoms) or "- no failing check"
        return (
            f"Resolved incident ({self.fault_class.replace('_', ' ')}, feed {self.feeds})\n"
            f"Symptoms:\n{symptoms}\n"
            f"Root cause: {self.root_cause}\n"
            f"Fix location: {self.fix_location}. {self.fix_summary}"
        )


class IncidentIndexer:
    """Embeds a resolved incident and stores it as a one-chunk document."""

    def __init__(self, repository: KnowledgeRepository, embedder: Embedder) -> None:
        """Create an indexer over a store and an embedder."""
        self._repository = repository
        self._embedder = embedder

    def index(self, incident: ResolvedIncident, *, namespace: str) -> IndexOutcome:
        """Store the incident, unless an identical copy was embedded by the same model."""
        text = incident.text()
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        if self._repository.is_current(
            namespace, INCIDENT_SOURCE, incident.slug, content_hash, self._embedder.model_name
        ):
            return IndexOutcome.UNCHANGED
        [vector] = self._embedder.embed_documents([text])
        return self._repository.store(
            DocumentToIndex(
                namespace=namespace,
                source=INCIDENT_SOURCE,
                slug=incident.slug,
                title=f"Resolved incident {incident.slug}",
                fault_class=incident.fault_class,
                feeds=incident.feeds,
                content_hash=content_hash,
                embedding_model=self._embedder.model_name,
                chunks=[ChunkToIndex(0, HEADING, text, vector)],
            )
        )
