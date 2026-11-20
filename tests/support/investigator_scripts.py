"""Scripted model answers and an investigator wired to them, shared by the Investigator tests."""

import json
from typing import Any

from detector_graphs import RUN_AT, market_graph

from lineageheal.agents.investigator import Budget, Investigator
from lineageheal.agents.tools import ToolContext, build_registry
from lineageheal.core.signals import RunSummary
from lineageheal.lineage.service import LineageService
from lineageheal.llm.client import Endpoint, LLMClient
from lineageheal.llm.fake import FakeProvider
from lineageheal.llm.limiter import TokenBucket
from lineageheal.llm.types import LLMError

FAILING_NODE = "slv_amfi__nav.nav_inr"
RAW_NODE = "raw.amfi.navall.nav"
BRONZE_NODE = "brz_amfi__navall.nav_inr"


def step(**fields: Any) -> str:
    """A scripted answer to the step prompt; defaults ask for a tool call with no opinion."""
    answer: dict[str, Any] = {"confidence": 0.3, "action": "call_tool", "reason": "test"}
    answer.update(fields)
    return json.dumps(answer)


def lineage_call(node: str = RAW_NODE, **fields: Any) -> str:
    """A step that walks upstream from `node`."""
    arguments = {"node": node, "direction": "upstream", "depth": 2}
    return step(tool="get_lineage", arguments=arguments, **fields)


def conclusion(**fields: Any) -> str:
    """A scripted final answer, valid unless a test changes it."""
    answer: dict[str, Any] = {
        "root_cause_node": RAW_NODE,
        "fault_class": "schema_drift",
        "explanation": "The raw NAV column was renamed.",
        "evidence_ids": [3],
        "confidence": 0.9,
        "alternatives": [],
        "fix_location": "dbt",
        "fix_reason": "Bronze can read the new column name.",
    }
    answer.update(fields)
    return json.dumps(answer)


def build(
    script: list[str | LLMError],
    *,
    budget: Budget | None = None,
    clock: Any = None,
) -> tuple[Investigator, FakeProvider]:
    """An investigator whose model replays `script`, and the provider to inspect afterwards."""
    provider = FakeProvider("gemini", script)
    client = LLMClient(
        [Endpoint(provider, TokenBucket(6000, capacity=100))], attempts=1, sleep=lambda _: None
    )
    lineage = LineageService(market_graph())
    summary = RunSummary(run_id="r1", generated_at=RUN_AT, counts={}, signals=())
    tools = build_registry(ToolContext(lineage=lineage, manifest={"nodes": {}}, summary=summary))
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    investigator = Investigator(
        client, tools, lineage, budget=budget or Budget(12, 120.0, 0.8), **kwargs
    )
    return investigator, provider
