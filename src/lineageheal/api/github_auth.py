"""GitHub sign-in: the OAuth code flow for the LineageHeal GitHub App (FR-API-02).

Plain httpx rather than an OAuth library (D-41, D-56): the flow is two calls, and keeping them
here means they are mocked with respx like every other call in the project. Error messages carry
GitHub's short error code at most, never a code, token or secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lineageheal.core.errors import LineageHealError

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105  # a URL, not a secret
USER_URL = "https://api.github.com/user"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# Only a request that never connected may be repeated: a code is single use, so repeating one
# that may have reached GitHub could turn a success into a failure.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)


class OAuthError(LineageHealError):
    """GitHub refused the sign-in or could not be reached."""


@dataclass(frozen=True)
class AccessToken:
    """A user's GitHub token and how long GitHub says it lives (None if it does not expire)."""

    value: SecretStr
    expires_in: int | None


@dataclass(frozen=True)
class GitHubUser:
    """Who signed in: the stable numeric id and the login."""

    id: int
    login: str


class GitHubOAuth:
    """Talks to GitHub for the sign-in flow."""

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        """Create the client.

        Args:
            client_id: The GitHub App's client id.
            client_secret: The GitHub App's client secret.
            redirect_uri: The callback URL, which must equal the one registered on the App.
            http: A client to use; defaults to one with explicit timeouts.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http or httpx.Client(timeout=TIMEOUT)

    def authorize_url(self, state: str) -> str:
        """The GitHub page the browser is sent to, carrying the anti-forgery `state`."""
        query = urlencode(
            {"client_id": self._client_id, "redirect_uri": self._redirect_uri, "state": state}
        )
        return f"{AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str) -> AccessToken:
        """Trade the one-time code from the callback for the user's token.

        Raises:
            OAuthError: GitHub rejected the code, or could not be reached.
        """
        try:
            response = self._post(code)
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub could not be reached: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OAuthError(f"GitHub answered the code exchange with {response.status_code}")
        body = _json(response)
        if "error" in body:  # GitHub reports a bad code with status 200 and an error field
            raise OAuthError(f"GitHub refused the code: {str(body['error'])[:60]}")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthError("GitHub's answer held no access token")
        expires = body.get("expires_in")
        return AccessToken(SecretStr(token), int(expires) if expires is not None else None)

    def fetch_user(self, token: SecretStr) -> GitHubUser:
        """Ask GitHub who a token belongs to.

        Raises:
            OAuthError: GitHub refused the token, or could not be reached.
        """
        try:
            response = self._get(token.get_secret_value())
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub could not be reached: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OAuthError(f"GitHub answered the user lookup with {response.status_code}")
        body = _json(response)
        try:
            return GitHubUser(id=int(body["id"]), login=str(body["login"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise OAuthError("GitHub's user answer had no id or login") from exc

    @retry(
        retry=retry_if_exception_type(_NOT_SENT),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    def _post(self, code: str) -> httpx.Response:
        return self._http.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

    @retry(
        retry=retry_if_exception_type(_NOT_SENT),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    def _get(self, token: str) -> httpx.Response:
        return self._http.get(
            USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise OAuthError("GitHub's answer was not JSON") from exc
    if not isinstance(body, dict):
        raise OAuthError("GitHub's answer was not a JSON object")
    return body
