"""The knowledge index in the application database: documents, their chunks and embeddings.

This module only stores and reads. What goes in (chunking, embedding) is `lineageheal.rag`,
and searching is added with hybrid retrieval (LH-048).
"""

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import KnowledgeChunk, KnowledgeDocument


class IndexOutcome(enum.StrEnum):
    """What storing a document did."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class DocumentToIndex:
    """A document and its embedded chunks, ready to store."""

    namespace: str
    source: str
    slug: str
    title: str
    fault_class: str
    feeds: str
    content_hash: str
    embedding_model: str
    chunks: Sequence["ChunkToIndex"]


@dataclass(frozen=True)
class ChunkToIndex:
    """One chunk with its vector."""

    position: int
    heading: str
    text: str
    embedding: list[float]


class KnowledgeRepository:
    """Stores indexed documents and keeps re-indexing cheap and safe to repeat."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def is_current(
        self, namespace: str, source: str, slug: str, content_hash: str, embedding_model: str
    ) -> bool:
        """Whether the stored copy has this content and was embedded by this model.

        The indexer asks before embedding, so an unchanged document costs no model time.
        """
        try:
            with Session(self._engine) as session:
                document = self._find(session, namespace, source, slug)
                if document is None or document.content_hash != content_hash:
                    return False
                models = session.scalars(
                    select(KnowledgeChunk.embedding_model)
                    .where(KnowledgeChunk.document_id == document.id)
                    .distinct()
                ).all()
                return list(models) == [embedding_model]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the knowledge index: {_why(exc)}") from exc

    def store(self, document: DocumentToIndex, *, at: datetime | None = None) -> IndexOutcome:
        """Add a document, or replace its chunks when its content or embedding model changed.

        The swap happens in one transaction, so a reader never sees a document with half its
        chunks.

        Raises:
            StorageError: The database rejected the write.
        """
        indexed_at = at or datetime.now(UTC)
        try:
            with Session(self._engine) as session, session.begin():
                existing = self._find(session, document.namespace, document.source, document.slug)
                if existing is None:
                    row = KnowledgeDocument(
                        namespace=document.namespace,
                        source=document.source,
                        slug=document.slug,
                        title=document.title,
                        fault_class=document.fault_class,
                        feeds=document.feeds,
                        content_hash=document.content_hash,
                        indexed_at=indexed_at,
                    )
                    session.add(row)
                    session.flush()
                    outcome = IndexOutcome.ADDED
                else:
                    row = existing
                    row.title = document.title
                    row.fault_class = document.fault_class
                    row.feeds = document.feeds
                    row.content_hash = document.content_hash
                    row.indexed_at = indexed_at
                    session.execute(
                        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == row.id)
                    )
                    outcome = IndexOutcome.UPDATED
                session.add_all(
                    KnowledgeChunk(
                        document_id=row.id,
                        position=chunk.position,
                        heading=chunk.heading,
                        text=chunk.text,
                        embedding=chunk.embedding,
                        embedding_model=document.embedding_model,
                    )
                    for chunk in document.chunks
                )
            return outcome
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not write the knowledge index: {_why(exc)}") from exc

    def remove_missing(self, namespace: str, source: str, keep: Sequence[str]) -> list[str]:
        """Delete documents of a source that are no longer in `keep`, returning their slugs.

        A runbook deleted from the repository must also leave the index, or the agents would
        keep citing advice nobody maintains.
        """
        try:
            with Session(self._engine) as session, session.begin():
                stale = session.scalars(
                    select(KnowledgeDocument.slug)
                    .where(
                        KnowledgeDocument.namespace == namespace,
                        KnowledgeDocument.source == source,
                        KnowledgeDocument.slug.not_in(list(keep)),
                    )
                    .order_by(KnowledgeDocument.slug)
                ).all()
                if stale:
                    session.execute(
                        delete(KnowledgeDocument).where(
                            KnowledgeDocument.namespace == namespace,
                            KnowledgeDocument.source == source,
                            KnowledgeDocument.slug.in_(stale),
                        )
                    )
                return list(stale)
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not update the knowledge index: {_why(exc)}") from exc

    def counts(self, namespace: str) -> tuple[int, int]:
        """The number of documents and chunks in a namespace."""
        try:
            with Session(self._engine) as session:
                documents = session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocument)
                    .where(KnowledgeDocument.namespace == namespace)
                )
                chunks = session.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                    .where(KnowledgeDocument.namespace == namespace)
                )
                return documents or 0, chunks or 0
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the knowledge index: {_why(exc)}") from exc

    @staticmethod
    def _find(session: Session, namespace: str, source: str, slug: str) -> KnowledgeDocument | None:
        return session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.namespace == namespace,
                KnowledgeDocument.source == source,
                KnowledgeDocument.slug == slug,
            )
        )


def _why(exc: SQLAlchemyError) -> str:
    return explain_database_error(exc)
