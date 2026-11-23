"""NFR-SEC-03: secret handling for sessions."""

import pytest

from lineageheal.api.security import (
    TokenCipher,
    TokenError,
    hash_session_id,
    new_token,
    tokens_match,
)
from lineageheal.core.errors import ConfigurationError

SECRET = "s" * 40


def test_new_tokens_are_long_and_never_repeat() -> None:
    tokens = {new_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)


def test_the_stored_hash_is_not_the_session_id_and_is_stable() -> None:
    session_id = new_token()

    assert hash_session_id(session_id) == hash_session_id(session_id)
    assert session_id not in hash_session_id(session_id)
    assert len(hash_session_id(session_id)) == 64


def test_tokens_match_only_when_equal() -> None:
    assert tokens_match("abc", "abc")
    assert not tokens_match("abc", "abd")
    assert not tokens_match("abc", "abcd")


def test_an_encrypted_token_round_trips_and_does_not_show_the_original() -> None:
    cipher = TokenCipher(SECRET)

    stored = cipher.encrypt("ghu_secret_token")

    assert "ghu_secret_token" not in stored
    assert cipher.decrypt(stored) == "ghu_secret_token"
    assert cipher.encrypt("ghu_secret_token") != stored  # a fresh nonce each time


def test_a_rotated_secret_cannot_read_old_tokens() -> None:
    stored = TokenCipher(SECRET).encrypt("ghu_x")

    with pytest.raises(TokenError):
        TokenCipher("t" * 40).decrypt(stored)


def test_a_tampered_token_is_refused() -> None:
    stored = TokenCipher(SECRET).encrypt("ghu_x")

    with pytest.raises(TokenError):
        TokenCipher(SECRET).decrypt(stored[:-4] + "AAAA")


def test_a_short_secret_is_refused_with_advice() -> None:
    with pytest.raises(ConfigurationError, match="openssl rand"):
        TokenCipher("short")
