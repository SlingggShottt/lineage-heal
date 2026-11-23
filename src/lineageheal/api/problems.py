"""Errors as RFC 9457 problem details (FR-API-03).

Every failure, whether a missing incident, a bad query parameter, an unknown URL or a bug,
answers in one shape, so the dashboard needs one error handler instead of one per endpoint.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from lineageheal.core.errors import LineageHealError

log = structlog.get_logger()

PROBLEM_MEDIA_TYPE = "application/problem+json"
# RFC 9457 only asks for a stable identifier, and a URN is one that needs no hosted page.
TYPE_PREFIX = "urn:lineageheal:problem:"


class FieldError(BaseModel):
    """One rejected input: where it was, and why. The submitted value is deliberately absent."""

    field: str
    message: str


class Problem(BaseModel):
    """The body of every error response."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: list[FieldError] | None = None


class ApiError(LineageHealError):
    """An error an endpoint raises on purpose, with the status and type the client should see."""

    def __init__(self, status: int, slug: str, title: str, detail: str | None = None) -> None:
        """Create the error.

        Args:
            status: The HTTP status code.
            slug: A short stable name such as `not-found`; it becomes part of the `type` URN, so
                clients may match on it and it must never change meaning.
            title: A short summary that is the same for every occurrence of this slug.
            detail: What went wrong this time. It reaches the client, so it must hold no secret.
        """
        super().__init__(detail or title)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail


# For `responses=` on a route or the app, so the OpenAPI schema documents the error body and the
# generated TypeScript client gets a type for it.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}}
    for status in (400, 401, 403, 404, 409, 422, 429, 500, 503)
}

_TITLES = {
    400: "Bad request",
    401: "Not signed in",
    403: "Not allowed",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Invalid request",
    429: "Too many requests",
}
_SLUGS = {
    400: "bad-request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    422: "invalid-request",
    429: "rate-limited",
}


def problem_response(
    status: int,
    slug: str,
    title: str,
    *,
    detail: str | None = None,
    instance: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a problem-details response."""
    body = Problem(
        type=TYPE_PREFIX + slug,
        title=title,
        status=status,
        detail=detail,
        instance=instance,
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def install_problem_handlers(app: FastAPI) -> None:
    """Make every error the app can raise come out as problem details."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return problem_response(
            exc.status, exc.slug, exc.title, detail=exc.detail, instance=request.url.path
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(
                field=".".join(str(part) for part in error["loc"]),
                message=str(error["msg"]),
            )
            for error in exc.errors()
        ]
        return problem_response(
            422,
            _SLUGS[422],
            _TITLES[422],
            detail="One or more inputs were rejected.",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        return problem_response(
            status,
            _SLUGS.get(status, "http-error"),
            _TITLES.get(status, "Request failed"),
            detail=exc.detail if isinstance(exc.detail, str) else None,
            instance=request.url.path,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The details go to the log only: an exception message can hold a connection string or
        # a path, and the client can do nothing with either.
        log.error("api_unhandled_error", path=request.url.path, error_type=type(exc).__name__)
        log.debug("api_unhandled_error_detail", exc_info=exc)
        return problem_response(
            500,
            "internal-error",
            "Internal error",
            detail="The server failed to handle the request.",
            instance=request.url.path,
        )
