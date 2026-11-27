"""FR-API-01: published runs are stored whole, replaced on republishing, newest scored first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from benchmark_reports import NOW, sample_report
from sqlalchemy import Engine

from lineageheal.store.benchmark import BenchmarkRepository


def publish(repo: BenchmarkRepository, run_id: str, *, scored: datetime = NOW) -> bool:
    report = sample_report(run_id, generated_at=scored)
    return repo.publish(run_id, report, generated_at=scored, at=scored + timedelta(minutes=5))


def test_a_run_comes_back_as_it_was_published(engine: Engine) -> None:
    repo = BenchmarkRepository(engine)
    report = sample_report("r1")

    assert repo.publish("r1", report, generated_at=NOW, at=NOW) is True

    stored = repo.get("r1")
    assert stored is not None
    assert stored["run_id"] == "r1"
    assert stored["systems"][0]["overall"]["top1"]["value"] == 1.0


def test_publishing_a_run_again_replaces_it_and_says_so(engine: Engine) -> None:
    repo = BenchmarkRepository(engine)
    publish(repo, "r1")
    newer = sample_report("r1", generated_at=NOW + timedelta(days=1))

    created = repo.publish("r1", newer, generated_at=NOW + timedelta(days=1), at=NOW)

    assert created is False
    stored = repo.get("r1")
    assert stored is not None
    assert stored["generated_at"] == (NOW + timedelta(days=1)).isoformat()
    assert repo.list(limit=10, offset=0)[1] == 1


def test_runs_are_listed_newest_scored_first_with_a_total_and_pages(engine: Engine) -> None:
    repo = BenchmarkRepository(engine)
    for n, run_id in enumerate(("old", "mid", "new")):
        publish(repo, run_id, scored=datetime(2026, 11, 20 + n, tzinfo=UTC))

    first, total = repo.list(limit=2, offset=0)
    rest, _ = repo.list(limit=2, offset=2)

    assert total == 3
    assert [r["run_id"] for r in first] == ["new", "mid"]
    assert [r["run_id"] for r in rest] == ["old"]


def test_a_run_that_was_never_published_is_none(engine: Engine) -> None:
    assert BenchmarkRepository(engine).get("nope") is None
