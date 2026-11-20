"""A small investigation: the market lineage graph, a run's manifest and signals, and sources."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from detector_graphs import failing, market_graph
from sqlalchemy import Engine

from lineageheal.agents.tools import ToolContext, ToolRegistry, build_registry
from lineageheal.core.signals import RunSummary
from lineageheal.lineage.service import LineageService
from lineageheal.rag.embeddings import HashingEmbedder
from lineageheal.rag.retriever import Retriever
from lineageheal.store.ingestion_log import IngestionLogRepository
from lineageheal.store.knowledge import ChunkToIndex, DocumentToIndex, KnowledgeRepository

NOW = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
TEST_ID = "test.lineageheal.not_null_brz_amfi__navall_nav_inr.abc"


def manifest() -> dict[str, Any]:
    return {
        "nodes": {
            "model.lineageheal.slv_amfi__nav": {
                "name": "slv_amfi__nav",
                "resource_type": "model",
                "original_file_path": "models/silver/slv_amfi__nav.sql",
                "description": "Cleaned NAVs.",
                "raw_code": "select nav as nav_inr from {{ ref('brz_amfi__navall') }}",
                "compiled_code": "select nav as nav_inr from bronze.brz_amfi__navall",
            },
            "model.lineageheal.brz_amfi__navall": {
                "name": "brz_amfi__navall",
                "resource_type": "model",
                "original_file_path": "models/bronze/brz_amfi__navall.sql",
                "raw_code": "select * from raw",
            },
            TEST_ID: {
                "resource_type": "test",
                "attached_node": "model.lineageheal.brz_amfi__navall",
                "column_name": "nav_inr",
                "config": {"severity": "ERROR"},
                "test_metadata": {"name": "not_null", "kwargs": {"column_name": "nav_inr"}},
                "raw_code": "{{ test_not_null(**_dbt_generic_test_kwargs) }}",
            },
            "test.lineageheal.assert_gold_untouched.def": {
                "resource_type": "test",
                "raw_code": "select 1 where false",
                "config": {"severity": "ERROR"},
            },
        }
    }


@pytest.fixture
def summary() -> RunSummary:
    signal = failing("brz_amfi__navall", "nav_inr", name="not_null_brz_amfi__navall_nav_inr")
    singular = failing(None, None, check="singular", name="assert_gold_untouched")
    return RunSummary(
        run_id="run-1",
        generated_at=NOW,
        counts={"fail": 2},
        signals=(
            signal.model_copy(update={"unique_id": TEST_ID}),
            singular.model_copy(update={"unique_id": "test.lineageheal.assert_gold_untouched.def"}),
        ),
    )


@pytest.fixture
def seed(tmp_path: Path) -> Path:
    path = tmp_path / "calendar.csv"
    path.write_text(
        "calendar_date,status,description,source,recorded_on\n"
        "2026-10-20,closed,Diwali holiday,nse_api,2026-10-01\n"
        "2026-10-24,special_session,Muhurat session,observed,2026-10-25\n"
    )
    return path


@pytest.fixture
def retriever(engine: Engine) -> Retriever:
    embedder = HashingEmbedder()
    repository = KnowledgeRepository(engine)
    for slug, fault_class, text in [
        ("nulls", "nulls", "a blank nav value fails not_null"),
        ("unit-change", "unit_change", "prices arrive in paise"),
    ]:
        repository.store(
            DocumentToIndex(
                namespace="live",
                source="runbook",
                slug=slug,
                title=slug,
                fault_class=fault_class,
                feeds="",
                content_hash="h",
                embedding_model=embedder.model_name,
                chunks=[ChunkToIndex(0, "Symptoms", text, embedder.embed_documents([text])[0])],
            )
        )
    return Retriever(repository, embedder)


@pytest.fixture
def context(summary: RunSummary, engine: Engine, retriever: Retriever, seed: Path) -> ToolContext:
    return ToolContext(
        lineage=LineageService(market_graph()),
        manifest=manifest(),
        summary=summary,
        ingestion_log=IngestionLogRepository(engine),
        retriever=retriever,
        calendar_seed=seed,
    )


@pytest.fixture
def registry(context: ToolContext) -> ToolRegistry:
    return build_registry(context)
