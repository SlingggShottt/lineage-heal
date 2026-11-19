"""A throwaway database on the development Postgres, shared by the integration tests."""

import os
import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

# The development database from docker-compose.yml. Override to test another server.
ADMIN_URL = os.environ.get(
    "LINEAGEHEAL_TEST_POSTGRES_URL",
    "postgresql+psycopg://lineageheal:lineageheal@localhost:5433/postgres",
)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A throwaway database on the development server, dropped afterwards."""
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"lh_test_{secrets.token_hex(4)}"
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except SQLAlchemyError:
        admin.dispose()
        pytest.skip("Postgres is not reachable; start it with `make up`")
    try:
        yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
