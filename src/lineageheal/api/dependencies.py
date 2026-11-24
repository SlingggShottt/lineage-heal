"""Dependencies shared by the routers: settings and the database engine.

Both hang off `app.state`, so a test builds an app with its own settings and engine and never
touches the process environment.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy import Engine

from lineageheal.api.decisions import DecisionService
from lineageheal.api.github_auth import GitHubOAuth
from lineageheal.api.problems import ApiError
from lineageheal.api.reconcile import Reconciler
from lineageheal.api.security import SESSION_COOKIE, TokenCipher, hash_session_id, tokens_match
from lineageheal.core.errors import ConfigurationError
from lineageheal.core.settings import Settings
from lineageheal.store.database import create_db_engine
from lineageheal.store.fixes import FixRepository
from lineageheal.store.reviews import ReviewRepository
from lineageheal.store.sessions import SessionRepository, StoredSession
from lineageheal.vcs.client import GitHubClient
from lineageheal.vcs.factory import build_github_client


def settings_of(request: Request) -> Settings:
    """The settings the app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def engine_of(request: Request) -> Engine:
    """The database engine, created on first use so the app starts without a database.

    Raises:
        ApiError: DATABASE_URL is not set (503), which says the service is not ready, not that
            the request was wrong.
    """
    state = request.app.state
    if state.engine is None:
        url = state.settings.database_url
        if url is None:
            raise ApiError(
                503, "database-unavailable", "Database unavailable", "No database is configured."
            )
        state.engine = create_db_engine(url.get_secret_value())
    engine: Engine = state.engine
    return engine


SettingsDep = Annotated[Settings, Depends(settings_of)]
EngineDep = Annotated[Engine, Depends(engine_of)]


def _sign_in_unavailable() -> ApiError:
    return ApiError(
        503,
        "sign-in-unavailable",
        "Sign-in unavailable",
        "Sign-in is not configured on this server.",
    )


def now_of(request: Request) -> datetime:
    """The current time, from the app's clock so a test can fix it."""
    clock: Callable[[], datetime] = request.app.state.clock
    return clock()


def oauth_of(request: Request) -> GitHubOAuth:
    """The sign-in client, built on first use from the settings.

    Raises:
        ApiError: Sign-in is not configured (503): the App's client id or secret is missing.
    """
    state = request.app.state
    if state.oauth is None:
        settings: Settings = state.settings
        if settings.github_oauth_client_id is None or settings.github_oauth_client_secret is None:
            raise _sign_in_unavailable()
        state.oauth = GitHubOAuth(
            settings.github_oauth_client_id,
            settings.github_oauth_client_secret,
            f"{settings.api_public_url.rstrip('/')}/auth/callback",
        )
    oauth: GitHubOAuth = state.oauth
    return oauth


def cipher_of(request: Request) -> TokenCipher:
    """The token cipher, built on first use from `SESSION_SECRET`.

    Raises:
        ApiError: The secret is missing or too short (503).
    """
    state = request.app.state
    if state.cipher is None:
        secret = state.settings.session_secret
        if secret is None:
            raise _sign_in_unavailable()
        try:
            state.cipher = TokenCipher(secret.get_secret_value())
        except ConfigurationError as exc:
            raise _sign_in_unavailable() from exc
    cipher: TokenCipher = state.cipher
    return cipher


def github_of(request: Request) -> GitHubClient:
    """The GitHub client, built on first use from the App's settings.

    Raises:
        ApiError: The App is not configured (503).
    """
    state = request.app.state
    if state.github is None:
        try:
            state.github = build_github_client(state.settings)
        except ConfigurationError as exc:
            raise ApiError(
                503,
                "github-unavailable",
                "GitHub unavailable",
                "GitHub is not configured on this server.",
            ) from exc
    github: GitHubClient = state.github
    return github


def reconciler_of(request: Request) -> Reconciler:
    """The reconciler for this request; without GitHub configured it has nothing to ask."""

    def github() -> GitHubClient | None:
        try:
            return github_of(request)
        except ApiError:
            return None

    engine = engine_of(request)
    return Reconciler(
        FixRepository(engine),
        ReviewRepository(engine),
        github,
        lambda: now_of(request),
        request.app.state.reconciled,
    )


def decisions_of(request: Request) -> DecisionService:
    """The service that carries out approvals and rejections."""
    return DecisionService(
        ReviewRepository(engine_of(request)),
        github_of(request),
        cipher_of(request),
        lambda: now_of(request),
    )


def optional_session(request: Request) -> StoredSession | None:
    """The signed-in session for this request, or None for an anonymous visitor.

    The database is only touched when a session cookie is present, so anonymous reads work with
    no database configured. An unknown or expired cookie is simply anonymous.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    repository = SessionRepository(engine_of(request))
    return repository.get(hash_session_id(cookie), now_of(request))


def require_session(
    session: Annotated[StoredSession | None, Depends(optional_session)],
) -> StoredSession:
    """The signed-in session, or a 401 problem for anyone else."""
    if session is None:
        raise ApiError(401, "unauthenticated", "Not signed in", "Sign in with GitHub first.")
    return session


def check_csrf(request: Request, session: StoredSession, settings: Settings) -> None:
    """Refuse a state-changing request that could have been forged by another site.

    Two independent checks (NFR-SEC-03): the `X-CSRF-Token` header, which only this dashboard
    can read from `/me`, must equal the session's token; and a browser's `Origin`, when sent,
    must be one of ours.

    Raises:
        ApiError: Either check fails (403).
    """
    sent = request.headers.get("x-csrf-token")
    if not sent or not tokens_match(sent, session.csrf_token):
        raise ApiError(403, "csrf-failed", "Request refused", "The CSRF token is missing or wrong.")
    origin = request.headers.get("origin")
    if origin is not None and origin not in allowed_origins(settings):
        raise ApiError(403, "csrf-failed", "Request refused", "The request came from another site.")


def allowed_origins(settings: Settings) -> frozenset[str]:
    """Origins that may make state-changing requests: the dashboard, the API and the CORS list."""
    origins = {o.strip() for o in settings.api_cors_origins.split(",") if o.strip()}
    for url in (settings.dashboard_url, settings.api_public_url):
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            origins.add(f"{parts.scheme}://{parts.netloc}")
    return frozenset(origins)


def approver_action(
    request: Request,
    session: Annotated[StoredSession, Depends(require_session)],
    settings: SettingsDep,
) -> StoredSession:
    """Guard for every endpoint that changes state: signed in, an approver, and not forged.

    The order matters: a stranger is told to sign in (401), a signed-in non-approver is refused
    (403) before the CSRF check, so nothing about the token is revealed to someone who could not
    act anyway (FR-REV-03).
    """
    if session.login.lower() not in settings.approver_logins:
        raise ApiError(403, "forbidden", "Not allowed", "Only approvers can make this change.")
    check_csrf(request, session, settings)
    return session


OptionalSessionDep = Annotated[StoredSession | None, Depends(optional_session)]
ApproverDep = Annotated[StoredSession, Depends(approver_action)]
DecisionServiceDep = Annotated[DecisionService, Depends(decisions_of)]
ReconcilerDep = Annotated[Reconciler, Depends(reconciler_of)]
