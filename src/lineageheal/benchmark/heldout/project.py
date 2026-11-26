"""The dbt project as it was before NSE's July 2024 change (design 12.4, D-81).

The replay must patch the pipeline NSE's change broke, not today's, which already reads UDiFF. So
the replay works on a scratch git repository: the project's dbt directory at `HEAD` with one
change, the NSE bhavcopy source and bronze model reading the old layout. Everything downstream
(silver, gold, tests) is untouched, because the old bronze model still emits the same typed
columns; it simply has no instrument id, name or settlement price, which UDiFF added. The
Fixer, the sandbox and the baselines then take this repository as their root.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import yaml

from lineageheal.benchmark.heldout.legacy import LEGACY_COLUMNS
from lineageheal.core.errors import BenchmarkError

BRONZE_MODEL = "dbt/models/bronze/brz_nse__cm_bhavcopy.sql"
SOURCES = "dbt/models/bronze/_bronze__sources.yml"

# Reads the old layout and emits the columns the UDiFF model does, so nothing downstream changes.
LEGACY_BRONZE_SQL = """\
-- NSE's bhavcopy as published before July 2024: no instrument id, name or settlement price.
with

raw as (
    select
        * exclude (trade_date),
        trade_date::date as _trade_date,
    from {{ source('raw_nse', 'cm_bhavcopy') }}
),

typed as (
    select
        cast(null as bigint) as instrument_id,
        trim("SYMBOL") as symbol,
        trim("SERIES") as series,
        nullif(trim("ISIN"), '') as isin,
        cast(null as varchar) as instrument_name,
        try_cast(trim("OPEN") as decimal(18, 4)) as open_price_inr,
        try_cast(trim("HIGH") as decimal(18, 4)) as high_price_inr,
        try_cast(trim("LOW") as decimal(18, 4)) as low_price_inr,
        try_cast(trim("CLOSE") as decimal(18, 4)) as close_price_inr,
        try_cast(trim("LAST") as decimal(18, 4)) as last_price_inr,
        try_cast(trim("PREVCLOSE") as decimal(18, 4)) as prev_close_price_inr,
        cast(null as decimal(18, 4)) as settlement_price_inr,
        try_cast(trim("TOTTRDQTY") as bigint) as volume,
        try_cast(trim("TOTTRDVAL") as decimal(20, 2)) as turnover_inr,
        try_cast(trim("TOTALTRADES") as bigint) as trades,
        try_strptime(trim("TIMESTAMP"), '%d-%b-%Y')::date as trade_date,
        _trade_date,
        filename as _source_file,
    from raw
)

select * from typed
"""

_COLUMN_NOTES = {
    "SYMBOL": "Ticker symbol.",
    "SERIES": "Series, such as EQ, BE or SM.",
    "OPEN": "Opening price in rupees.",
    "HIGH": "Highest price of the day in rupees.",
    "LOW": "Lowest price of the day in rupees.",
    "CLOSE": "Closing price in rupees.",
    "LAST": "Last traded price in rupees.",
    "PREVCLOSE": "Previous day's closing price in rupees.",
    "TOTTRDQTY": "Total traded quantity.",
    "TOTTRDVAL": "Total traded value in rupees.",
    "TIMESTAMP": "Trade date as DD-MON-YYYY, for example 05-JUL-2024.",
    "TOTALTRADES": "Number of trades.",
    "ISIN": "International Securities Identification Number.",
}


def build_prechange_repo(repo_root: Path, destination: Path) -> Path:
    """Create a git repository at `destination` holding the pre-July-2024 dbt project.

    Args:
        repo_root: The real repository; its committed `dbt/` directory is the starting point.
        destination: A folder that does not exist yet.

    Returns:
        `destination`, a repository with one commit, whose `dbt/` is ready for `dbt build`.

    Raises:
        BenchmarkError: The destination exists, git could not export the project, or the source
            definition does not have the table the overlay replaces.
    """
    if destination.exists():
        raise BenchmarkError(f"{destination} already exists")
    destination.mkdir(parents=True)
    archive = _git(repo_root, "archive", "--format=tar", "HEAD", "dbt", binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    (destination / BRONZE_MODEL).write_text(LEGACY_BRONZE_SQL, encoding="utf-8")
    _rewrite_source(destination / SOURCES)
    _git(destination, "init", "--quiet")
    _git(destination, "add", ".")
    _git(
        destination,
        "-c", "user.name=lineageheal-benchmark",
        "-c", "user.email=benchmark@example.invalid",
        "commit", "--quiet", "-m", "The dbt project before NSE's July 2024 format change",
    )  # fmt: skip
    return destination


def _rewrite_source(path: Path) -> None:
    """Declare the old layout's columns on the NSE bhavcopy source."""
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    for source in document.get("sources", []):
        for table in source.get("tables", []):
            if source["name"] == "raw_nse" and table["name"] == "cm_bhavcopy":
                table["columns"] = [
                    {"name": name, "description": _COLUMN_NOTES[name]} for name in LEGACY_COLUMNS
                ]
                path.write_text(
                    yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True),
                    encoding="utf-8",
                )
                return
    raise BenchmarkError("raw_nse.cm_bhavcopy is not declared in the bronze sources")


def _git(directory: Path, *args: str, binary: bool = False) -> Any:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(directory), *args],  # noqa: S607
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise BenchmarkError(
            f"git {args[0]} failed: {completed.stderr.decode(errors='replace').strip()[-300:]}"
        )
    return completed.stdout if binary else completed.stdout.decode()
