"""The client is built from settings, and a missing setting is named, not guessed."""

import pytest
from pydantic import SecretStr, ValidationError

from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.vcs import GitHubClient, build_github_client


def settings(**fields: object) -> Settings:
    return Settings(_env_file=None, **fields)  # type: ignore[arg-type]


def test_a_complete_configuration_builds_a_client() -> None:
    client = build_github_client(
        settings(
            github_app_id="1",
            github_app_private_key=SecretStr("k"),
            github_app_installation_id="2",
            github_repository="acme/pipeline",
        )
    )

    assert isinstance(client, GitHubClient)


def test_missing_settings_are_named() -> None:
    with pytest.raises(ConfigurationError) as error:
        build_github_client(settings(github_app_id="1"))

    message = str(error.value)
    assert "GITHUB_APP_PRIVATE_KEY" in message
    assert "GITHUB_APP_INSTALLATION_ID" in message
    assert "GITHUB_REPOSITORY" in message
    assert "GITHUB_APP_ID" not in message.replace("GITHUB_APP_INSTALLATION_ID", "")


@pytest.mark.parametrize("repository", ["noslash", "a/b/c", "a b/c", "/c"])
def test_a_repository_must_be_owner_slash_name(repository: str) -> None:
    with pytest.raises(ValidationError):
        settings(github_repository=repository)
