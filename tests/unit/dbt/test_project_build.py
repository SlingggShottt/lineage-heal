"""NFR-MAINT-02: the whole dbt project builds on fixture data through the shipped `ci` target."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dbt.cli.main import dbtRunner

if TYPE_CHECKING:
    import pytest

    from conftest import ParsedProject, Workspace

REPO_ROOT = Path(__file__).parents[3]
DBT_DIR = REPO_ROOT / "dbt"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def test_every_model_builds_on_fixture_data_and_every_check_passes(
    workspace: Workspace,
    parsed_project: ParsedProject,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command a CI job would run: `dbt build --target ci` over all four feeds' fixtures.

    The other scenarios build a few models each, so a broken reference or a test that only
    fails when two layers meet would escape them. This one uses the repository's own profile,
    not the test harness's, so a mistake in the `ci` target is caught here too.
    """
    workspace.store_amfi((FIXTURES / "amfi" / "navall_history_2026-10-15_sample.txt").read_bytes())
    workspace.store_nse((FIXTURES / "nse" / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes())
    workspace.store_security_master((FIXTURES / "nse" / "security_master_sample.csv").read_bytes())
    workspace.store_corporate_actions(
        (FIXTURES / "nse" / "corporate_actions_sample.json").read_bytes()
    )
    monkeypatch.chdir(REPO_ROOT)

    result = dbtRunner().invoke(
        [
            "build",
            "--project-dir", str(DBT_DIR),
            "--profiles-dir", str(DBT_DIR),
            "--target", "ci",
            "--target-path", str(tmp_path / "target"),
            "--log-path", str(tmp_path / "logs"),
            "--no-version-check",
            "--quiet",
        ]
    )  # fmt: skip

    assert result.result is not None, result.exception  # dbt stopped before running anything
    statuses = {r.node.name: str(r.status.value) for r in result.result.results}  # type: ignore[attr-defined]
    models = {
        node.name
        for node in parsed_project.manifest.nodes.values()
        if node.resource_type == "model"
    }
    assert result.success, {name: s for name, s in statuses.items() if s not in ("success", "pass")}
    # Nothing was skipped: a model missing here would mean a failed check upstream of it.
    assert {name for name in models if statuses.get(name) != "success"} == set()
    assert set(statuses.values()) <= {"success", "pass"}
    # All three layers took part, so the check above is not passing over an empty project.
    assert {name[:4] for name in models} >= {"brz_", "slv_", "gld_"}
