"""FR-API-02: the OAuth code flow, against a mocked GitHub."""

from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from pydantic import SecretStr

from lineageheal.api.github_auth import (
    TOKEN_URL,
    USER_URL,
    GitHubOAuth,
    OAuthError,
)

CALLBACK = "http://localhost:8000/auth/callback"


@pytest.fixture
def oauth() -> GitHubOAuth:
    return GitHubOAuth("Iv1.client", SecretStr("client-secret"), CALLBACK)


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_the_authorize_url_carries_the_client_callback_and_state(oauth: GitHubOAuth) -> None:
    url = urlsplit(oauth.authorize_url("st4te"))
    query = parse_qs(url.query)

    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/login/oauth/authorize")
    assert query == {"client_id": ["Iv1.client"], "redirect_uri": [CALLBACK], "state": ["st4te"]}
    assert "client-secret" not in url.geturl()


def test_a_code_is_exchanged_for_a_token_and_its_lifetime(
    oauth: GitHubOAuth, github: respx.MockRouter
) -> None:
    route = github.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "ghu_abc", "expires_in": 28800})
    )

    token = oauth.exchange_code("the-code")

    assert token.value.get_secret_value() == "ghu_abc"
    assert token.expires_in == 28800
    sent = parse_qs(route.calls[0].request.content.decode())
    assert sent["code"] == ["the-code"]
    assert sent["client_secret"] == ["client-secret"]
    assert route.calls[0].request.headers["accept"] == "application/json"


def test_a_token_that_does_not_expire_has_no_lifetime(
    oauth: GitHubOAuth, github: respx.MockRouter
) -> None:
    github.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "ghu_abc"}))

    assert oauth.exchange_code("c").expires_in is None


def test_a_bad_code_reported_with_status_200_is_an_error_without_the_code(
    oauth: GitHubOAuth, github: respx.MockRouter
) -> None:
    github.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"error": "bad_verification_code"})
    )

    with pytest.raises(OAuthError, match="bad_verification_code") as raised:
        oauth.exchange_code("secret-code")

    assert "secret-code" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["a list"]),
        httpx.Response(200, json={"token_type": "bearer"}),
    ],
)
def test_other_failed_exchanges_are_oauth_errors(
    oauth: GitHubOAuth, github: respx.MockRouter, response: httpx.Response
) -> None:
    github.post(TOKEN_URL).mock(return_value=response)

    with pytest.raises(OAuthError):
        oauth.exchange_code("c")


def test_the_user_is_looked_up_with_the_token(oauth: GitHubOAuth, github: respx.MockRouter) -> None:
    route = github.get(USER_URL).mock(
        return_value=httpx.Response(200, json={"id": 4000000001, "login": "Ada"})
    )

    user = oauth.fetch_user(SecretStr("ghu_abc"))

    assert (user.id, user.login) == (4000000001, "Ada")
    assert route.calls[0].request.headers["authorization"] == "Bearer ghu_abc"


@pytest.mark.parametrize(
    "response", [httpx.Response(401), httpx.Response(200, json={"login": "ada"})]
)
def test_a_refused_token_or_incomplete_answer_is_an_oauth_error(
    oauth: GitHubOAuth, github: respx.MockRouter, response: httpx.Response
) -> None:
    github.get(USER_URL).mock(return_value=response)

    with pytest.raises(OAuthError) as raised:
        oauth.fetch_user(SecretStr("ghu_abc"))

    assert "ghu_abc" not in str(raised.value)


def test_a_connection_failure_is_retried_then_reported_without_a_token(
    oauth: GitHubOAuth, github: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    route = github.get(USER_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(OAuthError, match="could not be reached") as raised:
        oauth.fetch_user(SecretStr("ghu_abc"))

    assert route.call_count == 3
    assert "ghu_abc" not in str(raised.value)
