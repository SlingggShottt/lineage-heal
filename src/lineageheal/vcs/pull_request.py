"""The text of a fix's pull request (FR-FIX-04), following the template in the design.

A reviewer decides from this page, so it carries the root cause, the path the fault took, the
evidence, what the patch does and how the sandbox checked it. Some of it began as feed data or
model output, so every field is flattened to one line, clipped, and stripped of `@` mentions,
which would otherwise notify real accounts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lineageheal.vcs.text import plain as _bounded

MAX_TITLE = 100
MAX_FIELD = 1200
MAX_LIST_ITEMS = 10


@dataclass(frozen=True)
class PullRequestContent:
    """A pull request's title and body."""

    title: str
    body: str


def render_pull_request(
    *,
    incident_id: int,
    summary: str,
    fault_class: str | None,
    confidence: float | None,
    explanation: str,
    lineage_path: Sequence[str],
    evidence: Sequence[str],
    change: str,
    sandbox: dict[str, Any],
    dashboard_url: str | None = None,
) -> PullRequestContent:
    """Write the pull request for a verified patch.

    Args:
        incident_id: The incident's ID.
        summary: The incident's one-line summary.
        fault_class: The Investigator's fault class.
        confidence: The Investigator's confidence, 0 to 1.
        explanation: Why the root cause is what it is.
        lineage_path: Nodes from the root cause to the failing column.
        evidence: One line per piece of evidence, with the tool and its finding.
        change: What the patch does and why.
        sandbox: The sandbox result as stored with the fix.
        dashboard_url: A link to the incident in the dashboard, if there is a deployment.
    """
    title = f"Fix incident {incident_id}: {_plain(summary, MAX_TITLE)}"
    verified = _verification(sandbox)
    path = " → ".join(_plain(node, 120) for node in lineage_path) or "not available"
    lines = [
        f"## Incident {incident_id}: {_plain(summary, 200)}",
        "",
        f"**Fault class:** {_plain(fault_class or 'unknown', 40)} · "
        f"**Confidence:** {'n/a' if confidence is None else f'{confidence:.2f}'}",
        "",
        "### Root cause",
        _plain(explanation),
        "",
        "### Lineage path",
        path,
        "",
        "### Evidence",
        *[f"- {_plain(item, 300)}" for item in evidence[:MAX_LIST_ITEMS]],
        "",
        "### Change",
        _plain(change),
        "",
        "### Verification",
        verified,
    ]
    if dashboard_url:
        lines += ["", f"[View incident in dashboard]({dashboard_url})"]
    return PullRequestContent(title, "\n".join(lines) + "\n")


def _verification(sandbox: dict[str, Any]) -> str:
    fixed = sandbox.get("fixed", [])
    before = sandbox.get("before", {})
    still_passing = sum(1 for status in before.values() if status in ("pass", "success"))
    return (
        f"Built `{_plain(str(sandbox.get('selector', '')), 100)}` in a sandbox with and without "
        f"the patch. {len(fixed)} node(s) that failed before now pass; {still_passing} that "
        "passed before were checked again and none broke."
    )


def _plain(text: str, limit: int = MAX_FIELD) -> str:
    return _bounded(text, limit)
