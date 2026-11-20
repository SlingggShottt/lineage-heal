"""Tools that read about the sources: the ingestion log, the calendar and past knowledge."""

from datetime import date
from typing import Literal

from pydantic import Field

from lineageheal.agents.tools.base import (
    MAX_ROWS,
    Payload,
    Tool,
    ToolArguments,
    ToolContext,
    ToolError,
    clip,
)
from lineageheal.ingestion.calendar import expectation, read_seed

Source = Literal["amfi", "nse"]
MAX_HITS = 5
MAX_HIT_CHARACTERS = 1200
MAX_ERROR_CHARACTERS = 300


class GetIngestionLogArguments(ToolArguments):
    """Which feed and trade date to look up."""

    source: Source
    dataset: str = Field(
        pattern=r"^[a-z_]{1,64}$", description="For example 'navall' or 'cm_bhavcopy'."
    )
    trade_date: date = Field(description="The trade date, as YYYY-MM-DD.")


def get_ingestion_log(context: ToolContext, args: GetIngestionLogArguments) -> Payload:
    """Every download attempt for a feed and date, oldest first, newest kept when too many."""
    if context.ingestion_log is None:
        raise ToolError("No database is configured, so the ingestion log cannot be read")
    attempts = context.ingestion_log.for_day(args.source, args.dataset, args.trade_date)
    omitted = max(len(attempts) - MAX_ROWS, 0)
    return {
        "source": args.source,
        "dataset": args.dataset,
        "trade_date": args.trade_date.isoformat(),
        "attempts": [
            {
                "outcome": a.outcome,
                "finished_at": a.finished_at.isoformat(),
                "http_status": a.http_status,
                "size_bytes": a.size_bytes,
                "sha256": a.sha256[:12] if a.sha256 else None,
                "object_key": a.object_key,
                "error": clip(a.error, MAX_ERROR_CHARACTERS) if a.error else None,
            }
            for a in attempts[omitted:]
        ],
        "older_attempts_omitted": omitted,
    }


class GetCalendarArguments(ToolArguments):
    """Which feed and date to check."""

    source: Source
    trade_date: date = Field(description="The date, as YYYY-MM-DD.")


def get_calendar(context: ToolContext, args: GetCalendarArguments) -> Payload:
    """Whether the feed was expected to publish a file on a date, and why."""
    entries = read_seed(context.calendar_seed)
    reason, expected = expectation(args.source, args.trade_date, entries)
    entry = next((e for e in entries if e.calendar_date == args.trade_date), None)
    return {
        "source": args.source,
        "trade_date": args.trade_date.isoformat(),
        "weekday": args.trade_date.strftime("%A"),
        "file_expected": expected,
        "reason": reason,
        "calendar_entry": (
            {"status": entry.status.value, "description": entry.description, "from": entry.source}
            if entry
            else None
        ),
    }


class SearchKnowledgeArguments(ToolArguments):
    """What to look for in past incidents and runbooks."""

    query: str = Field(min_length=1, max_length=500, description="Describe the failure in words.")
    fault_class: (
        Literal["schema_drift", "nulls", "duplicates", "unit_change", "late_data"] | None
    ) = None
    source: Literal["runbook", "incident"] | None = None
    limit: int = Field(default=3, ge=1, le=MAX_HITS)


def search_knowledge(context: ToolContext, args: SearchKnowledgeArguments) -> Payload:
    """The runbook sections and past incidents most similar to a description of the failure."""
    if context.retriever is None:
        raise ToolError("No knowledge index is available")
    hits = context.retriever.search(
        args.query, fault_class=args.fault_class, source=args.source, limit=args.limit
    )
    return {
        "hits": [
            {
                "source": hit.chunk.source,
                "document": hit.chunk.slug,
                "heading": hit.chunk.heading,
                "fault_class": hit.chunk.fault_class,
                "score": round(hit.score, 4),
                "text": clip(hit.chunk.text, MAX_HIT_CHARACTERS),
            }
            for hit in hits
        ]
    }


SOURCE_TOOLS = (
    Tool(
        "get_ingestion_log",
        "List the download attempts for a feed and trade date: outcome, HTTP status, size, "
        "file hash and error. Shows whether a file arrived, failed or was republished.",
        GetIngestionLogArguments,
        get_ingestion_log,
    ),
    Tool(
        "get_calendar",
        "Say whether a feed was expected to publish a file on a date (weekday, weekend, holiday).",
        GetCalendarArguments,
        get_calendar,
    ),
    Tool(
        "search_knowledge",
        "Find the runbook sections and past incidents most similar to a failure description.",
        SearchKnowledgeArguments,
        search_knowledge,
    ),
)
