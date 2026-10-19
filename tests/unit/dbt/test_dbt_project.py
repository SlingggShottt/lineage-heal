"""Guards on the dbt project's configuration (FR-WH-06 and the dbt rules in CLAUDE.md)."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from dbt.cli.main import dbtRunner

REPO_ROOT = Path(__file__).parents[3]
DBT_DIR = REPO_ROOT / "dbt"

PROFILE: dict[str, Any] = yaml.safe_load((DBT_DIR / "profiles.yml").read_text())["lineageheal"]
OUTPUTS: dict[str, dict[str, Any]] = PROFILE["outputs"]
LOCAL_TARGETS = ("dev", "ci", "bench")


def test_the_four_documented_targets_exist() -> None:
    assert set(OUTPUTS) == {"dev", "ci", "bench", "prod"}


def test_the_default_target_is_never_prod() -> None:
    assert PROFILE["target"] == "dev"


@pytest.mark.parametrize("target", LOCAL_TARGETS)
def test_development_ci_and_benchmark_targets_stay_on_local_duckdb(target: str) -> None:
    """The benchmark must never run against production (D-11)."""
    output = OUTPUTS[target]

    assert output["type"] == "duckdb"
    assert not str(output["path"]).startswith("md:")


def test_only_prod_uses_motherduck() -> None:
    assert str(OUTPUTS["prod"]["path"]).startswith("md:")


def test_profiles_hold_no_credentials() -> None:
    """The MotherDuck token comes from the environment, never from a checked-in file."""
    for output in OUTPUTS.values():
        for key, value in output.items():
            assert not any(word in f"{key}={value}".lower() for word in ("token", "password"))


@pytest.mark.parametrize("layer", ["bronze", "silver", "gold"])
def test_each_layer_builds_into_its_own_schema(layer: str) -> None:
    project = yaml.safe_load((DBT_DIR / "dbt_project.yml").read_text())

    assert project["models"]["lineageheal"][layer]["+schema"] == layer


def test_the_project_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)  # profiles use paths relative to the repository root

    result = dbtRunner().invoke(
        [
            "parse",
            "--project-dir", str(DBT_DIR),
            "--profiles-dir", str(DBT_DIR),
            "--target", "ci",
            "--target-path", str(tmp_path / "target"),
            "--log-path", str(tmp_path / "logs"),
            "--no-version-check",
            "--quiet",
        ]
    )  # fmt: skip

    assert result.success
