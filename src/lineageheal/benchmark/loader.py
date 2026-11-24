"""Reading fault specifications from YAML files (FR-BEN-01).

Each file holds one specification. A folder is loaded whole and refused if two files share an id,
since results are stored per id and a duplicate would overwrite another fault's result.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from lineageheal.benchmark.spec import FaultCase
from lineageheal.core.errors import BenchmarkError


def load_case(path: Path) -> FaultCase:
    """Read and validate one specification file.

    Raises:
        BenchmarkError: The file cannot be read, is not a YAML mapping, or breaks a rule. The
            message names the file and, for a rule, each field and why.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkError(f"{path.name}: cannot read the specification: {exc}") from exc
    if not isinstance(raw, dict):
        raise BenchmarkError(f"{path.name}: a specification is a YAML mapping")
    try:
        return FaultCase.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}"
            for error in exc.errors()
        )
        raise BenchmarkError(f"{path.name}: {problems}") from exc


def load_cases(folder: Path) -> list[FaultCase]:
    """Read every `*.yaml` file in a folder, ordered by id.

    Raises:
        BenchmarkError: The folder has no specifications, any file is invalid, or two share an id.
    """
    paths = sorted(folder.glob("*.yaml"))
    if not paths:
        raise BenchmarkError(f"No fault specifications (*.yaml) found in {folder}")
    cases: dict[str, tuple[FaultCase, str]] = {}
    for path in paths:
        case = load_case(path)
        if case.id in cases:
            raise BenchmarkError(
                f"{path.name}: id {case.id!r} is already used by {cases[case.id][1]}"
            )
        cases[case.id] = (case, path.name)
    return sorted((case for case, _ in cases.values()), key=lambda c: c.id)
