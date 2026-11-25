"""The sandbox: builds a patch's affected subgraph on a copy of the data and compares results.

A patch is only worth a pull request if it fixes what failed without breaking what passed
(FR-FIX-02). The sandbox gets that evidence by running the same `dbt build` twice, once on the
code as it is and once with the patch applied, each in its own copy of the warehouse so neither
run can see the other's tables and the real warehouse is never touched.

Safety rules, because the patch text came from a model:
- the patch is applied in a throwaway git worktree, never in the working tree;
- dbt runs against the `dev` target (local DuckDB) with a minimal environment, so no cloud
  token or other secret is visible to it and `prod` cannot be reached (D-11);
- every command is a fixed argument list with no shell, under a timeout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import structlog

from lineageheal.agents.fixer.patch import Patch
from lineageheal.core.errors import LineageHealError

log = structlog.get_logger()

# Statuses dbt writes to run_results.json. "warn" is a soft result: the build goes on, so it
# neither has to be fixed nor counts as breaking something.
GOOD = frozenset({"pass", "success"})
BAD = frozenset({"fail", "error", "skipped", "missing"})
LOG_TAIL_CHARACTERS = 3000
DBT_TARGET = "dev"
# dbt gets a fixed PATH and no inherited environment, so no credential in this process (a cloud
# token, an API key) is visible to a model-written patch that runs inside a dbt macro.
_SAFE_PATH = "/usr/bin:/bin"


class SandboxError(LineageHealError):
    """The sandbox could not be set up or run, which says nothing about the patch itself."""


@dataclass(frozen=True)
class BuildResult:
    """What one `dbt build` did: the status of each node it reported, and how it ended."""

    statuses: Mapping[str, str]
    exit_code: int
    log_tail: str
    seconds: float

    @property
    def produced_results(self) -> bool:
        """Whether dbt got far enough to build anything and report on it."""
        return bool(self.statuses)


Builder = Callable[[Path, Path, str, float], BuildResult]


@dataclass(frozen=True)
class SandboxResult:
    """The verdict on one patch, with the evidence for it."""

    passed: bool
    reason: str
    selector: str
    fixed: tuple[str, ...] = ()  # failed before the patch, fine after it
    still_failing: tuple[str, ...] = ()  # failed before the patch and still do
    regressions: tuple[str, ...] = ()  # fine before the patch, failing after it
    before: Mapping[str, str] = field(default_factory=dict)
    after: Mapping[str, str] = field(default_factory=dict)
    log_tail: str = ""
    seconds: float = 0.0

    def to_json(self) -> dict[str, Any]:
        """The record stored with the fix; plain data so it can sit in a JSON column."""
        return {
            "passed": self.passed,
            "reason": self.reason,
            "selector": self.selector,
            "fixed": list(self.fixed),
            "still_failing": list(self.still_failing),
            "regressions": list(self.regressions),
            "before": dict(self.before),
            "after": dict(self.after),
            "log_tail": self.log_tail,
            "seconds": round(self.seconds, 2),
        }

    def feedback(self) -> str:
        """What a model needs to revise the patch: which nodes are still wrong, and why."""
        lines = [self.reason]
        if self.still_failing:
            lines.append("Still failing: " + ", ".join(_short(n) for n in self.still_failing[:10]))
        if self.regressions:
            lines.append("Newly broken: " + ", ".join(_short(n) for n in self.regressions[:10]))
        if self.log_tail:
            lines.append("dbt output (end):\n" + self.log_tail[-1200:])
        return "\n".join(lines)


def compare(before: Mapping[str, str], after: Mapping[str, str], selector: str) -> SandboxResult:
    """Judge a patch from the node statuses of the build without it and the build with it.

    A node missing after the patch counts as failing, so a patch cannot "fix" a failing test by
    deleting it, and a passing test that disappears is a regression.
    """
    effective = {node: after.get(node, "missing") for node in before}
    failed_before = [n for n, s in before.items() if s in BAD]
    fixed = tuple(sorted(n for n in failed_before if effective[n] not in BAD))
    still = tuple(sorted(n for n in failed_before if effective[n] in BAD))
    regressions = tuple(
        sorted(n for n, s in before.items() if s not in BAD and effective[n] in BAD)
    ) + tuple(sorted(n for n, s in after.items() if n not in before and s in BAD))
    if not failed_before:
        reason = "nothing failed before the patch, so there is nothing to show it fixed"
        passed = False
    elif still:
        reason = f"{len(still)} node(s) that failed before the patch still fail"
        passed = False
    elif regressions:
        reason = f"the patch breaks {len(regressions)} node(s) that passed before"
        passed = False
    else:
        reason = f"fixed {len(fixed)} failing node(s) and broke nothing"
        passed = True
    return SandboxResult(
        passed=passed,
        reason=reason,
        selector=selector,
        fixed=fixed,
        still_failing=still,
        regressions=regressions,
        before=dict(before),
        after=dict(after),
    )


class Sandbox:
    """Verifies a patch in a throwaway worktree against copies of the warehouse."""

    def __init__(
        self,
        repo_root: Path,
        warehouse: Path,
        raw_zone: Path,
        *,
        timeout_seconds: float = 600.0,
        builder: Builder | None = None,
        target: str = DBT_TARGET,
    ) -> None:
        """Create a sandbox.

        Args:
            repo_root: The git repository holding the dbt project.
            warehouse: The DuckDB file with the incident's data. It is copied, never opened here.
            raw_zone: The raw zone folder dbt reads source files from.
            timeout_seconds: Longest one `dbt build` may run.
            builder: Runs one build; replaced in tests. Defaults to a real `dbt build`.
            target: The dbt target the warehouse file belongs to: `dev` for the live pipeline,
                `bench` for a benchmark scenario. It names the copied file and the target dbt
                builds on, so the sandbox can never reach `prod`.
        """
        self._repo = repo_root
        self._warehouse = warehouse
        self._raw_zone = raw_zone
        self._timeout = timeout_seconds
        self._builder = builder or self._dbt_build
        self._target = target

    def verify(self, patch: Patch, model: str) -> SandboxResult:
        """Build `model` and everything downstream of it without and with the patch.

        Args:
            patch: A patch already checked by `build_patch`.
            model: The dbt model at the root cause.

        Raises:
            SandboxError: The worktree, the data copy or git could not be used.
        """
        if not self._warehouse.is_file():
            raise SandboxError("The warehouse file does not exist; run dbt build first")
        selector = f"{model}+"
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="lineageheal-sandbox-") as scratch:
            tree = Path(scratch) / "tree"
            self._git(self._repo, "worktree", "add", "--detach", str(tree), "HEAD")
            try:
                before = self._build(tree, Path(scratch) / "before", selector)
                self._apply(tree, patch)
                after = self._build(tree, Path(scratch) / "after", selector)
            finally:
                self._git(self._repo, "worktree", "remove", "--force", str(tree), check=False)
                self._git(self._repo, "worktree", "prune", check=False)
        return replace(_judge(before, after, selector), seconds=time.monotonic() - started)

    def _build(self, tree: Path, data_dir: Path, selector: str) -> BuildResult:
        data_dir.mkdir(parents=True)
        try:
            shutil.copy2(self._warehouse, data_dir / f"{self._target}.duckdb")
        except OSError as exc:
            raise SandboxError(f"Could not copy the warehouse: {exc}") from exc
        return self._builder(tree / "dbt", data_dir, selector, self._timeout)

    def _apply(self, tree: Path, patch: Patch) -> None:
        diff = tree.parent / "patch.diff"
        diff.write_text(patch.diff, encoding="utf-8")
        self._git(tree, "apply", "--whitespace=nowarn", str(diff))

    @staticmethod
    def _git(directory: Path, *args: str, check: bool = True) -> None:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(directory), *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if check and completed.returncode != 0:
            raise SandboxError(f"git {args[0]} failed: {completed.stderr.strip()[-500:]}")

    def _dbt_build(
        self, project: Path, data_dir: Path, selector: str, timeout: float
    ) -> BuildResult:
        environment = {
            "PATH": _SAFE_PATH,
            "HOME": str(data_dir),  # dbt writes a small user file under HOME
            "DBT_DATA_DIR": str(data_dir),
            "RAW_ZONE_DIR": str(self._raw_zone.resolve()),
        }
        command = [
            sys.executable,
            "-c",
            "from dbt.cli.main import cli; cli()",
            "build",
            "--project-dir", str(project),
            "--profiles-dir", str(project),
            "--target", self._target,
            "--select", selector,
        ]  # fmt: skip
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
                cwd=project,
                check=False,
            )
            output, code = completed.stdout + completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            output, code = f"dbt build timed out after {timeout:.0f} seconds", -1
        return BuildResult(
            statuses=read_statuses(project / "target" / "run_results.json"),
            exit_code=code,
            log_tail=output[-LOG_TAIL_CHARACTERS:],
            seconds=time.monotonic() - started,
        )


def read_statuses(path: Path) -> dict[str, str]:
    """The status of every node in a `run_results.json`, or nothing if dbt wrote none."""
    try:
        results = json.loads(path.read_text(encoding="utf-8"))["results"]
    except (OSError, ValueError, KeyError):
        return {}
    return {str(r["unique_id"]): str(r["status"]) for r in results}


def _judge(before: BuildResult, after: BuildResult, selector: str) -> SandboxResult:
    if not before.produced_results:
        return SandboxResult(
            False,
            "the build produced no results even without the patch",
            selector,
            log_tail=before.log_tail,
        )
    if not after.produced_results:
        return SandboxResult(
            False,
            "the patched project did not build (dbt wrote no results)",
            selector,
            before=dict(before.statuses),
            log_tail=after.log_tail,
        )
    result = compare(before.statuses, after.statuses, selector)
    return replace(result, log_tail="" if result.passed else after.log_tail)


def _short(unique_id: str) -> str:
    return unique_id.split(".", 2)[-1] if unique_id.count(".") >= 2 else unique_id
