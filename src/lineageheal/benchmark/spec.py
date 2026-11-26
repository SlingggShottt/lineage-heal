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
# The first trading day NSE published UDiFF only: its old-format URL was not served from here.
CHANGE_DAY = date(2024, 7, 8)

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


class RealFile(_Strict):
    """Deliver the day's real file unchanged: the fault is that the pipeline cannot read it.

    Only meaningful for a replay (`pipeline: pre_july_2024`), where the project is the one that
    predates the file's layout.
    """

    type: Literal["real_file"]


Injection = Annotated[
    RenameColumn | BlankValue | DuplicateRow | ScaleValues | StaleDate | WithholdFile | RealFile,
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
    "real_file": "schema_drift",
}


class Expected(_Strict):
    """The right answer, which a system's diagnosis is scored against."""

    root_cause: str
    fix_location: FixLocation
    # Other nodes that are equally right, for a fault with no single column to name: a layout
    # change renames many at once, and finding any of them finds the change.
    also_accept: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_root_cause(self) -> Expected:
        for node in (self.root_cause, *self.also_accept):
            if not _ROOT_CAUSE.fullmatch(node):
                raise ValueError(
                    f"root_cause {node!r} must look like raw.<source>.<dataset>.<column>"
                )
        return self

    @property
    def accepted(self) -> frozenset[str]:
        """Every node a diagnosis may name and be counted right."""
        return frozenset({self.root_cause, *self.also_accept})


class FaultCase(_Strict):
    """One fault: where to inject it, how, and what the right diagnosis is."""

    version: Literal[1] = 1
    id: str
    fault_class: FaultClass = Field(alias="class")
    source: Literal["amfi", "nse", "fbil"]
    dataset: str = Field(min_length=1)
    trade_date: date
    # `heldout` is the replay of a real event: scored apart from the corpus and never indexed.
    split: Literal["seed", "test", "heldout"]
    seed: int = Field(ge=0)
    # `pre_july_2024` replays NSE's format change: the project is the one before it, and the
    # bhavcopy history before the change day is converted to the old layout (LH-071).
    pipeline: Literal["current", "pre_july_2024"] = "current"
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
        prefix = f"raw.{self.source}.{self.dataset}."
        for node in self.expected.accepted:
            if not node.startswith(prefix):
                raise ValueError(
                    f"root_cause must be in raw.{self.source}.{self.dataset}, not {node!r}"
                )
        replay = self.pipeline == "pre_july_2024"
        if replay and (self.source, self.dataset, self.trade_date) != (
            "nse",
            "cm_bhavcopy",
            CHANGE_DAY,
        ):
            raise ValueError(f"a pre_july_2024 replay is NSE bhavcopy on {CHANGE_DAY}")
        if self.injection.type == "real_file" and not replay:
            raise ValueError(
                "real_file delivers a file the project cannot read, so it needs a replay"
            )
        if self.split == "heldout" and not replay:
            raise ValueError("a held-out case is a replay")
        return self
