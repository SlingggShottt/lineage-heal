"""FR-API-01: liveness never needs the database; readiness checks it and its migrations."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lineageheal.api.app import create_app
from lineageheal.api.problems import TYPE_PREFIX
from lineageheal.core.settings import Settings
from lineageheal.store.migrate import head_revision, upgrade_database


def test_liveness_answers_with_no_database_configured(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


def test_readiness_without_a_database_is_a_503_problem(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "database-unavailable"


def test_readiness_reports_the_revision_when_the_database_is_migrated(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/api.db"
    upgrade_database(url)
    app = create_app(Settings(database_url=url))

    with TestClient(app) as client:
        body = client.get("/health/ready").json()

    assert body == {"status": "ready", "revision": head_revision(), "head": head_revision()}


def test_readiness_says_so_when_the_schema_is_behind(
    memory_engine: Engine, client: TestClient
) -> None:
    client.app.state.engine = memory_engine  # type: ignore[attr-defined]  # no migration run

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "schema-out-of-date"
    assert head_revision() in response.json()["detail"]


def test_readiness_does_not_expose_the_connection_string() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://u:hunter2@127.0.0.1:1/x"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert "hunter2" not in response.text
