"""The `bench` commands list the corpus and refuse what they cannot run."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lineageheal.cli import app

CASES = Path(__file__).parents[3] / "benchmark" / "cases"


def test_list_summarises_the_corpus_and_filters_by_split() -> None:
    result = CliRunner().invoke(app, ["bench", "list", "--cases", str(CASES), "--split", "seed"])

    assert result.exit_code == 0
    assert "100 cases" in result.output
    assert result.output.count("\nnse-") + result.output.count("\namfi-") == 30


def test_run_refuses_an_unknown_system_and_an_empty_selection() -> None:
    unknown = CliRunner().invoke(app, ["bench", "run", "--run-id", "x", "--system", "magic"])
    empty = CliRunner().invoke(
        app,
        ["bench", "run", "--run-id", "x", "--system", "none", "--cases", str(CASES),
         "--fault-class", "nulls", "--split", "nonsense"],
    )  # fmt: skip

    assert unknown.exit_code != 0
    assert empty.exit_code != 0


def test_run_records_a_failed_scenario_when_history_is_not_backfilled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BENCH_DIR", str(tmp_path / "bench"))
    monkeypatch.setenv("RAW_ZONE_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("DATABASE_URL", "")

    result = CliRunner().invoke(
        app, ["bench", "run", "--run-id", "t1", "--system", "none", "--limit", "2"]
    )

    assert result.exit_code == 1
    assert "2 scenarios failed" in result.output
    assert len(list((tmp_path / "bench" / "results" / "t1" / "results").glob("*.json"))) == 2


def test_the_plain_llm_system_without_a_model_key_fails_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is not read
    monkeypatch.setenv("BENCH_DIR", str(tmp_path))

    result = CliRunner().invoke(
        app, ["bench", "run", "--run-id", "t2", "--system", "plain-llm", "--limit", "1"]
    )

    assert result.exit_code == 1
    assert "error:" in result.output
