"""FR-API-02, FR-REV-03 and NFR-SEC-03: sign-in, sessions, the approver allowlist and CSRF."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from lineageheal.api.app import create_app
from lineageheal.api.dependencies import ApproverDep
from lineageheal.api.github_auth import TOKEN_URL, USER_URL
from lineageheal.api.problems import TYPE_PREFIX
from lineageheal.api.security import NEXT_COOKIE, SESSION_COOKIE, STATE_COOKIE, TokenCipher
from lineageheal.core.settings import Settings
from lineageheal.store.database import create_db_engine
from lineageheal.store.migrate import upgrade_database
from lineageheal.store.models import WebSession

T0 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
SECRET = "k" * 40
DASHBOARD = "https://dash.example.com"
GITHUB_TOKEN = "ghu_the_users_token"


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "github_oauth_client_id": "Iv1.client",
        "github_oauth_client_secret": "client-secret",
        "session_secret": SECRET,
        "approver_github_logins": "Ada, grace",
        "dashboard_url": DASHBOARD,
        "api_public_url": "http://localhost:8000",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'app.db'}"
    upgrade_database(url)
    engine = create_db_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(engine: Engine, clock: Clock) -> FastAPI:
    application = create_app(settings(), clock=clock)
    application.state.engine = engine

    # Stands in for LH-061's approve endpoint: any state-changing route guarded the same way.
    @application.post("/protected")
    def protected(session: ApproverDep) -> dict[str, str]:
        return {"login": session.login}

    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def sign_in(
    client: TestClient,
    github: respx.MockRouter,
    *,
    login: str = "ada",
    expires_in: int | None = 28800,
) -> httpx.Response:
    started = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    token: dict[str, object] = {"access_token": GITHUB_TOKEN}
    if expires_in is not None:
        token["expires_in"] = expires_in
    github.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token))
    github.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"id": 4_000_000_001, "login": login})
    )
    return client.get(
        "/auth/callback", params={"code": "one-time", "state": state}, follow_redirects=False
    )


def csrf_of(client: TestClient) -> str:
    token = client.get("/me").json()["csrf_token"]
    assert isinstance(token, str)
    return token


def test_an_anonymous_visitor_is_read_only_and_needs_no_database() -> None:
    with TestClient(create_app(settings())) as bare:
        body = bare.get("/me").json()

    assert body == {
        "signed_in": False,
        "login": None,
        "is_approver": False,
        "csrf_token": None,
        "expires_at": None,
    }


def test_login_sends_the_browser_to_github_with_a_matching_state_cookie(
    client: TestClient,
) -> None:
    response = client.get("/auth/login", follow_redirects=False)

    location = urlsplit(response.headers["location"])
    state = parse_qs(location.query)["state"][0]
    assert response.status_code == 302
    assert location.netloc == "github.com"
    assert parse_qs(location.query)["redirect_uri"] == ["http://localhost:8000/auth/callback"]
    cookie = response.headers["set-cookie"]
    assert f"{STATE_COOKIE}={state}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_sign_in_is_unavailable_until_it_is_configured() -> None:
    with TestClient(create_app(settings(github_oauth_client_id=None))) as bare:
        response = bare.get("/auth/login", follow_redirects=False)

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "sign-in-unavailable"


@pytest.mark.parametrize("state", [None, "wrong"])
def test_a_callback_that_does_not_match_the_state_cookie_is_refused(
    client: TestClient, github: respx.MockRouter, state: str | None
) -> None:
    client.get("/auth/login", follow_redirects=False)
    params = {"code": "c"} | ({"state": state} if state else {})

    response = client.get("/auth/callback", params=params, follow_redirects=False)

    assert response.status_code == 400
    assert response.json()["type"] == TYPE_PREFIX + "invalid-state"
    assert not github.calls  # nothing was sent to GitHub


def test_a_callback_with_no_login_attempt_is_refused(client: TestClient) -> None:
    response = client.get("/auth/callback", params={"code": "c", "state": "x"})

    assert response.status_code == 400


def test_signing_in_starts_a_session_the_browser_cannot_read(
    client: TestClient, github: respx.MockRouter, engine: Engine
) -> None:
    response = sign_in(client, github)

    assert response.status_code == 302
    assert response.headers["location"] == DASHBOARD + "/"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(SESSION_COOKIE + "="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Secure" not in session_cookie  # the test API URL is plain http
    me = client.get("/me").json()
    assert me["signed_in"] is True
    assert me["login"] == "ada"
    assert me["is_approver"] is True
    assert len(me["csrf_token"]) >= 43
    with Session(engine) as db:
        row = db.scalars(select(WebSession)).one()
    assert row.github_id == 4_000_000_001
    assert row.expires_at == T0 + timedelta(hours=8)


def test_the_stored_github_token_is_encrypted_and_the_session_id_is_hashed(
    client: TestClient, github: respx.MockRouter, engine: Engine
) -> None:
    sign_in(client, github)

    with Session(engine) as db:
        row = db.scalars(select(WebSession)).one()
    assert GITHUB_TOKEN not in row.token_encrypted
    assert TokenCipher(SECRET).decrypt(row.token_encrypted) == GITHUB_TOKEN
    assert client.cookies[SESSION_COOKIE] not in (row.id_hash, row.token_encrypted)
    assert len(row.id_hash) == 64


@pytest.mark.parametrize(("expires_in", "hours"), [(3600, 1), (10**7, 8), (None, 8)])
def test_a_session_never_outlives_its_github_token_or_eight_hours(
    client: TestClient,
    github: respx.MockRouter,
    engine: Engine,
    expires_in: int | None,
    hours: int,
) -> None:
    sign_in(client, github, expires_in=expires_in)

    with Session(engine) as db:
        assert db.scalars(select(WebSession)).one().expires_at == T0 + timedelta(hours=hours)


def test_the_cookie_is_secure_when_the_api_is_served_over_https(
    engine: Engine, clock: Clock, github: respx.MockRouter
) -> None:
    app = create_app(settings(api_public_url="https://api.example.com"), clock=clock)
    app.state.engine = engine

    with TestClient(app, base_url="https://api.example.com") as secure:
        response = sign_in(secure, github)

    cookie = next(c for c in response.headers.get_list("set-cookie") if SESSION_COOKIE in c)
    assert "Secure" in cookie


def test_declining_on_github_returns_to_the_dashboard_without_a_session(
    client: TestClient, github: respx.MockRouter, engine: Engine
) -> None:
    started = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]

    response = client.get(
        "/auth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )

    assert response.headers["location"] == DASHBOARD + "/?sign_in=denied"
    assert client.get("/me").json()["signed_in"] is False
    with Session(engine) as db:
        assert db.scalars(select(WebSession)).all() == []


def test_a_github_failure_returns_to_the_dashboard_without_a_session(
    client: TestClient, github: respx.MockRouter, engine: Engine
) -> None:
    started = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    github.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"error": "bad_code"}))

    response = client.get(
        "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )

    assert response.headers["location"] == DASHBOARD + "/?sign_in=failed"
    with Session(engine) as db:
        assert db.scalars(select(WebSession)).all() == []


def test_signing_in_clears_sessions_that_have_expired(
    client: TestClient, github: respx.MockRouter, engine: Engine, clock: Clock
) -> None:
    sign_in(client, github)
    clock.now = T0 + timedelta(hours=9)
    client.cookies.clear()

    sign_in(client, github, login="grace")

    with Session(engine) as db:
        assert [row.login for row in db.scalars(select(WebSession))] == ["grace"]


def test_an_expired_session_is_anonymous(
    client: TestClient, github: respx.MockRouter, clock: Clock
) -> None:
    sign_in(client, github)
    clock.now = T0 + timedelta(hours=8)

    assert client.get("/me").json()["signed_in"] is False


def test_a_cookie_that_names_no_session_is_anonymous(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "forged")

    assert client.get("/me").json()["signed_in"] is False


def test_a_signed_in_stranger_can_read_but_is_not_an_approver(
    client: TestClient, github: respx.MockRouter
) -> None:
    sign_in(client, github, login="mallory")

    me = client.get("/me").json()

    assert (me["signed_in"], me["is_approver"]) == (True, False)


def test_the_allowlist_ignores_case(client: TestClient, github: respx.MockRouter) -> None:
    sign_in(client, github, login="GRACE")

    response = client.post("/protected", headers={"X-CSRF-Token": csrf_of(client)})

    assert response.status_code == 200


def test_a_state_changing_request_needs_a_session(client: TestClient) -> None:
    response = client.post("/protected")

    assert response.status_code == 401
    assert response.json()["type"] == TYPE_PREFIX + "unauthenticated"


def test_a_signed_in_non_approver_is_refused_even_with_the_right_csrf_token(
    client: TestClient, github: respx.MockRouter
) -> None:
    sign_in(client, github, login="mallory")

    response = client.post("/protected", headers={"X-CSRF-Token": csrf_of(client)})

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "forbidden"


@pytest.mark.parametrize("header", [None, "wrong-token"])
def test_an_approver_without_the_right_csrf_token_is_refused(
    client: TestClient, github: respx.MockRouter, header: str | None
) -> None:
    sign_in(client, github)

    response = client.post("/protected", headers={"X-CSRF-Token": header} if header else {})

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "csrf-failed"


def test_a_request_from_another_site_is_refused_even_with_the_token(
    client: TestClient, github: respx.MockRouter
) -> None:
    sign_in(client, github)

    response = client.post(
        "/protected",
        headers={"X-CSRF-Token": csrf_of(client), "Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
    assert response.json()["type"] == TYPE_PREFIX + "csrf-failed"


def test_an_approver_with_the_token_from_the_dashboard_origin_may_act(
    client: TestClient, github: respx.MockRouter
) -> None:
    sign_in(client, github)

    response = client.post(
        "/protected", headers={"X-CSRF-Token": csrf_of(client), "Origin": DASHBOARD}
    )

    assert (response.status_code, response.json()) == (200, {"login": "ada"})


def test_signing_out_needs_the_csrf_token_then_ends_the_session(
    client: TestClient, github: respx.MockRouter, engine: Engine
) -> None:
    sign_in(client, github)

    refused = client.post("/auth/logout")
    done = client.post("/auth/logout", headers={"X-CSRF-Token": csrf_of(client)})

    assert refused.status_code == 403
    assert done.status_code == 204
    assert client.get("/me").json()["signed_in"] is False
    with Session(engine) as db:
        assert db.scalars(select(WebSession)).all() == []


def test_signing_out_when_not_signed_in_is_not_an_error(client: TestClient) -> None:
    assert client.post("/auth/logout").status_code == 204


def test_the_schema_documents_the_auth_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert {"/me", "/auth/login", "/auth/callback", "/auth/logout"} <= set(paths)


def _sign_in_with_next(
    client: TestClient, github: respx.MockRouter, next_path: str | None
) -> httpx.Response:
    params = {"next": next_path} if next_path is not None else None
    started = client.get("/auth/login", params=params, follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    github.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": GITHUB_TOKEN})
    )
    github.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"id": 4_000_000_001, "login": "ada"})
    )
    return client.get(
        "/auth/callback", params={"code": "one-time", "state": state}, follow_redirects=False
    )


def test_signing_in_returns_to_the_page_the_visitor_came_from(
    client: TestClient, github: respx.MockRouter
) -> None:
    response = _sign_in_with_next(client, github, "/incidents/42?tab=fix")

    assert response.headers["location"] == DASHBOARD + "/incidents/42?tab=fix"


def test_signing_in_without_a_page_returns_to_the_home_page(
    client: TestClient, github: respx.MockRouter
) -> None:
    response = _sign_in_with_next(client, github, None)

    assert response.headers["location"] == DASHBOARD + "/"


@pytest.mark.parametrize(
    "unsafe",
    [
        "//evil.example/x",
        "/\\evil.example",
        "https://evil.example/",
        "evil.example",
        "javascript:alert(1)",
        "/ok\r\nSet-Cookie: a=b",
        "/" + "a" * 600,
    ],
)
def test_a_next_path_that_could_leave_the_site_is_ignored(
    client: TestClient, github: respx.MockRouter, unsafe: str
) -> None:
    response = _sign_in_with_next(client, github, unsafe)

    assert response.headers["location"] == DASHBOARD + "/"


def test_a_tampered_next_cookie_is_checked_again_at_the_callback(
    client: TestClient, github: respx.MockRouter
) -> None:
    started = client.get("/auth/login", params={"next": "/incidents/1"}, follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    real = next(c for c in client.cookies.jar if c.name == NEXT_COOKIE)
    client.cookies.set(NEXT_COOKIE, "//evil.example", domain=real.domain, path="/auth")
    github.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": GITHUB_TOKEN})
    )
    github.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"id": 4_000_000_001, "login": "ada"})
    )

    response = client.get(
        "/auth/callback", params={"code": "one-time", "state": state}, follow_redirects=False
    )

    assert response.headers["location"] == DASHBOARD + "/"


def test_a_refused_sign_in_still_returns_to_the_page_and_says_why(
    client: TestClient,
) -> None:
    started = client.get("/auth/login", params={"next": "/incidents/7?a=1"}, follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]

    response = client.get(
        "/auth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )

    assert response.headers["location"] == DASHBOARD + "/incidents/7?a=1&sign_in=denied"


# --- through the dashboard's proxy (LH-115) -------------------------------------------------------


class StripPrefix:
    """Forwards requests under `prefix` with it removed, as the dashboard's proxy does (D-61).

    The test client's cookie jar then applies the browser's own path rules to the address the
    visitor really uses, `/api/auth/...`, while the application sees `/auth/...`.
    """

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self.prefix + "/"):
            path = scope["path"][len(self.prefix) :]
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)


@pytest.fixture
def browser(engine: Engine, clock: Clock) -> Iterator[TestClient]:
    """A visitor who reaches the API at `http://localhost:5173/api`, the dashboard's origin."""
    application = create_app(settings(api_public_url="http://localhost:5173/api"), clock=clock)
    application.state.engine = engine
    with TestClient(StripPrefix(application, "/api"), base_url="http://localhost:5173") as visitor:
        yield visitor


