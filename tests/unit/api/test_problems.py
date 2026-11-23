"""FR-API-03: every error is an RFC 9457 problem."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from lineageheal.api.app import create_app
from lineageheal.api.problems import PROBLEM_MEDIA_TYPE, TYPE_PREFIX, ApiError
from lineageheal.core.settings import Settings


class Body(BaseModel):
    count: int


def app_with_routes() -> TestClient:
    app: FastAPI = create_app(Settings())

    @app.get("/boom/{item}")
    def boom(item: int) -> None:
        raise ApiError(404, "not-found", "Not found", f"No item {item}")

    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError("postgresql://user:hunter2@host/db is unreachable")

    @app.post("/body")
    def body(payload: Body) -> Body:
        return payload

    return TestClient(app, raise_server_exceptions=False)


def test_an_error_raised_by_an_endpoint_is_a_problem_with_a_stable_type() -> None:
    response = app_with_routes().get("/boom/7")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.json() == {
        "type": TYPE_PREFIX + "not-found",
        "title": "Not found",
        "status": 404,
        "detail": "No item 7",
        "instance": "/boom/7",
    }


def test_a_rejected_input_lists_the_field_and_never_echoes_the_value() -> None:
    response = app_with_routes().post("/body", json={"count": "hunter2"})

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == TYPE_PREFIX + "invalid-request"
    assert problem["errors"][0]["field"] == "body.count"
    assert "hunter2" not in response.text


def test_a_bad_query_parameter_is_a_problem_too() -> None:
    response = app_with_routes().get("/boom/not-a-number")

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "path.item"


def test_an_unknown_url_and_a_wrong_method_are_problems(client: TestClient) -> None:
    missing = client.get("/nothing-here")
    wrong = client.post("/health")

    assert (missing.status_code, missing.json()["type"]) == (404, TYPE_PREFIX + "not-found")
    assert (wrong.status_code, wrong.json()["type"]) == (
        405,
        TYPE_PREFIX + "method-not-allowed",
    )
    assert missing.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


def test_a_bug_is_a_generic_500_that_leaks_nothing() -> None:
    response = app_with_routes().get("/crash")

    assert response.status_code == 500
    assert response.json()["type"] == TYPE_PREFIX + "internal-error"
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
