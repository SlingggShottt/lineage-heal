"""Tools that look at the data itself: raw file schemas, column profiles, rows and actions.

Faults show up here first. A renamed column is visible in a raw file's header before any model
fails, a unit change in a column's mean and range, and a split in the corporate actions around
a price jump. Every tool reads through `data_access`, which holds the safety rules, and takes
only names and dates the model could not use to reach anything else.
"""

from datetime import date, timedelta
from typing import Any

from pydantic import Field, model_validator

from lineageheal.agents.tools.base import (
    MAX_ROWS,
    Payload,
    Tool,
    ToolArguments,
    ToolContext,
    ToolError,
    clip,
)
from lineageheal.agents.tools.data_access import RawFileReader, Warehouse
from lineageheal.agents.tools.source_tools import Source

NAME = r"^[A-Za-z0-9_]{1,100}$"
MAX_SAMPLE_ROWS = 10
MAX_SAMPLE_COLUMNS = 20
MAX_VALUE_CHARACTERS = 200
MAX_COLUMNS_LISTED = 200


def _reader(context: ToolContext) -> RawFileReader:
    if context.raw_files is None:
        raise ToolError("No raw zone is available to read files from")
    return context.raw_files


def _warehouse(context: ToolContext) -> Warehouse:
    if context.warehouse is None:
        raise ToolError("No warehouse is available to read data from")
    return context.warehouse


class RawFileArguments(ToolArguments):
    """A feed and the trade date of its stored file."""

    source: Source
    dataset: str = Field(
        pattern=r"^[a-z_]{1,64}$", description="For example 'navall' or 'cm_bhavcopy'."
    )
    trade_date: date = Field(description="The trade date, as YYYY-MM-DD.")


def get_raw_schema(context: ToolContext, args: RawFileArguments) -> Payload:
    """The column names of the latest stored raw file for a feed and day, as published."""
    schema = _reader(context).schema(args.source, args.dataset, args.trade_date)
    return {
        "file": schema.key,
        "column_count": len(schema.columns),
        "columns": list(schema.columns[:MAX_COLUMNS_LISTED]),
    }


class CompareSchemasArguments(ToolArguments):
    """A feed and two trade dates whose files to compare."""

    source: Source
    dataset: str = Field(pattern=r"^[a-z_]{1,64}$")
    date_a: date = Field(description="The earlier day, as YYYY-MM-DD.")
    date_b: date = Field(description="The day to compare it with, as YYYY-MM-DD.")


def compare_schemas(context: ToolContext, args: CompareSchemasArguments) -> Payload:
    """Which columns a feed's file gained, lost or reordered between two days."""
    reader = _reader(context)
    first = reader.schema(args.source, args.dataset, args.date_a).columns
    second = reader.schema(args.source, args.dataset, args.date_b).columns
    removed = [name for name in first if name not in second]
    added = [name for name in second if name not in first]
    return {
        "date_a": args.date_a.isoformat(),
        "date_b": args.date_b.isoformat(),
        "identical": first == second,
        "removed": removed[:MAX_COLUMNS_LISTED],
        "added": added[:MAX_COLUMNS_LISTED],
        "reordered": not removed and not added and first != second,
        "column_count": [len(first), len(second)],
    }


class ProfileColumnArguments(ToolArguments):
    """A warehouse column, and optionally the day to look at."""

    model: str = Field(pattern=NAME, description="A dbt model, for example 'slv_amfi__nav'.")
    column: str = Field(pattern=NAME)
    trade_date: date | None = Field(
        default=None, description="Only rows for this day. Leave out for the whole table."
    )


def profile_column(context: ToolContext, args: ProfileColumnArguments) -> Payload:
    """Rows, nulls, distinct values, minimum, maximum and mean of a column."""
    warehouse = _warehouse(context)
    table = warehouse.table(args.model)
    return {
        "model": args.model,
        "column": args.column,
        "type": table.column(args.column).data_type,
        "trade_date": args.trade_date.isoformat() if args.trade_date else None,
        **warehouse.profile(table, args.column, args.trade_date),
    }


class CompareProfilesArguments(ToolArguments):
    """A warehouse column and two days to compare it on."""

    model: str = Field(pattern=NAME)
    column: str = Field(pattern=NAME)
    date_a: date = Field(description="The earlier day, as YYYY-MM-DD.")
    date_b: date = Field(description="The day to compare it with, as YYYY-MM-DD.")


