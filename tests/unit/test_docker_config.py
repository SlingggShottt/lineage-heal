"""NFR-PORT-01 and NFR-SEC-01: guards on the container configuration.

These read the files rather than start Docker, so they run anywhere. The mistakes they
catch (a published database port, a baked-in .env, a floating image tag) are easy to make
and hard to notice.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[2]
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text()
COMPOSE: dict[str, Any] = yaml.safe_load(COMPOSE_TEXT)
POSTGRES: dict[str, Any] = COMPOSE["services"]["postgres"]
APP: dict[str, Any] = COMPOSE["services"]["app"]
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text().splitlines()
MAKEFILE = (ROOT / "Makefile").read_text()


def instruction_lines(dockerfile: str) -> list[str]:
    """Dockerfile lines that are not comments or blank."""
    return [line.strip() for line in dockerfile.splitlines() if line.strip() and line[0] != "#"]


def test_postgres_uses_a_pinned_pgvector_image() -> None:
    assert re.fullmatch(r"pgvector/pgvector:\d+\.\d+\.\d+-pg17", POSTGRES["image"])


def test_postgres_is_published_on_the_loopback_interface_only() -> None:
    """Published on 0.0.0.0 it would be reachable from the rest of the network."""
    assert POSTGRES["ports"]
    assert all(str(port).startswith("127.0.0.1:") for port in POSTGRES["ports"])


def test_postgres_has_a_healthcheck_and_dependants_wait_for_it() -> None:
    assert "healthcheck" in POSTGRES
    assert APP["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_postgres_data_lives_in_a_named_volume() -> None:
    assert "pgdata" in COMPOSE["volumes"]
    assert any(str(v).startswith("pgdata:") for v in POSTGRES["volumes"])


def test_the_init_script_enables_pgvector() -> None:
    init_sql = (ROOT / "docker" / "postgres" / "init.sql").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in init_sql


def test_the_database_password_is_only_a_development_default() -> None:
    """It can be overridden from the environment, and no real credential appears."""
    assert "${POSTGRES_PASSWORD:-" in COMPOSE_TEXT
    assert "R2_" not in COMPOSE_TEXT
    assert "GITHUB_" not in COMPOSE_TEXT


def test_the_app_service_starts_only_on_demand() -> None:
    assert APP["profiles"] == ["app"]


def test_the_base_image_is_pinned_to_an_exact_version() -> None:
    (from_line,) = [line for line in instruction_lines(DOCKERFILE) if line.startswith("FROM")]

    assert re.search(r":\d+\.\d+\.\d+-slim-", from_line)
    assert "latest" not in from_line


def test_the_image_installs_exactly_what_the_lockfile_says() -> None:
    sync_lines = [line for line in instruction_lines(DOCKERFILE) if "uv sync" in line]

    assert sync_lines
    assert all("--locked" in line for line in sync_lines)


def test_the_image_runs_as_an_unprivileged_user() -> None:
    lines = instruction_lines(DOCKERFILE)
    last_user = [line for line in lines if line.startswith("USER")][-1]

    assert last_user == "USER app"


def test_the_image_never_copies_local_secrets_or_data() -> None:
    copied = " ".join(line for line in instruction_lines(DOCKERFILE) if line.startswith("COPY"))

    assert ".env" not in copied
    assert ".data" not in copied


def test_the_image_avoids_the_layers_that_doubled_its_size() -> None:
    """A recursive chown duplicates the environment; an uncached uv bakes its downloads in."""
    assert "chown -R" not in DOCKERFILE
    assert "--mount=type=cache" in DOCKERFILE


@pytest.mark.parametrize("entry", [".env", ".data", ".git", ".venv"])
def test_the_build_context_excludes_secrets_data_and_history(entry: str) -> None:
    assert entry in DOCKERIGNORE


def test_the_example_env_file_stays_in_the_build_context() -> None:
    assert "!.env.example" in DOCKERIGNORE


def test_make_up_and_down_drive_compose() -> None:
    assert re.search(r"^up:.*\n\tdocker compose up -d --wait postgres", MAKEFILE, re.M)
    assert re.search(r"^down:.*\n\tdocker compose down", MAKEFILE, re.M)
