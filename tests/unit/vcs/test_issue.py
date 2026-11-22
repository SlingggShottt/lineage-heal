"""FR-FIX-05: the issue tells an engineer what broke, what to change and how to check it."""

from lineageheal.agents.fixer import Suggestion
from lineageheal.vcs import render_issue

SUGGESTION = Suggestion(
    title="Point the loader at the new URL",
    target="the NSE bhavcopy loader's download URL",
    change="Use the UDiFF path.",
    draft="- old\n+ new",
    verify="Run ingest nse for one day.",
    risks="The new URL is unknown.",
)


def render(suggestion: Suggestion = SUGGESTION, **overrides: object):
    fields: dict[str, object] = {
        "incident_id": 3,
        "summary": "expected file missing",
        "fault_class": "late_data",
        "confidence": 0.8,
        "explanation": "NSE moved the file.",
        "evidence": ["get_ingestion_log: three failed downloads"],
        "suggestion": suggestion,
    }
    fields.update(overrides)
    return render_issue(**fields)  # type: ignore[arg-type]


def test_the_issue_has_every_section_and_says_no_pull_request_was_opened() -> None:
    issue = render(dashboard_url="https://dash.example/incidents/3")

    assert issue.title == "Suggested fix, incident 3: Point the loader at the new URL"
    for heading in ("### Root cause", "### Evidence", "### Suggested change", "### How to verify"):
        assert heading in issue.body
    assert "no pull request was opened" in issue.body
    assert "**Where:** the NSE bhavcopy loader's download URL" in issue.body
    assert "### Risks and unknowns" in issue.body
    assert "[View incident in dashboard](https://dash.example/incidents/3)" in issue.body


def test_the_sketch_is_fenced_and_optional_parts_are_left_out_when_empty() -> None:
    with_sketch = render().body
    without = render(SUGGESTION.model_copy(update={"draft": "", "risks": ""})).body

    assert "```\n- old\n+ new\n```" in with_sketch
    assert "Sketch" not in without
    assert "Risks" not in without


def test_a_sketch_cannot_close_its_own_fence_or_mention_people() -> None:
    sketch = SUGGESTION.model_copy(update={"draft": "```\nping @someone\n```\nnot code"})

    body = render(sketch).body

    assert "````\n" in body
    assert "@someone" not in body


def test_mentions_in_model_and_feed_text_are_defused() -> None:
    suggestion = SUGGESTION.model_copy(update={"change": "ask @octocat", "title": "@octocat fix"})

    issue = render(suggestion, summary="@feed scheme")

    assert "@octocat" not in issue.title + issue.body
    assert "@feed" not in issue.body
