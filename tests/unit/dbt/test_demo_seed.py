"""LH-100: the sample incidents fit the real project, read back through the API, and reset."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from lineageheal.agents.wiring import REPO_ROOT
from lineageheal.api.app import create_app
from lineageheal.cli import app as cli_app
from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.demo import seed as demo
from lineageheal.demo.scenarios import FINGERPRINT_PREFIX, AttemptPlan, Edit, ScenarioError
from lineageheal.lineage.builder import build_lineage
from lineageheal.lineage.graph import LineageGraph
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.models import Incident, PipelineRun
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
REPO = "acme/pipeline"


@pytest.fixture(scope="module")
def graph(compiled_manifest: dict[str, Any]) -> LineageGraph:
    return build_lineage(compiled_manifest)


@pytest.fixture
def seeded(
    engine: Engine, graph: LineageGraph, compiled_manifest: dict[str, Any]
) -> demo.SeedResult:
    return demo.seed(
        engine,
        graph=graph,
        manifest=compiled_manifest,
        repo_root=REPO_ROOT,
        repository=REPO,
        now=NOW,
    )


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    api = create_app(Settings())
    api.state.engine = engine
    with TestClient(api) as test_client:
        yield test_client


def ids_by_key(result: demo.SeedResult) -> dict[str, int]:
    return dict(result.seeded)


# --- storing ---------------------------------------------------------------------------------


def test_the_three_scenarios_fit_the_real_project_and_are_stored(
    seeded: demo.SeedResult, engine: Engine
) -> None:
    assert [key for key, _ in seeded.seeded] == [
        "amfi-schema-drift",
        "nse-unit-change",
        "amfi-late-data",
    ]
    incidents = {key: IncidentRepository(engine).get(i) for key, i in seeded.seeded}
    assert {k: v.status for k, v in incidents.items() if v} == {
        "amfi-schema-drift": "fix_proposed",
        "nse-unit-change": "fix_proposed",
        "amfi-late-data": "suggestion_filed",
    }
    for incident in incidents.values():
        assert incident is not None
        assert incident.fingerprint.startswith(FINGERPRINT_PREFIX)
        assert incident.classified_by == "investigator"
        assert incident.root_cause is not None
        assert incident.lineage_version


def test_seeding_twice_stores_each_scenario_once(
    seeded: demo.SeedResult,
    engine: Engine,
    graph: LineageGraph,
    compiled_manifest: dict[str, Any],
) -> None:
    again = demo.seed(
        engine,
        graph=graph,
        manifest=compiled_manifest,
        repo_root=REPO_ROOT,
        repository=REPO,
        now=NOW,
    )

    assert again.seeded == ()
    assert set(again.skipped) == {key for key, _ in seeded.seeded}
    with Session(engine) as session:
        assert len(session.scalars(select(Incident)).all()) == 3


def test_the_investigations_credit_no_model_with_work_it_did_not_do(
    seeded: demo.SeedResult, client: TestClient
) -> None:
    for _, incident_id in seeded.seeded:
        investigation = client.get(f"/incidents/{incident_id}").json()["investigation"]

        assert investigation["llm_calls"] == 0
        assert investigation["models"] == {"seed": "seed"}
        assert investigation["trace_url"] is None


# --- read back through the API ----------------------------------------------------------------


def test_the_list_shows_three_incidents_in_three_states_and_nothing_marks_them(
    seeded: demo.SeedResult, client: TestClient
) -> None:
    body = client.get("/incidents").json()

    assert body["total"] == 3
    assert [i["fault_class"] for i in body["items"]] == ["schema_drift", "unit_change", "late_data"]
    text = json.dumps(body).lower()
    for word in ("demo", "sample", "seed", "fake"):
        assert word not in text


def test_the_schema_drift_incident_has_a_published_patch_and_a_full_lineage_path(
    seeded: demo.SeedResult, client: TestClient
) -> None:
    incident_id = ids_by_key(seeded)["amfi-schema-drift"]

    detail = client.get(f"/incidents/{incident_id}").json()
    fix = client.get(f"/incidents/{incident_id}/fix").json()
    lineage = client.get(f"/incidents/{incident_id}/lineage").json()

    assert detail["root_cause"]["fix_location"] == "dbt"
    assert [s["name"] for s in detail["signals"]] == ["not_null_brz_amfi__navall_nav_inr"]
    assert [(h["document"], h["heading"]) for h in detail["related_runbooks"]] == [
        ("schema-drift", "Diagnosis"),
        ("schema-drift", "Fix"),
    ]
    assert detail["similar_incidents"] == []  # no earlier incident exists to cite
    assert fix["status"] == "published"
    assert fix["github_url"] == f"https://github.com/{REPO}/pull/101"
    assert fix["files"] == ["dbt/models/bronze/brz_amfi__navall.sql"]
    assert 'coalesce(trim("NAV"), trim("Net Asset Value"))' in fix["patch"]
    assert fix["sandbox"]["passed"] is True
    roles = {n["id"]: n["role"] for n in lineage["nodes"]}
    assert roles["raw.amfi.navall.Net Asset Value"] == "root"
    assert roles["brz_amfi__navall.nav_inr"] == "affected"
    assert roles["gld_mf_nav_daily.nav_inr"] == "affected"
    assert lineage["unreached"] == []


def test_the_unit_change_incident_shows_a_rejected_attempt_then_a_passing_one(
    seeded: demo.SeedResult, client: TestClient
) -> None:
    incident_id = ids_by_key(seeded)["nse-unit-change"]

    detail = client.get(f"/incidents/{incident_id}").json()
    fix = client.get(f"/incidents/{incident_id}/fix").json()

    assert [f["sandbox_passed"] for f in detail["fixes"]] == [False, True]
    assert [f["status"] for f in detail["fixes"]] == ["unverified", "verified"]
    assert fix["status"] == "verified"
    assert fix["github_url"] is None  # verified, not yet opened as a pull request
    assert "paise_from" in fix["patch"]
    assert fix["sandbox"]["fixed"]
    assert fix["sandbox"]["regressions"] == []


def test_the_late_data_incident_gets_a_filed_suggestion_and_a_root_only_lineage(
    seeded: demo.SeedResult, client: TestClient
) -> None:
    incident_id = ids_by_key(seeded)["amfi-late-data"]

    detail = client.get(f"/incidents/{incident_id}").json()
    fix = client.get(f"/incidents/{incident_id}/fix").json()
    lineage = client.get(f"/incidents/{incident_id}/lineage").json()

    assert detail["root_cause"]["fix_location"] == "external"
    assert {s["kind"] for s in detail["signals"]} == {"ingestion_error", "test_warning"}
    assert fix["kind"] == "suggestion"
    assert fix["patch"] is None
    assert fix["github_url"] == f"https://github.com/{REPO}/issues/102"
    assert fix["suggestion"]["draft"].startswith("uv run lineageheal ingest amfi --date ")
    assert [n["role"] for n in lineage["nodes"]] == ["root"]
    assert set(lineage["unreached"]) == {"gld_mf_nav_daily", "gld_market_daily_summary"}


# --- resetting --------------------------------------------------------------------------------


def test_reset_removes_only_what_was_seeded(
    seeded: demo.SeedResult, engine: Engine, graph: LineageGraph
) -> None:
    real_signal = Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id="real-run",
        detected_at=NOW,
        name="not_null_real",
        check_type="not_null",
    )
    RunRepository(engine).record(
        RunSummary(run_id="real-run", generated_at=NOW, counts={}, signals=(real_signal,))
    )
    real = IncidentRepository(engine).create(
        IncidentDraft(
            summary="a real incident",
            severity="error",
            fingerprint="real-fp",
            root_node=None,
            affected={},
            lineage_version=graph.version,
            fault_class="nulls",
            confidence=0.5,
            classified_by="rules",
            opened_at=NOW,
        )
    )
    IncidentRepository(engine).attach(real.id, "real-run", [real_signal], at=NOW)

    removed = demo.reset(engine)

    assert removed == (3, 3)
    with Session(engine) as session:
        assert [i.fingerprint for i in session.scalars(select(Incident))] == ["real-fp"]
        assert [r.run_id for r in session.scalars(select(PipelineRun))] == ["real-run"]
    assert demo.reset(engine) == (0, 0)


def test_a_scenario_can_be_seeded_again_after_a_reset(
    seeded: demo.SeedResult,
    engine: Engine,
    graph: LineageGraph,
    compiled_manifest: dict[str, Any],
) -> None:
    demo.reset(engine)

    again = demo.seed(
        engine,
        graph=graph,
        manifest=compiled_manifest,
        repo_root=REPO_ROOT,
        repository=REPO,
        now=NOW,
    )

    assert len(again.seeded) == 3


# --- when something is wrong ------------------------------------------------------------------


def test_a_scenario_that_fails_halfway_leaves_nothing_behind(
    engine: Engine,
    graph: LineageGraph,
    compiled_manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(demo, "_store_fix", broken)

    with pytest.raises(RuntimeError, match="disk full"):
        demo.seed(
            engine,
            graph=graph,
            manifest=compiled_manifest,
            repo_root=REPO_ROOT,
            repository=REPO,
            now=NOW,
        )

    with Session(engine) as session:
        assert session.scalars(select(Incident)).all() == []


def test_a_model_that_changed_underneath_a_scenario_is_reported_not_stored(
    tmp_path: Path,
) -> None:
    model = tmp_path / "dbt" / "models" / "bronze" / "b.sql"
    model.parent.mkdir(parents=True)
    model.write_text("select 1 as a\n")
    plan = AttemptPlan("dbt/models/bronze/b.sql", (Edit("select 2", "select 3"),), {}, True)

    with pytest.raises(ScenarioError, match="no longer contains exactly one copy"):
        demo._sandboxed(plan, tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@localhost:5433/db",
        "postgresql+psycopg://u:p@127.0.0.1/db",
        "postgresql+psycopg:///db",
        "sqlite+pysqlite:///x.db",
    ],
)
def test_a_database_on_this_machine_is_allowed(url: str) -> None:
    demo.require_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@db.neon.tech/db",
        "postgresql+psycopg://u:p@10.0.0.5:5432/db",
        "postgresql+psycopg://u:p@postgres/db",
    ],
)
def test_a_database_elsewhere_is_refused_without_echoing_the_password(url: str) -> None:
    with pytest.raises(ConfigurationError, match="not on this machine") as raised:
        demo.require_local_database(url)

    assert ":p@" not in str(raised.value)


def test_a_manifest_from_a_parse_is_refused_with_the_command_to_fix_it() -> None:
    parsed_only = {"nodes": {"model.x.a": {"resource_type": "model", "compiled_code": None}}}

    with pytest.raises(ConfigurationError, match="dbt compile"):
        demo.require_compiled(parsed_only)


# --- the command ------------------------------------------------------------------------------


def test_the_command_seeds_then_reports_what_is_already_there_then_resets(
    database_url: str,
    compiled_manifest: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(compiled_manifest))
    monkeypatch.setenv("DATABASE_URL", database_url)
    runner = CliRunner()
    seed_args = ["dev", "seed-demo", "--manifest", str(manifest), "--repository", REPO]

    first = runner.invoke(cli_app, seed_args)
    second = runner.invoke(cli_app, seed_args)
    reset = runner.invoke(cli_app, ["dev", "seed-demo", "--reset"])

    assert first.exit_code == 0, first.output
    assert first.output.count("seeded ") == 3
    assert second.output.count("is already stored") == 3
    assert reset.output.strip() == "removed 3 seeded incident(s) and 3 run(s)"


def test_the_command_refuses_a_remote_database_before_touching_it(
    compiled_manifest: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:secret@db.example.com/app")

    result = CliRunner().invoke(cli_app, ["dev", "seed-demo", "--repository", REPO])

    assert result.exit_code != 0
    assert "not on this machine" in result.output
    assert "secret" not in result.output


def test_the_command_says_what_is_missing_when_there_is_no_repository(
    database_url: str,
    compiled_manifest: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(compiled_manifest))
    monkeypatch.setenv("DATABASE_URL", database_url)

    result = CliRunner().invoke(cli_app, ["dev", "seed-demo", "--manifest", str(manifest)])

    assert result.exit_code != 0
    assert "GITHUB_REPOSITORY" in result.output
