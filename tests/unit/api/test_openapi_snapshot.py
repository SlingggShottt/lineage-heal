"""The dashboard's generated client is built from `web/openapi.json`; it must match the API."""

from pathlib import Path

from lineageheal.api.app import openapi_document

SNAPSHOT = Path(__file__).parents[3] / "web" / "openapi.json"


def test_the_committed_schema_is_what_the_api_serves() -> None:
    assert SNAPSHOT.read_text(encoding="utf-8") == openapi_document(), (
        "web/openapi.json is out of date; run `make web-types` and commit the result"
    )


def test_the_document_is_stable_text() -> None:
    assert openapi_document() == openapi_document()
    assert openapi_document().endswith("}\n")
