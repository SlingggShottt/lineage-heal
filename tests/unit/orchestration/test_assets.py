"""FR-ORC-01: raw assets download one trade date per partition and report what happened."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import dagster as dg
import pytest
import yaml

from lineageheal.core.errors import SourceUnavailableError
from lineageheal.core.settings import get_settings
from lineageheal.ingestion.log import NullRecorder
from lineageheal.ingestion.raw_zone import StoredObject
from lineageheal.ingestion.types import IngestionOutcome, IngestionResult
from lineageheal.orchestration import resources
from lineageheal.orchestration.assets import raw_asset_key, raw_assets
from lineageheal.orchestration.definitions import defs
from lineageheal.orchestration.partitions import TIMEZONE, daily_partitions
from lineageheal.orchestration.resources import IngestionResource
from lineageheal.store.ingestion_log import IngestionAttempt

REPO_ROOT = Path(__file__).parents[3]
PARTITION = "2026-10-16"
SHA256 = "a" * 64

DATASETS = [
    ("amfi", "navall"),
    ("nse", "cm_bhavcopy"),
    ("nse", "security_master"),
    ("nse", "corporate_actions"),
]

# What the fake was asked to download, in order.
CALLS: list[tuple[str, str, date]] = []


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    CALLS.clear()


class FakeIngestion(IngestionResource):
    """Stands in for the real resource: no network, no raw zone."""

    outcome: IngestionOutcome = IngestionOutcome.STORED
    fail: bool = False

    def run(self, source: str, dataset: str, trade_date: date) -> IngestionResult:
        CALLS.append((source, dataset, trade_date))
        if self.fail:
            raise SourceUnavailableError(f"{source} is down")
        stored = None
        if self.outcome is not IngestionOutcome.NO_DATA:
            key = f"raw/source={source}/dataset={dataset}/trade_date={trade_date}/{SHA256[:16]}__f"
            stored = StoredObject(
                key=key,
                sha256=SHA256,
                size_bytes=1234,
                created=self.outcome is IngestionOutcome.STORED,
            )
        return IngestionResult(
            source=source,
            dataset=dataset,
            trade_date=trade_date,
            outcome=self.outcome,
            http_status=404 if stored is None else 200,
            stored=stored,
        )


def materialize(
    source: str, dataset: str, ingestion: IngestionResource, *, raise_on_error: bool = True
) -> dg.ExecuteInProcessResult:
    asset = next(a for a in raw_assets if a.key == raw_asset_key(source, dataset))
    return dg.materialize(
        [asset],
        partition_key=PARTITION,
        resources={"ingestion": ingestion},
        raise_on_error=raise_on_error,
    )


def materialization(result: dg.ExecuteInProcessResult) -> dg.AssetMaterialization:
    (event,) = result.get_asset_materialization_events()
    return event.step_materialization_data.materialization


# --- one asset per dataset ---------------------------------------------------------------


def test_there_is_one_raw_asset_for_each_dataset_the_loaders_offer() -> None:
    assert {key for a in raw_assets for key in a.keys} == {
        raw_asset_key(source, dataset) for source, dataset in DATASETS
    }


def test_asset_keys_are_the_names_of_the_dbt_sources_that_read_the_files() -> None:
    """LH-031 links dbt to these assets by key, so a rename on either side must be caught."""
    sources = yaml.safe_load((REPO_ROOT / "dbt/models/bronze/_bronze__sources.yml").read_text())
    dbt_keys = {
        dg.AssetKey([source["name"], table["name"]])
        for source in sources["sources"]
        if source["name"] != "raw_zone"  # reads every dataset's files, not one of them
        for table in source["tables"]
    }

    assert {key for a in raw_assets for key in a.keys} == dbt_keys


@pytest.mark.parametrize(("source", "dataset"), DATASETS)
def test_materialising_a_partition_downloads_that_trade_date(source: str, dataset: str) -> None:
    result = materialize(source, dataset, FakeIngestion())

    assert result.success
    assert [(source, dataset, date(2026, 10, 16))] == CALLS


def test_a_stored_file_is_reported_with_its_key_size_and_hash() -> None:
    result = materialize("nse", "cm_bhavcopy", FakeIngestion())

    metadata = materialization(result).metadata
    assert metadata["outcome"].value == "stored"
    assert metadata["size_bytes"].value == 1234
    assert metadata["sha256"].value == SHA256
    assert str(metadata["object_key"].value).startswith("raw/source=nse/dataset=cm_bhavcopy/")


def test_a_repeat_of_an_identical_file_is_unchanged_and_keeps_the_same_data_version() -> None:
    stored = materialization(materialize("amfi", "navall", FakeIngestion()))
    repeat = materialization(
        materialize("amfi", "navall", FakeIngestion(outcome=IngestionOutcome.UNCHANGED))
    )

    assert repeat.metadata["outcome"].value == "unchanged"
    # Same hash, same version, so nothing downstream is marked stale for a file that did not change.
    assert repeat.tags["dagster/data_version"] == stored.tags["dagster/data_version"] == SHA256


def test_a_day_with_no_file_is_a_success_that_says_so() -> None:
    """A holiday is not an error (CLAUDE.md, Data)."""
    result = materialize("nse", "cm_bhavcopy", FakeIngestion(outcome=IngestionOutcome.NO_DATA))

    assert result.success
    metadata = materialization(result).metadata
    assert metadata["outcome"].value == "no_data"
    assert metadata["http_status"].value == 404
    assert "object_key" not in metadata


def test_a_source_that_is_down_fails_the_run_with_the_reason() -> None:
    result = materialize("nse", "cm_bhavcopy", FakeIngestion(fail=True), raise_on_error=False)

    assert not result.success
    (failure,) = result.get_step_failure_events()
    error = failure.event_specific_data.error  # type: ignore[union-attr]
    assert error.cause is not None
    assert "SourceUnavailableError: nse is down" in error.cause.message


# --- partitions and pools ----------------------------------------------------------------


def test_partitions_are_indian_trade_dates_from_the_first_day_of_history() -> None:
    assert daily_partitions.get_first_partition_key() == "2024-01-01"
    assert daily_partitions.timezone == TIMEZONE == "Asia/Kolkata"
    assert daily_partitions.has_partition_key("2024-01-01")
    assert not daily_partitions.has_partition_key("2023-12-31")


def test_every_raw_asset_shares_one_pool_so_sources_are_fetched_one_step_at_a_time() -> None:
    assert {a.op.pool for a in raw_assets} == {"ingestion"}


def test_the_instance_config_limits_the_pool_to_one_and_turns_telemetry_off(tmp_path: Path) -> None:
    shutil.copy(REPO_ROOT / "dagster_home" / "dagster.yaml", tmp_path / "dagster.yaml")

    with dg.DagsterInstance.from_config(str(tmp_path)) as instance:
        assert instance.global_op_concurrency_default_limit == 1
        assert not instance.telemetry_enabled
        assert instance.run_retries_enabled
        assert instance.run_retries_max_retries == 1


def test_the_definitions_load_with_both_resources_and_the_raw_assets() -> None:
    assert set(defs.resources) == {"ingestion", "dbt"}
    assert {raw_asset_key(source, dataset) for source, dataset in DATASETS} <= set(
        defs.resolve_all_asset_keys()
    )


# --- the real resource -------------------------------------------------------------------


class RecordingRecorder(NullRecorder):
    def __init__(self) -> None:
        self.attempts: list[IngestionAttempt] = []
        self.closed = False

    def record(self, attempt: IngestionAttempt) -> None:
        self.attempts.append(attempt)

    def close(self) -> None:
        self.closed = True


class StubLoader:
    source = "amfi"
    dataset = "navall"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def load(self, trade_date: date) -> IngestionResult:
        if self.fail:
            raise SourceUnavailableError("amfi is down")
        return IngestionResult(
            source=self.source,
            dataset=self.dataset,
            trade_date=trade_date,
            outcome=IngestionOutcome.NO_DATA,
            http_status=404,
            stored=None,
        )


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> RecordingRecorder:
    recorder = RecordingRecorder()
    monkeypatch.setattr(resources, "open_recorder", lambda settings: recorder)
    return recorder


def stub_loader(monkeypatch: pytest.MonkeyPatch, loader: StubLoader) -> None:
    @contextmanager
    def open_loader(settings: object, source: str, dataset: str) -> Iterator[StubLoader]:
        assert (source, dataset) == ("amfi", "navall")
        yield loader

    monkeypatch.setattr(resources, "open_loader", open_loader)


def test_the_real_resource_logs_each_attempt_as_the_command_line_does(
    monkeypatch: pytest.MonkeyPatch, recorder: RecordingRecorder
) -> None:
    get_settings.cache_clear()
    stub_loader(monkeypatch, StubLoader())

    result = IngestionResource().run("amfi", "navall", date(2026, 10, 16))

    assert result.outcome is IngestionOutcome.NO_DATA
    assert [(a.source, a.dataset, a.outcome) for a in recorder.attempts] == [
        ("amfi", "navall", "no_data")
    ]
    assert recorder.closed


def test_the_real_resource_logs_a_failure_and_still_closes_the_log(
    monkeypatch: pytest.MonkeyPatch, recorder: RecordingRecorder
) -> None:
    get_settings.cache_clear()
    stub_loader(monkeypatch, StubLoader(fail=True))

    with pytest.raises(SourceUnavailableError, match="amfi is down"):
        IngestionResource().run("amfi", "navall", date(2026, 10, 16))

    assert [(a.outcome, a.error) for a in recorder.attempts] == [("failed", "amfi is down")]
    assert recorder.closed
