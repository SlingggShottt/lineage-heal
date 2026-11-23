"""The incidents `seed-demo` stores, described as plain data.

Each scenario is built from the project itself, so it stays consistent with it: node names come
from the lineage graph, test ids from the compiled manifest, runbook hits from `docs/runbooks/`,
the calendar answer from the real calendar seed, and a patch is the real model file with the
edit applied. A scenario that no longer fits (a model was renamed) fails loudly instead of
storing something that points at nothing.

Nothing here claims work a model did not do: the investigations record zero model calls.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from lineageheal.agents.investigator.models import (
    Conclusion,
    Evidence,
    Investigation,
    InvestigationStatus,
)
from lineageheal.core.errors import LineageHealError
from lineageheal.core.signals import Severity, Signal, SignalKind
from lineageheal.ingestion.calendar import expectation, read_seed
from lineageheal.lineage.graph import LineageGraph
from lineageheal.lineage.service import LineageService
from lineageheal.rag.chunking import parse_runbook

# Marks a seeded incident's fingerprint; `reset` finds them by it. The dashboard never shows it.
FINGERPRINT_PREFIX = "seed:"
# Stands in for the model and prompt a real run would record; no model made these diagnoses.
PROVENANCE = "seed"
NAMESPACE = uuid.UUID("5d0a2b7e-5b1e-4f5e-9a54-0d0f6c1d2a11")
BRONZE_AMFI = "dbt/models/bronze/brz_amfi__navall.sql"
BRONZE_NSE = "dbt/models/bronze/brz_nse__cm_bhavcopy.sql"
PRICE_COLUMNS = (
    ("OpnPric", "open_price_inr"),
    ("HghPric", "high_price_inr"),
    ("LwPric", "low_price_inr"),
    ("ClsPric", "close_price_inr"),
    ("LastPric", "last_price_inr"),
    ("PrvsClsgPric", "prev_close_price_inr"),
    ("SttlmPric", "settlement_price_inr"),
)


class ScenarioError(LineageHealError):
    """The project no longer matches what a scenario expects, so it cannot be seeded."""


@dataclass(frozen=True)
class Edit:
    """Replace one exact piece of text in a file."""

    old: str
    new: str


@dataclass(frozen=True)
class AttemptPlan:
    """One patch the Fixer tried and how the sandbox judged it."""

    path: str
    edits: tuple[Edit, ...]
    sandbox: dict[str, Any]
    passed: bool


@dataclass(frozen=True)
class Scenario:
    """One incident with everything the dashboard shows about it."""

    key: str
    opened_at: datetime
    run_id: str
    run_at: datetime
    skipped_models: tuple[str, ...]
    signals: tuple[Signal, ...]
    summary: str
    severity: str
    root_node: str
    affected: dict[str, Any]
    fault_class: str
    confidence: float
    investigation: Investigation
    attempts: tuple[AttemptPlan, ...] = ()
    publish_number: int | None = None  # a pull request (for a patch) or issue (for a suggestion)
    suggestion: dict[str, Any] | None = None


def build_scenarios(
    *,
    now: datetime,
    graph: LineageGraph,
    manifest: Mapping[str, Any],
    repo_root: Path,
    first_number: int,
) -> list[Scenario]:
    """The three scenarios, dated relative to `now`.

    Args:
        now: The current time; incidents are opened hours and days before it.
        graph: The lineage graph of the project, built from its manifest.
        manifest: The compiled dbt manifest the graph came from.
        repo_root: The repository, for runbooks, the calendar seed and the models a patch edits.
        first_number: The pull request or issue number given to the first scenario.

    Raises:
        ScenarioError: A node, test or file a scenario names is not in the project.
    """
    world = _World(now, graph, manifest, repo_root)
    return [
        _amfi_schema_drift(world, first_number),
        _nse_unit_change(world),
        _amfi_late_data(world, first_number + 1),
    ]


class _World:
    """What the scenarios look things up in."""

    def __init__(
        self, now: datetime, graph: LineageGraph, manifest: Mapping[str, Any], repo_root: Path
    ) -> None:
        self.now = now.astimezone(UTC)
        self.graph = graph
        self.service = LineageService(graph)
        self.manifest = manifest
        self.repo_root = repo_root
        self._nodes = {node.id for node in graph.nodes}

    def node(self, node_id: str) -> str:
        if node_id not in self._nodes:
            raise ScenarioError(f"The lineage graph has no node {node_id!r}")
        return node_id

    def unique_id(self, name: str, resource_type: str) -> str:
        for unique_id, node in self.manifest.get("nodes", {}).items():
            if node.get("name") == name and node.get("resource_type") == resource_type:
                return str(unique_id)
        raise ScenarioError(f"The manifest has no {resource_type} named {name!r}")

    def model_id(self, name: str) -> str:
        return self.unique_id(name, "model")

    def test_id(self, name: str) -> str:
        return self.unique_id(name, "test")

    def runbook_hits(
        self, slug: str, headings: tuple[str, ...], scores: tuple[float, ...]
    ) -> dict[str, Any]:
        path = self.repo_root / "docs" / "runbooks" / f"{slug}.md"
        try:
            parsed = parse_runbook(slug, path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ScenarioError(f"Cannot read the runbook {slug}: {exc}") from exc
        chunks = {chunk.heading: chunk for chunk in parsed.chunks}
        missing = [h for h in headings if h not in chunks]
        if missing:
            raise ScenarioError(f"The runbook {slug} has no section {missing[0]!r}")
        return {
            "hits": [
                {
                    "source": "runbook",
                    "document": slug,
                    "heading": heading,
                    "fault_class": parsed.fault_class,
                    "score": score,
                    "text": chunks[heading].text[:1200],
                }
                for heading, score in zip(headings, scores, strict=True)
            ]
        }

    def lineage_payload(self, node: str, depth: int) -> dict[str, Any]:
        traversal = self.service.upstream(self.node(node), depth)
        return {
            "node": node,
            "direction": "upstream",
            "lineage_version": self.service.version,
            "inferred": traversal.inferred,
            "reached": [{"node": r.node, "distance": r.distance} for r in traversal.reached],
            "reached_omitted": 0,
            "edges": [
                f"{e.source} -> {e.target} ({e.kind.value}{', inferred' if e.inferred else ''})"
                for e in traversal.edges
            ],
            "edges_omitted": 0,
        }

    def raw_columns(self, table: str) -> list[str]:
        prefix = f"{table}."
        return sorted(
            node[len(prefix) :]
            for node in self._nodes
            if node.startswith(prefix)
            and node[len(prefix) :] not in ("*", "filename", "trade_date")
        )

    def test_signal(
        self,
        run_at: datetime,
        run_id: str,
        *,
        name: str,
        check: str,
        model: str,
        column: str | None,
        failures: int,
        severity: Severity = Severity.ERROR,
    ) -> Signal:
        kind = SignalKind.TEST_FAILED if severity is Severity.ERROR else SignalKind.TEST_WARNING
        return Signal(
            kind=kind,
            severity=severity,
            run_id=run_id,
            detected_at=run_at,
            name=name,
            unique_id=self.test_id(name),
            check_type=check,
            models=(model,),
            model=model,
            column=column,
            failures=failures,
            message=f"Got {failures} result{'s' if failures != 1 else ''}, configured to fail"
            if severity is Severity.ERROR
            else f"Got {failures} result{'s' if failures != 1 else ''}, configured to warn",
        )


def _trading_day(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _run_id(key: str, day: date) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{key}:{day.isoformat()}"))


def _investigation(
    opened_at: datetime, evidence: list[Evidence], conclusion: Conclusion
) -> Investigation:
    return Investigation(
        status=InvestigationStatus.CONCLUDED,
        conclusion=conclusion,
        partial=None,
        evidence=evidence,
        llm_calls=0,
        elapsed_seconds=float(len(evidence) * 7 + 11),
        model_ids={PROVENANCE: PROVENANCE},
        prompt_hashes={PROVENANCE: "0" * 64},
    )


def _evidence(
    opened_at: datetime,
    number: int,
    tool: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    *,
    node: str | None = None,
    verdict: str | None = None,
) -> Evidence:
    return Evidence(
        id=number,
        tool=tool,
        arguments=arguments,
        ok=True,
        content="",
        payload=payload,
        node=node,
        verdict=verdict,  # type: ignore[arg-type]
        collected_at=opened_at + timedelta(seconds=80 + number * 9),
    )


def _sandbox(
    selector: str,
    before: dict[str, str],
    after: dict[str, str],
    *,
    reason: str,
    seconds: float,
) -> dict[str, Any]:
    bad = {"fail", "error", "skipped", "missing"}
    failed = [n for n, s in before.items() if s in bad]
    still = [n for n in failed if after.get(n, "missing") in bad]
    regressions = [n for n, s in before.items() if s not in bad and after.get(n, "missing") in bad]
    return {
        "passed": not still and not regressions and bool(failed),
        "reason": reason,
        "selector": selector,
        "fixed": sorted(n for n in failed if n not in still),
        "still_failing": sorted(still),
        "regressions": sorted(regressions),
        "before": before,
        "after": after,
        "log_tail": "",
        "seconds": seconds,
    }


# --- 1. AMFI renames its NAV column ---------------------------------------------------------


def _amfi_schema_drift(world: _World, number: int) -> Scenario:
    opened = world.now - timedelta(hours=3, minutes=20)
    day = _trading_day(opened.date())
    previous = _trading_day(day - timedelta(days=1))
    run_id, run_at = _run_id("amfi-schema-drift", day), opened - timedelta(minutes=6)
    root = world.node("raw.amfi.navall.Net Asset Value")
    column = world.node("brz_amfi__navall.nav_inr")
    test = "not_null_brz_amfi__navall_nav_inr"
    signal = world.test_signal(
        run_at, run_id, name=test, check="not_null", model="brz_amfi__navall",
        column="nav_inr", failures=8742,
    )  # fmt: skip
    affected = {
        "models": [
            "brz_amfi__navall",
            "slv_amfi__nav",
            "gld_mf_nav_daily",
            "gld_market_daily_summary",
        ],
        "columns": [column],
        "gold_tables": ["gld_mf_nav_daily", "gld_market_daily_summary"],
    }
    columns = world.raw_columns("raw.amfi.navall")
    renamed = ["NAV" if c == "Net Asset Value" else c for c in columns]
    evidence = [
        _evidence(opened, 1, "get_test_failure", {"test": test}, {
            "name": test, "kind": "test_failed", "severity": "error", "check_type": "not_null",
            "models": ["brz_amfi__navall"], "model": "brz_amfi__navall", "column": "nav_inr",
            "failures": 8742, "message": "Got 8742 results, configured to fail", "definition": None,
        }),
        _evidence(opened, 2, "get_lineage", {"node": column, "direction": "upstream", "depth": 2},
                  world.lineage_payload(column, 2)),
        _evidence(opened, 3, "compare_schemas",
                  {"source": "amfi", "dataset": "navall", "date_a": str(previous),
                   "date_b": str(day)},
                  {"date_a": str(previous), "date_b": str(day), "identical": False,
                   "removed": ["Net Asset Value"], "added": ["NAV"], "reordered": False,
                   "column_count": [len(columns), len(renamed)]},
                  node=root, verdict="supports"),
        _evidence(opened, 4, "search_knowledge",
                  {"query": "not_null fails on every row of one file after a column was renamed",
                   "fault_class": "schema_drift"},
                  world.runbook_hits("schema-drift", ("Diagnosis", "Fix"), (0.0328, 0.0161))),
    ]  # fmt: skip
    conclusion = Conclusion(
        root_cause_node=root,
        fault_class="schema_drift",
        explanation=(
            f'AMFI\'s report for {day} publishes the NAV under the header "NAV" instead of '
            '"Net Asset Value". The bronze model selects columns by header name, so every row '
            "loads with a null NAV and the not_null test fails on all 8,742 rows. The row count "
            "is normal, which is why the volume monitor stayed quiet."
        ),
        evidence_ids=[1, 2, 3],
        confidence=0.93,
        fix_location="dbt",
        fix_reason="Bronze can read the new header while still reading the old one.",
    )
    models = {
        "brz": world.model_id("brz_amfi__navall"),
        "slv": world.model_id("slv_amfi__nav"),
        "gld": world.model_id("gld_mf_nav_daily"),
        "sum": world.model_id("gld_market_daily_summary"),
        "test": world.test_id(test),
    }
    before = {
        models["brz"]: "success", models["test"]: "fail", models["slv"]: "skipped",
        models["gld"]: "skipped", models["sum"]: "skipped",
    }  # fmt: skip
    after = {name: ("pass" if name == models["test"] else "success") for name in before}
    attempt = AttemptPlan(
        BRONZE_AMFI,
        (
            Edit(
                '        try_cast(trim("Net Asset Value") as decimal(18, 4)) as nav_inr,',
                "        try_cast(\n"
                '            coalesce(trim("NAV"), trim("Net Asset Value")) as decimal(18, 4)\n'
                "        ) as nav_inr,",
            ),
        ),
        _sandbox(
            "brz_amfi__navall+", before, after,
            reason="fixed 1 failing node(s) and broke nothing", seconds=41.8,
        ),
        True,
    )  # fmt: skip
    return Scenario(
        key="amfi-schema-drift",
        opened_at=opened,
        run_id=run_id,
        run_at=run_at,
        skipped_models=("slv_amfi__nav", "gld_mf_nav_daily", "gld_market_daily_summary"),
        signals=(signal,),
        summary="not_null on brz_amfi__navall.nav_inr (8742 rows)",
        severity="error",
        root_node=root,
        affected=affected,
        fault_class="schema_drift",
        confidence=0.93,
        investigation=_investigation(opened, evidence, conclusion),
        attempts=(attempt,),
        publish_number=number,
    )


# --- 2. NSE prices arrive in paise ----------------------------------------------------------


def _nse_unit_change(world: _World) -> Scenario:
    opened = world.now - timedelta(days=1, hours=4, minutes=10)
    day = _trading_day(opened.date())
    previous = _trading_day(day - timedelta(days=1))
    run_id, run_at = _run_id("nse-unit-change", day), opened - timedelta(minutes=5)
    root = world.node("brz_nse__cm_bhavcopy.close_price_inr")
    column = world.node("slv_nse__equity_eod.close_price_inr")
    test = "distribution_anomaly_slv_nse__equity_eod_nse__close_price_inr__price_date__0_2"
    signal = world.test_signal(
        run_at, run_id, name=test, check="distribution_anomaly", model="slv_nse__equity_eod",
        column="close_price_inr", failures=1,
    )  # fmt: skip
    affected = {
        "models": ["brz_nse__cm_bhavcopy", "slv_nse__equity_eod", "gld_equity_daily_prices",
                   "gld_market_daily_summary"],
        "columns": [column],
        "gold_tables": ["gld_equity_daily_prices", "gld_market_daily_summary"],
    }  # fmt: skip
    schema = world.raw_columns("raw.nse.cm_bhavcopy")
    profile_args = {"model": "slv_nse__equity_eod", "column": "close_price_inr",
                    "date_a": str(previous), "date_b": str(day)}  # fmt: skip
    evidence = [
        _evidence(opened, 1, "get_test_failure", {"test": test}, {
            "name": test, "kind": "test_failed", "severity": "error",
            "check_type": "distribution_anomaly", "models": ["slv_nse__equity_eod"],
            "model": "slv_nse__equity_eod", "column": "close_price_inr", "failures": 1,
            "message": "Got 1 result, configured to fail", "definition": None,
        }),
        _evidence(opened, 2, "compare_profiles", profile_args, {
            "model": "slv_nse__equity_eod", "column": "close_price_inr",
            str(previous): {"rows": 2417, "nulls": 0, "null_share": 0.0, "distinct": 2203,
                            "min": "0.0500", "max": "73905.0000", "mean": "1871.4213"},
            str(day): {"rows": 2422, "nulls": 0, "null_share": 0.0, "distinct": 2209,
                       "min": "5.0000", "max": "7390500.0000", "mean": "187142.1305"},
            "change": {"rows_ratio": 1.002069, "null_share_change": 0.0,
                       "mean_ratio": 100.0, "max_ratio": 100.0},
        }, node=root, verdict="supports"),
        _evidence(opened, 3, "get_raw_schema", {"source": "nse", "dataset": "cm_bhavcopy",
                                                "trade_date": str(day)}, {
            "file": (
                f"nse/cm_bhavcopy/trade_date={day}/"
                f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
            ),
            "column_count": len(schema), "columns": schema,
        }, node="raw.nse.cm_bhavcopy.ClsPric", verdict="refutes"),
        _evidence(opened, 4, "search_knowledge",
                  {"query": "every price is about 100 times larger than the previous day",
                   "fault_class": "unit_change"},
                  world.runbook_hits("unit-change", ("Symptoms", "Diagnosis"), (0.0328, 0.0161))),
    ]  # fmt: skip
    conclusion = Conclusion(
        root_cause_node=root,
        fault_class="unit_change",
        explanation=(
            f"From {day} the bhavcopy prices arrive in paise: the mean close price is 100 times "
            "the previous day's and so is the maximum, while the row count and the column names "
            "are unchanged. No corporate action explains a hundredfold move across every "
            "instrument, so the values are in the wrong unit rather than the market having moved."
        ),
        evidence_ids=[1, 2, 3],
        confidence=0.9,
        fix_location="dbt",
        fix_reason="Bronze can convert prices to rupees from the first affected date.",
        alternatives=[],
    )
    models = {m: world.model_id(m) for m in (
        "brz_nse__cm_bhavcopy", "slv_nse__equity_eod", "gld_equity_daily_prices",
        "gld_market_daily_summary",
    )}  # fmt: skip
    tests = {t: world.test_id(t) for t in (
        test, "assert_brz_nse__cm_bhavcopy_prices_within_day_range",
        "assert_brz_nse__cm_bhavcopy_traded_instruments_have_a_price",
    )}  # fmt: skip
    before = {
        models["brz_nse__cm_bhavcopy"]: "success", models["slv_nse__equity_eod"]: "success",
        tests[test]: "fail", models["gld_equity_daily_prices"]: "skipped",
        models["gld_market_daily_summary"]: "skipped",
        tests["assert_brz_nse__cm_bhavcopy_prices_within_day_range"]: "pass",
        tests["assert_brz_nse__cm_bhavcopy_traded_instruments_have_a_price"]: "pass",
    }  # fmt: skip
    within_range = tests["assert_brz_nse__cm_bhavcopy_prices_within_day_range"]
    first_after = {
        **dict.fromkeys(models.values(), "success"),
        **dict.fromkeys(tests.values(), "pass"),
        within_range: "fail",  # rescaling only the close puts it outside the day's low and high
    }
    second_after = {**dict.fromkeys(models.values(), "success"),
                    **dict.fromkeys(tests.values(), "pass")}  # fmt: skip
    only_close = (
        Edit(
            '        try_cast(trim("ClsPric") as decimal(18, 4)) as close_price_inr,',
            '        try_cast(trim("ClsPric") as decimal(18, 4))\n'
            f"        / case when _trade_date >= date '{day}' then 100 else 1 end\n"
            "        as close_price_inr,",
        ),
    )
    every_price = (
        Edit(
            "with\n\n-- The folder's",
            f"{{% set paise_from = '{day}' %}}\n\nwith\n\n-- The folder's",
        ),
        *(
            Edit(
                f'        try_cast(trim("{raw}") as decimal(18, 4)) as {name},',
                f'        try_cast(trim("{raw}") as decimal(18, 4))\n'
                "        / case when _trade_date >= date '{{ paise_from }}'"
                " then 100 else 1 end\n"
                f"        as {name},",
            )
            for raw, name in PRICE_COLUMNS
        ),
    )
    attempts = (
        AttemptPlan(
            BRONZE_NSE, only_close,
            _sandbox("brz_nse__cm_bhavcopy+", before, first_after,
                     reason="the patch breaks 1 node(s) that passed before", seconds=47.2),
            False,
        ),
        AttemptPlan(
            BRONZE_NSE, every_price,
            _sandbox("brz_nse__cm_bhavcopy+", before, second_after,
                     reason="fixed 1 failing node(s) and broke nothing", seconds=52.6),
            True,
        ),
    )  # fmt: skip
    return Scenario(
        key="nse-unit-change",
        opened_at=opened,
        run_id=run_id,
        run_at=run_at,
        skipped_models=("gld_equity_daily_prices", "gld_market_daily_summary"),
        signals=(signal,),
        summary="distribution_anomaly on slv_nse__equity_eod.close_price_inr (1 rows)",
        severity="error",
        root_node=root,
        affected=affected,
        fault_class="unit_change",
        confidence=0.9,
        investigation=_investigation(opened, evidence, conclusion),
        attempts=attempts,
        publish_number=None,
    )


# --- 3. AMFI's report for a weekday never arrives -------------------------------------------


def _amfi_late_data(world: _World, number: int) -> Scenario:
    opened = world.now - timedelta(days=2, hours=1, minutes=40)
    day = _trading_day(opened.date())
    run_id, run_at = _run_id("amfi-late-data", day), opened - timedelta(minutes=4)
    root = world.node("raw.amfi.navall.*")
    seed = read_seed(world.repo_root / "dbt" / "seeds" / "seed_calendar_nse.csv")
    reason, expected = expectation("amfi", day, seed)
    expected_name = "file_present_when_expected_slv_meta__calendar_"
    ingestion = Signal(
        kind=SignalKind.INGESTION_ERROR,
        severity=Severity.ERROR,
        run_id=run_id,
        detected_at=run_at,
        name=f"amfi/navall/{day}",
        check_type="ingestion",
        message="download failed, HTTP 503 Service Unavailable",
    )
    missing = world.test_signal(
        run_at, run_id, name=expected_name, check="file_present_when_expected",
        model="slv_meta__calendar", column=None, failures=1, severity=Severity.WARN,
    )  # fmt: skip
    attempts_log = [
        {"outcome": "failed", "finished_at": (opened - timedelta(hours=5, minutes=30)).isoformat(),
         "http_status": 503, "size_bytes": None, "sha256": None, "object_key": None,
         "error": "HTTP 503 Service Unavailable"},
        {"outcome": "failed", "finished_at": (opened - timedelta(hours=2, minutes=30)).isoformat(),
         "http_status": 503, "size_bytes": None, "sha256": None, "object_key": None,
         "error": "HTTP 503 Service Unavailable"},
    ]  # fmt: skip
    evidence = [
        _evidence(opened, 1, "get_test_failure", {"test": expected_name}, {
            "name": expected_name, "kind": "test_warning", "severity": "warn",
            "check_type": "file_present_when_expected", "models": ["slv_meta__calendar"],
            "model": "slv_meta__calendar", "column": None, "failures": 1,
            "message": "Got 1 result, configured to warn", "definition": None,
        }),
        _evidence(opened, 2, "get_ingestion_log",
                  {"source": "amfi", "dataset": "navall", "trade_date": str(day)},
                  {"source": "amfi", "dataset": "navall", "trade_date": str(day),
                   "attempts": attempts_log, "older_attempts_omitted": 0},
                  node=root, verdict="supports"),
        _evidence(opened, 3, "get_calendar", {"source": "amfi", "trade_date": str(day)}, {
            "source": "amfi", "trade_date": str(day), "weekday": day.strftime("%A"),
            "file_expected": expected, "reason": reason, "calendar_entry": None,
        }),
        _evidence(opened, 4, "search_knowledge",
                  {"query": "a trading day has no report and the download failed",
                   "fault_class": "late_data"},
                  world.runbook_hits("late-data", ("Symptoms", "Fix"), (0.0328, 0.0161))),
    ]  # fmt: skip
    conclusion = Conclusion(
        root_cause_node=root,
        fault_class="late_data",
        explanation=(
            f"AMFI publishes a report every day, and {day} is a {day.strftime('%A')}, so a file "
            "was expected. Both download attempts for that date ended in HTTP 503 and nothing "
            "later answered them, so no file was ever stored. The models built on the data that "
            "did arrive; the day is simply missing."
        ),
        evidence_ids=[1, 2, 3],
        confidence=0.88,
        fix_location="external",
        fix_reason=(
            "No dbt change brings back a file that was never stored; the download must be repeated."
        ),
    )
    suggestion = {
        "title": f"Download AMFI's NAV report for {day} again and retry on HTTP 503",
        "target": "The AMFI loader and the scheduled download (ingestion)",
        "change": (
            f"Run the AMFI download for {day} again once the portal answers; the loader skips days "
            "it already holds, so only this day is requested. The two earlier attempts both ended "
            "in 503, so give the loader a longer back-off for that status before it records a "
            "failure, so a short outage on AMFI's side does not leave a gap."
        ),
        "draft": f"uv run lineageheal ingest amfi --date {day}",
        "verify": (
            f"lineageheal log shows a stored attempt for amfi/navall {day}, and the next dbt build "
            "no longer warns from file_present_when_expected."
        ),
        "risks": (
            "AMFI may republish the day with different values later; silver keeps the newest one."
        ),
    }
    return Scenario(
        key="amfi-late-data",
        opened_at=opened,
        run_id=run_id,
        run_at=run_at,
        skipped_models=(),
        signals=(ingestion, missing),
        summary=f"ingestion on amfi/navall/{day} and 1 more signal",
        severity="error",
        root_node=root,
        affected={
            "models": ["slv_meta__calendar", "gld_mf_nav_daily", "gld_market_daily_summary"],
            "columns": [],
            "gold_tables": ["gld_mf_nav_daily", "gld_market_daily_summary"],
        },
        fault_class="late_data",
        confidence=0.88,
        investigation=_investigation(opened, evidence, conclusion),
        publish_number=number,
        suggestion=suggestion,
    )
