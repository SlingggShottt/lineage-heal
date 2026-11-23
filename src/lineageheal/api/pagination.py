"""Pagination for list endpoints (FR-API-03).

Offset pagination with a total: the tables the dashboard shows hold at most thousands of rows,
so a total for a pager is worth more than the stability a cursor would add.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


class Page[T](BaseModel):
    """One page of a list, with enough to draw a pager."""

    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class PageParams:
    """The `limit` and `offset` query parameters.

    A value out of range is refused with a 422 problem rather than clamped, so a client that
    asks for 10,000 rows learns the limit instead of silently getting fewer.
    """

    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Rows per page.")] = DEFAULT_LIMIT
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0

    def page_of[T](self, items: Sequence[T], total: int) -> Page[T]:
        """Wrap rows already fetched for this page together with the total row count."""
        return Page(items=list(items), total=total, limit=self.limit, offset=self.offset)


PageQuery = Annotated[PageParams, Depends()]
