"""Builds the `LLMClient` from settings, so agents never wire providers themselves.

Gemini is preferred and Groq is the fallback (D-07). A provider with no key is left out rather
than failing, so a developer with only one key can still run; with no key at all the error says
which variables to set.
"""

from __future__ import annotations

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.llm.client import Endpoint, LLMClient
from lineageheal.llm.gemini import GeminiProvider
from lineageheal.llm.groq import GroqProvider
from lineageheal.llm.limiter import TokenBucket

PROVIDERS = ("gemini", "groq")


def provider_order(settings: Settings) -> list[str]:
    """The provider names in `LLM_PROVIDER_ORDER`, preferred first.

    Raises:
        ConfigurationError: A name is not a provider, or one is listed twice.
    """
    names = [
        name.strip().lower() for name in settings.llm_provider_order.split(",") if name.strip()
    ]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown or len(set(names)) != len(names) or not names:
        raise ConfigurationError(
            f"LLM_PROVIDER_ORDER must list each of {', '.join(PROVIDERS)} at most once, "
            f"not {settings.llm_provider_order!r}"
        )
    return names


def build_endpoints(settings: Settings) -> list[Endpoint]:
    """Return one rate-limited endpoint per configured provider, in `LLM_PROVIDER_ORDER`.

    Raises:
        ConfigurationError: The order names an unknown provider, or neither GEMINI_API_KEY nor
            GROQ_API_KEY is set.
    """
    endpoints: list[Endpoint] = []
    timeout = settings.llm_attempt_timeout_seconds
    for name in provider_order(settings):
        if name == "gemini" and settings.gemini_api_key is not None:
            endpoints.append(
                Endpoint(
                    GeminiProvider(
                        settings.gemini_api_key,
                        standard_model=settings.gemini_model,
                        light_model=settings.gemini_light_model,
                        timeout_seconds=timeout,
                    ),
                    TokenBucket(settings.gemini_requests_per_minute),
                )
            )
        if name == "groq" and settings.groq_api_key is not None:
            endpoints.append(
                Endpoint(
                    GroqProvider(
                        settings.groq_api_key, model=settings.groq_model, timeout_seconds=timeout
                    ),
                    TokenBucket(settings.groq_requests_per_minute),
                )
            )
    if not endpoints:
        raise ConfigurationError(
            "No LLM provider is configured; set GEMINI_API_KEY and/or GROQ_API_KEY in .env"
        )
    return endpoints


def build_client(settings: Settings) -> LLMClient:
    """Create the client agents use, from the configured providers."""
    return LLMClient(
        build_endpoints(settings),
        call_deadline_seconds=settings.llm_call_deadline_seconds,
        cooldown_seconds=settings.llm_cooldown_seconds,
    )
