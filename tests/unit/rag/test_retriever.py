"""FR-KB-02, FR-KB-03: hybrid retrieval with filters, over an isolated namespace."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.embeddings import HashingEmbedder
from lineageheal.rag.retriever import Retriever
from lineageheal.store.knowledge import (
    ChunkToIndex,
    DocumentToIndex,
    KnowledgeRepository,
    query_words,
)

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
EMBEDDER = HashingEmbedder()

DOCS = {
    ("runbook", "nulls", "nulls"): ["a blank nav value fails the not_null test"],
    ("runbook", "unit-change", "unit_change"): [
        "prices arrive in paise instead of rupees, a hundred times too large"
    ],
    ("incident", "inc-7", "unit_change"): ["par value switched to paise after a corporate action"],
    ("runbook", "late-data", "late_data"): ["the file never arrived on a trading day"],
}


def store(repository: KnowledgeRepository, namespace: str = "live") -> None:
    for (source, slug, fault_class), texts in DOCS.items():
        repository.store(
            DocumentToIndex(
                namespace=namespace,
                source=source,
                slug=slug,
                title=slug,
                fault_class=fault_class,
                feeds="",
                content_hash="h",
                embedding_model=EMBEDDER.model_name,
                chunks=[
                    ChunkToIndex(i, "Symptoms", text, EMBEDDER.embed_documents([text])[0])
                    for i, text in enumerate(texts)
                ],
            ),
            at=NOW,
        )


@pytest.fixture
def repository(engine: Engine) -> KnowledgeRepository:
    repository = KnowledgeRepository(engine)
    store(repository)
    return repository


@pytest.fixture
def retriever(repository: KnowledgeRepository) -> Retriever:
    return Retriever(repository, EMBEDDER)


def test_the_best_matching_chunk_comes_first(retriever: Retriever) -> None:
    hits = retriever.search("the nav is blank and not_null fails")

    assert hits[0].chunk.slug == "nulls"


def test_a_hit_says_where_each_search_ranked_it(retriever: Retriever) -> None:
    top = retriever.search("prices in paise a hundred times too large")[0]

    assert top.chunk.slug == "unit-change"
    assert (top.vector_rank, top.keyword_rank) == (1, 1)
    assert top.score > 0


def test_a_chunk_found_by_words_alone_still_appears(
    repository: KnowledgeRepository,
) -> None:
    # A vector search always returns the nearest chunks; the keyword side is what decides
    # that some chunks do not match at all, so a hit can be absent from the keyword ranking.
    hits = Retriever(repository, EMBEDDER).search("paise", limit=4)

    assert {h.chunk.slug for h in hits if h.keyword_rank is not None} == {"unit-change", "inc-7"}
    assert any(h.keyword_rank is None for h in hits)


def test_filters_narrow_by_fault_class_and_by_source(retriever: Retriever) -> None:
    by_class = retriever.search("paise", fault_class="unit_change")
    by_source = retriever.search("paise", source="incident")

    assert {h.chunk.fault_class for h in by_class} == {"unit_change"}
    assert {h.chunk.slug for h in by_source} == {"inc-7"}


def test_a_filter_that_matches_nothing_returns_nothing(retriever: Retriever) -> None:
    assert retriever.search("paise", fault_class="schema_drift") == []


def test_the_limit_caps_the_hits(retriever: Retriever) -> None:
    assert len(retriever.search("a file value", limit=2)) == 2


def test_a_namespace_cannot_see_another_namespace(
    repository: KnowledgeRepository,
) -> None:
    store(repository, namespace="bench")
    slugs = {"nulls", "unit-change", "inc-7", "late-data"}

    live = Retriever(repository, EMBEDDER).search("paise", limit=20)
    bench = Retriever(repository, EMBEDDER, namespace="bench").search("paise", limit=20)
    empty = Retriever(repository, EMBEDDER, namespace="other").search("paise")

    assert {h.chunk.slug for h in live} <= slugs
    assert {h.chunk.chunk_id for h in live}.isdisjoint({h.chunk.chunk_id for h in bench})
    assert empty == []


def test_a_blank_query_is_refused(retriever: Retriever) -> None:
    with pytest.raises(KnowledgeError, match="non-empty"):
        retriever.search("   ")


def test_query_words_are_lower_case_unique_and_free_of_syntax() -> None:
    assert query_words("Not_NULL fails; fails! (a | b)") == ["not", "null", "fails", "a", "b"]
