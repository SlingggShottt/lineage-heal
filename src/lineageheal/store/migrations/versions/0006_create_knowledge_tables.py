"""Create the knowledge document and chunk tables, with vector and full-text indexes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from lineageheal.store.types import EmbeddingType

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
FAULT_CLASSES = "'schema_drift', 'nulls', 'duplicates', 'unit_change', 'late_data', 'unknown'"


def upgrade() -> None:
    """Create the tables; on Postgres also enable pgvector and add the full-text index."""
    if op.get_bind().dialect.name == "postgresql":
        # Needs a role allowed to create extensions; the Compose image and Neon both allow it.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_document",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("fault_class", sa.String(length=32), nullable=False),
        sa.Column("feeds", sa.String(length=256), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("indexed_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "source IN ('runbook', 'incident')", name=op.f("ck_knowledge_document_source_is_known")
        ),
        sa.CheckConstraint(
            f"fault_class IN ({FAULT_CLASSES})",
            name=op.f("ck_knowledge_document_fault_class_is_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_document")),
        sa.UniqueConstraint(
            "namespace", "source", "slug", name=op.f("uq_knowledge_document_identity")
        ),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("document_id", BIG_ID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", EmbeddingType, nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_document.id"],
            name="fk_knowledge_chunk_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunk")),
        sa.UniqueConstraint("document_id", "position", name=op.f("uq_knowledge_chunk_position")),
    )
    # Cosine distance, because bge embeddings are compared by direction. On SQLite the options
    # are ignored and this is a plain index, kept so the models and migrations describe the
    # same schema.
    op.create_index(
        "ix_knowledge_chunk_embedding",
        "knowledge_chunk",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    if op.get_bind().dialect.name == "postgresql":
        # An expression index: keyword search queries repeat this exact expression to use it.
        op.execute(
            "CREATE INDEX ix_knowledge_chunk_text_search ON knowledge_chunk "
            "USING gin (to_tsvector('english', text))"
        )


def downgrade() -> None:
    """Drop the tables; the pgvector extension stays, since other things may use it."""
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
