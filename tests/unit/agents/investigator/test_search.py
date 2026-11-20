"""Seeding and the frontier."""

from datetime import UTC, datetime

import pytest
from detector_graphs import failing, market_graph

from lineageheal.agents.detector.classify import Verdict
from lineageheal.agents.detector.grouping import SignalGroup
from lineageheal.agents.investigator import search
from lineageheal.core.signals import Severity, Signal, SignalKind
from lineageheal.lineage.service import LineageService


def group_of(*signals: Signal, root: str | None = "slv_amfi__nav.nav_inr") -> SignalGroup:
    return SignalGroup(signals, root, (), (), ())


VERDICT = Verdict("nulls", 0.6, "Blank NAVs", "rules")


def test_seeding_asks_for_knowledge_the_failing_check_and_the_lineage() -> None:
    calls = search.seed_calls(group_of(failing("slv_amfi__nav", "nav_inr")), VERDICT)

    assert [name for name, _ in calls] == ["search_knowledge", "get_test_failure", "get_lineage"]
    assert calls[2][1]["direction"] == "upstream"


def test_a_failed_download_is_looked_up_in_the_ingestion_log() -> None:
    download = Signal(
        kind=SignalKind.INGESTION_ERROR,
        severity=Severity.ERROR,
        run_id="r",
        detected_at=datetime(2026, 10, 16, tzinfo=UTC),
        name="amfi/navall/2026-10-15",
        check_type="ingestion_error",
        models=(),
        message="HTTP 500",
    )

    calls = search.seed_calls(group_of(download, root=None), VERDICT)

    assert calls == [
        ("search_knowledge", {"query": "Blank NAVs", "limit": 3}),
        (
            "get_ingestion_log",
            {"source": "amfi", "dataset": "navall", "trade_date": "2026-10-15"},
        ),
    ]


def test_only_the_first_few_failing_checks_are_seeded() -> None:
    many = [failing("slv_amfi__nav", "nav_inr", name=f"t{i}") for i in range(6)]

    calls = search.seed_calls(group_of(*many), VERDICT)

    assert [n for n, _ in calls].count("get_test_failure") == search.MAX_SEED_CHECKS


def test_the_frontier_starts_at_the_failure_and_goes_upstream() -> None:
    service = LineageService(market_graph())

    nodes = search.frontier(service, "slv_amfi__nav.nav_inr", set())

    assert nodes[0] == ("slv_amfi__nav.nav_inr", 0)
    assert [n for n, _ in nodes][:3] == [
        "slv_amfi__nav.nav_inr",
        "brz_amfi__navall.nav_inr",
        "raw.amfi.navall.nav",
    ]
    assert [d for _, d in nodes] == sorted(d for _, d in nodes)


def test_refuted_nodes_are_left_out_and_no_start_means_no_frontier() -> None:
    service = LineageService(market_graph())

    nodes = search.frontier(service, "slv_amfi__nav.nav_inr", {"brz_amfi__navall.nav_inr"})

    assert "brz_amfi__navall.nav_inr" not in [n for n, _ in nodes]
    assert search.frontier(service, None, set()) == []


def test_raw_nodes_stay_on_offer_past_the_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "FRONTIER_SIZE", 2)
    service = LineageService(market_graph())

    nodes = [n for n, _ in search.frontier(service, "gld_mf_nav_daily.nav_inr", set())]

    assert "raw.amfi.navall.nav" in nodes
    assert len(nodes) == 3
