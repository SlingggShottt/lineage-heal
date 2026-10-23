"""Reading a dbt run: which tests failed, which models errored, and what was skipped.

dbt writes two files for every run. `manifest.json` describes the project (each test's model,
column and kind) and `run_results.json` says what happened to each node. Joining them turns a
result such as "test.lineageheal.not_null_brz_amfi__navall_nav_inr failed with 1 row" into a
signal that names the model and column (FR-DET-01). Nothing here touches the warehouse or the
clock, so the same two files always give the same answer.
"""

from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind

# Enough to read a failure, short enough not to bloat a record with a query plan.
MAX_MESSAGE_LENGTH = 2000


def _parse_time(text: str) -> datetime:
    """Parse dbt's ISO timestamp, which ends in Z, as an aware UTC datetime."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _name(unique_id: str, node: Mapping[str, Any]) -> str:
    """The node's name; a dbt id is `<type>.<project>.<name>`, plus a hash for a test."""
    return str(node.get("name") or unique_id.split(".")[2])


def _model_names(node: Mapping[str, Any], nodes: Mapping[str, Any]) -> tuple[str, ...]:
    """The models a test reads, in a stable order."""
    parents = (
        parent
        for parent in node.get("depends_on", {}).get("nodes", [])
        if parent.startswith("model.")
    )
    return tuple(sorted(_name(parent, nodes.get(parent, {})) for parent in parents))


def _signal(
    result: Mapping[str, Any],
    node: Mapping[str, Any],
    nodes: Mapping[str, Any],
    *,
    run_id: str,
    at: datetime,
) -> Signal | None:
    """Describe a result that is a problem, or return None for one that is not."""
    unique_id = str(result["unique_id"])
    status = result["status"]
    is_test = unique_id.startswith("test.")
    if is_test and status == "fail":
        kind, severity = SignalKind.TEST_FAILED, Severity.ERROR
    elif is_test and status == "warn":
        kind, severity = SignalKind.TEST_WARNING, Severity.WARN
    elif is_test and status == "error":
        kind, severity = SignalKind.TEST_ERROR, Severity.ERROR
    elif not is_test and status == "error":
        kind, severity = SignalKind.MODEL_ERROR, Severity.ERROR
    else:
        return None

    name = _name(unique_id, node)
    failures = result.get("failures")
    if is_test:
        models = _model_names(node, nodes)
        attached = node.get("attached_node")
        model = (
            _name(attached, nodes.get(attached, {}))
            if attached
            else (models[0] if models else None)
        )
        check_type = (node.get("test_metadata") or {}).get("name") or "singular"
        column = node.get("column_name") or None
    else:
        models, model, check_type, column = (name,), name, "model", None
    return Signal(
        kind=kind,
        severity=severity,
        run_id=run_id,
        detected_at=at,
        name=name,
        unique_id=unique_id,
        check_type=str(check_type),
        models=models,
        model=model,
        column=column,
        failures=int(failures) if failures is not None else None,
        message=str(result.get("message") or "")[:MAX_MESSAGE_LENGTH],
    )


def summarize_run(
    run_results: Mapping[str, Any], manifest: Mapping[str, Any], *, run_id: str
) -> RunSummary:
    """Turn a dbt run's two artifact files into a summary and its failure signals.

    Args:
        run_results: The parsed `run_results.json`.
        manifest: The parsed `manifest.json` of the same run.
        run_id: The pipeline run these artifacts belong to.

    Returns:
        The counts by status, the models skipped because something upstream failed, and a
        signal for each failed or erroring test and each model that errored, errors first.
    """
    nodes = manifest.get("nodes", {})
    metadata = run_results["metadata"]
    at = _parse_time(metadata["generated_at"])
    counts: Counter[str] = Counter()
    skipped_models: set[str] = set()
    signals: list[Signal] = []
    for result in run_results["results"]:
        counts[str(result["status"])] += 1
        unique_id = str(result["unique_id"])
        node = nodes.get(unique_id, {})
        if result["status"] == "skipped" and unique_id.startswith("model."):
            skipped_models.add(_name(unique_id, node))
        signal = _signal(result, node, nodes, run_id=run_id, at=at)
        if signal is not None:
            signals.append(signal)
    signals.sort(key=lambda s: (s.severity is not Severity.ERROR, s.kind.value, s.name))
    return RunSummary(
        run_id=run_id,
        dbt_invocation_id=metadata.get("invocation_id"),
        generated_at=at,
        counts=dict(counts),
        skipped_models=tuple(sorted(skipped_models)),
        signals=tuple(signals),
    )


def run_error_summary(run_id: str, message: str, *, at: datetime) -> RunSummary:
    """Summarise a run that stopped before dbt wrote any results.

    A profile that cannot connect, a model that does not parse or a crashed process leaves no
    `run_results.json`, but the run still failed and the Detector must hear about it.
    """
    signal = Signal(
        kind=SignalKind.RUN_ERROR,
        severity=Severity.ERROR,
        run_id=run_id,
        detected_at=at,
        name="dbt",
        check_type="run",
        message=message[:MAX_MESSAGE_LENGTH],
    )
    return RunSummary(run_id=run_id, generated_at=at, counts={}, signals=(signal,))
