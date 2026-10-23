"""FR-DET-01: a dbt run's artifacts become failure signals that name the model and column."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lineageheal.core.signals import Severity, SignalKind
from lineageheal.lineage.dbt_artifacts import MAX_MESSAGE_LENGTH, run_error_summary, summarize_run

GENERATED_AT = "2026-10-22T09:40:25.358448Z"
PROJECT = "lineageheal"


def model(name: str) -> tuple[str, dict[str, Any]]:
    unique_id = f"model.{PROJECT}.{name}"
    return unique_id, {"name": name, "resource_type": "model"}


def generic_test(
    name: str, check: str, *, on: str, column: str | None = None, also: tuple[str, ...] = ()
) -> tuple[str, dict[str, Any]]:
    unique_id = f"test.{PROJECT}.{name}.abc123"
    parents = [f"model.{PROJECT}.{m}" for m in (on, *also)]
    return unique_id, {
        "name": name,
        "resource_type": "test",
        "test_metadata": {"name": check, "kwargs": {}},
        "attached_node": f"model.{PROJECT}.{on}",
        "column_name": column,
        "depends_on": {"nodes": parents},
    }


def singular_test(name: str, *, reads: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    unique_id = f"test.{PROJECT}.{name}"
    return unique_id, {
        "name": name,
        "resource_type": "test",
        "depends_on": {"nodes": [f"model.{PROJECT}.{m}" for m in reads]},
    }


def artifacts(
    nodes: list[tuple[str, dict[str, Any]]], results: list[tuple[str, str, int | None, str]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A run_results and a manifest: results are (unique_id, status, failures, message)."""
    run_results = {
        "metadata": {"generated_at": GENERATED_AT, "invocation_id": "inv-1"},
        "results": [
            {"unique_id": uid, "status": status, "failures": failures, "message": message}
            for uid, status, failures, message in results
        ],
    }
    return run_results, {"nodes": dict(nodes)}


BRONZE_ID, BRONZE = model("brz_amfi__navall")
SILVER_ID, SILVER = model("slv_amfi__nav")
GOLD_ID, GOLD = model("gld_mf_nav_daily")
NULL_ID, NULL_TEST = generic_test(
    "not_null_brz_amfi__navall_nav_inr", "not_null", on="brz_amfi__navall", column="nav_inr"
)
FRESH_ID, FRESH_TEST = generic_test(
    "fresh_within_slv_amfi__nav_nav_date__3", "fresh_within", on="slv_amfi__nav", column=None
)
MATCH_ID, MATCH_TEST = singular_test(
    "assert_gld_mf_nav_daily_match_silver", reads=("gld_mf_nav_daily", "slv_amfi__nav")
)
NODES = [
    (BRONZE_ID, BRONZE),
    (SILVER_ID, SILVER),
    (GOLD_ID, GOLD),
    (NULL_ID, NULL_TEST),
    (FRESH_ID, FRESH_TEST),
    (MATCH_ID, MATCH_TEST),
]


def summarize(results: list[tuple[str, str, int | None, str]], run_id: str = "run-1"):
    run_results, manifest = artifacts(NODES, results)
    return summarize_run(run_results, manifest, run_id=run_id)


# --- what is and is not a signal ---------------------------------------------------------


def test_a_clean_run_has_no_signals_and_counts_each_status() -> None:
    summary = summarize([(BRONZE_ID, "success", None, "OK"), (NULL_ID, "pass", 0, "")])

    assert summary.signals == ()
    assert summary.counts == {"success": 1, "pass": 1}
    assert not summary.has_failures


def test_a_failed_test_names_its_model_and_column_and_how_many_rows_failed() -> None:
    summary = summarize([(NULL_ID, "fail", 1, "Got 1 result, configured to fail if != 0")])

    (signal,) = summary.signals
    assert signal.kind is SignalKind.TEST_FAILED
    assert signal.severity is Severity.ERROR
    assert (signal.model, signal.column, signal.check_type) == (
        "brz_amfi__navall",
        "nav_inr",
        "not_null",
    )
    assert signal.models == ("brz_amfi__navall",)
    assert signal.failures == 1
    assert signal.name == "not_null_brz_amfi__navall_nav_inr"
    assert signal.unique_id == NULL_ID
    assert summary.has_failures


def test_a_test_that_only_warns_is_a_warning_and_does_not_count_as_a_failure() -> None:
    summary = summarize([(FRESH_ID, "warn", 1, "Got 1 result, configured to warn if != 0")])

    (signal,) = summary.signals
    assert (signal.kind, signal.severity) == (SignalKind.TEST_WARNING, Severity.WARN)
    assert signal.check_type == "fresh_within"
    assert signal.column is None
    assert not summary.has_failures


