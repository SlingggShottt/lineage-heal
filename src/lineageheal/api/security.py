"""Session ids, CSRF tokens and the encryption of stored GitHub tokens (NFR-SEC-03).

Nothing here touches the network or the database; it is the small set of secret-handling
functions the sign-in code needs, kept apart so each one can be tested on its own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from lineageheal.core.errors import ConfigurationError, LineageHealError

SESSION_COOKIE = "lh_session"
# Carries the OAuth `state` between the redirect to GitHub and the callback.
STATE_COOKIE = "lh_oauth_state"
MIN_SECRET_LENGTH = 32


class TokenError(LineageHealError):
    """A stored token could not be decrypted, for example after the secret was rotated."""


def new_token() -> str:
    """A fresh unguessable value (256 bits), used for session ids, CSRF tokens and `state`."""
    return secrets.token_urlsafe(32)


def hash_session_id(session_id: str) -> str:
    """The value stored in place of a session id, so a copy of the table is not a set of cookies.

    A plain SHA-256 is enough because the id is 256 random bits, not a password.
    """
    return hashlib.sha256(session_id.encode()).hexdigest()


def tokens_match(first: str, second: str) -> bool:
    """Compare two secret values without revealing, by timing, how much of them agree."""
    return hmac.compare_digest(first.encode(), second.encode())


class TokenCipher:
    """Encrypts the GitHub token kept with a session, with a key derived from `SESSION_SECRET`."""

    def __init__(self, secret: str) -> None:
        """Create a cipher.

        Raises:
            ConfigurationError: The secret is too short to be a good key.
        """
        if len(secret) < MIN_SECRET_LENGTH:
            raise ConfigurationError(
                f"SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters; "
                "generate one with `openssl rand -hex 32`"
            )
        # HKDF so the key does not depend on the secret's length or alphabet, and a different
        # `info` would give an unrelated key if the secret is ever reused for something else.
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"lineageheal-session-token"
        ).derive(secret.encode())
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt and authenticate a value."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Return the original value.

        Raises:
            TokenError: The value was not produced with this secret, or was altered.
        """
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise TokenError("The stored token cannot be decrypted with this secret") from exc
