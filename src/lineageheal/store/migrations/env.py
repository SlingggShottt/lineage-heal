"""Alembic environment: where migrations get their database and their models."""

from alembic import context
from sqlalchemy import create_engine, pool

from lineageheal.core.settings import get_settings
from lineageheal.store.models import Base

config = context.config

# `lineageheal db upgrade` passes the URL in; the plain `alembic` command reads DATABASE_URL.
url: str = config.attributes.get("database_url") or get_settings().require_database_url()

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting, for review."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the database."""
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
