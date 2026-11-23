"""Signed-in dashboard sessions in the application database (FR-API-02).

The API hashes the session id and encrypts the GitHub token before calling in here, so this
module only ever holds values that are safe to store and cannot be turned back into a cookie.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import WebSession


@dataclass(frozen=True)
class StoredSession:
    """One session as stored."""

    id_hash: str
    github_id: int
    login: str
    token_encrypted: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


class SessionRepository:
    """Creates, finds and removes sessions."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def create(self, stored: StoredSession) -> None:
        """Store a new session.

        Raises:
            StorageError: The session could not be written, or its id hash is already in use.
        """
        try:
            with Session(self._engine) as session, session.begin():
                session.add(WebSession(**vars(stored)))
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not store the session: {explain_database_error(exc)}"
            ) from exc

    def get(self, id_hash: str, now: datetime) -> StoredSession | None:
        """Return a session that exists and has not expired, or None.

        An expired session is reported exactly like a missing one, so no caller can forget the
        check. Reading never deletes; `prune_expired` does that.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                row = session.get(WebSession, id_hash)
                if row is None or row.expires_at <= now:
                    return None
                return StoredSession(
                    id_hash=row.id_hash,
                    github_id=row.github_id,
                    login=row.login,
                    token_encrypted=row.token_encrypted,
                    csrf_token=row.csrf_token,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not read the session: {explain_database_error(exc)}"
            ) from exc

    def delete(self, id_hash: str) -> bool:
        """Remove a session (sign out). Returns whether there was one to remove.

        Raises:
            StorageError: The database could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                result = cast(
                    CursorResult[Any],
                    session.execute(delete(WebSession).where(WebSession.id_hash == id_hash)),
                )
                return result.rowcount > 0
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not delete the session: {explain_database_error(exc)}"
            ) from exc

    def prune_expired(self, now: datetime) -> int:
        """Remove every session that has expired and return how many there were.

        Raises:
            StorageError: The database could not be written.
        """
        try:
            with Session(self._engine) as session, session.begin():
                result = cast(
                    CursorResult[Any],
                    session.execute(delete(WebSession).where(WebSession.expires_at <= now)),
                )
                return result.rowcount
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not prune sessions: {explain_database_error(exc)}") from exc
