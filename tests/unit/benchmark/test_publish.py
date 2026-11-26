"""FR-API-01: publishing a run stores exactly its report, scoring it first if needed."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from benchmark_reports import NOW, sample_report, sample_run
from sqlalchemy import Engine
from typer.testing import CliRunner

from lineageheal.benchmark.publish import publish_run
from lineageheal.benchmark.results import ResultStore
from lineageheal.cli import app
from lineageheal.core.errors import BenchmarkError
from lineageheal.store.benchmark import BenchmarkRepository


def scored_run(tmp_path: Path) -> tuple[ResultStore, dict[str, object]]:
    results, cases, manifest = sample_run("r1")
    store = ResultStore(tmp_path / "results", "r1")
    store.write_manifest(manifest)
    for item in results:
        store.save(item)
    return store, cases  # type: ignore[return-value]


def test_a_run_that_was_not_scored_is_scored_published_and_its_report_kept(
    tmp_path: Path, engine: Engine
) -> None:
    store, cases = scored_run(tmp_path)

    report, created = publish_run(store, cases, BenchmarkRepository(engine), now=NOW)  # type: ignore[arg-type]

    assert created is True
    assert report.run_id == "r1"
    assert (store.root / "report.json").is_file()  # what was published is what is on disk
    stored = BenchmarkRepository(engine).get("r1")
    assert stored is not None
    assert stored["systems"][0]["name"] == "lineageheal"


def test_an_existing_report_is_published_as_it_is_and_republishing_replaces_it(
    tmp_path: Path, engine: Engine
) -> None:
    store, cases = scored_run(tmp_path)
    report = sample_report("r1", generated_at=NOW - timedelta(days=3))
    (store.root / "report.json").write_text(json.dumps(report))
    repo = BenchmarkRepository(engine)

    first, created = publish_run(store, cases, repo, now=NOW)  # type: ignore[arg-type]
    _, again = publish_run(store, cases, repo, now=NOW)  # type: ignore[arg-type]

    assert first.generated_at == report["generated_at"]  # the file's own, not a fresh scoring
    assert (created, again) == (True, False)


def test_a_report_in_an_old_shape_is_refused_with_the_field_and_the_fix(
    tmp_path: Path, engine: Engine
) -> None:
    store, cases = scored_run(tmp_path)
    old = sample_report("r1")
    del old["goals"]["g1_accuracy"]
    (store.root / "report.json").write_text(json.dumps(old))

    with pytest.raises(BenchmarkError, match=r"report\.json does not fit.*bench report"):
        publish_run(store, cases, BenchmarkRepository(engine))  # type: ignore[arg-type]
    assert BenchmarkRepository(engine).get("r1") is None  # nothing was written


def test_a_run_with_no_results_is_refused(tmp_path: Path, engine: Engine) -> None:
    store = ResultStore(tmp_path / "results", "empty")
    store.write_manifest({"run_id": "empty"})

    with pytest.raises(BenchmarkError, match="no results to publish"):
        publish_run(store, {}, BenchmarkRepository(engine))


def test_the_publish_command_stores_the_run_and_says_what_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is not read
    monkeypatch.setenv("BENCH_DIR", str(tmp_path / "bench"))
    monkeypatch.setenv("DATABASE_URL", database_url)
    store = ResultStore(tmp_path / "bench" / "results", "r1")
    store.write_manifest(sample_run("r1")[2])
    (store.root / "report.json").write_text(json.dumps(sample_report("r1")))

    first = CliRunner().invoke(app, ["bench", "publish", "--run-id", "r1"])
    second = CliRunner().invoke(app, ["bench", "publish", "--run-id", "r1"])

    assert first.exit_code == 0, first.output
    assert "published r1: 2 systems, 4 test faults" in first.output
    assert "replaced r1" in second.output


def test_the_publish_command_without_a_database_says_which_setting_to_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BENCH_DIR", str(tmp_path / "bench"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = CliRunner().invoke(app, ["bench", "publish", "--run-id", "r1"])

    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
