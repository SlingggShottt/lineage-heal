"""The daily partitions every raw asset shares.

One partition is one trade date. They start on the day the loaders' history starts and end
the day before today in India, because a day's files are published after it closes. A run
for partition D is therefore the "previous trade date" run the daily schedule launches.
"""

from dagster import DailyPartitionsDefinition

from lineageheal.ingestion.runner import EARLIEST_BACKFILL

# Trade dates are Asia/Kolkata calendar dates (CLAUDE.md, Data), whatever the server's zone.
TIMEZONE = "Asia/Kolkata"

daily_partitions = DailyPartitionsDefinition(
    start_date=EARLIEST_BACKFILL.isoformat(), timezone=TIMEZONE
)
