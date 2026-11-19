"""Hybrid retrieval over the knowledge index (FR-KB-02).

A query is searched twice: by meaning (vector similarity) and by words (full text). Vectors find
a runbook described in different words; full text finds an exact name such as a column or a
check that an embedding blurs. The two lists are merged by reciprocal rank fusion.
"""

from dataclasses import dataclass

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.embeddings import Embedder
from lineageheal.rag.fusion import reciprocal_rank_fusion
from lineageheal.rag.indexer import LIVE_NAMESPACE
from lineageheal.store.knowledge import ChunkMatch, KnowledgeRepository

# Each search returns this many candidates before fusion. More than the final limit, so that a
# chunk ranked fifth by one search and third by the other can still reach the top.
CANDIDATES = 20


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk, with why it ranked where it did."""

    chunk: ChunkMatch
    score: float
    vector_rank: int | None  # 1 is the closest; None when the vector search did not return it
    keyword_rank: int | None


class Retriever:
    """Searches one namespace of the knowledge index."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedder: Embedder,
        *,
        namespace: str = LIVE_NAMESPACE,
    ) -> None:
        """Create a retriever bound to a namespace, so a benchmark run cannot reach the live one."""
        self._repository = repository
        self._embedder = embedder
        self._namespace = namespace

    def search(
        self,
        query: str,
        *,
        fault_class: str | None = None,
        source: str | None = None,
        limit: int = 5,
    ) -> list[Hit]:
        """Find the chunks that best answer a query, best first.

        Args:
            query: A description of the failure or a question about it.
            fault_class: Only chunks of documents about this fault class.
            source: Only `runbook` or `incident` documents.
            limit: The most hits to return.

        Raises:
            KnowledgeError: The query is empty.
            StorageError: The database could not be searched.
        """
        if not query.strip():
            raise KnowledgeError("A search needs a non-empty query")
        by_vector = self._repository.vector_search(
            self._namespace,
            self._embedder.embed_query(query),
            limit=CANDIDATES,
            fault_class=fault_class,
            source=source,
        )
        by_words = self._repository.keyword_search(
            self._namespace, query, limit=CANDIDATES, fault_class=fault_class, source=source
        )
        chunks = {match.chunk_id: match for match in (*by_vector, *by_words)}
        vector_ranks = {match.chunk_id: i for i, match in enumerate(by_vector, start=1)}
        keyword_ranks = {match.chunk_id: i for i, match in enumerate(by_words, start=1)}
        fused = reciprocal_rank_fusion(
            [[m.chunk_id for m in by_vector], [m.chunk_id for m in by_words]]
        )
        return [
            Hit(chunks[chunk_id], score, vector_ranks.get(chunk_id), keyword_ranks.get(chunk_id))
            for chunk_id, score in fused[:limit]
        ]
