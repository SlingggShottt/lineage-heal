"""The knowledge index on a real Postgres with pgvector (needs `make up`).

SQLite stores vectors as JSON, so only here can we see the vector column, the HNSW index and
full-text search work as the retrieval task (LH-048) will rely on them.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from schema_drift import schema_differences
from sqlalchemy import Engine, text

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.embeddings import Embedder, FastEmbedder, HashingEmbedder
from lineageheal.rag.indexer import RunbookIndexer
from lineageheal.store.database import create_db_engine
from lineageheal.store.knowledge import KnowledgeRepository
from lineageheal.store.migrate import upgrade_database

RUNBOOKS = Path(__file__).parents[2] / "docs" / "runbooks"

NEAREST = text(
    """
    SELECT d.slug, c.heading
    FROM knowledge_chunk c JOIN knowledge_document d ON d.id = c.document_id
    ORDER BY c.embedding <=> CAST(:query AS vector)
    LIMIT 3
    """
)
KEYWORD = text(
    """
    SELECT DISTINCT d.slug
    FROM knowledge_chunk c JOIN knowledge_document d ON d.id = c.document_id
    WHERE to_tsvector('english', c.text) @@ plainto_tsquery('english', :words)
    ORDER BY d.slug
    """
)


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    yield engine
    engine.dispose()


def index_with(engine: Engine, embedder: Embedder) -> None:
    RunbookIndexer(KnowledgeRepository(engine), embedder).index(RUNBOOKS)


def test_the_migration_matches_the_models_and_creates_both_search_indexes(engine: Engine) -> None:
    with engine.connect() as connection:
        differences = schema_differences(connection)
        indexes = {
            row.indexname: row.indexdef
            for row in connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
                {"t": "knowledge_chunk"},
            )
        }
        extension = connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()

    assert differences == []
    assert extension == "vector"
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_knowledge_chunk_embedding"]
    assert (
        "USING gin (to_tsvector('english'::regconfig, text))"
        in indexes["ix_knowledge_chunk_text_search"]
    )


def test_the_nearest_vector_is_the_chunk_a_query_is_closest_to(engine: Engine) -> None:
    embedder = HashingEmbedder()
    index_with(engine, embedder)
    query = embedder.embed_query("a required value arrives blank and the not_null test fails")

    with engine.connect() as connection:
        rows = connection.execute(NEAREST, {"query": str(query)}).all()

    assert rows[0][0] == "nulls"


def test_full_text_search_finds_the_runbooks_that_use_a_word(engine: Engine) -> None:
    index_with(engine, HashingEmbedder())

    with engine.connect() as connection:
        slugs = connection.execute(KEYWORD, {"words": "paise"}).scalars().all()

    assert "unit-change" in slugs


def test_indexing_twice_on_postgres_changes_nothing(engine: Engine) -> None:
    index_with(engine, HashingEmbedder())
    first = KnowledgeRepository(engine).counts("live")

    index_with(engine, HashingEmbedder())

    assert KnowledgeRepository(engine).counts("live") == first == (5, 20)


@pytest.mark.slow
def test_the_real_model_puts_the_matching_runbook_first_for_a_paraphrase(
    engine: Engine, tmp_path: Path
) -> None:
    """Needs the bge model: downloaded once (about 130 MB) into the project's cache folder."""
    embedder = FastEmbedder("BAAI/bge-small-en-v1.5", Path(".data/models"))
    try:
        embedder.embed_query("warm up")
    except KnowledgeError as exc:
        pytest.skip(f"the embedding model is not available: {exc}")
    index_with(engine, embedder)
    query = embedder.embed_query("share prices suddenly a hundred times too big")

    with engine.connect() as connection:
        rows = connection.execute(NEAREST, {"query": str(query)}).all()

    assert rows[0][0] == "unit-change"
