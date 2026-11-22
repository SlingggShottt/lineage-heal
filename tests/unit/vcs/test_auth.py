"""IF-SW-06: the App signs in with a short-lived JWT and gets a cached installation token."""

import httpx
import jwt
import pytest
import respx
from pydantic import SecretStr

from lineageheal.core.errors import VCSError
from lineageheal.vcs import AppAuth
from lineageheal.vcs.client import make_http_client

from .conftest import APP_TOKEN, CLOCK


def test_the_app_jwt_is_signed_by_the_key_and_short_lived(
    auth: AppAuth, private_key: SecretStr
) -> None:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_key.get_secret_value().encode(), password=None)
    claims = jwt.decode(
        auth.app_jwt(),
        key.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )  # type: ignore[arg-type]

    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] <= 10 * 60
    assert claims["iat"] < CLOCK < claims["exp"]


def test_a_key_with_escaped_newlines_works(
    private_key: SecretStr, github: respx.MockRouter
) -> None:
    escaped = SecretStr(private_key.get_secret_value().strip().replace("\n", "\\n"))
    auth = AppAuth("123", escaped, "42", http=make_http_client(), clock=lambda: CLOCK)

    assert auth.token() == APP_TOKEN


def test_the_token_is_cached_until_close_to_expiry(
    private_key: SecretStr, github: respx.MockRouter
) -> None:
    now = [CLOCK]
    auth = AppAuth("123", private_key, "42", http=make_http_client(), clock=lambda: now[0])

    auth.token()
    auth.token()
    assert github.calls.call_count == 1

    now[0] = CLOCK + 3600 - 60  # inside the refresh margin of the 1 hour expiry
    auth.token()
    assert github.calls.call_count == 2


def test_the_installation_request_carries_the_jwt_not_a_token(
    auth: AppAuth, github: respx.MockRouter
) -> None:
    auth.token()

    header = github.calls.last.request.headers["authorization"]
    assert header.startswith("Bearer ")
    assert jwt.get_unverified_header(header.removeprefix("Bearer "))["alg"] == "RS256"


def test_a_refused_app_gives_a_message_naming_the_settings(
    private_key: SecretStr, github: respx.MockRouter
) -> None:
    github.post("/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(401, json={"message": "bad"})
    )
    auth = AppAuth("123", private_key, "42", http=make_http_client(), clock=lambda: CLOCK)

    with pytest.raises(VCSError, match="GITHUB_APP_ID"):
        auth.token()


def test_a_key_that_is_not_a_key_is_an_error_without_the_key_in_it() -> None:
    auth = AppAuth("123", SecretStr("not a pem"), "42", http=make_http_client())

    with pytest.raises(VCSError) as error:
        auth.app_jwt()

    assert "not a pem" not in str(error.value)


def test_a_network_failure_is_a_vcs_error(private_key: SecretStr, github: respx.MockRouter) -> None:
    github.post("/app/installations/42/access_tokens").mock(side_effect=httpx.ReadTimeout("slow"))
    auth = AppAuth("123", private_key, "42", http=make_http_client(), clock=lambda: CLOCK)

    with pytest.raises(VCSError, match="could not be reached"):
        auth.token()
