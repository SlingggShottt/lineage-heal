import json

import pytest
import structlog

from lineageheal.core.logging import configure_logging


def emitted_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    """Parse the JSON log lines written to stderr."""
    err = capsys.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line]


def test_json_logs_carry_event_level_timestamp_and_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", log_format="json")

    structlog.get_logger("test").info("file_stored", source="amfi", size_bytes=42)

    (line,) = emitted_lines(capsys)
    assert line["event"] == "file_stored"
    assert line["level"] == "info"
    assert line["source"] == "amfi"
    assert line["size_bytes"] == 42
    assert str(line["timestamp"]).endswith("Z")


def test_logs_go_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", log_format="json")

    structlog.get_logger("test").info("file_stored")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "file_stored" in captured.err


def test_messages_below_the_configured_level_are_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="WARNING", log_format="json")
    log = structlog.get_logger("test")

    log.info("lifecycle_event")
    log.warning("recoverable_problem")

    events = [line["event"] for line in emitted_lines(capsys)]
    assert events == ["recoverable_problem"]


def test_fields_that_look_like_credentials_are_masked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", log_format="json")

    structlog.get_logger("test").info(
        "connecting", r2_secret_access_key="super-secret", api_key="abc", bucket="raw"
    )

    (line,) = emitted_lines(capsys)
    assert line["r2_secret_access_key"] == "***"
    assert line["api_key"] == "***"
    assert line["bucket"] == "raw"


def test_console_format_is_human_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", log_format="console")

    structlog.get_logger("test").info("file_stored", source="amfi")

    err = capsys.readouterr().err
    assert "file_stored" in err
    assert "source" in err
    assert not err.lstrip().startswith("{")


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(KeyError):
        configure_logging(level="LOUD")
