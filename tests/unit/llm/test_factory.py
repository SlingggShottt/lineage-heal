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


def both(**extra: str | float) -> Settings:
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        gemini_api_key=SecretStr("g"),
        groq_api_key=SecretStr("q"),
        **extra,
    )


def test_the_provider_order_setting_puts_groq_first() -> None:
    endpoints = build_endpoints(both(llm_provider_order="groq,gemini"))

    assert [e.provider.name for e in endpoints] == ["groq", "gemini"]


def test_a_provider_without_a_key_is_left_out_whatever_the_order() -> None:
    only_gemini = Settings(_env_file=None, gemini_api_key=SecretStr("g"), groq_api_key=None)  # type: ignore[arg-type]

    endpoints = build_endpoints(
        only_gemini.model_copy(update={"llm_provider_order": "groq,gemini"})
    )

    assert [e.provider.name for e in endpoints] == ["gemini"]


@pytest.mark.parametrize("order", ["gemini,gpt", "gemini,gemini", ",", ""])
def test_an_unusable_order_is_a_configuration_error_naming_the_setting(order: str) -> None:
    with pytest.raises(ConfigurationError, match="LLM_PROVIDER_ORDER"):
        build_endpoints(both(llm_provider_order=order))


def test_the_attempt_timeout_reaches_every_provider() -> None:
    endpoints = build_endpoints(both(llm_attempt_timeout_seconds=12.0))

    for endpoint in endpoints:
        assert endpoint.provider._client.timeout.read == 12.0  # type: ignore[attr-defined]


def test_the_client_gets_the_deadline_and_cooldown_from_settings() -> None:
    llm = build_client(both(llm_call_deadline_seconds=45.0, llm_cooldown_seconds=20.0))

    assert llm._deadline == 45.0
    assert llm._cooldown == 20.0
