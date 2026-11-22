"""FR-FIX-05: a suggestion is stored, filed as one issue, and never as a pull request."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from lineageheal.agents.fixer import Suggestion
from lineageheal.agents.fixer.filing import file_suggestion
from lineageheal.core.errors import VCSError
from lineageheal.store.fixes import FixRepository
from lineageheal.store.incidents import IncidentDraft, IncidentRepository, StoredIncident
from lineageheal.vcs.client import Issue

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
SUGGESTION = Suggestion(title="t", target="the loader", change="c", verify="v")


class FakeGitHub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.issues: list[tuple[str, str, tuple[str, ...]]] = []

    def open_issue(self, title: str, body: str, labels: tuple[str, ...] = ()) -> Issue:
        if self.fail:
            raise VCSError("GitHub could not be reached")
        self.issues.append((title, body, labels))
        return Issue(9, "https://github.com/acme/pipeline/issues/9")


@pytest.fixture
def incident(engine: Engine) -> StoredIncident:
    return IncidentRepository(engine).create(
        IncidentDraft("s", "error", "fp", None, {}, None, "late_data", 0.5, "rules", NOW)
    )


def status(engine: Engine, incident: StoredIncident) -> str:
    stored = IncidentRepository(engine).get(incident.id)
    assert stored is not None
    return stored.status


def test_a_suggestion_is_stored_filed_and_linked(engine: Engine, incident: StoredIncident) -> None:
    fixes, github = FixRepository(engine), FakeGitHub()

    filed = file_suggestion(fixes, github, incident, SUGGESTION, ["e"], at=NOW)

    assert (filed.issue_number, filed.already_filed) == (9, False)
    [title, _, labels] = github.issues[0]
    assert title.startswith("Suggested fix, incident")
    assert labels == ("lineageheal", "suggested-fix")
    [fix] = fixes.for_incident(incident.id)
    assert (fix.kind, fix.status, fix.patch, fix.files) == ("suggestion", "published", None, [])
    assert fix.suggestion is not None
    assert fix.suggestion["target"] == "the loader"
    assert (fix.github_number, fix.github_url) == (9, "https://github.com/acme/pipeline/issues/9")
    assert status(engine, incident) == "suggestion_filed"


def test_filing_twice_opens_one_issue(engine: Engine, incident: StoredIncident) -> None:
    fixes, github = FixRepository(engine), FakeGitHub()
    file_suggestion(fixes, github, incident, SUGGESTION, [], at=NOW)

    again = file_suggestion(fixes, github, incident, SUGGESTION, [], at=NOW)

    assert again.already_filed
    assert again.issue_number == 9
    assert len(github.issues) == 1
    assert len(fixes.for_incident(incident.id)) == 1


def test_when_github_fails_the_draft_is_kept_and_the_incident_is_unchanged(
    engine: Engine, incident: StoredIncident
) -> None:
    fixes = FixRepository(engine)

    with pytest.raises(VCSError):
        file_suggestion(fixes, FakeGitHub(fail=True), incident, SUGGESTION, [], at=NOW)

    [fix] = fixes.for_incident(incident.id)
    assert (fix.kind, fix.status, fix.github_url) == ("suggestion", "unverified", None)
    assert status(engine, incident) == incident.status


def test_a_retry_after_a_failure_reuses_the_draft_row(
    engine: Engine, incident: StoredIncident
) -> None:
    fixes = FixRepository(engine)
    with pytest.raises(VCSError):
        file_suggestion(fixes, FakeGitHub(fail=True), incident, SUGGESTION, [], at=NOW)

    file_suggestion(fixes, FakeGitHub(), incident, SUGGESTION, [], at=NOW)

    [fix] = fixes.for_incident(incident.id)
    assert fix.status == "published"
    assert status(engine, incident) == "suggestion_filed"


def test_only_an_issue_can_be_opened_on_this_path() -> None:
    # The filing code is given nothing that can branch, commit or open a pull request.
    from lineageheal.agents.fixer.filing import IssueOpener

    assert set(IssueOpener.__protocol_attrs__) == {"open_issue"}  # type: ignore[attr-defined]
