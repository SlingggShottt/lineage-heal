"""FR-UI-08: the runbooks the dashboard lists and shows."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lineageheal.api.app import create_app
from lineageheal.core.settings import Settings

RUNBOOK = """# A required value arrives blank

**Fault class:** nulls · **Feeds:** AMFI NAVAll

## Symptoms
- A `not_null` test fails.

## Fix
Re-download the day.
"""


def client_for(folder: Path) -> TestClient:
    return TestClient(create_app(Settings(runbooks_dir=folder)))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "nulls.md").write_text(RUNBOOK, encoding="utf-8")
    (tmp_path / "broken.md").write_text("no template here", encoding="utf-8")
    return tmp_path


def test_runbooks_are_listed_by_name_and_a_malformed_one_is_left_out(folder: Path) -> None:
    body = client_for(folder).get("/runbooks").json()

    assert body["total"] == 1
    assert body["items"] == [
        {
            "slug": "nulls",
            "title": "A required value arrives blank",
            "fault_class": "nulls",
            "feeds": "AMFI NAVAll",
        }
    ]


def test_a_runbook_comes_with_its_markdown_and_the_sections_the_index_uses(folder: Path) -> None:
    body = client_for(folder).get("/runbooks/nulls").json()

    assert body["markdown"] == RUNBOOK
    assert [s["heading"] for s in body["sections"]] == ["Symptoms", "Fix"]
    assert body["sections"][1]["text"] == "Re-download the day."


def test_an_unknown_runbook_is_a_404_problem(folder: Path) -> None:
    response = client_for(folder).get("/runbooks/absent")

    assert response.status_code == 404
    assert response.json()["type"] == "urn:lineageheal:problem:not-found"


@pytest.mark.parametrize("slug", ["..%2Fsecret", "Nulls", "a.b", "-x", "x" * 80])
def test_a_slug_that_could_name_a_path_never_reaches_the_folder(folder: Path, slug: str) -> None:
    response = client_for(folder).get(f"/runbooks/{slug}")

    assert response.status_code in (404, 422)


def test_a_missing_folder_gives_an_empty_list(tmp_path: Path) -> None:
    body = client_for(tmp_path / "absent").get("/runbooks").json()

    assert body["items"] == []
    assert body["total"] == 0


def test_the_projects_own_runbooks_all_parse_and_list() -> None:
    body = TestClient(create_app(Settings())).get("/runbooks").json()

    assert {item["fault_class"] for item in body["items"]} == {
        "schema_drift",
        "nulls",
        "duplicates",
        "unit_change",
        "late_data",
    }
