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

    # The DuckDB file the Investigator's data tools read, always read-only. It is the dev
    # target's file by default; the benchmark points it at the sandbox it is replaying in.
    warehouse_path: Path = Path(".data/dev.duckdb")

    # Gap between requests to a source site (FR-ING-10). Raise it if a source pushes back.
    ingest_min_interval_seconds: float = Field(default=3.0, ge=0)

    # Postgres for application state. Optional: without it ingestion still runs and simply
    # keeps no log. A secret, because the URL carries the password.
    database_url: SecretStr | None = None

    # LLM providers. Keys are secrets and optional here, so
    # commands that never call a model run without them. Model IDs are configuration, not code:
    # a retired model is fixed in .env, not by a release. Per-minute rates sit below the free
    # tiers' documented limits (docs/techstack.md) so the client limiter, not a 429, paces calls.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_light_model: str = "gemini-3.1-flash-lite"
    groq_model: str = "openai/gpt-oss-120b"
    gemini_requests_per_minute: float = Field(default=10.0, gt=0)
    groq_requests_per_minute: float = Field(default=20.0, gt=0)

    # What one investigation may spend (FR-INV-05). The call limit includes the final answer;
    # twelve fits a few minutes of the free tiers' per-minute limits.
    investigator_max_llm_calls: int = Field(default=12, ge=2)
    investigator_max_seconds: float = Field(default=120.0, gt=0)
    investigator_confidence_threshold: float = Field(default=0.8, gt=0, le=1)

    # What one proposed patch may be (FR-FIX-01). The call limit is the first answer plus the
    # corrections a rejected answer is sent back for; sandbox retries are counted separately.
    fixer_max_llm_calls: int = Field(default=2, ge=1)
    fixer_max_files: int = Field(default=4, ge=1)
    fixer_max_changed_lines: int = Field(default=120, ge=1)

    # Embeddings run locally (D-06). The stored vectors are only comparable with ones from the
    # same model, so the name is recorded with each chunk and a changed setting re-indexes.
    # The knowledge tables hold 384-dimension vectors, so the model must produce that many.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # fastembed would otherwise download the model into the system temp folder on every reboot.
    embedding_cache_dir: Path = Path(".data/models")

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
