import pytest

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings, get_settings

R2_VARIABLES = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no R2 or logging variables, whatever the developer exported."""
    for name in (*R2_VARIABLES, "LOG_LEVEL", "LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)


def make_settings() -> Settings:
    # _env_file=None keeps a developer's real .env out of the test.
    return Settings(_env_file=None)


def test_defaults_are_safe_for_local_development() -> None:
    settings = make_settings()

    assert settings.log_level == "INFO"
    assert settings.log_format == "console"


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
