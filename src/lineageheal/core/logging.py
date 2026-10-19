"""Structured logging setup.

Modules log with `structlog.get_logger(__name__)` and pass context as key-value
fields. `configure_logging` is called once at startup by each entry point.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.typing import Processor

# Field names containing any of these are masked, as a backstop in case a secret is
# passed to a log call by mistake (secrets must never reach logs).
_SENSITIVE_FRAGMENTS = ("secret", "token", "password", "api_key", "private_key")
_REDACTED = "***"


def _redact_secrets(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask the value of any field whose name looks like a credential."""
    for key in event_dict:
        if any(fragment in key.lower() for fragment in _SENSITIVE_FRAGMENTS):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO", log_format: str = "console") -> None:
    """Configure structlog for the current process.

    Logs go to stderr so that stdout stays free for command output.

    Args:
        level: Minimum level to emit: DEBUG, INFO, WARNING or ERROR.
        log_format: "json" for machine-readable lines, anything else for console output.
    """
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
        structlog.processors.format_exc_info,
        renderer,
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        # Off so tests (and a second call to this function) see the new configuration.
        cache_logger_on_first_use=False,
    )
