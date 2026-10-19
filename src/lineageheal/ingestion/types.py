"""Types shared by every feed loader."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from lineageheal.ingestion.raw_zone import StoredObject


class IngestionOutcome(StrEnum):
    """What happened when a loader processed one trade date."""

    STORED = "stored"  # a new object was written to the raw zone
    UNCHANGED = "unchanged"  # an identical file was already stored
    NO_DATA = "no_data"  # the source published nothing for this date


@dataclass(frozen=True)
class IngestionResult:
    """The result of loading one source and dataset for one trade date."""

    source: str
    dataset: str
    trade_date: date
    outcome: IngestionOutcome
    http_status: int
    stored: StoredObject | None  # None when the source had no data
    staged_key: str | None = None  # the unzipped copy, for archived feeds


class Loader(Protocol):
    """What the command-line tool needs from a feed loader."""

    source: str
    dataset: str

    def load(self, trade_date: date) -> IngestionResult:
        """Download one trade date's file and store it in the raw zone."""
        ...
