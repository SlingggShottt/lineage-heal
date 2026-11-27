"""Published benchmark runs, for the API to serve (FR-API-01).

A run is published as its whole scored report, keyed by the run id. This module only stores and
reads; checking that a report has the right shape is `core.benchmark_report`'s job, done before
anything is written.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import BenchmarkRun
from lineageheal.store.types import json_safe


class BenchmarkRepository:
    """Publishes and reads scored benchmark runs."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def publish(
        self, run_id: str, report: dict[str, Any], *, generated_at: datetime, at: datetime
    ) -> bool:
        """Store a run's report, replacing an earlier publication of the same run.

        Returns:
            True when the run is new, False when it replaced an existing one.

        Raises:
            StorageError: The database rejected the write.
        """
        try:
            with Session(self._engine) as session, session.begin():
                row = session.get(BenchmarkRun, run_id)
                created = row is None
                if row is None:
                    row = BenchmarkRun(run_id=run_id)
                    session.add(row)
                row.generated_at = generated_at
                row.published_at = at
                row.test_cases = int(report.get("test_cases", 0))
                row.report = json_safe(report)
            return created
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not publish the run: {explain_database_error(exc)}") from exc

    def list(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        """The reports of published runs, newest scored first, and how many there are in all.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                total = session.scalar(select(func.count()).select_from(BenchmarkRun)) or 0
                rows = session.scalars(
                    select(BenchmarkRun)
                    .order_by(BenchmarkRun.generated_at.desc(), BenchmarkRun.run_id)
                    .limit(limit)
                    .offset(offset)
                )
                return [dict(row.report) for row in rows], total
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the runs: {explain_database_error(exc)}") from exc

    def get(self, run_id: str) -> dict[str, Any] | None:
        """One run's report, or None when it was never published.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                row = session.get(BenchmarkRun, run_id)
                return None if row is None else dict(row.report)
        except SQLAlchemyError as exc:
            raise StorageError(f"Could not read the run: {explain_database_error(exc)}") from exc
