"""The knowledge index in the application database: documents, their chunks and embeddings.

This module only stores and reads. What goes in (chunking, embedding) and how the two kinds of
search are combined is `lineageheal.rag`; here each search is a single ranked query.
"""

import enum
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Engine, Float, delete, func, literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import KnowledgeChunk, KnowledgeDocument
from lineageheal.store.types import EMBEDDING_DIMENSIONS


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


@dataclass(frozen=True)
class ChunkMatch:
    """A chunk found by one search, with the document it belongs to."""

    chunk_id: int
    slug: str
    source: str
    title: str
    fault_class: str
    heading: str
    text: str


_WORD = re.compile(r"[a-z0-9]+")


def query_words(query: str) -> list[str]:
    """The lower-case alphanumeric words of a query, in order and without repeats.

    Anything else is dropped, which also keeps a query from being read as full-text syntax.
    """
    return list(dict.fromkeys(_WORD.findall(query.lower())))


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

    def vector_search(
        self,
        namespace: str,
        embedding: Sequence[float],
        *,
        limit: int,
        fault_class: str | None = None,
        source: str | None = None,
    ) -> list[ChunkMatch]:
        """The chunks nearest to a query vector by cosine distance, best first.

        Postgres ranks with pgvector's `<=>`, which the HNSW index serves. SQLite has no vector
        type, so it ranks in Python; that is only for unit tests and small stores.
        """
        try:
            with Session(self._engine) as session:
                query = self._chunks(session, namespace, fault_class, source)
                if self._engine.dialect.name == "postgresql":
                    distance = KnowledgeChunk.embedding.op("<=>", return_type=Float)(
                        literal(list(embedding), Vector(EMBEDDING_DIMENSIONS))
                    )
                    rows = session.execute(query.order_by(distance, KnowledgeChunk.id).limit(limit))
                    return [_match(chunk, doc) for chunk, doc in rows]
                scored = [
                    (_cosine_distance(embedding, chunk.embedding), chunk.id, chunk, doc)
                    for chunk, doc in session.execute(query)
                ]
                scored.sort(key=lambda item: item[:2])
                return [_match(chunk, doc) for _, _, chunk, doc in scored[:limit]]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not search the knowledge index: {_why(exc)}") from exc

    def keyword_search(
        self,
        namespace: str,
        query: str,
        *,
        limit: int,
        fault_class: str | None = None,
        source: str | None = None,
    ) -> list[ChunkMatch]:
        """The chunks containing any of the query's words, best first.

        A chunk needs only one word to match, because queries here are sentences describing a
        failure and requiring every word would find nothing. Postgres ranks with `ts_rank_cd`
        over English-stemmed text, using the GIN index; SQLite counts matching words, which is
        cruder but orders the same way for the small cases the unit tests use.
        """
        words = query_words(query)
        if not words:
            return []
        try:
            with Session(self._engine) as session:
                base = self._chunks(session, namespace, fault_class, source)
                if self._engine.dialect.name == "postgresql":
                    tsquery = func.to_tsquery("english", " | ".join(words))
                    vector = func.to_tsvector("english", KnowledgeChunk.text)
                    rows = session.execute(
                        base.where(vector.op("@@")(tsquery))
                        .order_by(func.ts_rank_cd(vector, tsquery).desc(), KnowledgeChunk.id)
                        .limit(limit)
                    )
                    return [_match(chunk, doc) for chunk, doc in rows]
                scored = []
                for chunk, doc in session.execute(base):
                    present = set(_WORD.findall(chunk.text.lower()))
                    hits = sum(word in present for word in words)
                    if hits:
                        scored.append((-hits, chunk.id, chunk, doc))
                scored.sort(key=lambda item: item[:2])
                return [_match(chunk, doc) for _, _, chunk, doc in scored[:limit]]
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not search the knowledge index: {_why(exc)}") from exc

    @staticmethod
    def _chunks(
        session: Session, namespace: str, fault_class: str | None, source: str | None
    ) -> Select[KnowledgeChunk, KnowledgeDocument]:
        query = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeDocument.namespace == namespace)
        )
        if fault_class is not None:
            query = query.where(KnowledgeDocument.fault_class == fault_class)
        if source is not None:
            query = query.where(KnowledgeDocument.source == source)
        return query

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


def _match(chunk: KnowledgeChunk, document: KnowledgeDocument) -> ChunkMatch:
    return ChunkMatch(
        chunk_id=chunk.id,
        slug=document.slug,
        source=document.source,
        title=document.title,
        fault_class=document.fault_class,
        heading=chunk.heading,
        text=chunk.text,
    )


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0:
        return 1.0
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True)) / norm
