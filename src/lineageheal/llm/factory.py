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


def build_endpoints(settings: Settings) -> list[Endpoint]:
    """Return one rate-limited endpoint per configured provider, preferred first.

    Raises:
        ConfigurationError: Neither GEMINI_API_KEY nor GROQ_API_KEY is set.
    """
    endpoints: list[Endpoint] = []
    if settings.gemini_api_key is not None:
        endpoints.append(
            Endpoint(
                GeminiProvider(
                    settings.gemini_api_key,
                    standard_model=settings.gemini_model,
                    light_model=settings.gemini_light_model,
                ),
                TokenBucket(settings.gemini_requests_per_minute),
            )
        )
    if settings.groq_api_key is not None:
        endpoints.append(
            Endpoint(
                GroqProvider(settings.groq_api_key, model=settings.groq_model),
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
    return LLMClient(build_endpoints(settings))