def test_a_test_that_could_not_run_is_an_error() -> None:
    summary = summarize([(NULL_ID, "error", None, "Runtime Error in test")])

    (signal,) = summary.signals
    assert (signal.kind, signal.severity) == (SignalKind.TEST_ERROR, Severity.ERROR)
    assert signal.failures is None


def test_a_model_that_errored_is_a_model_error_naming_the_model() -> None:
    summary = summarize(
        [(BRONZE_ID, "error", None, "Runtime Error in model brz_amfi__navall\n  Binder Error")]
    )

    (signal,) = summary.signals
    assert signal.kind is SignalKind.MODEL_ERROR
    assert (signal.model, signal.models, signal.check_type, signal.column) == (
        "brz_amfi__navall",
        ("brz_amfi__navall",),
        "model",
        None,
    )
    assert "Binder Error" in signal.message


def test_a_singular_test_has_no_kind_and_names_every_model_it_reads() -> None:
    summary = summarize([(MATCH_ID, "fail", 3, "Got 3 results")])

    (signal,) = summary.signals
    assert signal.check_type == "singular"
    assert signal.models == ("gld_mf_nav_daily", "slv_amfi__nav")
    assert signal.model == "gld_mf_nav_daily"  # the first of the models it reads, in order
    assert signal.column is None


def test_models_skipped_because_of_a_failure_are_listed_but_are_not_signals() -> None:
    summary = summarize(
        [
            (NULL_ID, "fail", 1, "x"),
            (SILVER_ID, "skipped", None, ""),
            (GOLD_ID, "skipped", None, ""),
            (FRESH_ID, "skipped", None, ""),  # a skipped test says nothing about a model
        ]
    )

    assert summary.skipped_models == ("gld_mf_nav_daily", "slv_amfi__nav")
    assert len(summary.signals) == 1
    assert summary.counts["skipped"] == 3


# --- details -----------------------------------------------------------------------------


def test_errors_come_first_then_warnings_in_a_stable_order() -> None:
    summary = summarize(
        [(FRESH_ID, "warn", 1, ""), (NULL_ID, "fail", 1, ""), (BRONZE_ID, "error", None, "")]
    )

    assert [(s.severity.value, s.kind.value) for s in summary.signals] == [
        ("error", "model_error"),
        ("error", "test_failed"),
        ("warn", "test_warning"),
    ]


def test_signals_carry_the_run_id_and_the_time_dbt_reported() -> None:
    summary = summarize([(NULL_ID, "fail", 1, "")], run_id="abc")

    assert summary.run_id == summary.signals[0].run_id == "abc"
    assert summary.dbt_invocation_id == "inv-1"
    assert summary.generated_at == datetime(2026, 10, 22, 9, 40, 25, 358448, tzinfo=UTC)
    assert summary.signals[0].detected_at == summary.generated_at


def test_a_long_message_is_cut_so_a_record_stays_small() -> None:
    summary = summarize([(BRONZE_ID, "error", None, "x" * (MAX_MESSAGE_LENGTH + 500))])

    assert len(summary.signals[0].message) == MAX_MESSAGE_LENGTH


def test_a_result_for_a_node_missing_from_the_manifest_still_gives_a_signal() -> None:
    results = [("test.lineageheal.not_null_x_y.def456", "fail", 2, "boom")]
    run_results, manifest = artifacts([], results)

    summary = summarize_run(run_results, manifest, run_id="r")

    (signal,) = summary.signals
    assert signal.name == "not_null_x_y"
    assert signal.models == ()
    assert signal.model is None


def test_the_same_artifacts_always_give_the_same_summary() -> None:
    results = [
        (NULL_ID, "fail", 1, "x"),
        (FRESH_ID, "warn", 1, "y"),
        (SILVER_ID, "skipped", None, ""),
    ]

    assert summarize(results) == summarize(results)


def test_a_run_that_wrote_no_results_is_still_one_error_signal() -> None:
    at = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)

    summary = run_error_summary("run-9", "Cannot open file", at=at)

    (signal,) = summary.signals
    assert (signal.kind, signal.severity, signal.name) == (
        SignalKind.RUN_ERROR,
        Severity.ERROR,
        "dbt",
    )
    assert signal.message == "Cannot open file"
    assert summary.counts == {}
    assert summary.has_failures
