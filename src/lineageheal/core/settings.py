"""Typed application settings.

This is the only module that reads configuration from the environment. Everything
else receives a `Settings` object (or a value taken from one), which keeps secrets
out of call sites and makes configuration easy to fake in tests.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from lineageheal.core.errors import ConfigurationError


class R2Credentials(BaseModel):
    """Everything needed to talk to the Cloudflare R2 raw zone."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    access_key_id: SecretStr
    secret_access_key: SecretStr
    bucket: str

    @property
    def endpoint_url(self) -> str:
        """The S3-compatible endpoint for this account."""
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional `.env` file.

    Credentials are optional here because each command needs only some of them; use
    the accessor for the service you need (for example `r2_credentials`) to fail
    with a clear message when something is missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # .env also holds variables for services not built yet
        # .env.example ships blank values; treat them as unset rather than as "".
        env_ignore_empty=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Console output is readable while developing; JSON is for CI logs.
    log_format: Literal["console", "json"] = "console"

    # "local" is the safe default so nothing reaches the shared bucket by accident;
    # the scheduled pipeline and the demo set "r2" explicitly.
    raw_zone_backend: Literal["local", "r2"] = "local"
    raw_zone_dir: Path = Path(".data/raw")

    # Gap between requests to a source site (FR-ING-10). Raise it if a source pushes back.
    ingest_min_interval_seconds: float = Field(default=3.0, ge=0)

    # Postgres for application state. Optional: without it ingestion still runs and simply
    # keeps no log. A secret, because the URL carries the password.
    database_url: SecretStr | None = None

    r2_account_id: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str | None = None

    def require_database_url(self) -> str:
        """Return the database URL, or explain that it is not set.

        Raises:
            ConfigurationError: DATABASE_URL is not set. The message names the variable.
        """
        if self.database_url is None:
            raise ConfigurationError(
                "DATABASE_URL is not set; start Postgres with `make up` and set it in .env"
            )
        return self.database_url.get_secret_value()

    def r2_credentials(self) -> R2Credentials:
        """Return the R2 credentials, or explain exactly which variables are missing.

        Returns:
            The validated credentials.

        Raises:
            ConfigurationError: One or more R2 variables are unset. The message names
                the variables, never their values.
        """
        account_id = self.r2_account_id
        access_key_id = self.r2_access_key_id
        secret_access_key = self.r2_secret_access_key
        bucket = self.r2_bucket

        if (
            account_id is None
            or access_key_id is None
            or secret_access_key is None
            or bucket is None
        ):
            named = {
                "R2_ACCOUNT_ID": account_id,
                "R2_ACCESS_KEY_ID": access_key_id,
                "R2_SECRET_ACCESS_KEY": secret_access_key,
                "R2_BUCKET": bucket,
            }
            missing = [name for name, value in named.items() if value is None]
            raise ConfigurationError(f"Missing R2 settings: {', '.join(missing)}")

        return R2Credentials(
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket=bucket,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, read once on first use."""
    return Settings()
