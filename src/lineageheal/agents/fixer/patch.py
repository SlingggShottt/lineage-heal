"""Checks a fix's files and turns them into a unified diff (FR-FIX-01).

Every path a fix names is checked here before anything else happens to it, because the Fixer
may only change the dbt project's models, macros and schema files and must never reach loaders,
raw data, CI or application code. A path is refused unless it is an existing, plain text file
under an allowed folder, with no way to climb out of it. The size limits keep a patch reviewable.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lineageheal.core.errors import LineageHealError

MODELS_DIR = "dbt/models"
MACROS_DIR = "dbt/macros"
SQL_SUFFIXES = (".sql",)
YAML_SUFFIXES = (".yml", ".yaml")
NO_NEWLINE_MARKER = "\\ No newline at end of file\n"


class PatchError(LineageHealError):
    """A proposed change is not allowed; the message says why, for a model to read."""


@dataclass(frozen=True, slots=True)
class PatchLimits:
    """How large a patch may be."""

    max_files: int = 4
    max_changed_lines: int = 120
    max_file_characters: int = 20_000


DEFAULT_LIMITS = PatchLimits()


@dataclass(frozen=True, slots=True)
class Patch:
    """A checked change: a unified diff against the repository root and what it touches."""

    diff: str
    files: tuple[str, ...]
    changed_lines: int


def check_path(path: str) -> str:
    """Return the normalised path if a fix may change it.

    Allowed: `.sql` files under `dbt/models/` or `dbt/macros/`, and `.yml` or `.yaml` schema
    files under `dbt/models/`.

    Raises:
        PatchError: The path is absolute, climbs out with `..`, points into `.git`, or is
            outside the allowed folders or file types.
    """
    if not path or "\\" in path or "\x00" in path:
        raise PatchError(f"{path!r} is not a usable path")
    posix = PurePosixPath(path)
    normal = posix.as_posix()
    # A path that changes when normalised (`./a`, `a//b`) is refused rather than quietly rewritten.
    if posix.is_absolute() or ".." in posix.parts or ".git" in posix.parts or normal != path:
        raise PatchError(f"{path!r} is not a plain path inside the repository")
    in_models = normal.startswith(f"{MODELS_DIR}/")
    in_macros = normal.startswith(f"{MACROS_DIR}/")
    if in_models and normal.endswith(SQL_SUFFIXES + YAML_SUFFIXES):
        return normal
    if in_macros and normal.endswith(SQL_SUFFIXES):
        return normal
    raise PatchError(
        f"{path!r} is outside what a fix may change: .sql under {MODELS_DIR}/ or {MACROS_DIR}/, "
        f"or .yml under {MODELS_DIR}/"
    )


def build_patch(
    root: Path, edits: list[tuple[str, str]], limits: PatchLimits = DEFAULT_LIMITS
) -> Patch:
    """Check each edit and compute one diff for all of them.

    Args:
        root: The repository root the paths are relative to.
        edits: Pairs of path and the file's complete new content.
        limits: The size limits.

    Raises:
        PatchError: A path is not allowed, a file does not exist or is a link, an edit changes
            nothing, a file is named twice, or the patch exceeds a limit.
    """
    if not edits:
        raise PatchError("the fix changes no file")
    if len(edits) > limits.max_files:
        raise PatchError(f"the fix changes {len(edits)} files; the limit is {limits.max_files}")
    paths = [check_path(path) for path, _ in edits]
    if len(set(paths)) != len(paths):
        raise PatchError("a file is named more than once")

    parts: list[str] = []
    changed = 0
    for path, (_, new_content) in zip(paths, edits, strict=True):
        original = _read_existing(root, path)
        new_text = new_content if new_content.endswith("\n") else new_content + "\n"
        if "\x00" in new_text:
            raise PatchError(f"{path} would contain a null byte")
        if len(new_text) > limits.max_file_characters:
            raise PatchError(f"{path} is longer than {limits.max_file_characters} characters")
        if new_text == original:
            raise PatchError(f"{path} is unchanged")
        diff, lines = _diff(path, original, new_text)
        parts.append(diff)
        changed += lines
    if changed > limits.max_changed_lines:
        raise PatchError(
            f"the fix changes {changed} lines; the limit is {limits.max_changed_lines}"
        )
    return Patch("".join(parts), tuple(paths), changed)


def _read_existing(root: Path, path: str) -> str:
    target = root / path
    if target.is_symlink() or not target.is_file():
        raise PatchError(f"{path} is not an existing regular file")
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root.resolve())
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PatchError(f"{path} cannot be read as text inside the repository") from exc


def _diff(path: str, original: str, new_text: str) -> tuple[str, int]:
    old_lines = original.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    out: list[str] = []
    changed = 0
    for line in difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", n=3):
        out.append(line)
        if not line.endswith("\n"):
            # A last line with no newline needs the marker, or `git apply` reads it as longer.
            out.append("\n" + NO_NEWLINE_MARKER)
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            changed += 1
    return "".join(out), changed
