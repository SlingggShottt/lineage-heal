"""Runbooks for the dashboard's runbooks screen (FR-UI-08).

Public and read-only. The folder is a setting; when it is missing the list is empty.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from lineageheal.api.dependencies import SettingsDep
from lineageheal.api.health_schemas import Runbook, RunbookSummary
from lineageheal.api.pagination import Page, PageQuery
from lineageheal.api.problems import PROBLEM_RESPONSES, ApiError
from lineageheal.api.runbook_view import list_runbooks, read_runbook

router = APIRouter(tags=["runbooks"], responses={404: PROBLEM_RESPONSES[404]})

Slug = Annotated[
    str,
    Path(
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$", description="The runbook's file name, no extension."
    ),
]


@router.get("/runbooks", summary="List runbooks")
def get_runbooks(settings: SettingsDep, page: PageQuery) -> Page[RunbookSummary]:
    """Runbooks by name, with their fault class and feeds."""
    runbooks = list_runbooks(settings.runbooks_dir)
    return page.page_of(runbooks[page.offset : page.offset + page.limit], len(runbooks))


@router.get("/runbooks/{slug}", summary="One runbook")
def get_runbook(slug: Slug, settings: SettingsDep) -> Runbook:
    """The runbook's markdown and its sections."""
    runbook = read_runbook(settings.runbooks_dir, slug)
    if runbook is None:
        raise ApiError(404, "not-found", "Runbook not found", f"There is no runbook {slug!r}.")
    return runbook
