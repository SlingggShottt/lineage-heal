"""Authentication as the LineageHeal GitHub App (IF-SW-06, NFR-SEC-02).

The App proves who it is with a short-lived JWT signed by its private key, and trades that for
an installation token that carries only the permissions the App was given. Installation tokens
last an hour, so one is cached and replaced shortly before it expires. Neither the key nor a
token is ever logged or put in an error message.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

import httpx
import jwt
from pydantic import SecretStr

from lineageheal.core.errors import VCSError

API_URL = "https://api.github.com"
# GitHub rejects a JWT valid for more than ten minutes, and a clock slightly ahead of GitHub's.
_JWT_LIFETIME_SECONDS = 9 * 60
_CLOCK_SKEW_SECONDS = 60
# Replace a cached token this long before it expires, so a request never carries one about to lapse.
_REFRESH_MARGIN_SECONDS = 5 * 60


class AppAuth:
    """Issues installation tokens for one installation of the GitHub App."""

    def __init__(
        self,
        app_id: str,
        private_key: SecretStr,
        installation_id: str,
        *,
        http: httpx.Client,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create the authenticator.

        Args:
            app_id: The App's numeric ID.
            private_key: The App's PEM private key, with real newlines or backslash-n.
            installation_id: The installation of the App on the repository.
            http: The client used to call GitHub; its base URL is GitHub's API.
            clock: Seconds since the epoch; replaced in tests.
        """
        self._app_id = app_id
        self._key = private_key
        self._installation_id = installation_id
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def app_jwt(self) -> str:
        """A signed JWT that identifies the App itself.

        Raises:
            VCSError: The private key is not a usable RSA key.
        """
        now = int(self._clock())
        claims = {
            "iat": now - _CLOCK_SKEW_SECONDS,
            "exp": now + _JWT_LIFETIME_SECONDS,
            "iss": self._app_id,
        }
        pem = self._key.get_secret_value().replace("\\n", "\n")
        try:
            return jwt.encode(claims, pem, algorithm="RS256")
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            raise VCSError("The GitHub App private key is not a usable RSA key") from exc

    def token(self) -> str:
        """An installation token, fetched again only when the cached one is close to expiring.

        Raises:
            VCSError: GitHub refused the App's credentials or could not be reached.
        """
        if self._token is not None and self._clock() < self._expires_at - _REFRESH_MARGIN_SECONDS:
            return self._token
        try:
            response = self._http.post(
                f"/app/installations/{self._installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {self.app_jwt()}"},
            )
        except httpx.HTTPError as exc:
            raise VCSError(f"GitHub could not be reached: {type(exc).__name__}") from exc
        if response.status_code != 201:
            raise VCSError(
                f"GitHub refused the App's credentials (HTTP {response.status_code}); "
                "check GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY and GITHUB_APP_INSTALLATION_ID"
            )
        body = response.json()
        self._token = str(body["token"])
        self._expires_at = datetime.fromisoformat(body["expires_at"]).timestamp()
        return self._token
