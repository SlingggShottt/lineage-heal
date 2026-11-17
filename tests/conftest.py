"""Shared pytest configuration.

Tests are marked by the directory they live in, so the markers in the style guide
(unit, integration, e2e, and dbt for the tests that run real dbt builds) never depend on
someone remembering to add a decorator.
"""

from collections.abc import Iterator

import pytest
import structlog

from lineageheal.core.settings import Settings, get_settings

_LAYERS = ("unit", "integration", "e2e")

# Every environment variable Settings reads. Cleared for each test so results never
# depend on what a developer has exported in their shell.
_SETTINGS_VARIABLES = (
    "LOG_LEVEL",
    "LOG_FORMAT",
    "RAW_ZONE_BACKEND",
    "RAW_ZONE_DIR",
    "INGEST_MIN_INTERVAL_SECONDS",
    "DATABASE_URL",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_LIGHT_MODEL",
    "GROQ_MODEL",
    "GEMINI_REQUESTS_PER_MINUTE",
    "GROQ_REQUESTS_PER_MINUTE",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    """Undo any logging configuration a test applied.

    configure_logging binds structlog to the current stderr. Under pytest that is a
    temporary capture object, closed after the test, which would break later tests
    that log.
    """
    yield
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def _clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all settings variables from the process environment for this test."""
    for name in _SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    # A developer's own .env sets real values (a database URL, API keys). Tests that run from
    # the repository root would read it and could act on the real services it points at.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()  # tests of code using get_settings() must see fresh values


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the layer marker that matches each test's top-level directory under tests/."""
    for item in items:
        parts = item.path.parts
        for layer in _LAYERS:
            if layer in parts:
                item.add_marker(getattr(pytest.mark, layer))
                break
        if "dbt" in parts:
            item.add_marker(pytest.mark.dbt)
