"""A small market lineage graph and a signal factory shared by the Detector's tests.

AMFI:  raw nav -> brz_amfi__navall -> slv_amfi__nav -> gld_mf_nav_daily
NSE:   raw close -> brz_nse__cm_bhavcopy -> slv_nse__equity_eod -> gld_equity_daily_prices
Both silver models are filtered by seed_calendar_nse.day (a filter edge to their row sets).
"""

from __future__ import annotations

from datetime import UTC, datetime

from lineageheal.core.signals import Severity, Signal, SignalKind
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind

RUN_AT = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)

_RAW = [
    "raw.amfi.navall.nav",
    "raw.amfi.navall.*",
    "raw.nse.cm_bhavcopy.close",
    "raw.nse.cm_bhavcopy.*",
]
_MODELS = [
    "brz_amfi__navall.nav_inr",
    "brz_amfi__navall.*",
    "slv_amfi__nav.nav_inr",
    "slv_amfi__nav.*",
    "gld_mf_nav_daily.nav_inr",
    "gld_mf_nav_daily.*",
    "brz_nse__cm_bhavcopy.close_price_inr",
    "slv_nse__equity_eod.close_price_inr",
    "slv_nse__equity_eod.*",
    "gld_equity_daily_prices.close_price_inr",
]
_EDGES = [
    ("raw.amfi.navall.nav", "brz_amfi__navall.nav_inr", EdgeKind.DIRECT),
    ("brz_amfi__navall.nav_inr", "slv_amfi__nav.nav_inr", EdgeKind.DIRECT),
    ("slv_amfi__nav.nav_inr", "gld_mf_nav_daily.nav_inr", EdgeKind.DIRECT),
    ("raw.nse.cm_bhavcopy.close", "brz_nse__cm_bhavcopy.close_price_inr", EdgeKind.DIRECT),
    (
        "brz_nse__cm_bhavcopy.close_price_inr",
        "slv_nse__equity_eod.close_price_inr",
        EdgeKind.TRANSFORM,
    ),
    (
        "slv_nse__equity_eod.close_price_inr",
        "gld_equity_daily_prices.close_price_inr",
        EdgeKind.TRANSFORM,
    ),
    ("seed_calendar_nse.day", "slv_amfi__nav.*", EdgeKind.FILTER),
    ("seed_calendar_nse.day", "slv_nse__equity_eod.*", EdgeKind.FILTER),
]


def _node(node_id: str, kind: NodeKind) -> LineageNode:
    table, column = node_id.rsplit(".", 1)
    return LineageNode(table, column, kind)


def market_graph(version: str = "v1") -> LineageGraph:
    nodes = [_node(n, NodeKind.RAW) for n in _RAW]
    nodes += [_node(n, NodeKind.MODEL) for n in _MODELS]
    nodes.append(_node("seed_calendar_nse.day", NodeKind.SEED))
    edges = [LineageEdge(a, b, kind) for a, b, kind in _EDGES]
    return LineageGraph(version, tuple(sorted(nodes)), tuple(sorted(edges)))


def failing(
    model: str | None,
    column: str | None,
    *,
    check: str = "not_null",
    name: str | None = None,
    severity: Severity = Severity.ERROR,
    kind: SignalKind = SignalKind.TEST_FAILED,
    failures: int | None = 1,
    run_id: str = "run-1",
) -> Signal:
    label = name or f"{check}_{model}_{column}"
    return Signal(
        kind=kind,
        severity=severity,
        run_id=run_id,
        detected_at=RUN_AT,
        name=label,
        check_type=check,
        models=(model,) if model else (),
        model=model,
        column=column,
        failures=failures,
        message=f"Got {failures} results\nmore detail",
    )
