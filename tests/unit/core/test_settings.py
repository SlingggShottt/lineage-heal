from pathlib import Path

import pytest

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings, get_settings


def make_settings() -> Settings:
    # _env_file=None keeps a developer's real .env out of the test.
    return Settings(_env_file=None)


def test_defaults_are_safe_for_local_development() -> None:
    settings = make_settings()

    assert settings.log_level == "INFO"
    assert settings.log_format == "console"
    assert settings.raw_zone_backend == "local"
    assert settings.raw_zone_dir == Path(".data/raw")
    assert settings.warehouse_path == Path(".data/dev.duckdb")


def test_requests_to_sources_are_spaced_three_seconds_apart_by_default() -> None:
    assert make_settings().ingest_min_interval_seconds == 3.0


def test_the_request_interval_cannot_be_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_MIN_INTERVAL_SECONDS", "-1")

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        make_settings()


def test_raw_zone_backend_can_be_switched_to_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAW_ZONE_BACKEND", "r2")

    assert make_settings().raw_zone_backend == "r2"


def test_values_come_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "json")

    settings = make_settings()

    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"


def test_blank_values_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A freshly copied .env.example has blank values, which must not count as set."""
    monkeypatch.setenv("R2_BUCKET", "")

    assert make_settings().r2_bucket is None


def test_r2_credentials_are_built_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_ACCOUNT_ID", "abc123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "super-secret")
    monkeypatch.setenv("R2_BUCKET", "raw-bucket")

    credentials = make_settings().r2_credentials()

    assert credentials.bucket == "raw-bucket"
    assert credentials.endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert credentials.secret_access_key.get_secret_value() == "super-secret"


def test_secrets_are_masked_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "super-secret")

    assert "super-secret" not in repr(make_settings())


def test_missing_r2_settings_are_named_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("R2_ACCOUNT_ID", "abc123")

    with pytest.raises(ConfigurationError) as raised:
        make_settings().r2_credentials()

    message = str(raised.value)
    assert "R2_BUCKET" in message
    assert "R2_ACCESS_KEY_ID" in message
    assert "R2_ACCOUNT_ID" not in message
    assert "abc123" not in message


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()


def test_the_database_url_is_optional_and_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().database_url is None

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:hunter2@host/db")

    settings = make_settings()
    assert "hunter2" not in repr(settings)
    assert settings.require_database_url() == "postgresql+psycopg://user:hunter2@host/db"


def test_a_missing_database_url_is_explained_by_name() -> None:
    with pytest.raises(ConfigurationError, match="DATABASE_URL is not set"):
        make_settings().require_database_url()


def test_model_ids_and_rates_come_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "some-new-model")
    monkeypatch.setenv("GROQ_REQUESTS_PER_MINUTE", "5")

    settings = make_settings()

    assert settings.gemini_model == "some-new-model"
    assert settings.groq_requests_per_minute == 5


def test_llm_keys_are_optional_and_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().gemini_api_key is None
    monkeypatch.setenv("GEMINI_API_KEY", "sekret-value")

    assert "sekret-value" not in repr(make_settings())


def test_approver_logins_are_split_trimmed_and_lower_cased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert make_settings().approver_logins == frozenset()
    monkeypatch.setenv("APPROVER_GITHUB_LOGINS", " Ada ,grace,, ")

    assert make_settings().approver_logins == frozenset({"ada", "grace"})


def test_sign_in_secrets_are_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "s3cret-session-key")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "s3cret-client")

    shown = repr(make_settings())

    assert "s3cret-session-key" not in shown
    assert "s3cret-client" not in shown
