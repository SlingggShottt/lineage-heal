"""The report schema accepts what `bench report` writes and refuses what does not fit."""

from __future__ import annotations

import copy
import json

import pytest
from benchmark_reports import sample_report
from pydantic import ValidationError

from lineageheal.core.benchmark_report import BenchmarkReport


def test_a_report_built_by_the_report_command_fits_the_schema() -> None:
    report = BenchmarkReport.model_validate(sample_report())

    assert [s.name for s in report.systems] == ["lineageheal", "plain-llm"]
    assert report.systems[0].overall.top1.value == 1.0
    assert report.goals is not None
    assert report.goals.g4_by_class["nulls"].result == "ahead"


def test_a_report_round_trips_through_json_unchanged() -> None:
    report = BenchmarkReport.model_validate(sample_report())

    again = BenchmarkReport.model_validate(json.loads(report.model_dump_json()))

    assert again == report


def test_a_report_without_lineageheal_has_no_goals_and_still_fits() -> None:
    data = sample_report()
    data["goals"] = None

    assert BenchmarkReport.model_validate(data).goals is None


def test_a_missing_or_misnamed_field_is_refused() -> None:
    data = sample_report()
    broken = copy.deepcopy(data)
    del broken["systems"][0]["overall"]["top1"]
    renamed = copy.deepcopy(data)
    renamed["goals"]["G1_accuracy"] = renamed["goals"].pop("g1_accuracy")

    with pytest.raises(ValidationError, match="top1"):
        BenchmarkReport.model_validate(broken)
    with pytest.raises(ValidationError, match="g1_accuracy"):
        BenchmarkReport.model_validate(renamed)
