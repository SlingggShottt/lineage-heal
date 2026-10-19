"""Shared pytest configuration.

Tests are marked by the directory they live in, so the markers in the style guide
(unit, integration, e2e) never depend on someone remembering to add a decorator.
"""

import pytest

_LAYERS = ("unit", "integration", "e2e")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the layer marker that matches each test's top-level directory under tests/."""
    for item in items:
        parts = item.path.parts
        for layer in _LAYERS:
            if layer in parts:
                item.add_marker(getattr(pytest.mark, layer))
                break
