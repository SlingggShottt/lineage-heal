"""FR-DET-02 to FR-DET-05: incidents opened per cause, never twice for the same unresolved one."""

from __future__ import annotations

from datetime import timedelta

import pytest
from detector_graphs import RUN_AT, failing, market_graph
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from lineageheal.agents.detector.classify import Classifier, Verdict
from lineageheal.agents.detector.detect import Detector, Outcome, fingerprint
from lineageheal.agents.detector.grouping import SignalGroup, group_signals
from lineageheal.core.errors import StorageError
from lineageheal.core.signals import RunSummary, Signal
from lineageheal.store.incidents import IncidentRepository
from lineageheal.store.lineage import LineageRepository
from lineageheal.store.models import Incident
from lineageheal.store.runs import RunRepository

BRONZE = ("brz_amfi__navall", "nav_inr")
SILVER = ("slv_amfi__nav", "nav_inr")
NSE = ("slv_nse__equity_eod", "close_price_inr")


class CountingClassifier(Classifier):
    """Rules only, and a count of how many groups it was asked about."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def classify(self, group: SignalGroup) -> Verdict:
        self.calls += 1
        return super().classify(group)


@pytest.fixture
def incidents(engine: Engine) -> IncidentRepository:
    return IncidentRepository(engine)


@pytest.fixture
def runs(engine: Engine) -> RunRepository:
    return RunRepository(engine)


@pytest.fixture
def detector(incidents: IncidentRepository) -> Detector:
    return Detector(incidents, CountingClassifier())


@pytest.fixture(autouse=True)
def stored_graph(engine: Engine) -> None:
    LineageRepository(engine).save(market_graph("v1"), activated_at=RUN_AT)


def record(runs: RunRepository, run_id: str, *signals: Signal) -> list[Signal]:
    mine = [s.model_copy(update={"run_id": run_id}) for s in signals]
    runs.record(RunSummary(run_id=run_id, generated_at=RUN_AT, counts={}, signals=tuple(mine)))
    return mine


def test_each_shared_cause_becomes_one_incident_with_its_diagnosis(
    detector: Detector, runs: RunRepository, incidents: IncidentRepository
) -> None:
    signals = record(
        runs, "run-1", failing(*BRONZE), failing(*SILVER, check="volume_anomaly"), failing(*NSE)
    )

    detected = detector.detect("run-1", signals, market_graph("v1"), at=RUN_AT)

    assert [d.outcome for d in detected] == [Outcome.OPENED, Outcome.OPENED]
    amfi, nse = (d.incident for d in detected)
    assert (amfi.status, amfi.severity, amfi.fault_class) == ("detected", "error", "nulls")
    assert amfi.root_node == "brz_amfi__navall.nav_inr"
    assert amfi.affected["gold_tables"] == ["gld_mf_nav_daily"]
    assert amfi.affected["columns"] == [
        "brz_amfi__navall.nav_inr",
        "slv_amfi__nav.nav_inr",
    ]
    assert amfi.lineage_version == "v1"
    assert amfi.classified_by == "rules"
    assert len(incidents.signals_of(amfi.id)) == 2
    assert len(incidents.signals_of(nse.id)) == 1


def test_running_detection_again_changes_nothing(
    detector: Detector, runs: RunRepository, incidents: IncidentRepository
) -> None:
    signals = record(runs, "run-1", failing(*BRONZE))
    first = detector.detect("run-1", signals, market_graph("v1"), at=RUN_AT)

    again = detector.detect("run-1", signals, market_graph("v1"), at=RUN_AT)

    assert [d.outcome for d in again] == [Outcome.UNCHANGED]
    assert again[0].incident.id == first[0].incident.id
    assert len(incidents.signals_of(first[0].incident.id)) == 1


def test_the_same_unresolved_cause_in_a_later_run_joins_the_open_incident(
    detector: Detector, runs: RunRepository, incidents: IncidentRepository
) -> None:
    first = detector.detect(
        "run-1", record(runs, "run-1", failing(*BRONZE)), market_graph("v1"), at=RUN_AT
    )
    later = RUN_AT + timedelta(days=1)

    second = detector.detect(
        "run-2", record(runs, "run-2", failing(*BRONZE)), market_graph("v1"), at=later
    )

    assert second[0].outcome is Outcome.UPDATED
    assert second[0].incident.id == first[0].incident.id
    assert second[0].incident.updated_at == later
    assert len(incidents.signals_of(first[0].incident.id)) == 2


def test_a_resolved_incident_is_not_reopened_by_the_same_cause_returning(
    detector: Detector, runs: RunRepository, incidents: IncidentRepository, engine: Engine
) -> None:
    first = detector.detect(
        "run-1", record(runs, "run-1", failing(*BRONZE)), market_graph("v1"), at=RUN_AT
    )
    with Session(engine) as session, session.begin():
        session.execute(
            update(Incident)
            .where(Incident.id == first[0].incident.id)
            .values(status="resolved", resolved_at=RUN_AT)
        )

    second = detector.detect(
        "run-2", record(runs, "run-2", failing(*BRONZE)), market_graph("v1"), at=RUN_AT
    )

    assert second[0].outcome is Outcome.OPENED
    assert second[0].incident.id != first[0].incident.id


def test_a_cause_already_known_costs_no_classification(
    runs: RunRepository, incidents: IncidentRepository
) -> None:
    classifier = CountingClassifier()
    detector = Detector(incidents, classifier)
    detector.detect("run-1", record(runs, "run-1", failing(*BRONZE)), market_graph("v1"), at=RUN_AT)

    detector.detect("run-2", record(runs, "run-2", failing(*BRONZE)), market_graph("v1"), at=RUN_AT)

    assert classifier.calls == 1


def test_a_group_of_warnings_opens_a_warning_incident(
    detector: Detector, runs: RunRepository
) -> None:
    warn = failing(*SILVER, check="fresh_within", severity="warn", kind="test_warning")

    [item] = detector.detect("run-1", record(runs, "run-1", warn), market_graph("v1"), at=RUN_AT)

    assert (item.incident.severity, item.incident.fault_class) == ("warn", "late_data")


def test_a_signal_that_cannot_be_placed_still_gets_an_incident(
    detector: Detector, runs: RunRepository
) -> None:
    run_error = failing(None, None, kind="run_error", check="run", name="dbt")

    [item] = detector.detect(
        "run-1", record(runs, "run-1", run_error), market_graph("v1"), at=RUN_AT
    )

    assert item.incident.root_node is None
    assert item.incident.fault_class == "unknown"


def test_the_fingerprint_depends_on_the_root_and_the_rule_class_only() -> None:
    [a] = group_signals([failing(*BRONZE)], market_graph())
    [b] = group_signals([failing(*BRONZE), failing(*SILVER)], market_graph())

    assert fingerprint(a, "nulls") == fingerprint(b, "nulls")
    assert fingerprint(a, "nulls") != fingerprint(a, "duplicates")


def test_an_incident_cannot_point_at_a_graph_that_was_never_stored(
    detector: Detector, runs: RunRepository
) -> None:
    signals = record(runs, "run-1", failing(*BRONZE))

    with pytest.raises(StorageError):
        detector.detect("run-1", signals, market_graph("never-saved"), at=RUN_AT)