def compare_profiles(context: ToolContext, args: CompareProfilesArguments) -> Payload:
    """A column's profile on two days, with how much each figure changed.

    A mean that is a hundred times larger with the same row count points at a unit change; a
    row count that halves points at a partial file.
    """
    warehouse = _warehouse(context)
    table = warehouse.table(args.model)
    first = warehouse.profile(table, args.column, args.date_a)
    second = warehouse.profile(table, args.column, args.date_b)
    return {
        "model": args.model,
        "column": args.column,
        args.date_a.isoformat(): first,
        args.date_b.isoformat(): second,
        "change": {
            "rows_ratio": _ratio(second["rows"], first["rows"]),
            "null_share_change": (
                None
                if first["null_share"] is None or second["null_share"] is None
                else round(second["null_share"] - first["null_share"], 6)
            ),
            "mean_ratio": _ratio(second["mean"], first["mean"]),
            "max_ratio": _ratio(second["max"], first["max"]),
        },
    }


def _ratio(new: Any, old: Any) -> float | None:
    try:
        denominator = float(old)
        return round(float(new) / denominator, 6) if denominator else None
    except (TypeError, ValueError):
        return None


class SampleRowsArguments(ToolArguments):
    """Which rows of a model to show."""

    model: str = Field(pattern=NAME)
    columns: list[str] = Field(
        default_factory=list,
        max_length=MAX_SAMPLE_COLUMNS,
        description="Columns to show; leave empty for all.",
    )
    trade_date: date | None = None
    match_column: str | None = Field(default=None, pattern=NAME)
    match_value: str | None = Field(
        default=None, max_length=100, description="Show only rows where match_column equals this."
    )

    @model_validator(mode="after")
    def _match_is_complete(self) -> "SampleRowsArguments":
        if (self.match_column is None) != (self.match_value is None):
            raise ValueError("match_column and match_value go together")
        return self


def sample_rows(context: ToolContext, args: SampleRowsArguments) -> Payload:
    """A few rows of a model, optionally for one day or one value, to see what the data holds."""
    warehouse = _warehouse(context)
    table = warehouse.table(args.model)
    match = (
        (args.match_column, args.match_value)
        if args.match_column is not None and args.match_value is not None
        else None
    )
    rows = warehouse.sample(
        table, columns=args.columns, day=args.trade_date, match=match, limit=MAX_SAMPLE_ROWS
    )
    return {
        "model": args.model,
        "rows": [
            {
                name: clip(value, MAX_VALUE_CHARACTERS) if isinstance(value, str) else value
                for name, value in row.items()
            }
            for row in rows
        ],
        "shown": len(rows),
        "limit": MAX_SAMPLE_ROWS,
    }


class GetCorporateActionsArguments(ToolArguments):
    """A symbol and the date to look around."""

    symbol: str = Field(pattern=r"^[A-Za-z0-9&\-_.]{1,30}$", description="NSE ticker symbol.")
    trade_date: date = Field(description="The date of the price move, as YYYY-MM-DD.")
    window_days: int = Field(default=7, ge=0, le=30, description="Days either side to include.")


def get_corporate_actions(context: ToolContext, args: GetCorporateActionsArguments) -> Payload:
    """Splits, bonuses, dividends and other actions with an ex-date near a date.

    A price that jumps with no action behind it is a data fault; one that jumps on an ex-date
    of a split or bonus is genuine.
    """
    window = timedelta(days=args.window_days)
    rows = _warehouse(context).actions_near(
        args.symbol, args.trade_date - window, args.trade_date + window, MAX_ROWS
    )
    return {
        "symbol": args.symbol,
        "trade_date": args.trade_date.isoformat(),
        "window_days": args.window_days,
        "actions": [
            {
                name: clip(value, MAX_VALUE_CHARACTERS) if isinstance(value, str) else value
                for name, value in row.items()
            }
            for row in rows
        ],
    }


DATA_TOOLS = (
    Tool(
        "get_raw_schema",
        "List the column names of the latest stored raw file for a feed and trade date, exactly "
        "as published. Shows a renamed, dropped or added column before any model reads it.",
        RawFileArguments,
        get_raw_schema,
    ),
    Tool(
        "compare_schemas",
        "Compare the raw file columns of a feed on two days: columns added, removed or reordered.",
        CompareSchemasArguments,
        compare_schemas,
    ),
    Tool(
        "profile_column",
        "Row count, nulls, distinct values, minimum, maximum and mean of a model's column, "
        "for one day or the whole table.",
        ProfileColumnArguments,
        profile_column,
    ),
    Tool(
        "compare_profiles",
        "Profile a model's column on two days and show the ratio of each figure. A mean that "
        "changes about a hundredfold suggests a unit change; a halved row count, a partial file.",
        CompareProfilesArguments,
        compare_profiles,
    ),
    Tool(
        "sample_rows",
        f"Show up to {MAX_SAMPLE_ROWS} rows of a model, optionally for one day or where one "
        "column equals a value.",
        SampleRowsArguments,
        sample_rows,
    ),
    Tool(
        "get_corporate_actions",
        "List splits, bonuses, dividends and other corporate actions for a symbol with an "
        "ex-date near a date, to tell a genuine price jump from a fault.",
        GetCorporateActionsArguments,
        get_corporate_actions,
    ),
)
