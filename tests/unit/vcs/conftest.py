"""A throwaway App key and a client wired to a mocked GitHub."""

from collections.abc import Iterator

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from lineageheal.vcs import AppAuth, GitHubClient
from lineageheal.vcs.client import make_http_client

REPO = "acme/pipeline"
APP_TOKEN = "ghs_installation_token"
EXPIRES = "2030-01-01T01:00:00Z"
CLOCK = 1_893_456_000.0  # 2030-01-01T00:00:00Z


@pytest.fixture(scope="session")
def private_key() -> SecretStr:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return SecretStr(pem.decode())


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as router:
        router.post("/app/installations/42/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": APP_TOKEN, "expires_at": EXPIRES})
        )
        yield router


@pytest.fixture
def auth(private_key: SecretStr, github: respx.MockRouter) -> AppAuth:
    return AppAuth("123", private_key, "42", http=make_http_client(), clock=lambda: CLOCK)


@pytest.fixture
def client(auth: AppAuth) -> GitHubClient:
    return GitHubClient(auth, REPO, base_branch="main", http=make_http_client())
