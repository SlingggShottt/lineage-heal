"""FR-BEN-03 and FR-KB-03: LineageHeal runs inside the scenario and learns only from seed cases."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lineageheal.benchmark.knowledge import (
    BENCH_NAMESPACE,
    REPORT_FILE,
    build_index,
    resolved_incident,
    symptom_lines,
)
from lineageheal.benchmark.models import Scenario
from lineageheal.benchmark.scenario import ScenarioBuilder
from lineageheal.benchmark.spec import FaultCase
from lineageheal.benchmark.systems.lineageheal import LineageHealSystem, rank
from lineageheal.core.errors import BenchmarkError
from lineageheal.core.settings import Settings
from lineageheal.core.signals import RunSummary, Severity, Signal, SignalKind
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone
from lineageheal.llm.client import Endpoint, LLMClient
from lineageheal.llm.fake import FakeProvider
from lineageheal.llm.limiter import TokenBucket
from lineageheal.llm.types import LLMError
from lineageheal.orchestration.run_artifacts import SUMMARY_FILE, run_prefix
from lineageheal.rag.embeddings import HashingEmbedder
from lineageheal.rag.incidents import INCIDENT_SOURCE, IncidentIndexer
from lineageheal.rag.retriever import Retriever
from lineageheal.store.database import create_db_engine
from lineageheal.store.incidents import StoredIncident
from lineageheal.store.knowledge import KnowledgeRepository
from lineageheal.store.migrate import upgrade_database

FIXTURES = Path(__file__).parents[2] / "fixtures"
RUNBOOKS = Path(__file__).parents[3] / "docs" / "runbooks"
REPORT = (FIXTURES / "amfi" / "navall_history_2026-10-15_sample.txt").read_bytes()
FAULT_DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def case(case_id: str, split: str = "seed") -> FaultCase:
    return FaultCase.model_validate(
        {
            "id": case_id,
            "class": "nulls",
            "source": "amfi",
            "dataset": "navall",
            "trade_date": FAULT_DAY.isoformat(),
            "split": split,
            "seed": 1,
            "injection": {"type": "blank_value", "column": "Net Asset Value"},
            "expected": {"root_cause": "raw.amfi.navall.Net Asset Value", "fix_location": "dbt"},
        }
    )


def signal(message: str = "Got 8742 results, configured to fail\nmore") -> Signal:
    return Signal(
        kind=SignalKind.TEST_FAILED,
        severity=Severity.ERROR,
        run_id="bench-x",
        detected_at=NOW,
        name="not_null_brz_amfi__navall_nav_inr",
        check_type="not_null",
        models=("brz_amfi__navall",),
        model="brz_amfi__navall",
        column="nav_inr",
        failures=8742,
        message=message,
    )


def source_zone(root: Path, days: int = 12) -> LocalObjectStore:
    store = LocalObjectStore(root)
    for offset in range(days):
        RawZone(store).put_if_absent(
            source="amfi",
            dataset="navall",
            trade_date=FAULT_DAY - timedelta(days=offset),
            filename="NAVAll.txt",
            content=REPORT + f"\r\n# day {offset}".encode(),
        )
    return store


def no_dbt(project: Path, data: Path, raw: Path, target: Path, timeout: float) -> tuple[int, str]:
    return 1, "fake dbt"


def builder(tmp_path: Path, source: LocalObjectStore) -> ScenarioBuilder:
    (tmp_path / "scratch").mkdir(exist_ok=True)
    return ScenarioBuilder(source, tmp_path, tmp_path / "scratch", dbt_build=no_dbt)


def stored(
    node: str | None, confidence: float, alternatives: list[dict[str, Any]] | None = None
) -> StoredIncident:
    root_cause = (
        None
        if node is None
        else {
            "node": node,
            "alternatives": alternatives or [],
            "fix_location": "dbt",
            "partial": False,
        }
    )
    return StoredIncident(
        id=1,
        status="investigated",
        summary="",
        severity="error",
        fingerprint="f",
        lineage_version=None,
        fault_class="nulls",
        confidence=confidence,
        classified_by="investigator",
        root_node=node,
        affected={},
        opened_at=NOW,
        updated_at=NOW,
        root_cause=root_cause,
    )


# -- the isolated index ----------------------------------------------------------------------


def test_the_index_holds_runbooks_and_seed_incidents_and_nothing_from_the_test_split(
    tmp_path: Path,
) -> None:
    path = tmp_path / "knowledge.db"

    report = build_index(
        path,
        [case("amfi-nulls-001"), case("amfi-nulls-002")],
        builder(tmp_path, source_zone(tmp_path / "src")),
        HashingEmbedder(),
        RUNBOOKS,
    )

    assert report.incidents == ("amfi-nulls-001", "amfi-nulls-002")
    engine = create_db_engine(f"sqlite+pysqlite:///{path}")
    try:
        documents, _ = KnowledgeRepository(engine).counts(BENCH_NAMESPACE)
        assert KnowledgeRepository(engine).counts("live") == (0, 0)
    finally:
        engine.dispose()
    assert documents == report.runbooks + 2
    assert json.loads(path.with_name(REPORT_FILE).read_text())["incidents"] == list(
        report.incidents
    )


def test_a_test_case_can_never_be_indexed(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="only seed cases"):
        build_index(
            tmp_path / "k.db",
            [case("amfi-nulls-001"), case("amfi-nulls-009", "test")],
            builder(tmp_path, source_zone(tmp_path / "src")),
            HashingEmbedder(),
            RUNBOOKS,
        )
    assert not (tmp_path / "k.db").exists()


def test_a_seed_case_that_cannot_be_built_is_skipped_and_named(tmp_path: Path) -> None:
    thin = source_zone(tmp_path / "thin", days=3)

    report = build_index(
        tmp_path / "k.db",
        [case("amfi-nulls-001")],
        builder(tmp_path, thin),
        HashingEmbedder(),
        RUNBOOKS,
    )

    assert report.incidents == ()
    assert "backfill" in report.skipped["amfi-nulls-001"]


def test_an_existing_index_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "k.db").write_bytes(b"x")

    with pytest.raises(BenchmarkError, match="already exists"):
        build_index(
            tmp_path / "k.db",
            [],
            builder(tmp_path, source_zone(tmp_path / "s")),
            HashingEmbedder(),
            RUNBOOKS,
        )


def test_a_resolved_incident_is_retrievable_by_its_symptoms_with_the_answer_attached(
    tmp_path: Path,
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'k.db'}"
    upgrade_database(url)
    engine = create_db_engine(url)
    try:
        repository = KnowledgeRepository(engine)
        embedder = HashingEmbedder()
        incident = resolved_incident(case("amfi-nulls-001"), [signal()])
        indexer = IncidentIndexer(repository, embedder)
        indexer.index(incident, namespace=BENCH_NAMESPACE)
        again = indexer.index(incident, namespace=BENCH_NAMESPACE)

        hits = Retriever(repository, embedder, namespace=BENCH_NAMESPACE).search(
            "not_null on brz_amfi__navall.nav_inr failed", source=INCIDENT_SOURCE
        )
        none_live = Retriever(repository, embedder).search("not_null on brz_amfi__navall")
    finally:
        engine.dispose()

    assert again.value == "unchanged"
    assert "raw.amfi.navall.Net Asset Value" in hits[0].chunk.text
    assert hits[0].chunk.text.index("Symptoms") < hits[0].chunk.text.index("Root cause")
    assert none_live == []


def test_symptom_lines_show_check_place_rows_and_the_first_message_line() -> None:
    assert symptom_lines([signal()]) == (
        "not_null on brz_amfi__navall.nav_inr, 8742 rows. Got 8742 results, configured to fail",
    )


# -- the system ------------------------------------------------------------------------------


def unreachable_client() -> LLMClient:
    provider = FakeProvider("gemini", [LLMError("down")] * 30)
    return LLMClient(
        [Endpoint(provider, TokenBucket(6000, capacity=100))], attempts=1, sleep=lambda _: None
    )


def scenario(tmp_path: Path) -> Scenario:
    raw = tmp_path / "sandbox" / "raw"
    store = LocalObjectStore(raw)
    summary = RunSummary(run_id="bench-x", generated_at=NOW, counts={}, signals=(signal(),))
    store.put(
        f"{run_prefix('bench-x')}/{SUMMARY_FILE}", summary.model_dump_json().encode(), metadata={}
    )
    store.put(
        f"{run_prefix('bench-x')}/manifest.json", b'{"nodes": {}, "sources": {}}', metadata={}
    )
    return Scenario(
        case("amfi-nulls-001", "test"),
        tmp_path / "sandbox",
        raw,
        tmp_path / "sandbox" / "bench.duckdb",
        "bench-x",
        summary,
        1,
        0.0,
    )


def test_the_system_works_on_a_copy_of_the_index_and_counts_its_own_model_use(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template.db"
    upgrade_database(f"sqlite+pysqlite:///{template}")
    before = template.read_bytes()
    client = unreachable_client()
    system = LineageHealSystem(Settings(_env_file=None), client, template, HashingEmbedder())
    found = scenario(tmp_path)

    result = system.diagnose(found)

    assert template.read_bytes() == before  # the shared index is never written
    assert (found.root / "application.db").is_file()  # the work happened in the sandbox
    assert result.root_causes == ()  # the model was unreachable, so nothing was concluded
    assert result.llm_calls == 0  # failed attempts are not billed
    assert "1 incident" in result.notes


def test_a_second_fault_is_costed_from_its_own_usage_only(tmp_path: Path) -> None:
    template = tmp_path / "template.db"
    upgrade_database(f"sqlite+pysqlite:///{template}")
    client = unreachable_client()
    client.usage.calls, client.usage.input_tokens = 7, 900  # earlier faults' use
    system = LineageHealSystem(Settings(_env_file=None), client, template, HashingEmbedder())

    result = system.diagnose(scenario(tmp_path))

    assert (result.llm_calls, result.input_tokens) == (0, 0)


# -- ranking ---------------------------------------------------------------------------------


def test_incidents_are_ranked_by_confidence_then_their_alternatives_follow() -> None:
    from lineageheal.benchmark.systems.lineageheal import _ranked_nodes

    weak = stored("raw.a.b.weak", 0.4)
    strong = stored(
        "raw.a.b.strong",
        0.9,
        [{"node": "raw.a.b.alt", "confidence": 0.5}, {"node": "raw.a.b.weak", "confidence": 0.9}],
    )

    nodes = _ranked_nodes(rank([weak, strong]))

    assert [n for n, _ in nodes] == ["raw.a.b.strong", "raw.a.b.weak", "raw.a.b.alt"]


def test_an_incident_with_no_root_cause_is_not_ranked() -> None:
    assert rank([stored(None, 0.0)]) == []
