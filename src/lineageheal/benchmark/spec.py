"""The benchmark's fault specification (FR-BEN-01, design 12.1).

A specification says what to damage, how, and what the right diagnosis is, as data that can be
reviewed in a pull request and replayed exactly. It is validated when loaded, so a fault whose
label does not match what it does (a rename filed as nulls) never reaches a benchmark run.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EARLIEST_TRADE_DATE = date(2024, 1, 1)

FaultClass = Literal["schema_drift", "nulls", "duplicates", "unit_change", "late_data"]
FixLocation = Literal["dbt", "outside_dbt"]

# `raw.<source>.<dataset>.<column>`, or `.*` for a fault with no column to point at (a missing
# file). Published column names have spaces (`Net Asset Value`), so only a dot is excluded.
_ROOT_CAUSE = re.compile(r"^raw\.[a-z0-9_]+\.[a-z0-9_]+\.(\*|[^.]+)$")
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class _Strict(BaseModel):
    """Unknown fields are errors: a misspelt parameter must not be silently ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RenameColumn(_Strict):
    """Give a column a new name in the header, as if the publisher changed its schema."""

    type: Literal["rename_column"]
    column: str = Field(min_length=1)
    new_name: str = Field(min_length=1)


class BlankValue(_Strict):
    """Empty one row's value in a column, as if a value went missing."""

    type: Literal["blank_value"]
    column: str = Field(min_length=1)


class DuplicateRow(_Strict):
    """Repeat one data row directly after itself."""

    type: Literal["duplicate_row"]


class ScaleValues(_Strict):
    """Multiply every number in a column, as if the publisher changed units (paise for rupees)."""

    type: Literal["scale_values"]
    column: str = Field(min_length=1)
    factor: int = Field(ge=2, le=1_000_000)


class StaleDate(_Strict):
    """Give every data row an old date, as if a stale report were served."""

    type: Literal["stale_date"]
    column: str = Field(min_length=1)
    value: str = Field(min_length=1)


class WithholdFile(_Strict):
    """Store nothing for the day, as if the file never arrived."""

    type: Literal["withhold_file"]


Injection = Annotated[
    RenameColumn | BlankValue | DuplicateRow | ScaleValues | StaleDate | WithholdFile,
    Field(discriminator="type"),
]

# The class each kind of damage belongs to. A specification must agree with this table.
CLASS_OF_INJECTION: dict[str, FaultClass] = {
    "rename_column": "schema_drift",
    "blank_value": "nulls",
    "duplicate_row": "duplicates",
    "scale_values": "unit_change",
    "stale_date": "late_data",
    "withhold_file": "late_data",
}


class Expected(_Strict):
    """The right answer, which a system's diagnosis is scored against."""

    root_cause: str
    fix_location: FixLocation

    @model_validator(mode="after")
    def _check_root_cause(self) -> Expected:
        if not _ROOT_CAUSE.fullmatch(self.root_cause):
            raise ValueError(
                f"root_cause {self.root_cause!r} must look like raw.<source>.<dataset>.<column>"
            )
        return self


class FaultCase(_Strict):
    """One fault: where to inject it, how, and what the right diagnosis is."""

    version: Literal[1] = 1
    id: str
    fault_class: FaultClass = Field(alias="class")
    source: Literal["amfi", "nse", "fbil"]
    dataset: str = Field(min_length=1)
    trade_date: date
    split: Literal["seed", "test"]
    seed: int = Field(ge=0)
    injection: Injection
    expected: Expected

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _check_consistency(self) -> FaultCase:
        if not _ID.fullmatch(self.id):
            raise ValueError(f"id {self.id!r} must be lowercase words joined by hyphens")
        if self.trade_date < EARLIEST_TRADE_DATE:
            raise ValueError(f"trade_date must be {EARLIEST_TRADE_DATE} or later")
        expected_class = CLASS_OF_INJECTION[self.injection.type]
        if self.fault_class != expected_class:
            raise ValueError(
                f"{self.injection.type} is a {expected_class} fault, not {self.fault_class}"
            )
        if self.fault_class == "late_data" and self.expected.fix_location != "outside_dbt":
            # No dbt change brings a missing or stale file back (D-51).
            raise ValueError("a late_data fault can only be fixed outside_dbt")
        if not self.expected.root_cause.startswith(f"raw.{self.source}.{self.dataset}."):
            raise ValueError(
                f"root_cause must be in raw.{self.source}.{self.dataset}, "
                f"not {self.expected.root_cause!r}"
            )
        return self
