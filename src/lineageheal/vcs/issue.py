"""The text of a suggested-fix issue (FR-FIX-05).

The reader is an engineer who has to make a change outside the dbt project, so the issue says
what broke, why the Investigator thinks so, what to change and how to confirm it. Model and feed
text are flattened to one line per field (the sketch keeps its lines, in a code fence) and
stripped of `@` mentions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lineageheal.agents.fixer.suggestion import Suggestion
from lineageheal.vcs.text import plain

LABELS = ("lineageheal", "suggested-fix")
MAX_LIST_ITEMS = 10


@dataclass(frozen=True)
class IssueContent:
    """An issue's title and body."""

    title: str
    body: str


def render_issue(
    *,
    incident_id: int,
    summary: str,
    fault_class: str | None,
    confidence: float | None,
    explanation: str,
    evidence: Sequence[str],
    suggestion: Suggestion,
    dashboard_url: str | None = None,
) -> IssueContent:
    """Write the issue for a root cause outside dbt.

    Args:
        incident_id: The incident's ID.
        summary: The incident's one-line summary.
        fault_class: The Investigator's fault class.
        confidence: The Investigator's confidence, 0 to 1.
        explanation: Why the root cause is what it is.
        evidence: One line per piece of evidence.
        suggestion: The drafted change.
        dashboard_url: A link to the incident in the dashboard, if there is a deployment.
    """
    lines = [
        f"## Incident {incident_id}: {plain(summary, 200)}",
        "",
        "The root cause is outside the dbt project, so no pull request was opened. "
        "This is a suggestion for a person to apply.",
        "",
        f"**Fault class:** {plain(fault_class or 'unknown', 40)} · "
        f"**Confidence:** {'n/a' if confidence is None else f'{confidence:.2f}'}",
        "",
        "### Root cause",
        plain(explanation, 1200),
        "",
        "### Evidence",
        *[f"- {plain(item, 300)}" for item in evidence[:MAX_LIST_ITEMS]],
        "",
        "### Suggested change",
        f"**Where:** {plain(suggestion.target, 300)}",
        "",
        plain(suggestion.change, 1500),
    ]
    if suggestion.draft.strip():
        lines += ["", "Sketch (illustrative, not tested):", "", *_fenced(suggestion.draft)]
    lines += ["", "### How to verify", plain(suggestion.verify, 600)]
    if suggestion.risks.strip():
        lines += ["", "### Risks and unknowns", plain(suggestion.risks, 600)]
    if dashboard_url:
        lines += ["", f"[View incident in dashboard]({dashboard_url})"]
    return IssueContent(
        f"Suggested fix, incident {incident_id}: {plain(suggestion.title, 100)}",
        "\n".join(lines) + "\n",
    )


def _fenced(sketch: str) -> list[str]:
    # A fence the sketch cannot close: longer than any run of backticks inside it.
    ticks = "`" * max(3, _longest_backtick_run(sketch) + 1)
    return [ticks, sketch.replace("@", "@\u200b").strip("\n"), ticks]


def _longest_backtick_run(text: str) -> int:
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return longest
