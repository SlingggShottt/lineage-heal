"""FR-API-01: the schema the generated client depends on, and cross-origin access."""

from fastapi.testclient import TestClient

from lineageheal.api.app import create_app
from lineageheal.core.settings import Settings


def test_the_openapi_schema_documents_the_error_body_and_the_health_paths(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()

    assert {"/health", "/health/ready"} <= set(schema["paths"])
    assert {"Problem", "FieldError"} <= set(schema["components"]["schemas"])
    not_found = schema["paths"]["/health"]["get"]["responses"]["404"]
    assert "application/problem+json" in not_found["content"]


def test_the_dashboards_origin_may_call_the_api_with_credentials() -> None:
    app = create_app(Settings(api_cors_origins="https://dash.example.com"))

    with TestClient(app) as client:
        allowed = client.get("/health", headers={"Origin": "https://dash.example.com"})
        other = client.get("/health", headers={"Origin": "https://evil.example.com"})

    assert allowed.headers["access-control-allow-origin"] == "https://dash.example.com"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in other.headers


def test_no_origins_configured_means_no_cross_origin_access() -> None:
    app = create_app(Settings(api_cors_origins=""))

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://dash.example.com"})

    assert "access-control-allow-origin" not in response.headers
