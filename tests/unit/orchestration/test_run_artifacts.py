"""FR-WH-07: a dbt run's artifacts and failure signals are kept, whether or not it succeeded."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lineageheal.core.errors import ArtifactError, StorageError
from lineageheal.core.signals import SignalKind
from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.orchestration.run_artifacts import (
    load_run_summary,
    record_dbt_run,
    run_prefix,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
RUN = "5b1f2c3e-1111-4222-8333-444455556666"

MANIFEST = {
    "nodes": {
        "model.p.brz_amfi__navall": {"name": "brz_amfi__navall", "resource_type": "model"},
        "test.p.not_null_brz_amfi__navall_nav_inr.abc": {
            "name": "not_null_brz_amfi__navall_nav_inr",
            "resource_type": "test",
            "test_metadata": {"name": "not_null"},
            "attached_node": "model.p.brz_amfi__navall",
            "column_name": "nav_inr",
            "depends_on": {"nodes": ["model.p.brz_amfi__navall"]},
        },
    }
}
RUN_RESULTS = {
    "metadata": {"generated_at": "2026-10-22T09:40:25.358448Z", "invocation_id": "inv"},
    "results": [
        {
            "unique_id": "model.p.brz_amfi__navall",
            "status": "success",
            "failures": None,
            "message": "OK",
        },
        {
            "unique_id": "test.p.not_null_brz_amfi__navall_nav_inr.abc",
            "status": "fail",
            "failures": 1,
            "message": "Got 1 result",
        },
    ],
}


class RecordingStore(LocalObjectStore):
    """A local store that remembers the order of its writes."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writes: list[str] = []

    def put(self, key: str, data: bytes, *, metadata: dict[str, str]) -> None:
        self.writes.append(key)
        super().put(key, data, metadata=metadata)


@pytest.fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path / "zone")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    folder = tmp_path / "target"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(MANIFEST))
    (folder / "run_results.json").write_text(json.dumps(RUN_RESULTS))
    return folder


def test_a_run_s_artifacts_and_summary_are_kept_under_its_own_folder(
    store: RecordingStore, target: Path
) -> None:
    summary = record_dbt_run(store, RUN, target, recorded_at=NOW)

    prefix = f"runs/{RUN}"
    assert json.loads(store.get(f"{prefix}/manifest.json")) == MANIFEST
    assert json.loads(store.get(f"{prefix}/run_results.json")) == RUN_RESULTS
    assert [s.kind for s in summary.signals] == [SignalKind.TEST_FAILED]
    assert load_run_summary(store, RUN) == summary


def test_the_summary_is_written_last_so_its_presence_means_the_folder_is_complete(
    store: RecordingStore, target: Path
) -> None:
    record_dbt_run(store, RUN, target, recorded_at=NOW)

    assert [key.rsplit("/", 1)[1] for key in store.writes] == [
        "manifest.json",
        "run_results.json",
        "signals.json",
    ]


def test_the_stored_copy_is_the_file_dbt_wrote_byte_for_byte(
    store: RecordingStore, target: Path
) -> None:
    record_dbt_run(store, RUN, target, recorded_at=NOW)

    assert store.get(f"runs/{RUN}/run_results.json") == (target / "run_results.json").read_bytes()


def test_a_run_that_wrote_no_results_still_gets_a_summary_with_a_run_error(
    store: RecordingStore, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    summary = record_dbt_run(store, RUN, empty, recorded_at=NOW, error="Cannot open the database")

    (signal,) = summary.signals
    assert signal.kind is SignalKind.RUN_ERROR
    assert signal.message == "Cannot open the database"
    assert signal.detected_at == NOW
    assert store.writes == [f"runs/{RUN}/signals.json"]  # nothing to copy


def test_a_run_that_stopped_without_a_reason_says_so(store: RecordingStore, tmp_path: Path) -> None:
    summary = record_dbt_run(store, RUN, tmp_path / "missing", recorded_at=NOW)

    assert "without writing run_results.json" in summary.signals[0].message


def test_an_artifact_that_is_not_valid_json_is_an_error_not_a_silent_gap(
    store: RecordingStore, target: Path
) -> None:
    (target / "run_results.json").write_text("{not json")

    with pytest.raises(ArtifactError, match=r"run_results\.json"):
        record_dbt_run(store, RUN, target, recorded_at=NOW)
    assert store.writes == []


def test_an_artifact_that_is_not_a_json_object_is_rejected(
    store: RecordingStore, target: Path
) -> None:
    (target / "manifest.json").write_text("[1, 2]")

    with pytest.raises(ArtifactError, match="not a JSON object"):
        record_dbt_run(store, RUN, target, recorded_at=NOW)


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "a b", "x" * 65, "run;id"])
def test_a_run_id_that_is_not_a_plain_identifier_is_refused(bad: str) -> None:
    with pytest.raises(ArtifactError, match="usable run id"):
        run_prefix(bad)


def test_there_is_no_summary_for_a_run_that_was_never_recorded(store: RecordingStore) -> None:
    with pytest.raises(StorageError):
        load_run_summary(store, RUN)


def test_a_summary_that_is_damaged_is_reported_as_such(store: RecordingStore) -> None:
    store.put(f"runs/{RUN}/signals.json", b'{"run_id": 5}', metadata={})

    with pytest.raises(ArtifactError, match="Invalid signals summary"):
        load_run_summary(store, RUN)


def test_two_runs_do_not_overwrite_each_other(store: RecordingStore, target: Path) -> None:
    record_dbt_run(store, "run-a", target, recorded_at=NOW)
    (target / "run_results.json").write_text(json.dumps({**RUN_RESULTS, "results": []}))
    record_dbt_run(store, "run-b", target, recorded_at=NOW)

    assert len(load_run_summary(store, "run-a").signals) == 1
    assert load_run_summary(store, "run-b").signals == ()
