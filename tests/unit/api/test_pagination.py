"""FR-API-03: list endpoints are paginated, and a bad page request is refused."""

import pytest
from fastapi.testclient import TestClient

from lineageheal.api.app import create_app
from lineageheal.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, PageQuery
from lineageheal.core.settings import Settings

ROWS = list(range(60))


def listing() -> TestClient:
    app = create_app(Settings())

    @app.get("/numbers")
    def numbers(page: PageQuery) -> Page[int]:
        chosen = ROWS[page.offset : page.offset + page.limit]
        return page.page_of(chosen, total=len(ROWS))

    return TestClient(app)


def test_the_first_page_uses_the_default_limit_and_reports_the_total() -> None:
    body = listing().get("/numbers").json()

    assert body["items"] == ROWS[:DEFAULT_LIMIT]
    assert (body["total"], body["limit"], body["offset"]) == (60, DEFAULT_LIMIT, 0)


def test_a_later_page_and_a_short_last_page() -> None:
    body = listing().get("/numbers", params={"limit": 25, "offset": 50}).json()

    assert body["items"] == ROWS[50:]
    assert body["offset"] == 50


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": MAX_LIMIT + 1}, {"offset": -1}, {"limit": "many"}],
)
def test_a_page_out_of_range_is_refused_not_clamped(params: dict[str, object]) -> None:
    response = listing().get("/numbers", params=params)  # type: ignore[arg-type]

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"].startswith("query.")
