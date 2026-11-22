"""Builds the GitHub client from settings."""

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.vcs.auth import AppAuth
from lineageheal.vcs.client import GitHubClient, make_http_client


def build_github_client(settings: Settings) -> GitHubClient:
    """A client for the configured repository, acting as the LineageHeal GitHub App.

    Raises:
        ConfigurationError: A required setting is missing. The message names the variable.
    """
    app_id = settings.github_app_id
    key = settings.github_app_private_key
    installation = settings.github_app_installation_id
    repository = settings.github_repository
    if app_id is None or key is None or installation is None or repository is None:
        missing = [
            name
            for name, value in (
                ("GITHUB_APP_ID", app_id),
                ("GITHUB_APP_PRIVATE_KEY", key),
                ("GITHUB_APP_INSTALLATION_ID", installation),
                ("GITHUB_REPOSITORY", repository),
            )
            if value is None
        ]
        raise ConfigurationError(f"Set {', '.join(missing)} in .env to use GitHub")
    http = make_http_client()
    auth = AppAuth(app_id, key, installation, http=http)
    return GitHubClient(auth, repository, base_branch=settings.github_base_branch, http=http)
