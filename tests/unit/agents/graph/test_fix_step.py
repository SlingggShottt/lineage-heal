"""FR-FIX-02/04/05: concluded incidents are fixed one by one; nothing opens without --publish."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from detector_graphs import failing, market_graph
from investigator_scripts import FAILING_NODE, build, conclusion, lineage_call
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lineageheal.agents.fixer import Patch, Suggestion
from lineageheal.agents.fixer.fixer import FixerResult, FixerStatus
from lineageheal.agents.fixer.sandbox import SandboxError, SandboxResult, compare
from lineageheal.agents.fixer.suggestion import SuggestionResult, SuggestionStatus
from lineageheal.agents.graph import (
    FixDependencies,
    FixKind,
    HealDependencies,
    run_heal_report,
)
from lineageheal.core.errors import VCSError
from lineageheal.core.signals import RunSummary
from lineageheal.lineage.service import LineageService
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import IncidentDraft, IncidentRepository, StoredIncident
from lineageheal.store.investigations import InvestigationRepository
from lineageheal.store.models import Incident
from lineageheal.store.runs import RunRepository
from lineageheal.vcs.client import Issue, PullRequest

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
RUN = "run-1"
PATCH = Patch(
    "--- a/dbt/models/x.sql\n+++ b/dbt/models/x.sql\n@@ -1 +1 @@\n-a\n+b\n",
    ("dbt/models/x.sql",),
    2,
    {"dbt/models/x.sql": "b\n"},
)
PASS = compare({"t": "fail"}, {"t": "pass"}, "m+")
FAIL = compare({"t": "fail"}, {"t": "fail"}, "m+")
SUGGESTION = Suggestion(title="t", target="the loader", change="c", verify="v")


class FakeFixer:
    def __init__(self, *results: FixerResult) -> None:
        self.results = list(results)
        self.calls = 0

    def propose(self, incident: StoredIncident, feedback: str | None = None) -> FixerResult:
        self.calls += 1
        return self.results.pop(0)


class FakeSandbox:
    def __init__(self, *verdicts: SandboxResult | SandboxError) -> None:
        self.verdicts = list(verdicts)

    def verify(self, patch: Patch, model: str) -> SandboxResult:
        verdict = self.verdicts.pop(0)
        if isinstance(verdict, SandboxError):
            raise verdict
        return verdict


class FakeSuggester:
    def __init__(self, status: SuggestionStatus = SuggestionStatus.DRAFTED) -> None:
        self.status = status

    def draft(
        self, incident: StoredIncident, evidence: list[dict[str, Any]] | None = None
    ) -> SuggestionResult:
        suggestion = SUGGESTION if self.status is SuggestionStatus.DRAFTED else None
        return SuggestionResult(self.status, "why not", suggestion, llm_calls=1)


class FakeGitHub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def base_branch_sha(self) -> str:
        self.calls.append("sha")
        return "base1"

    def create_branch(self, incident_id: int, sha: str) -> str:
        self.calls.append("branch")
        if self.fail:
            raise VCSError("Reference already exists")
        return f"heal/{incident_id}"

    def commit_files(self, branch: str, parent_sha: str, files: Any, message: str) -> str:
        self.calls.append("commit")
        return "c1"

    def open_pull_request(self, branch: str, title: str, body: str) -> PullRequest:
        self.calls.append("pull")
        return PullRequest(7, "https://github.com/a/b/pull/7", "open", False, branch, "main")

    def open_issue(self, title: str, body: str, labels: tuple[str, ...] = ()) -> Issue:
        self.calls.append("issue")
        return Issue(9, "https://github.com/a/b/issues/9")


class GitHubFailsOnce(FakeGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def create_branch(self, incident_id: int, sha: str) -> str:
        if not self.failed:
            self.failed = True
            raise VCSError("Reference already exists")
        return super().create_branch(incident_id, sha)


def patched() -> FixerResult:
    return FixerResult(FixerStatus.PATCHED, patch=PATCH, llm_calls=1)


def open_incident(engine: Engine, name: str) -> int:
    repository = IncidentRepository(engine)
    created = repository.create(
        IncidentDraft(
            summary="not_null on slv_amfi__nav.nav_inr",
            severity="error",
            fingerprint=f"fp-{name}",
            root_node=FAILING_NODE,
            affected={
                "models": ["slv_amfi__nav", "gld_mf_nav_daily"],
                "columns": ["slv_amfi__nav.nav_inr"],
                "gold_tables": ["gld_mf_nav_daily"],
            },
            lineage_version=None,
            fault_class="nulls",
            confidence=0.6,
            classified_by="rules",
            opened_at=NOW,
        )
    )
    repository.attach(created.id, RUN, [failing("slv_amfi__nav", "nav_inr", name=name)], at=NOW)
    return created.id


@pytest.fixture
def ids(engine: Engine) -> list[int]:
    signals = (
        failing("slv_amfi__nav", "nav_inr", name="a"),
        failing("slv_amfi__nav", "nav_inr", name="b"),
    )
    RunRepository(engine).record(
        RunSummary(run_id=RUN, generated_at=NOW, counts={}, signals=signals)
    )
    return [open_incident(engine, "a"), open_incident(engine, "b")]


def fixing(
    engine: Engine,
    fixer: FakeFixer,
    sandbox: FakeSandbox,
    github: FakeGitHub | None = None,
    suggester: FakeSuggester | None = None,
) -> FixDependencies:
    return FixDependencies(
        fixer=fixer,
        sandbox=sandbox,
        suggester=suggester or FakeSuggester(),
        fixes=FixRepository(engine),
        lineage=LineageService(market_graph()),
        now=lambda: NOW,
        max_attempts=2,
        github=github,
    )


def heal(
    engine: Engine, ids: list[int], script: list[Any], make_fixing: Any, only: int | None = None
) -> Any:
    investigator, _ = build(script)
    deps = HealDependencies(
        detect=lambda: list(ids),
        incidents=IncidentRepository(engine),
        investigations=InvestigationRepository(engine),
        make_investigator=lambda: investigator,
        now=lambda: NOW,
        make_fixing=make_fixing,
    )
    return run_heal_report(deps, RUN, only_incident=only)


def concluded(times: int = 1, **fields: Any) -> list[Any]:
    return [lineage_call(action="conclude"), conclusion(**fields)] * times


def status_of(engine: Engine, incident_id: int) -> str:
    with Session(engine) as session:
        row = session.get(Incident, incident_id)
        assert row is not None
        return row.status


def test_a_verified_patch_is_stored_but_nothing_is_opened_without_publish(
    engine: Engine, ids: list[int]
) -> None:
    deps = fixing(engine, FakeFixer(patched()), FakeSandbox(PASS))

    report = heal(engine, ids, concluded(), lambda: deps, only=ids[0])

    [fix] = report.fixes
    assert fix.kind is FixKind.PATCH_VERIFIED
    assert "--publish" in fix.detail
    [stored] = FixRepository(engine).for_incident(ids[0])
    assert (stored.status, stored.github_url) == ("verified", None)
    assert status_of(engine, ids[0]) == "fix_proposed"


def test_with_publish_a_verified_patch_becomes_a_pull_request(
    engine: Engine, ids: list[int]
) -> None:
    github = FakeGitHub()
    deps = fixing(engine, FakeFixer(patched()), FakeSandbox(PASS), github)

    report = heal(engine, ids, concluded(), lambda: deps, only=ids[0])

    [fix] = report.fixes
    assert fix.kind is FixKind.PATCH_PUBLISHED
    assert fix.pull_request_url == "https://github.com/a/b/pull/7"
    assert github.calls == ["sha", "branch", "commit", "pull"]
    [stored] = FixRepository(engine).for_incident(ids[0])
    assert (stored.status, stored.branch, stored.github_number) == (
        "published",
        f"heal/{ids[0]}",
        7,
    )


def test_a_failed_patch_is_retried_and_when_none_pass_no_pull_request_is_opened(
    engine: Engine, ids: list[int]
) -> None:
    github = FakeGitHub()
    fixer = FakeFixer(patched(), patched())
    deps = fixing(engine, fixer, FakeSandbox(FAIL, FAIL), github)

    report = heal(engine, ids, concluded(), lambda: deps, only=ids[0])

    [fix] = report.fixes
    assert (fix.kind, fix.attempts) == (FixKind.UNVERIFIED, 2)
    assert github.calls == []
    assert status_of(engine, ids[0]) == "fix_unverified"
    assert len(FixRepository(engine).for_incident(ids[0])) == 2


def test_a_root_cause_outside_dbt_gets_a_suggestion_issue_and_no_patch(
    engine: Engine, ids: list[int]
) -> None:
    github = FakeGitHub()
    fixer = FakeFixer()
    deps = fixing(engine, fixer, FakeSandbox(), github)

    report = heal(engine, ids, concluded(fix_location="external"), lambda: deps, only=ids[0])

    [fix] = report.fixes
    assert fix.kind is FixKind.SUGGESTION_FILED
    assert fix.issue_url == "https://github.com/a/b/issues/9"
    assert github.calls == ["issue"]
    assert fixer.calls == 0
    assert status_of(engine, ids[0]) == "suggestion_filed"


def test_a_suggestion_without_publish_is_only_stored(engine: Engine, ids: list[int]) -> None:
    deps = fixing(engine, FakeFixer(), FakeSandbox())

    report = heal(engine, ids, concluded(fix_location="external"), lambda: deps, only=ids[0])

    assert report.fixes[0].kind is FixKind.SUGGESTION_DRAFTED
    [stored] = FixRepository(engine).for_incident(ids[0])
    assert (stored.kind, stored.status) == ("suggestion", "unverified")


def test_a_suggestion_the_model_could_not_draft_is_reported(engine: Engine, ids: list[int]) -> None:
    deps = fixing(
        engine,
        FakeFixer(),
        FakeSandbox(),
        suggester=FakeSuggester(SuggestionStatus.UNAVAILABLE),
    )

    report = heal(engine, ids, concluded(fix_location="external"), lambda: deps, only=ids[0])

    assert report.fixes[0].kind is FixKind.NO_SUGGESTION


def test_a_crash_in_one_incident_does_not_stop_the_others(engine: Engine, ids: list[int]) -> None:
    deps = fixing(
        engine, FakeFixer(patched(), patched()), FakeSandbox(PASS, PASS), GitHubFailsOnce()
    )

    report = heal(engine, ids, concluded(2), lambda: deps)

    assert [f.kind for f in report.fixes] == [FixKind.FAILED, FixKind.PATCH_PUBLISHED]
    assert "Reference already exists" in report.fixes[0].detail


def test_a_sandbox_that_cannot_run_is_a_failed_fix_not_a_crash(
    engine: Engine, ids: list[int]
) -> None:
    deps = fixing(engine, FakeFixer(patched()), FakeSandbox(SandboxError("git failed")))

    report = heal(engine, ids, concluded(), lambda: deps, only=ids[0])

    assert report.fixes[0].kind is FixKind.FAILED
    assert "git failed" in report.fixes[0].detail


def test_nothing_is_fixed_when_fixing_is_not_configured(engine: Engine, ids: list[int]) -> None:
    report = heal(engine, ids, concluded(), None, only=ids[0])

    assert report.fixes == []
    assert report.outcomes[0].kind.value == "concluded"
    assert status_of(engine, ids[0]) == "investigating"


def test_an_incident_that_was_not_concluded_is_not_fixed(engine: Engine, ids: list[int]) -> None:
    made: list[int] = []

    def make() -> FixDependencies:
        made.append(1)
        return fixing(engine, FakeFixer(), FakeSandbox())

    from lineageheal.llm.types import LLMError

    report = heal(engine, ids, [LLMError("down")] * 6, make, only=ids[0])

    assert report.fixes == []
    assert made == []


def test_an_incident_with_a_published_fix_is_left_alone_on_a_second_run(
    engine: Engine, ids: list[int]
) -> None:
    github = FakeGitHub()
    deps = fixing(engine, FakeFixer(patched()), FakeSandbox(PASS), github)
    heal(engine, ids, concluded(), lambda: deps, only=ids[0])

    # The incident is investigated again only if allowed; here fix is driven directly.
    from lineageheal.agents.graph.fixing import FixNodes

    again = FixNodes(
        fixing(engine, FakeFixer(), FakeSandbox(), github),
        IncidentRepository(engine),
        InvestigationRepository(engine),
    ).fix_one(ids[0])

    assert again.kind is FixKind.SKIPPED
    assert github.calls.count("pull") == 1


def test_a_verified_patch_that_was_never_published_can_be_published_later(
    engine: Engine, ids: list[int]
) -> None:
    from lineageheal.agents.graph.fixing import FixNodes

    heal(
        engine,
        ids,
        concluded(),
        lambda: fixing(engine, FakeFixer(patched()), FakeSandbox(PASS)),
        only=ids[0],
    )
    github = FakeGitHub()

    again = FixNodes(
        fixing(engine, FakeFixer(patched()), FakeSandbox(PASS), github),
        IncidentRepository(engine),
        InvestigationRepository(engine),
    ).fix_one(ids[0])

    assert again.kind is FixKind.PATCH_PUBLISHED
    assert github.calls == ["sha", "branch", "commit", "pull"]
