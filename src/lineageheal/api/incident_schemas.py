"""The shapes the incident endpoints return, and the conversions from what the store holds.

They are the API's contract: the dashboard's TypeScript types are generated from them, so a field
is only added here on purpose. Nothing that could hold a secret or a local path is exposed: the
sandbox's raw dbt output and the GitHub tokens never appear.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from lineageheal.api.lineage_view import LineageView
from lineageheal.core.signals import Signal
from lineageheal.store.fixes import StoredFix
from lineageheal.store.incidents import StoredIncident
from lineageheal.store.investigations import StoredInvestigation

# Mirrors of the store's allowed values; a test fails if they drift apart.
IncidentStatus = Literal[
    "detected",
    "investigating",
    "fix_proposed",
    "suggestion_filed",
    "fix_unverified",
    "unresolved",
    "approved",
    "rejected",
    "resolved",
]
FaultClass = Literal["schema_drift", "nulls", "duplicates", "unit_change", "late_data", "unknown"]
Severity = Literal["error", "warn"]
Feed = Literal["amfi", "nse", "fbil"]

MAX_SIMILAR = 5
MAX_SNIPPET = 600


class IncidentSummary(BaseModel):
    """One row of the incidents list."""

    id: int
    status: IncidentStatus
    summary: str
    severity: Severity
    fault_class: FaultClass | None
    confidence: float | None
    classified_by: str | None
    root_node: str | None
    affected_models: int
    affected_gold_tables: int
    opened_at: datetime
    updated_at: datetime


class Alternative(BaseModel):
    """A root cause the Investigator considered and ranked lower."""

    node: str
    fault_class: str
    explanation: str
    confidence: float


class RootCause(BaseModel):
    """The Investigator's conclusion, or its best guess when it ran out of budget."""

    node: str | None
    explanation: str | None
    confidence: float | None
    fix_location: Literal["dbt", "external"] | None
    fix_reason: str | None
    partial: bool
    # Numbers of the evidence items (`Evidence.number`) that support the conclusion.
    evidence_ids: list[int]
    alternatives: list[Alternative]


class Affected(BaseModel):
    """What the incident touches."""

    models: list[str]
    columns: list[str]
    gold_tables: list[str]


class SignalOut(BaseModel):
    """One failure the Detector grouped into the incident."""

    kind: str
    severity: str
    name: str
    check_type: str
    model: str | None
    column: str | None
    failures: int | None
    message: str
    detected_at: datetime


class EvidenceOut(BaseModel):
    """One tool result the Investigator collected."""

    number: int  # 1-based, in collection order; `RootCause.evidence_ids` refer to it
    tool: str
    arguments: dict[str, Any]
    output: dict[str, Any]
    hypothesis: str | None
    collected_at: datetime


class InvestigationOut(BaseModel):
    """The Investigator's run on this incident."""

    status: str
    started_at: datetime
    finished_at: datetime | None
    llm_calls: int
    input_tokens: int
    output_tokens: int
    models: dict[str, str]
    error: str | None
    # Not recorded until tracing is added (LH-043).
    trace_url: str | None
    evidence: list[EvidenceOut]


class KnowledgeHit(BaseModel):
    """A past incident or runbook section the Investigator found similar."""

    source: Literal["runbook", "incident"]
    document: str
    heading: str
    fault_class: str | None
    score: float
    text: str


class FixSummary(BaseModel):
    """A fix in brief; the full diff is at `/incidents/{id}/fix`."""

    id: int
    kind: Literal["dbt_patch", "suggestion"]
    status: str
    files: list[str]
    sandbox_passed: bool | None
    github_url: str | None
    github_number: int | None
    created_at: datetime


class IncidentDetail(IncidentSummary):
    """Everything the incident page shows except the lineage graph and the full diff."""

    root_cause: RootCause | None
    affected: Affected
    signals: list[SignalOut]
    investigation: InvestigationOut | None
    similar_incidents: list[KnowledgeHit]
    related_runbooks: list[KnowledgeHit]
    fixes: list[FixSummary]


class SandboxOut(BaseModel):
    """How the sandbox judged a patch: node statuses before and after, never the raw dbt output."""

    passed: bool
    reason: str
    selector: str
    fixed: list[str]
    still_failing: list[str]
    regressions: list[str]
    before: dict[str, str]
    after: dict[str, str]
    seconds: float


class FixDetail(FixSummary):
    """A fix with its diff, sandbox verdict and, for a root cause outside dbt, its suggestion."""

    incident_id: int
    patch: str | None
    sandbox: SandboxOut | None
    suggestion: dict[str, Any] | None
    branch: str | None
    updated_at: datetime


class LineageNodeOut(BaseModel):
    """A node of the incident's lineage slice."""

    id: str
    table: str
    column: str
    kind: Literal["raw", "seed", "model"]
    role: Literal["root", "affected", "path"]


class LineageEdgeOut(BaseModel):
    """An upstream node feeding a downstream one."""

    source: str
    target: str
    kind: Literal["direct", "transform", "aggregate", "filter"]
    inferred: bool


class LineageOut(BaseModel):
    """The paths from an incident's root cause to what it broke."""

    version: str
    root_node: str
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]
    unreached: list[str]
    truncated: bool


def to_summary(incident: StoredIncident) -> IncidentSummary:
    """The list row for an incident."""
    return IncidentSummary(**_summary_fields(incident))


