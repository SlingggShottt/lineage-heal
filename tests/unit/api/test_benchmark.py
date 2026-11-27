"""FR-API-01, FR-UI-07: published benchmark runs are served read-only, with problem details."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from benchmark_reports import NOW, sample_report
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lineageheal.api.app import create_app
from lineageheal.core.settings import Settings
from lineageheal.store.benchmark import BenchmarkRepository


@pytest.fixture
def client(engine: Engine) -> TestClient:
    api = create_app(Settings(), clock=lambda: NOW)
    api.state.engine = engine
    return TestClient(api)


def publish(engine: Engine, run_id: str, day: int = 20) -> None:
    scored = datetime(2026, 11, day, tzinfo=UTC)
    BenchmarkRepository(engine).publish(
        run_id, sample_report(run_id, generated_at=scored), generated_at=scored, at=NOW
    )


def test_the_list_is_empty_before_anything_is_published(client: TestClient) -> None:
    body = client.get("/benchmark/runs").json()

    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_the_list_shows_each_runs_headline_numbers_per_system_newest_first(
    client: TestClient, engine: Engine
) -> None:
    publish(engine, "older", 20)
    publish(engine, "newer", 25)

    body = client.get("/benchmark/runs").json()

    assert [r["run_id"] for r in body["items"]] == ["newer", "older"]
    lineageheal = body["items"][0]["systems"][0]
    assert lineageheal["name"] == "lineageheal"
    assert lineageheal["top1"]["value"] == 1.0
    assert lineageheal["patch_pass"]["total"] == 4
    assert lineageheal["median_seconds"] == 80.0
    assert body["total"] == 2


def test_the_list_is_paginated(client: TestClient, engine: Engine) -> None:
    for n in range(3):
        publish(engine, f"r{n}", 20 + n)

    body = client.get("/benchmark/runs", params={"limit": 1, "offset": 1}).json()

    assert [r["run_id"] for r in body["items"]] == ["r1"]
    assert body["total"] == 3


def test_one_run_is_served_in_full(client: TestClient, engine: Engine) -> None:
    publish(engine, "r1")

    body = client.get("/benchmark/runs/r1").json()

    assert body["run_id"] == "r1"
    assert body["manifest"]["lockfile_sha256"] == "abc123"
    assert set(body["systems"][0]["by_class"]) == {"nulls", "schema_drift"}
    assert body["goals"]["g4_by_class"]["nulls"]["result"] == "ahead"


def test_an_unknown_run_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/benchmark/runs/nope")

    assert response.status_code == 404
    assert response.json()["type"] == "urn:lineageheal:problem:not-found"
    assert "nope" in response.json()["detail"]


def test_a_run_id_that_cannot_name_a_run_is_refused_not_looked_up(client: TestClient) -> None:
    assert client.get("/benchmark/runs/a b").status_code == 422
    assert client.get("/benchmark/runs/" + "x" * 65).status_code == 422


def test_there_is_no_way_to_write_a_run_through_the_api(client: TestClient) -> None:
    assert client.post("/benchmark/runs", json=sample_report()).status_code == 405
    assert client.put("/benchmark/runs/r1", json=sample_report()).status_code == 405
    assert client.delete("/benchmark/runs/r1").status_code == 405


def test_a_run_published_in_an_old_shape_is_a_503_that_says_to_publish_it_again(
    client: TestClient, engine: Engine
) -> None:
    old = sample_report("r1")
    del old["systems"][0]["overall"]["top1"]
    BenchmarkRepository(engine).publish("r1", old, generated_at=NOW, at=NOW + timedelta(1))

    response = client.get("/benchmark/runs/r1")

    assert response.status_code == 503
    assert response.json()["type"] == "urn:lineageheal:problem:benchmark-report-unreadable"
    assert "publish it again" in response.json()["detail"]
    assert "top1" not in response.text  # nothing of the stored content is echoed
