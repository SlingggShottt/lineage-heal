"""FR-FIX-02: a patch is judged by building the affected subgraph with and without it."""

import subprocess
from pathlib import Path

import duckdb
import pytest

from lineageheal.agents.fixer import Sandbox, SandboxError, build_patch, compare
from lineageheal.agents.fixer.sandbox import BuildResult

MODEL = "model.p.m"
TEST = "test.p.not_null_m_v"
OTHER = "test.p.unique_m_v"


def test_a_failing_test_that_now_passes_is_a_pass() -> None:
    result = compare({MODEL: "success", TEST: "fail"}, {MODEL: "success", TEST: "pass"}, "m+")

    assert result.passed
    assert result.fixed == (TEST,)


def test_a_failure_that_remains_fails_the_patch() -> None:
    result = compare({MODEL: "success", TEST: "fail"}, {MODEL: "success", TEST: "fail"}, "m+")

    assert not result.passed
    assert result.still_failing == (TEST,)
    assert "Still failing" in result.feedback()


def test_breaking_a_test_that_passed_fails_the_patch() -> None:
    before = {MODEL: "success", TEST: "fail", OTHER: "pass"}
    after = {MODEL: "success", TEST: "pass", OTHER: "fail"}

    result = compare(before, after, "m+")

    assert not result.passed
    assert result.regressions == (OTHER,)
    assert "Newly broken" in result.feedback()


def test_deleting_a_failing_test_does_not_fix_it() -> None:
    result = compare({MODEL: "success", TEST: "fail"}, {MODEL: "success"}, "m+")

    assert not result.passed
    assert result.still_failing == (TEST,)


def test_deleting_a_passing_test_is_a_regression() -> None:
    result = compare({TEST: "fail", OTHER: "pass"}, {TEST: "pass"}, "m+")

    assert not result.passed
    assert result.regressions == (OTHER,)


def test_a_node_skipped_after_the_patch_counts_as_broken() -> None:
    result = compare({MODEL: "success", TEST: "fail"}, {MODEL: "error", TEST: "skipped"}, "m+")

    assert not result.passed


def test_a_warning_before_and_after_is_neither_fixed_nor_broken() -> None:
    result = compare({TEST: "fail", OTHER: "warn"}, {TEST: "pass", OTHER: "warn"}, "m+")

    assert result.passed


def test_nothing_failing_before_means_nothing_was_shown_to_be_fixed() -> None:
    result = compare({MODEL: "success", TEST: "pass"}, {MODEL: "success", TEST: "pass"}, "m+")

    assert not result.passed
    assert "nothing failed" in result.reason


def test_a_result_is_plain_json_data() -> None:
    record = compare({TEST: "fail"}, {TEST: "pass"}, "m+").to_json()

    assert record["passed"] is True
    assert record["selector"] == "m+"
    assert record["fixed"] == [TEST]


# --- a real worktree and a real dbt build, on a tiny project -------------------------------

PROJECT = {
    "dbt/dbt_project.yml": "name: p\nversion: '1.0.0'\nconfig-version: 2\nprofile: p\n"
    "flags:\n  send_anonymous_usage_stats: false\n",
    "dbt/profiles.yml": "p:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
    "      path: \"{{ env_var('DBT_DATA_DIR') }}/dev.duckdb\"\n",
    "dbt/models/m.sql": "select cast(null as integer) as v\n",
    "dbt/models/m.yml": "version: 2\nmodels:\n  - name: m\n    columns:\n      - name: v\n"
    "        tests: [not_null]\n",
    "dbt/models/n.sql": "select v from {{ ref('m') }}\n",
}


def git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for path, text in PROJECT.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    git(root.parent, "init", str(root))
    git(root, "add", ".")
    git(root, "commit", "-m", "init")
    return root


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.duckdb"
    duckdb.connect(str(path)).close()
    return path


def sandbox(repo: Path, warehouse: Path, tmp_path: Path) -> Sandbox:
    return Sandbox(repo, warehouse, tmp_path / "raw", timeout_seconds=120)


def test_a_patch_that_fixes_the_failing_test_passes_in_a_real_build(
    repo: Path, warehouse: Path, tmp_path: Path
) -> None:
    before_size = warehouse.stat().st_size
    patch = build_patch(repo, [("dbt/models/m.sql", "select 1 as v\n")])

    result = sandbox(repo, warehouse, tmp_path).verify(patch, "m")

    assert result.passed, result.feedback()
    assert any("not_null" in node for node in result.fixed)
    assert warehouse.stat().st_size == before_size
    assert (repo / "dbt/models/m.sql").read_text() == PROJECT["dbt/models/m.sql"]
    worktrees = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "worktree", "list"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert len(worktrees.strip().splitlines()) == 1


def test_a_patch_that_does_not_fix_the_test_fails_with_feedback(
    repo: Path, warehouse: Path, tmp_path: Path
) -> None:
    patch = build_patch(repo, [("dbt/models/m.sql", "select cast(null as integer) as v -- x\n")])

    result = sandbox(repo, warehouse, tmp_path).verify(patch, "m")

    assert not result.passed
    assert any("not_null" in node for node in result.still_failing)


def test_a_patch_that_breaks_the_build_fails_with_the_dbt_output(
    repo: Path, warehouse: Path, tmp_path: Path
) -> None:
    patch = build_patch(repo, [("dbt/models/m.sql", "select from from\n")])

    result = sandbox(repo, warehouse, tmp_path).verify(patch, "m")

    assert not result.passed
    assert result.log_tail or result.still_failing


def test_a_patch_that_does_not_apply_is_a_sandbox_error(
    repo: Path, warehouse: Path, tmp_path: Path
) -> None:
    patch = build_patch(repo, [("dbt/models/m.sql", "select 1 as v\n")])
    (repo / "dbt/models/m.sql").write_text("select 2 as v\n")
    git(repo, "commit", "-am", "move on")

    with pytest.raises(SandboxError, match="apply"):
        sandbox(repo, warehouse, tmp_path).verify(patch, "m")


def test_a_missing_warehouse_is_a_sandbox_error(repo: Path, tmp_path: Path) -> None:
    patch = build_patch(repo, [("dbt/models/m.sql", "select 1 as v\n")])

    with pytest.raises(SandboxError, match="warehouse"):
        Sandbox(repo, tmp_path / "none.duckdb", tmp_path).verify(patch, "m")


def test_the_builder_runs_in_its_own_copy_of_the_data(
    repo: Path, warehouse: Path, tmp_path: Path
) -> None:
    seen: list[Path] = []

    def builder(project: Path, data_dir: Path, selector: str, timeout: float) -> BuildResult:
        seen.append(data_dir)
        assert (data_dir / "dev.duckdb").is_file()
        assert selector == "m+"
        return BuildResult({TEST: "fail" if len(seen) == 1 else "pass"}, 0, "", 0.1)

    patch = build_patch(repo, [("dbt/models/m.sql", "select 1 as v\n")])

    result = Sandbox(repo, warehouse, tmp_path, builder=builder).verify(patch, "m")

    assert result.passed
    assert len(set(seen)) == 2
    assert warehouse not in seen