def to_detail(
    incident: StoredIncident,
    signals: list[Signal],
    investigation: StoredInvestigation | None,
    fixes: list[StoredFix],
) -> IncidentDetail:
    """The incident page's data, from the stored incident, signals, evidence and fixes."""
    affected = incident.affected
    similar_incidents, runbooks = _knowledge(investigation)
    return IncidentDetail(
        **_summary_fields(incident),
        root_cause=_root_cause(incident.root_cause, incident.confidence),
        affected=Affected(
            models=list(affected.get("models", [])),
            columns=list(affected.get("columns", [])),
            gold_tables=list(affected.get("gold_tables", [])),
        ),
        signals=[SignalOut(**s.model_dump(include=set(SignalOut.model_fields))) for s in signals],
        investigation=_investigation(investigation),
        similar_incidents=similar_incidents,
        related_runbooks=runbooks,
        fixes=[to_fix_summary(fix) for fix in fixes],
    )


def to_fix_summary(fix: StoredFix) -> FixSummary:
    """A fix in brief."""
    return FixSummary(**_fix_fields(fix))


def to_fix_detail(fix: StoredFix) -> FixDetail:
    """A fix with its diff and sandbox verdict."""
    return FixDetail(
        **_fix_fields(fix),
        incident_id=fix.incident_id,
        patch=fix.patch,
        sandbox=_sandbox(fix.sandbox_result),
        suggestion=fix.suggestion,
        branch=fix.branch,
        updated_at=fix.updated_at,
    )


def to_lineage(view: LineageView) -> LineageOut:
    """The lineage slice in its wire form."""
    return LineageOut(
        version=view.version,
        root_node=view.root_node,
        nodes=[LineageNodeOut(**vars(node)) for node in view.nodes],
        edges=[
            LineageEdgeOut(source=e.source, target=e.target, kind=e.kind.value, inferred=e.inferred)
            for e in view.edges
        ],
        unreached=list(view.unreached),
        truncated=view.truncated,
    )


def _summary_fields(incident: StoredIncident) -> dict[str, Any]:
    return {
        "id": incident.id,
        "status": incident.status,
        "summary": incident.summary,
        "severity": incident.severity,
        "fault_class": incident.fault_class,
        "confidence": incident.confidence,
        "classified_by": incident.classified_by,
        "root_node": incident.root_node,
        "affected_models": len(incident.affected.get("models", [])),
        "affected_gold_tables": len(incident.affected.get("gold_tables", [])),
        "opened_at": incident.opened_at,
        "updated_at": incident.updated_at,
    }


def _root_cause(
    stored: dict[str, Any] | None, incident_confidence: float | None
) -> RootCause | None:
    if stored is None:
        return None
    # A concluded cause keeps its confidence on the incident; only a best guess stores its own.
    confidence = stored.get("confidence")
    return RootCause(
        node=stored.get("node"),
        explanation=stored.get("explanation"),
        confidence=incident_confidence if confidence is None else confidence,
        fix_location=stored.get("fix_location"),
        fix_reason=stored.get("fix_reason"),
        partial=bool(stored.get("partial", False)),
        evidence_ids=list(stored.get("evidence_ids", [])),
        alternatives=[Alternative(**a) for a in stored.get("alternatives", [])],
    )


def _investigation(stored: StoredInvestigation | None) -> InvestigationOut | None:
    if stored is None:
        return None
    return InvestigationOut(
        status=stored.status,
        started_at=stored.started_at,
        finished_at=stored.finished_at,
        llm_calls=stored.llm_calls,
        input_tokens=stored.input_tokens,
        output_tokens=stored.output_tokens,
        models=stored.model_ids,
        error=stored.error,
        trace_url=None,
        evidence=[
            EvidenceOut(
                number=number,
                tool=item["tool"],
                arguments=item["arguments"],
                output=item["output"],
                hypothesis=item["hypothesis"],
                collected_at=item["collected_at"],
            )
            for number, item in enumerate(stored.evidence, start=1)
        ],
    )


def _knowledge(
    investigation: StoredInvestigation | None,
) -> tuple[list[KnowledgeHit], list[KnowledgeHit]]:
    """Past incidents and runbooks from the Investigator's own `search_knowledge` calls.

    Reading what it already found, rather than searching again, keeps the embedding model off
    the API host and shows the reviewer exactly what the agent saw.
    """
    best: dict[tuple[str, str, str], KnowledgeHit] = {}
    for item in investigation.evidence if investigation else []:
        if item["tool"] != "search_knowledge":
            continue
        for raw in item["output"].get("hits", []):
            hit = KnowledgeHit(
                source=raw["source"],
                document=raw["document"],
                heading=raw["heading"],
                fault_class=raw.get("fault_class"),
                score=raw["score"],
                text=str(raw["text"])[:MAX_SNIPPET],
            )
            key = (hit.source, hit.document, hit.heading)
            if key not in best or hit.score > best[key].score:
                best[key] = hit
    ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
    return (
        [h for h in ranked if h.source == "incident"][:MAX_SIMILAR],
        [h for h in ranked if h.source == "runbook"][:MAX_SIMILAR],
    )


def _fix_fields(fix: StoredFix) -> dict[str, Any]:
    sandbox = fix.sandbox_result
    return {
        "id": fix.id,
        "kind": fix.kind,
        "status": fix.status,
        "files": fix.files,
        "sandbox_passed": None if sandbox is None else bool(sandbox.get("passed")),
        "github_url": fix.github_url,
        "github_number": fix.github_number,
        "created_at": fix.created_at,
    }


def _sandbox(stored: dict[str, Any] | None) -> SandboxOut | None:
    if stored is None:
        return None
    return SandboxOut(
        passed=bool(stored.get("passed")),
        reason=str(stored.get("reason", "")),
        selector=str(stored.get("selector", "")),
        fixed=list(stored.get("fixed", [])),
        still_failing=list(stored.get("still_failing", [])),
        regressions=list(stored.get("regressions", [])),
        before=dict(stored.get("before", {})),
        after=dict(stored.get("after", {})),
        seconds=float(stored.get("seconds", 0.0)),
    )
