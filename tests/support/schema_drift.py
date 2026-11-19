"""Compare the models with a migrated database, ignoring what the models cannot describe."""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection

from lineageheal.store.models import Base

# Expression indexes that migrations create on Postgres only; a model cannot declare them
# portably, and Alembic would otherwise report each as an index the models lack.
POSTGRES_ONLY_INDEXES = frozenset({"ix_knowledge_chunk_text_search"})


def _include(_obj: object, name: str | None, type_: str, _reflected: bool, _compare: Any) -> bool:
    return not (type_ == "index" and name in POSTGRES_ONLY_INDEXES)


def schema_differences(connection: Connection) -> list[Any]:
    """What Alembic would change to make the database match the models; empty when they agree."""
    context = MigrationContext.configure(connection, opts={"include_object": _include})
    return list(compare_metadata(context, Base.metadata))
