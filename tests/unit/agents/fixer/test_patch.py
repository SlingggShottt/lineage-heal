"""FR-FIX-01: a patch may only touch allow-listed dbt files and must stay small."""

import subprocess
from pathlib import Path

import pytest

from lineageheal.agents.fixer import PatchError, PatchLimits, build_patch, check_path

ORIGINAL = "select\n    nav as nav_inr\nfrom {{ source('raw', 'navall') }}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for path, text in {
        "dbt/models/bronze/brz_a.sql": ORIGINAL,
        "dbt/models/bronze/_bronze.yml": "version: 2\nmodels: []\n",
        "dbt/macros/helper.sql": "{% macro helper() %}1{% endmacro %}\n",
        "src/lineageheal/cli.py": "print('hi')\n",
        "dbt/dbt_project.yml": "name: x\n",
        "dbt/seeds/calendar.csv": "a,b\n",
    }.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    "path",
    [
        "dbt/models/silver/slv_a.sql",
        "dbt/models/silver/_silver.yml",
        "dbt/models/silver/_silver.yaml",
        "dbt/macros/helper.sql",
    ],
)
def test_the_dbt_models_macros_and_schema_files_are_allowed(path: str) -> None:
    assert check_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "src/lineageheal/cli.py",  # application code
        "dbt/dbt_project.yml",  # project settings, not a model
        "dbt/seeds/calendar.csv",  # data
        "dbt/models/silver/data.csv",
        "dbt/macros/schema.yml",  # YAML is for models only
        ".github/workflows/ci.yml",
        "docs/context.md",
        "dbt/models/../../src/x.sql",
        "dbt/models/silver/../../dbt_project.yml",
        "/etc/passwd",
        "/home/me/dbt/models/a.sql",
        "dbt/models/.git/config.sql",
        "dbt\\models\\a.sql",
        "dbt/models/a\x00.sql",
        "./dbt/models/a.sql",
        "dbtx/models/a.sql",
        "",
    ],
)
def test_everything_else_is_refused(path: str) -> None:
    with pytest.raises(PatchError):
        check_path(path)


def test_a_patch_is_a_unified_diff_that_git_can_apply(repo: Path) -> None:
    new = "select\n    \"Net Asset Value\" as nav_inr\nfrom {{ source('raw', 'navall') }}\n"

    patch = build_patch(repo, [("dbt/models/bronze/brz_a.sql", new)])

    assert patch.files == ("dbt/models/bronze/brz_a.sql",)
    assert patch.changed_lines == 2
    assert patch.diff.startswith(
        "--- a/dbt/models/bronze/brz_a.sql\n+++ b/dbt/models/bronze/brz_a.sql\n"
    )
    (repo / "change.diff").write_text(patch.diff)
    apply = ["git", "apply", "--check", "change.diff"]
    subprocess.run(apply, cwd=repo, check=True)  # noqa: S603
    subprocess.run(["git", "apply", "change.diff"], cwd=repo, check=True)  # noqa: S607
    assert (repo / "dbt/models/bronze/brz_a.sql").read_text() == new


def test_two_files_make_one_diff_and_a_missing_final_newline_is_added(repo: Path) -> None:
    patch = build_patch(
        repo,
        [
            ("dbt/models/bronze/brz_a.sql", "select 1"),
            ("dbt/macros/helper.sql", "{% macro helper() %}2{% endmacro %}"),
        ],
    )

    assert patch.diff.count("\n--- a/") + patch.diff.startswith("--- a/") == 2
    assert not patch.diff.endswith("No newline at end of file\n")


def test_a_file_that_had_no_final_newline_gets_the_marker_git_needs(repo: Path) -> None:
    target = repo / "dbt/models/bronze/brz_a.sql"
    target.write_text("select 1")

    patch = build_patch(repo, [("dbt/models/bronze/brz_a.sql", "select 2\n")])

    assert "\\ No newline at end of file" in patch.diff
    (repo / "change.diff").write_text(patch.diff)
    subprocess.run(["git", "apply", "--check", "change.diff"], cwd=repo, check=True)  # noqa: S607


def test_an_edit_that_changes_nothing_is_refused(repo: Path) -> None:
    with pytest.raises(PatchError, match="unchanged"):
        build_patch(repo, [("dbt/models/bronze/brz_a.sql", ORIGINAL)])


def test_a_file_that_does_not_exist_is_refused_so_nothing_new_is_created(repo: Path) -> None:
    with pytest.raises(PatchError, match="not an existing regular file"):
        build_patch(repo, [("dbt/models/bronze/new.sql", "select 1\n")])


def test_a_symlink_is_refused_even_when_it_points_at_an_allowed_file(repo: Path) -> None:
    (repo / "dbt/models/bronze/link.sql").symlink_to(repo / "dbt/models/bronze/brz_a.sql")

    with pytest.raises(PatchError, match="not an existing regular file"):
        build_patch(repo, [("dbt/models/bronze/link.sql", "select 1\n")])


def test_a_disallowed_path_is_refused_before_any_file_is_read(repo: Path) -> None:
    with pytest.raises(PatchError, match="outside what a fix may change"):
        build_patch(
            repo,
            [("dbt/models/bronze/brz_a.sql", "select 1\n"), ("src/lineageheal/cli.py", "x\n")],
        )


def test_no_edits_the_same_file_twice_and_too_many_files_are_refused(repo: Path) -> None:
    with pytest.raises(PatchError, match="changes no file"):
        build_patch(repo, [])
    path = "dbt/models/bronze/brz_a.sql"
    with pytest.raises(PatchError, match="more than once"):
        build_patch(repo, [(path, "a\n"), (path, "b\n")])
    with pytest.raises(PatchError, match="limit is 1"):
        build_patch(
            repo, [(path, "a\n"), ("dbt/macros/helper.sql", "b\n")], PatchLimits(max_files=1)
        )


def test_a_patch_that_changes_too_many_lines_is_refused(repo: Path) -> None:
    big = "".join(f"select {i}\n" for i in range(50))

    with pytest.raises(PatchError, match="limit is 40"):
        build_patch(repo, [("dbt/models/bronze/brz_a.sql", big)], PatchLimits(max_changed_lines=40))


def test_a_file_longer_than_the_limit_is_refused(repo: Path) -> None:
    with pytest.raises(PatchError, match="longer than"):
        build_patch(
            repo,
            [("dbt/models/bronze/brz_a.sql", "x" * 100)],
            PatchLimits(max_file_characters=50),
        )
