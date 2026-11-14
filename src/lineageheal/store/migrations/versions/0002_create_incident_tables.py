"""Create the pipeline run, signal, incident, agent run, evidence, fix and review tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, plain JSON on SQLite, as in lineageheal.store.types.JSONType.
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Create the tables in dependency order."""
    op.create_table(
        "pipeline_run",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("dbt_invocation_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generated_at", TIMESTAMP, nullable=False),
        sa.Column("counts", JSON, nullable=False),
        sa.Column("skipped_models", JSON, nullable=False),
        sa.Column("artifacts_prefix", sa.Text(), nullable=True),
        sa.Column("recorded_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')", name=op.f("ck_pipeline_run_status_is_known")
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_pipeline_run")),
    )

    op.create_table(
        "incident",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fault_class", sa.String(length=32), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", JSON, nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("opened_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.Column("resolved_at", TIMESTAMP, nullable=True),
        sa.CheckConstraint(
            "status IN ('detected', 'investigating', 'fix_proposed', 'suggestion_filed', "
            "'fix_unverified', 'unresolved', 'approved', 'rejected', 'resolved')",
            name=op.f("ck_incident_status_is_known"),
        ),
        sa.CheckConstraint(
            "fault_class IS NULL OR fault_class IN ('schema_drift', 'nulls', 'duplicates', "
            "'unit_change', 'late_data', 'unknown')",
            name=op.f("ck_incident_fault_class_is_known"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_incident_confidence_is_a_probability"),
        ),
        sa.CheckConstraint(
            "(status = 'resolved' AND resolved_at IS NOT NULL) OR "
            "(status <> 'resolved' AND resolved_at IS NULL)",
            name=op.f("ck_incident_resolved_means_dated"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident")),
    )
    op.create_index("ix_incident_status", "incident", ["status"])

    op.create_table(
        "signal",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", BIG_ID, nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("detected_at", TIMESTAMP, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unique_id", sa.Text(), nullable=True),
        sa.Column("check_type", sa.String(length=64), nullable=False),
        sa.Column("models", JSON, nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("column_name", sa.String(length=128), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('test_failed', 'test_warning', 'test_error', 'model_error', 'run_error')",
            name=op.f("ck_signal_kind_is_known"),
        ),
        sa.CheckConstraint(
            "severity IN ('error', 'warn')", name=op.f("ck_signal_severity_is_known")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["pipeline_run.run_id"], name="fk_signal_run_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incident.id"], name="fk_signal_incident_id", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal")),
        sa.UniqueConstraint("run_id", "kind", "name", name="uq_signal_run_kind_name"),
    )
    op.create_index("ix_signal_incident_id", "signal", ["incident_id"])
    op.create_index("ix_signal_model", "signal", ["model"])

    op.create_table(
        "agent_run",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("incident_id", BIG_ID, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", TIMESTAMP, nullable=False),
        sa.Column("finished_at", TIMESTAMP, nullable=True),
        sa.Column("model_ids", JSON, nullable=False),
        sa.Column("prompt_versions", JSON, nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("trace_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'budget_exhausted')",
            name=op.f("ck_agent_run_status_is_known"),
        ),
        sa.CheckConstraint(
            "status = 'running' OR finished_at IS NOT NULL",
            name=op.f("ck_agent_run_finished_runs_say_when"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error IS NOT NULL", name=op.f("ck_agent_run_failures_say_why")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incident.id"], name="fk_agent_run_incident_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run")),
    )
    op.create_index("ix_agent_run_incident_id", "agent_run", ["incident_id"])

    op.create_table(
        "evidence",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("agent_run_id", BIG_ID, nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("arguments", JSON, nullable=False),
        sa.Column("output", JSON, nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("collected_at", TIMESTAMP, nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_run.id"], name="fk_evidence_agent_run_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index("ix_evidence_agent_run_id", "evidence", ["agent_run_id"])

    op.create_table(
        "fix",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("incident_id", BIG_ID, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("files", JSON, nullable=False),
        sa.Column("sandbox_result", JSON, nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("github_url", sa.Text(), nullable=True),
        sa.Column("github_number", sa.Integer(), nullable=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "kind IN ('dbt_patch', 'suggestion')", name=op.f("ck_fix_kind_is_known")
        ),
        sa.CheckConstraint(
            "status IN ('unverified', 'verified', 'published', 'merged', 'closed')",
            name=op.f("ck_fix_status_is_known"),
        ),
        sa.CheckConstraint(
            "kind <> 'dbt_patch' OR patch IS NOT NULL", name=op.f("ck_fix_patches_have_a_diff")
        ),
        sa.CheckConstraint(
            "kind <> 'dbt_patch' OR status NOT IN ('verified', 'published', 'merged') "
            "OR sandbox_result IS NOT NULL",
            name=op.f("ck_fix_patches_are_sandboxed_first"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('published', 'merged') OR github_url IS NOT NULL",
            name=op.f("ck_fix_published_fixes_link_to_github"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incident.id"], name="fk_fix_incident_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fix")),
    )
    op.create_index("ix_fix_incident_id", "fix", ["incident_id"])

    op.create_table(
        "review",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("fix_id", BIG_ID, nullable=False),
        sa.Column("reviewer_login", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name=op.f("ck_review_decision_is_known")
        ),
        sa.CheckConstraint(
            "decision <> 'rejected' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name=op.f("ck_review_rejections_say_why"),
        ),
        sa.ForeignKeyConstraint(
            ["fix_id"], ["fix.id"], name="fk_review_fix_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review")),
    )
    op.create_index("ix_review_fix_id", "review", ["fix_id"])


def downgrade() -> None:
    """Drop the tables, children first."""
    op.drop_index("ix_review_fix_id", table_name="review")
    op.drop_table("review")
    op.drop_index("ix_fix_incident_id", table_name="fix")
    op.drop_table("fix")
    op.drop_index("ix_evidence_agent_run_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_agent_run_incident_id", table_name="agent_run")
    op.drop_table("agent_run")
    op.drop_index("ix_signal_model", table_name="signal")
    op.drop_index("ix_signal_incident_id", table_name="signal")
    op.drop_table("signal")
    op.drop_index("ix_incident_status", table_name="incident")
    op.drop_table("incident")
    op.drop_table("pipeline_run")
