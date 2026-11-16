"""D-07: the factory wires Gemini first and Groq second, from settings alone."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.llm.factory import build_client, build_endpoints


def settings(**keys: SecretStr) -> Settings:
    # Explicit None for both keys, and no .env file, so a developer's real keys never reach a test.
    values: dict[str, SecretStr | None] = {"gemini_api_key": None, "groq_api_key": None}
    values.update(keys)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_gemini_is_preferred_and_groq_is_the_fallback() -> None:
    endpoints = build_endpoints(
        settings(gemini_api_key=SecretStr("g"), groq_api_key=SecretStr("q"))
    )

    assert [e.provider.name for e in endpoints] == ["gemini", "groq"]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"gemini_api_key": SecretStr("g")}, ["gemini"]),
        ({"groq_api_key": SecretStr("q")}, ["groq"]),
    ],
)
def test_a_provider_without_a_key_is_left_out(
    keys: dict[str, SecretStr], expected: list[str]
) -> None:
    assert [e.provider.name for e in build_endpoints(settings(**keys))] == expected


def test_no_keys_is_an_error_that_names_the_variables() -> None:
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        build_client(settings())
