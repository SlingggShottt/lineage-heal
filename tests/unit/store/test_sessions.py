"""FR-API-02: sessions are stored hashed, expire, and can be removed."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.store.database import create_db_engine
from lineageheal.store.migrate import upgrade_database
from lineageheal.store.models import WebSession
from lineageheal.store.sessions import SessionRepository, StoredSession

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
LIVE = StoredSession(
    id_hash="a" * 64,
    github_id=4_000_000_001,
    login="ada",
    token_encrypted="gAAAA-encrypted",
    csrf_token="c" * 43,
    created_at=NOW,
    expires_at=NOW + timedelta(hours=8),
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'app.db'}"
    upgrade_database(url)
    engine = create_db_engine(url)
    yield engine
    engine.dispose()


def test_a_stored_session_is_read_back_unchanged(engine: Engine) -> None:
    repository = SessionRepository(engine)

    repository.create(LIVE)

    assert repository.get(LIVE.id_hash, NOW + timedelta(hours=1)) == LIVE


def test_an_unknown_session_is_none(engine: Engine) -> None:
    assert SessionRepository(engine).get("f" * 64, NOW) is None


def test_an_expired_session_is_reported_as_missing_but_not_deleted(engine: Engine) -> None:
    repository = SessionRepository(engine)
    repository.create(LIVE)
    after = LIVE.expires_at

    assert repository.get(LIVE.id_hash, after) is None
    with Session(engine) as session:
        assert session.get(WebSession, LIVE.id_hash) is not None  # reading never deletes


def test_signing_out_removes_the_session_and_says_whether_there_was_one(engine: Engine) -> None:
    repository = SessionRepository(engine)
    repository.create(LIVE)

    assert repository.delete(LIVE.id_hash) is True
    assert repository.delete(LIVE.id_hash) is False
    assert repository.get(LIVE.id_hash, NOW) is None


def test_pruning_removes_only_what_has_expired(engine: Engine) -> None:
    repository = SessionRepository(engine)
    old = replace(LIVE, id_hash="b" * 64, expires_at=NOW + timedelta(hours=1))
    repository.create(LIVE)
    repository.create(old)

    removed = repository.prune_expired(NOW + timedelta(hours=2))

    assert removed == 1
    assert repository.get(LIVE.id_hash, NOW + timedelta(hours=2)) == LIVE
    assert repository.get(old.id_hash, NOW) is None


def test_the_same_id_cannot_be_stored_twice(engine: Engine) -> None:
    repository = SessionRepository(engine)
    repository.create(LIVE)

    with pytest.raises(StorageError, match="Could not store the session"):
        repository.create(LIVE)


def test_a_session_that_expires_before_it_starts_is_refused_by_the_database(
    engine: Engine,
) -> None:
    with pytest.raises(StorageError, match="ck_web_session_expires_after_creation"):
        SessionRepository(engine).create(replace(LIVE, expires_at=NOW - timedelta(seconds=1)))


def test_the_error_messages_never_contain_the_token(engine: Engine) -> None:
    repository = SessionRepository(engine)
    repository.create(LIVE)

    with pytest.raises(StorageError) as raised:
        repository.create(LIVE)

    assert LIVE.token_encrypted not in str(raised.value)
