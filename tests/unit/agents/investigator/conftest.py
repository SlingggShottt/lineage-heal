"""Fixtures for the Investigator tests: one incident on a NAV column."""

import pytest
from detector_graphs import failing
from investigator_scripts import FAILING_NODE

from lineageheal.agents.detector.classify import Verdict
from lineageheal.agents.detector.grouping import SignalGroup


@pytest.fixture
def group() -> SignalGroup:
    return SignalGroup(
        signals=(failing("slv_amfi__nav", "nav_inr"),),
        root_node=FAILING_NODE,
        models=("slv_amfi__nav", "gld_mf_nav_daily"),
        columns=("slv_amfi__nav.nav_inr",),
        gold_tables=("gld_mf_nav_daily",),
    )


@pytest.fixture
def verdict() -> Verdict:
    return Verdict("nulls", 0.6, "Every NAV is blank.", "rules")
