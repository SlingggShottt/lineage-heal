"""FR-API-01 and FR-UI-02 to 05: the read-only incident endpoints, with no sign-in."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from detector_graphs import RUN_AT, failing, market_graph
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from lineageheal.agents.fixer import Patch
from lineageheal.agents.investigator.models import (
    Conclusion,
    Evidence,
    Investigation,
    InvestigationStatus,
)
from lineageheal.api import incident_schemas
from lineageheal.api.app import create_app
from lineageheal.api.problems import TYPE_PREFIX
from lineageheal.core.settings import Settings
from lineageheal.core.signals import RunSummary
from lineageheal.store import models
from lineageheal.store.fixes import FixRepository, SandboxedPatch
from lineageheal.store.incidents import IncidentDraft, IncidentRepository
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.lineage import LineageRepository
from lineageheal.store.runs import RunRepository

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
DIFF = "--- a/dbt/models/bronze/b.sql\n+++ b/dbt/models/bronze/b.sql\n@@ -1 +1 @@\n-a\n+b\n"
LOG_WITH_A_PATH = "/scratch/lineageheal-sandbox-xyz/tree/dbt/models/bronze/b.sql failed"
AFFECTED = {
    "models": ["brz_amfi__navall", "slv_amfi__nav", "gld_mf_nav_daily"],
    "columns": ["brz_amfi__navall.nav_inr"],
    "gold_tables": ["gld_mf_nav_daily"],
}


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app(Settings())
    app.state.engine = engine
    with TestClient(app) as test_client:
        yield test_client


def open_incident(
    engine: Engine,
    minutes: int = 0,
    *,
    root: str | None = "raw.amfi.navall.nav",
    fault_class: str = "schema_drift",
    version: str | None = "v1",
    affected: dict[str, Any] | None = None,
) -> int:
    LineageRepository(engine).save(market_graph("v1"))
    draft = IncidentDraft(
        summary="not_null on brz_amfi__navall.nav_inr",
        severity="error",
        fingerprint=f"fp-{minutes}-{root}",
        root_node=root,
        affected=AFFECTED if affected is None else affected,
        lineage_version=version,
        fault_class=fault_class,
        confidence=0.9,
        classified_by="rules",
        opened_at=NOW + timedelta(minutes=minutes),
    )
    return IncidentRepository(engine).create(draft).id


def knowledge_hits() -> dict[str, Any]:
    def hit(source: str, document: str, score: float, heading: str = "Fix") -> dict[str, Any]:
        return {
            "source": source,
            "document": document,
            "heading": heading,
            "fault_class": "schema_drift",
            "score": score,
            "text": "x" * 900,
        }

    return {
        "hits": [
            hit("runbook", "schema-drift", 0.03),
            hit("incident", "incident-7", 0.02),
            hit("runbook", "schema-drift", 0.05),  # the same section again, scored higher
        ]
    }


def investigate(engine: Engine, incident_id: int) -> None:
    repository = InvestigationRepository(engine)
    run_id = repository.start(incident_id, NOW)
    found = Investigation(
        status=InvestigationStatus.CONCLUDED,
        conclusion=Conclusion(
            root_cause_node="raw.amfi.navall.nav",
            fault_class="schema_drift",
            explanation="The NAV column was renamed upstream.",
            evidence_ids=[2],
            confidence=0.9,
            fix_location="dbt",
            fix_reason="Bronze can read the new name.",
        ),
        partial=None,
        evidence=[
            Evidence(1, "get_lineage", {"node": "a"}, True, "c", {"nodes": ["a"]}),
            Evidence(
                2, "search_knowledge", {"query": "q"}, True, "c", knowledge_hits(),
                collected_at=NOW,
            ),
        ],
        llm_calls=3,
        elapsed_seconds=4.0,
        model_ids={"investigator.step.v1": "gemini-x"},
        prompt_hashes={"investigator.step.v1": "ab" * 32},
        input_tokens=100,
        output_tokens=20,
    )  # fmt: skip
    repository.finish(run_id, found, NOW + timedelta(minutes=1))


def sandboxed(passed: bool = True) -> SandboxedPatch:
    result = {
        "passed": passed,
        "reason": "fixed 1 failing node(s) and broke nothing",
        "selector": "brz_amfi__navall+",
        "fixed": ["test.not_null"],
        "still_failing": [],
        "regressions": [],
        "before": {"test.not_null": "fail"},
        "after": {"test.not_null": "pass"},
        "log_tail": LOG_WITH_A_PATH,
        "seconds": 12.345,
    }
    return SandboxedPatch(Patch(DIFF, ("dbt/models/bronze/b.sql",), 2), result, passed)


# --- the list -------------------------------------------------------------------------------


def test_an_empty_database_lists_an_empty_page(client: TestClient) -> None:
    body = client.get("/incidents").json()

    assert body == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_incidents_are_listed_newest_first_in_pages(client: TestClient, engine: Engine) -> None:
    ids = [open_incident(engine, minutes) for minutes in (0, 10, 20)]

    first = client.get("/incidents", params={"limit": 2}).json()
    rest = client.get("/incidents", params={"limit": 2, "offset": 2}).json()

    assert [i["id"] for i in first["items"]] == [ids[2], ids[1]]
    assert (first["total"], first["limit"]) == (3, 2)
    assert [i["id"] for i in rest["items"]] == [ids[0]]
    row = first["items"][0]
    assert row["affected_models"] == 3
    assert row["affected_gold_tables"] == 1
    assert row["fault_class"] == "schema_drift"
    assert "root_cause" not in row  # the list stays light


def test_the_list_filters_by_status_class_severity_and_feed(
    client: TestClient, engine: Engine
) -> None:
    amfi = open_incident(engine, 0, root="brz_amfi__navall.nav_inr", fault_class="nulls")
    nse = open_incident(engine, 1, root="brz_nse__cm_bhavcopy.close_price_inr")

    def ids(**params: str) -> list[int]:
        return [i["id"] for i in client.get("/incidents", params=params).json()["items"]]

    assert ids(feed="amfi") == [amfi]
    assert ids(feed="nse") == [nse]
    assert ids(fault_class="nulls") == [amfi]
    assert ids(status="detected", severity="error") == [nse, amfi]
    assert ids(status="approved") == []


@pytest.mark.parametrize(
    ("param", "value"),
    [("status", "bogus"), ("fault_class", "bogus"), ("severity", "bogus"), ("feed", "bogus")],
)
def test_an_unknown_filter_value_is_a_422_that_names_the_parameter(
    client: TestClient, param: str, value: str
) -> None:
    response = client.get("/incidents", params={param: value})

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == f"query.{param}"


def test_the_filter_values_match_what_the_database_allows() -> None:
    assert get_args(incident_schemas.IncidentStatus) == models.INCIDENT_STATUSES
    assert get_args(incident_schemas.FaultClass) == models.FAULT_CLASSES
    assert get_args(incident_schemas.Severity) == models.INCIDENT_SEVERITIES


# --- one incident ---------------------------------------------------------------------------


def test_an_incident_with_no_investigation_or_fix_still_reads(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["id"] == incident_id
    assert body["root_cause"] is None
    assert body["investigation"] is None
    assert body["fixes"] == []
    assert body["affected"]["gold_tables"] == ["gld_mf_nav_daily"]


def test_the_detail_shows_the_diagnosis_evidence_signals_and_similar_knowledge(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    signal = failing("brz_amfi__navall", "nav_inr")
    RunRepository(engine).record(
        RunSummary(run_id="run-1", generated_at=RUN_AT, counts={}, signals=(signal,))
    )
    IncidentRepository(engine).attach(incident_id, "run-1", [signal], at=NOW)
    investigate(engine, incident_id)

    body = client.get(f"/incidents/{incident_id}").json()

    assert body["root_cause"]["explanation"] == "The NAV column was renamed upstream."
    assert body["root_cause"]["fix_location"] == "dbt"
    assert body["root_cause"]["confidence"] == 0.9  # the incident's, which a conclusion sets
    assert body["root_cause"]["evidence_ids"] == [2]
    assert [s["name"] for s in body["signals"]] == [signal.name]
    investigation = body["investigation"]
    assert investigation["status"] == "succeeded"
    assert (investigation["llm_calls"], investigation["trace_url"]) == (3, None)
    assert [(e["number"], e["tool"]) for e in investigation["evidence"]] == [
        (1, "get_lineage"),
        (2, "search_knowledge"),
    ]
    runbook, incident = body["related_runbooks"], body["similar_incidents"]
    assert [(h["document"], h["score"]) for h in runbook] == [("schema-drift", 0.05)]
    assert [h["document"] for h in incident] == ["incident-7"]
    assert len(runbook[0]["text"]) == 600  # snippets are cut for the page


def test_the_detail_lists_fixes_in_brief_with_their_links(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    fixes = FixRepository(engine)
    [fix_id] = fixes.save_attempts(incident_id, [sandboxed()], NOW)
    fixes.mark_published(fix_id, "heal/1", 42, "https://github.com/o/r/pull/42", NOW)

    [fix] = client.get(f"/incidents/{incident_id}").json()["fixes"]

    assert (fix["kind"], fix["status"], fix["sandbox_passed"]) == ("dbt_patch", "published", True)
    assert (fix["github_number"], fix["github_url"]) == (42, "https://github.com/o/r/pull/42")
    assert "patch" not in fix  # the diff is at /fix


@pytest.mark.parametrize("path", ["", "/lineage", "/fix"])
def test_an_unknown_incident_is_a_404_problem_on_every_endpoint(
    client: TestClient, path: str
) -> None:
    response = client.get(f"/incidents/999{path}")

    assert response.status_code == 404
    assert response.json()["type"] == TYPE_PREFIX + "not-found"
    assert response.json()["detail"] == "There is no incident 999."


@pytest.mark.parametrize("incident_id", ["0", "-1", "abc", str(2**63)])
def test_an_id_that_cannot_be_an_incident_is_a_422_not_a_database_error(
    client: TestClient, incident_id: str
) -> None:
    assert client.get(f"/incidents/{incident_id}").status_code == 422


# --- lineage --------------------------------------------------------------------------------


def test_the_lineage_slice_marks_the_root_the_path_and_what_broke(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine, affected={**AFFECTED, "columns": ["slv_amfi__nav.nav_inr"]})

    body = client.get(f"/incidents/{incident_id}/lineage").json()

    roles = {n["id"]: n["role"] for n in body["nodes"]}
    assert roles == {
        "raw.amfi.navall.nav": "root",
        "brz_amfi__navall.nav_inr": "path",
        "slv_amfi__nav.nav_inr": "affected",
        "gld_mf_nav_daily.nav_inr": "affected",
    }
    assert {(e["source"], e["target"]) for e in body["edges"]} >= {
        ("raw.amfi.navall.nav", "brz_amfi__navall.nav_inr")
    }
    assert (body["version"], body["root_node"], body["unreached"]) == (
        "v1",
        "raw.amfi.navall.nav",
        [],
    )


def test_the_lineage_uses_the_graph_version_the_incident_was_diagnosed_against(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    LineageRepository(engine).save(market_graph("v2"), activated_at=NOW + timedelta(hours=1))

    assert client.get(f"/incidents/{incident_id}/lineage").json()["version"] == "v1"


def test_an_incident_with_no_stored_version_uses_the_current_graph(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine, version=None)

    assert client.get(f"/incidents/{incident_id}/lineage").json()["version"] == "v1"


@pytest.mark.parametrize("root", [None, "raw.amfi.navall.renamed"])
def test_lineage_that_cannot_be_drawn_is_a_404_that_says_why(
    client: TestClient, engine: Engine, root: str | None
) -> None:
    incident_id = open_incident(engine, root=root)

    response = client.get(f"/incidents/{incident_id}/lineage")

    assert response.status_code == 404
    assert response.json()["type"] == TYPE_PREFIX + "lineage-unavailable"


# --- the fix --------------------------------------------------------------------------------


def test_the_fix_endpoint_returns_the_newest_fix_with_its_diff_and_verdict(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    FixRepository(engine).save_attempts(incident_id, [sandboxed(False), sandboxed(True)], NOW)

    body = client.get(f"/incidents/{incident_id}/fix").json()

    assert body["status"] == "verified"
    assert body["patch"] == DIFF
    assert body["files"] == ["dbt/models/bronze/b.sql"]
    assert body["sandbox"]["passed"] is True
    assert body["sandbox"]["fixed"] == ["test.not_null"]
    assert body["sandbox"]["after"] == {"test.not_null": "pass"}
    assert body["sandbox"]["seconds"] == 12.345


def test_the_sandbox_output_tail_with_its_local_paths_is_never_exposed(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)
    FixRepository(engine).save_attempts(incident_id, [sandboxed()], NOW)

    for path in (f"/incidents/{incident_id}/fix", f"/incidents/{incident_id}"):
        text = client.get(path).text
        assert "log_tail" not in text
        assert "/scratch/lineageheal-sandbox" not in text


def test_a_suggestion_is_returned_as_a_fix_with_no_diff(client: TestClient, engine: Engine) -> None:
    incident_id = open_incident(engine)
    FixRepository(engine).save_suggestion(incident_id, {"title": "Change the loader URL"}, NOW)

    body = client.get(f"/incidents/{incident_id}/fix").json()

    assert body["kind"] == "suggestion"
    assert body["patch"] is None
    assert body["sandbox"] is None
    assert body["suggestion"] == {"title": "Change the loader URL"}


def test_an_incident_with_no_fix_yet_is_a_404_on_the_fix_endpoint(
    client: TestClient, engine: Engine
) -> None:
    incident_id = open_incident(engine)

    response = client.get(f"/incidents/{incident_id}/fix")

    assert response.status_code == 404
    assert response.json()["detail"] == f"Incident {incident_id} has no fix yet."


# --- when the database is not there ---------------------------------------------------------


def test_with_no_database_configured_the_endpoints_say_service_unavailable() -> None:
    with TestClient(create_app(Settings())) as bare:
        response = bare.get("/incidents")

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "database-unavailable"


def test_a_database_failure_is_a_503_that_does_not_leak_its_error() -> None:
    app = create_app(Settings())
    app.state.engine = create_engine("sqlite://")  # no tables: every query fails

    with TestClient(app) as broken:
        response = broken.get("/incidents")

    assert response.status_code == 503
    assert response.json()["type"] == TYPE_PREFIX + "database-unavailable"
    assert response.json()["detail"] == "The database could not be read."
    assert "no such table" not in response.text
