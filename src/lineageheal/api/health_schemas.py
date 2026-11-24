"""Response models for the feed health and runbook endpoints (FR-API-01)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

FeedStatus = Literal["ok", "late", "failing", "no_data"]


class FeedHealth(BaseModel):
    """One dataset's freshness, as the ingestion log shows it."""

    source: str
    dataset: str
    status: FeedStatus
    latest_trade_date: date | None
    # Days behind the newest date any feed has, never behind the clock (D-37).
    lag_days: int | None
    last_attempt_at: datetime | None
    last_attempt_outcome: str | None
    # The loader's own words for a failed attempt, clipped; empty otherwise.
    last_error: str | None


class RunHealth(BaseModel):
    """The newest dbt run in brief."""

    run_id: str
    status: Literal["succeeded", "failed"]
    generated_at: datetime
    errors: int
    warnings: int
    skipped_models: int


class FeedsHealth(BaseModel):
    """Everything the overview screen shows about the pipeline's inputs."""

    as_of: date | None
    feeds: list[FeedHealth]
    latest_run: RunHealth | None


class RunbookSection(BaseModel):
    """One section of a runbook, as the knowledge index splits it."""

    heading: str
    text: str


class RunbookSummary(BaseModel):
    """A runbook in the list."""

    slug: str
    title: str
    fault_class: str
    feeds: str


class Runbook(RunbookSummary):
    """A runbook with its text."""

    markdown: str
    sections: list[RunbookSection]