def _mock_github_user(github: respx.MockRouter) -> None:
    github.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": GITHUB_TOKEN})
    )
    github.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"id": 4_000_000_001, "login": "ada"})
    )


def test_the_sign_in_cookies_are_scoped_to_the_path_the_browser_uses(
    browser: TestClient,
) -> None:
    started = browser.get(
        "/api/auth/login", params={"next": "/incidents/7"}, follow_redirects=False
    )

    assert parse_qs(urlsplit(started.headers["location"]).query)["redirect_uri"] == [
        "http://localhost:5173/api/auth/callback"
    ]
    cookies = started.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Path=/api/auth" in cookie for cookie in cookies)


def test_signing_in_through_the_dashboards_proxy_works(
    browser: TestClient, github: respx.MockRouter
) -> None:
    """Before LH-115 the state cookie was not sent to `/api/auth/callback`: `invalid-state`."""
    started = browser.get(
        "/api/auth/login", params={"next": "/incidents/7"}, follow_redirects=False
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    _mock_github_user(github)

    response = browser.get(
        "/api/auth/callback", params={"code": "one-time", "state": state}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == DASHBOARD + "/incidents/7"
    assert browser.get("/api/me").json()["login"] == "ada"


@pytest.mark.parametrize("layout", ["direct", "proxied"])
def test_the_sign_in_cookies_are_removed_once_sign_in_ends(
    layout: str,
    client: TestClient,
    browser: TestClient,
    github: respx.MockRouter,
) -> None:
    """Deleting a cookie only works with the path it was set with."""
    visitor, prefix = (client, "") if layout == "direct" else (browser, "/api")
    started = visitor.get(f"{prefix}/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    _mock_github_user(github)
    assert {STATE_COOKIE, NEXT_COOKIE} <= {c.name for c in visitor.cookies.jar}

    visitor.get(
        f"{prefix}/auth/callback",
        params={"code": "one-time", "state": state},
        follow_redirects=False,
    )

    assert {c.name for c in visitor.cookies.jar} == {SESSION_COOKIE}
