"""FR-KB-01: indexed documents are stored once, replaced when they change and removed when gone."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.store.knowledge import (
    ChunkToIndex,
    DocumentToIndex,
    IndexOutcome,
    KnowledgeRepository,
)
from lineageheal.store.models import KnowledgeChunk, KnowledgeDocument

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
VECTOR = [0.5] * 384


def document(
    slug: str = "nulls", *, content_hash: str = "h1", model: str = "m1", chunks: int = 2
) -> DocumentToIndex:
    return DocumentToIndex(
        namespace="live",
        source="runbook",
        slug=slug,
        title="A value goes missing",
        fault_class="nulls",
        feeds="AMFI",
        content_hash=content_hash,
        embedding_model=model,
        chunks=[ChunkToIndex(i, f"Heading {i}", f"text {i}", VECTOR) for i in range(chunks)],
    )


@pytest.fixture
def repository(engine: Engine) -> KnowledgeRepository:
    return KnowledgeRepository(engine)


def test_a_new_document_is_added_with_its_chunks_and_vectors(
    repository: KnowledgeRepository, engine: Engine
) -> None:
    assert repository.store(document(), at=NOW) is IndexOutcome.ADDED

    with Session(engine) as session:
        chunks = session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.position)).all()
    assert [(c.position, c.heading, c.embedding_model) for c in chunks] == [
        (0, "Heading 0", "m1"),
        (1, "Heading 1", "m1"),
    ]
    assert chunks[0].embedding == VECTOR
    assert repository.counts("live") == (1, 2)


def test_a_changed_document_replaces_its_chunks_in_place(
    repository: KnowledgeRepository, engine: Engine
) -> None:
    repository.store(document(), at=NOW)

    outcome = repository.store(document(content_hash="h2", chunks=3), at=NOW)

    assert outcome is IndexOutcome.UPDATED
    assert repository.counts("live") == (1, 3)
    with Session(engine) as session:
        assert session.scalar(select(KnowledgeDocument.content_hash)) == "h2"


def test_a_document_is_current_only_for_the_same_content_and_model(
    repository: KnowledgeRepository,
) -> None:
    assert not repository.is_current("live", "runbook", "nulls", "h1", "m1")
    repository.store(document(), at=NOW)

    assert repository.is_current("live", "runbook", "nulls", "h1", "m1")
    assert not repository.is_current("live", "runbook", "nulls", "h2", "m1")
    assert not repository.is_current("live", "runbook", "nulls", "h1", "m2")
    assert not repository.is_current("bench", "runbook", "nulls", "h1", "m1")


def test_namespaces_keep_the_same_document_apart(repository: KnowledgeRepository) -> None:
    repository.store(document(), at=NOW)
    other = document()
    repository.store(
        DocumentToIndex(**{**other.__dict__, "namespace": "bench"}),  # type: ignore[arg-type]
        at=NOW,
    )

    assert repository.counts("live") == (1, 2)
    assert repository.counts("bench") == (1, 2)


def test_documents_missing_from_the_source_are_removed_with_their_chunks(
    repository: KnowledgeRepository,
) -> None:
    for slug in ("nulls", "duplicates", "late-data"):
        repository.store(document(slug), at=NOW)

    removed = repository.remove_missing("live", "runbook", ["nulls"])

    assert removed == ["duplicates", "late-data"]
    assert repository.counts("live") == (1, 2)


def test_the_database_refuses_an_unknown_source(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        session.add(
            KnowledgeDocument(
                namespace="live",
                source="blog",
                slug="x",
                title="t",
                fault_class="nulls",
                feeds="",
                content_hash="h",
                indexed_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
