"""Whether each feed is fresh, from the ingestion log and the latest run (FR-API-01).

The API host has no warehouse, so this reads only the application database. Freshness is judged
against the newest date any feed has, never the clock, the same rule the dbt expected-file check
uses (D-37), so a replay or a paused project does not show every feed as late.
"""

from __future__ import annotations

from datetime import date

from lineageheal.api.health_schemas import FeedHealth, FeedsHealth, FeedStatus, RunHealth
from lineageheal.store.ingestion_log import FeedState, IngestionLogRepository
from lineageheal.store.runs import RunRepository

# The datasets the loaders offer. A fresh database has an empty log, and the overview still needs
# something to draw, so each is reported as `no_data` until it has a file. A test keeps this in
# step with `ingestion.runner.DATASETS`, which the API may not import.
KNOWN_FEEDS: tuple[tuple[str, str], ...] = (
    ("amfi", "navall"),
    ("nse", "cm_bhavcopy"),
    ("nse", "security_master"),
    ("nse", "corporate_actions"),
    ("fbil", "reference_rates"),
)
# Weekends and market holidays leave a feed two or three days behind another without a fault;
# beyond this it is late. The exact judgement per trading day is dbt's expected-file check.
LATE_AFTER_DAYS = 3
ERROR_LIMIT = 200


def feeds_health(log: IngestionLogRepository, runs: RunRepository) -> FeedsHealth:
    """The state of every known feed and of the newest run.

    Raises:
        StorageError: The database could not be read.
    """
    states = [log.feed_state(source, dataset) for source, dataset in KNOWN_FEEDS]
    dates = [s.latest_trade_date for s in states if s.latest_trade_date is not None]
    newest = max(dates) if dates else None
    latest = runs.latest()
    return FeedsHealth(
        as_of=newest,
        feeds=[_feed(state, newest) for state in states],
        latest_run=None
        if latest is None
        else RunHealth(
            run_id=latest.run_id,
            status="failed" if latest.status == "failed" else "succeeded",
            generated_at=latest.generated_at,
            errors=latest.errors,
            warnings=latest.warnings,
            skipped_models=latest.skipped_models,
        ),
    )


def _feed(state: FeedState, newest: date | None) -> FeedHealth:
    lag = None
    if state.latest_trade_date is not None and newest is not None:
        lag = (newest - state.latest_trade_date).days
    attempt = state.last_attempt
    failed = attempt is not None and attempt.outcome == "failed"
    status: FeedStatus
    if failed:
        status = "failing"
    elif state.latest_trade_date is None:
        status = "no_data"
    elif lag is not None and lag > LATE_AFTER_DAYS:
        status = "late"
    else:
        status = "ok"
    return FeedHealth(
        source=state.source,
        dataset=state.dataset,
        status=status,
        latest_trade_date=state.latest_trade_date,
        lag_days=lag,
        last_attempt_at=None if attempt is None else attempt.finished_at,
        last_attempt_outcome=None if attempt is None else attempt.outcome,
        last_error=(attempt.error or "")[:ERROR_LIMIT] if failed and attempt else None,
    )
