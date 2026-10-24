"""NFR-SEC-05 and NFR-SEC-01: guards on the repository's GitHub configuration.

These read the files in `.github/` and the settings page. GitHub's own settings cannot be tested
from here; the page that describes them is, so that it cannot drift from the workflow it names.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[2]
GITHUB = ROOT / ".github"
SETTINGS_PAGE = (ROOT / "docs" / "repository_settings.md").read_text()
WORKFLOWS = sorted((GITHUB / "workflows").glob("*.yml"))
DEPENDABOT: dict[str, Any] = yaml.safe_load((GITHUB / "dependabot.yml").read_text())
UPDATES: list[dict[str, Any]] = DEPENDABOT["updates"]

# Conventional Commit types the project allows (docs/style_guide.md, section 2.2).
COMMIT_TYPES = {"feat", "fix", "refactor", "perf", "test", "docs", "build", "ci", "chore"}


def front_matter(path: Path) -> dict[str, Any]:
    """The YAML block between the leading `---` lines of an issue template."""
    text = path.read_text()
    assert text.startswith("---\n"), f"{path.name} has no front matter"
    block = text.split("---\n")[1]
    data = yaml.safe_load(block)
    assert isinstance(data, dict)
    return data


# --- pull request and issue templates ------------------------------------------------------


def test_the_pull_request_template_has_the_sections_every_pull_request_uses() -> None:
    template = (GITHUB / "PULL_REQUEST_TEMPLATE.md").read_text()

    headings = re.findall(r"^## (.+)$", template, flags=re.MULTILINE)

    assert headings == ["What", "Why", "How", "Testing", "Checklist"]
    assert template.count("- [ ] ") == 3
    assert "backlog.md updated" in template


@pytest.mark.parametrize("name", ["bug_report.md", "task.md"])
def test_each_issue_template_says_what_it_is_for_and_labels_the_issue(name: str) -> None:
    data = front_matter(GITHUB / "ISSUE_TEMPLATE" / name)

    assert {"name", "about", "title", "labels"} <= set(data)


def test_a_bug_report_asks_for_the_run_id_but_never_for_secrets() -> None:
    text = (GITHUB / "ISSUE_TEMPLATE" / "bug_report.md").read_text()

    assert "lineageheal signals" in text
    assert "Do not paste" in text
    assert ".env" in text


def test_blank_issues_are_off_so_every_issue_starts_from_a_template() -> None:
    config = yaml.safe_load((GITHUB / "ISSUE_TEMPLATE" / "config.yml").read_text())

    assert config["blank_issues_enabled"] is False


# --- Dependabot ----------------------------------------------------------------------------


def test_dependabot_is_on_version_2_and_covers_every_ecosystem_the_repository_uses() -> None:
    assert DEPENDABOT["version"] == 2
    ecosystems = {update["package-ecosystem"] for update in UPDATES}
    assert ecosystems == {"uv", "github-actions", "docker", "docker-compose"}


def test_every_directory_dependabot_watches_exists() -> None:
    """Dependabot reports an error for a directory that is missing."""
    for update in UPDATES:
        folder = ROOT / update["directory"].lstrip("/")
        assert folder.is_dir(), f"{update['package-ecosystem']}: {update['directory']}"


@pytest.mark.parametrize(
    ("manifest", "ecosystem"),
    [
        ("pyproject.toml", "uv"),
        ("uv.lock", "uv"),
        ("docker/Dockerfile", "docker"),
        ("docker-compose.yml", "docker-compose"),
        (".github/workflows/ci.yml", "github-actions"),
        ("web/package.json", "npm"),
    ],
)
def test_a_manifest_in_the_repository_has_a_dependabot_entry(manifest: str, ecosystem: str) -> None:
    """When the dashboard adds web/package.json, this fails until npm is added."""
    if not (ROOT / manifest).exists():
        pytest.skip(f"{manifest} does not exist yet")

    assert ecosystem in {update["package-ecosystem"] for update in UPDATES}


def test_updates_arrive_weekly_on_a_fixed_morning_in_india() -> None:
    for update in UPDATES:
        schedule = update["schedule"]
        assert schedule["interval"] == "weekly"
        assert schedule["day"] == "monday"
        assert schedule["timezone"] == "Asia/Kolkata"


def test_dependabot_titles_start_with_a_conventional_commit_type() -> None:
    """The squash-merge commit is made from the title, and commit messages are linted."""
    for update in UPDATES:
        prefixes = {update["commit-message"]["prefix"]}
        prefixes.add(update["commit-message"].get("prefix-development", "build"))
        assert prefixes <= COMMIT_TYPES, update["package-ecosystem"]


def test_dagster_packages_update_together_because_dagster_dbt_pins_dagster() -> None:
    (python,) = [u for u in UPDATES if u["package-ecosystem"] == "uv"]

    assert python["groups"]["dagster"]["patterns"] == ["dagster*"]
    # a major version is never swept into the routine group
    assert python["groups"]["python-minor-and-patch"]["update-types"] == ["minor", "patch"]


def test_the_python_base_image_stays_on_the_python_the_project_supports() -> None:
    (docker,) = [u for u in UPDATES if u["package-ecosystem"] == "docker"]
    ignored = {rule["dependency-name"]: rule.get("update-types") for rule in docker["ignore"]}

    assert set(ignored["python"]) == {"version-update:semver-major", "version-update:semver-minor"}
    # uv is bumped by hand with its three coupled pins; no update types means all are ignored
    assert "ghcr.io/astral-sh/uv" in ignored
    assert ignored["ghcr.io/astral-sh/uv"] is None
    assert 'requires-python = ">=3.12,<3.13"' in (ROOT / "pyproject.toml").read_text()


# --- the workflows and the settings page ---------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit(workflow: Path) -> None:
    """A tag can be moved to different code; a commit cannot (NFR-SEC-01)."""
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", workflow.read_text(), flags=re.MULTILINE)

    assert uses, "no actions found, so the pattern is wrong"
    for reference in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference


def ci_job_names() -> list[str]:
    workflow = yaml.safe_load((GITHUB / "workflows" / "ci.yml").read_text())
    return [job["name"] for job in workflow["jobs"].values()]


def test_the_settings_page_names_the_ci_job_that_branch_protection_requires() -> None:
    """If the job is renamed, the required check stops matching and merges would block."""
    (job,) = ci_job_names()

    assert f"`{job}`" in SETTINGS_PAGE
    assert f'"{job}"' in SETTINGS_PAGE


def test_the_branch_protection_payload_in_the_settings_page_is_valid_and_strict_enough() -> None:
    match = re.search(r"--input - <<'EOF'\n(\{.*?\n\})\nEOF", SETTINGS_PAGE, flags=re.DOTALL)
    assert match, "no payload found"

    payload = json.loads(match.group(1))

    # the API rejects a payload without these four keys
    assert {"required_status_checks", "enforce_admins", "required_pull_request_reviews"} <= set(
        payload
    )
    assert "restrictions" in payload
    assert payload["required_status_checks"]["contexts"] == ci_job_names()
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


def test_the_settings_page_commands_never_hard_code_the_repository() -> None:
    assert "REPO=$(gh repo view" in SETTINGS_PAGE
    assert "SlingggShott" not in SETTINGS_PAGE
