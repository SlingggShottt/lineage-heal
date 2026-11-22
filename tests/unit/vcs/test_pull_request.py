"""FR-FIX-04: the pull request says what a reviewer needs and cannot notify strangers."""

from lineageheal.vcs import render_pull_request

SANDBOX = {
    "selector": "brz_amfi__navall+",
    "fixed": ["test.p.not_null_x"],
    "before": {"test.p.not_null_x": "fail", "model.p.a": "success", "test.p.unique_a": "pass"},
}


def render(**overrides: object):
    fields: dict[str, object] = {
        "incident_id": 12,
        "summary": "not_null on brz_amfi__navall.nav_inr",
        "fault_class": "schema_drift",
        "confidence": 0.9,
        "explanation": "The raw NAV column was renamed.",
        "lineage_path": ["raw.navall.nav", "brz_amfi__navall.nav_inr"],
        "evidence": ["compare_schemas: removed nav, added Net Asset Value"],
        "change": "Read the new column name.",
        "sandbox": SANDBOX,
    }
    fields.update(overrides)
    return render_pull_request(**fields)  # type: ignore[arg-type]


def test_the_page_has_every_section_of_the_template() -> None:
    page = render(dashboard_url="https://dash.example/incidents/12")

    assert page.title == "Fix incident 12: not_null on brz_amfi__navall.nav_inr"
    for heading in ("### Root cause", "### Lineage path", "### Evidence", "### Change"):
        assert heading in page.body
    assert "raw.navall.nav → brz_amfi__navall.nav_inr" in page.body
    assert "**Confidence:** 0.90" in page.body
    assert "1 node(s) that failed before now pass; 2 that passed before" in page.body
    assert "[View incident in dashboard](https://dash.example/incidents/12)" in page.body


def test_there_is_no_dashboard_link_without_a_deployment() -> None:
    assert "View incident" not in render().body


def test_mentions_in_feed_text_are_defused_and_long_text_is_clipped() -> None:
    page = render(summary="@octocat " + "x" * 500, explanation="ping @someone\n\nnow")

    assert "@octocat" not in page.title
    assert "@someone" not in page.body
    assert "@​someone" in page.body
    assert len(page.title) < 125
    assert "\n\nnow" not in page.body


def test_a_missing_path_and_confidence_are_shown_plainly() -> None:
    page = render(lineage_path=[], confidence=None, fault_class=None)

    assert "not available" in page.body
    assert "**Confidence:** n/a" in page.body
