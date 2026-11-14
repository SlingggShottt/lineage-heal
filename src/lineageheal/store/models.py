"""SQLAlchemy models for application state.

Alembic migrations in `migrations/versions` are written by hand to match these models; a
test fails if the two ever disagree.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from lineageheal.store.types import JSONType, UTCDateTime

# Fixed names for keys, indexes and checks keep migrations stable and readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}

# Outcomes of one ingestion attempt. "failed" is an attempt that raised an error; the other
# three mirror `IngestionOutcome` in the ingestion package.
OUTCOMES = ("stored", "unchanged", "no_data", "failed")

# What a signal reports and how seriously; mirrors `SignalKind` and `Severity` in core.signals.
SIGNAL_KINDS = ("test_failed", "test_warning", "test_error", "model_error", "run_error")
SIGNAL_SEVERITIES = ("error", "warn")
RUN_STATUSES = ("succeeded", "failed")

# The incident lifecycle of docs/design_architecture.md, section 9.
INCIDENT_STATUSES = (
    "detected",
    "investigating",
    "fix_proposed",
    "suggestion_filed",
    "fix_unverified",
    "unresolved",
    "approved",
    "rejected",
    "resolved",
)
FAULT_CLASSES = ("schema_drift", "nulls", "duplicates", "unit_change", "late_data", "unknown")
AGENT_RUN_STATUSES = ("running", "succeeded", "failed", "budget_exhausted")
FIX_KINDS = ("dbt_patch", "suggestion")
# unverified: drafted, not passed in the sandbox; verified: passed; published: a pull request or
# issue exists; merged and closed are what a human did with it on GitHub.
FIX_STATUSES = ("unverified", "verified", "published", "merged", "closed")
REVIEW_DECISIONS = ("approved", "rejected")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """SQL for a check that a column holds one of a fixed set of values."""
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


# SQLite only auto-increments a plain INTEGER primary key.
BigId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    """Base class for all models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IngestionLog(Base):
    """One row per ingestion attempt, successful or not (FR-ING-06).

    It records what was asked for, what came back and when, so that late or missing data
    can be detected and so that the latest stored version of a republished file can be
    chosen by when it arrived.
    """

    __tablename__ = "ingestion_log"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('stored', 'unchanged', 'no_data', 'failed')", name="outcome_is_known"
        ),
        # A file that exists must be fully described.
        CheckConstraint(
            "outcome NOT IN ('stored', 'unchanged') OR "
            "(object_key IS NOT NULL AND sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name="stored_files_are_described",
        ),
        CheckConstraint("outcome <> 'failed' OR error IS NOT NULL", name="failures_say_why"),
        Index("ix_ingestion_log_lookup", "source", "dataset", "trade_date"),
        Index("ix_ingestion_log_object_key", "object_key"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PipelineRun(Base):
    """One dbt run, as summarised by the signals file the run wrote (FR-WH-07).

    The id is the orchestrator's run id, so a run keeps one identity from Dagster through
    the files under `runs/<run_id>/` to this table.
    """

    __tablename__ = "pipeline_run"
    __table_args__ = (CheckConstraint(_one_of("status", RUN_STATUSES), name="status_is_known"),)

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dbt_invocation_id: Mapped[str | None] = mapped_column(String(64))
    # "failed" when any signal is an error, which is what starts the healing workflow;
    # warnings alone leave a run "succeeded".
    status: Mapped[str] = mapped_column(String(16))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    counts: Mapped[dict[str, int]] = mapped_column(JSONType)
    skipped_models: Mapped[list[str]] = mapped_column(JSONType)
    # Where the run's manifest and results are kept in the raw zone's object store.
    artifacts_prefix: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Incident(Base):
    """A group of signals with one diagnosis, from detection to a human decision (FR-DET)."""

    __tablename__ = "incident"
    __table_args__ = (
        CheckConstraint(_one_of("status", INCIDENT_STATUSES), name="status_is_known"),
        CheckConstraint(
            "fault_class IS NULL OR " + _one_of("fault_class", FAULT_CLASSES),
            name="fault_class_is_known",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_is_a_probability",
        ),
        # An incident is resolved exactly when it says when.
        CheckConstraint(
            "(status = 'resolved' AND resolved_at IS NOT NULL) OR "
            "(status <> 'resolved' AND resolved_at IS NULL)",
            name="resolved_means_dated",
        ),
        Index("ix_incident_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32))
    fault_class: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    # The Investigator's conclusion: the node, whether the fix is inside dbt, and why.
    root_cause: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Signal(Base):
    """One failure observation from a pipeline run (FR-DET-01).

    It names the model and column involved and says nothing about the cause. `incident_id`
    stays empty until the Detector groups the signal into an incident.
    """

    __tablename__ = "signal"
    __table_args__ = (
        CheckConstraint(_one_of("kind", SIGNAL_KINDS), name="kind_is_known"),
        CheckConstraint(_one_of("severity", SIGNAL_SEVERITIES), name="severity_is_known"),
        # Importing a run twice must not duplicate its signals.
        UniqueConstraint("run_id", "kind", "name", name="uq_signal_run_kind_name"),
        Index("ix_signal_incident_id", "incident_id"),
        Index("ix_signal_model", "model"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_run.run_id", ondelete="CASCADE", name="fk_signal_run_id")
    )
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incident.id", ondelete="SET NULL", name="fk_signal_incident_id")
    )
    kind: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    name: Mapped[str] = mapped_column(Text)  # the test or model that reported it
    unique_id: Mapped[str | None] = mapped_column(Text)
    check_type: Mapped[str] = mapped_column(String(64))
    models: Mapped[list[str]] = mapped_column(JSONType)
    model: Mapped[str | None] = mapped_column(String(128))
    column_name: Mapped[str | None] = mapped_column(String(128))
    failures: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)


class AgentRun(Base):
    """One healing execution for an incident, with the settings that make it reproducible."""

    __tablename__ = "agent_run"
    __table_args__ = (
        CheckConstraint(_one_of("status", AGENT_RUN_STATUSES), name="status_is_known"),
        CheckConstraint(
            "status = 'running' OR finished_at IS NOT NULL", name="finished_runs_say_when"
        ),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failures_say_why"),
        Index("ix_agent_run_incident_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incident.id", ondelete="CASCADE", name="fk_agent_run_incident_id")
    )
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # NFR-REPRO-01: which models and prompt versions produced this run.
    model_ids: Mapped[dict[str, str]] = mapped_column(JSONType)
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSONType)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    trace_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    """One tool output the agent collected while investigating (FR-INV-02)."""

    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_agent_run_id", "agent_run_id"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE", name="fk_evidence_agent_run_id")
    )
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONType)
    output: Mapped[Any] = mapped_column(JSONType)  # bounded by the tool, never a whole table
    hypothesis: Mapped[str | None] = mapped_column(Text)  # what it supports or refutes
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Fix(Base):
    """A proposed change: a dbt patch, or a suggestion for a root cause outside dbt (FR-FIX)."""

    __tablename__ = "fix"
    __table_args__ = (
        CheckConstraint(_one_of("kind", FIX_KINDS), name="kind_is_known"),
        CheckConstraint(_one_of("status", FIX_STATUSES), name="status_is_known"),
        CheckConstraint("kind <> 'dbt_patch' OR patch IS NOT NULL", name="patches_have_a_diff"),
        # No patch is verified, published or merged without a sandbox result on record.
        CheckConstraint(
            "kind <> 'dbt_patch' OR status NOT IN ('verified', 'published', 'merged') "
            "OR sandbox_result IS NOT NULL",
            name="patches_are_sandboxed_first",
        ),
        CheckConstraint(
            "status NOT IN ('published', 'merged') OR github_url IS NOT NULL",
            name="published_fixes_link_to_github",
        ),
        Index("ix_fix_incident_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incident.id", ondelete="CASCADE", name="fk_fix_incident_id")
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    patch: Mapped[str | None] = mapped_column(Text)  # a unified diff, for a dbt patch
    files: Mapped[list[str]] = mapped_column(JSONType)
    sandbox_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    branch: Mapped[str | None] = mapped_column(String(255))
    github_url: Mapped[str | None] = mapped_column(Text)
    github_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Review(Base):
    """An approver's decision on a fix (FR-REV-01, FR-REV-02)."""

    __tablename__ = "review"
    __table_args__ = (
        CheckConstraint(_one_of("decision", REVIEW_DECISIONS), name="decision_is_known"),
        CheckConstraint(
            "decision <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="rejections_say_why",
        ),
        Index("ix_review_fix_id", "fix_id"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    fix_id: Mapped[int] = mapped_column(
        ForeignKey("fix.id", ondelete="CASCADE", name="fk_review_fix_id")
    )
    reviewer_login: Mapped[str] = mapped_column(String(64))  # a GitHub login
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime())
