"""Sign in with GitHub, sign out, and say who the visitor is (FR-API-02).

Anyone may read; signing in only identifies a person. Whether they may approve is decided on the
server for each state-changing request (`approver_action`), never by what the dashboard shows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from lineageheal.api.dependencies import (
    EngineDep,
    OptionalSessionDep,
    SettingsDep,
    check_csrf,
    cipher_of,
    engine_of,
    now_of,
    oauth_of,
)
from lineageheal.api.github_auth import OAuthError
from lineageheal.api.problems import PROBLEM_RESPONSES, ApiError
from lineageheal.api.security import (
    SESSION_COOKIE,
    STATE_COOKIE,
    hash_session_id,
    new_token,
    tokens_match,
)
from lineageheal.core.settings import Settings
from lineageheal.store.sessions import SessionRepository, StoredSession

log = structlog.get_logger()
router = APIRouter(tags=["auth"])

# GitHub's user tokens last 8 hours, and a session never outlives the token it holds.
MAX_SESSION = timedelta(hours=8)
STATE_LIFETIME_SECONDS = 600


class Me(BaseModel):
    """Who is asking. Anonymous visitors get `signed_in: false` and nothing else."""

    signed_in: bool
    login: str | None = None
    is_approver: bool = False
    # Echoed back in X-CSRF-Token on every state-changing request.
    csrf_token: str | None = None
    expires_at: datetime | None = None


@router.get("/me", summary="The current visitor")
def me(session: OptionalSessionDep, settings: SettingsDep) -> Me:
    """Report the signed-in person, and whether they may approve."""
    if session is None:
        return Me(signed_in=False)
    return Me(
        signed_in=True,
        login=session.login,
        is_approver=session.login.lower() in settings.approver_logins,
        csrf_token=session.csrf_token,
        expires_at=session.expires_at,
    )


@router.get(
    "/auth/login",
    summary="Start signing in with GitHub",
    status_code=302,
    response_class=RedirectResponse,
    responses={503: PROBLEM_RESPONSES[503]},
)
def login(request: Request, settings: SettingsDep) -> RedirectResponse:
    """Send the browser to GitHub, with a one-time `state` kept in a short-lived cookie."""
    oauth = oauth_of(request)
    state = new_token()
    response = RedirectResponse(oauth.authorize_url(state), status_code=302)
    # Always Lax whatever the session cookie uses: GitHub sends the browser back with a
    # cross-site navigation, which a Strict cookie would not accompany.
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_LIFETIME_SECONDS,
        path="/auth",
        httponly=True,
        secure=_secure(settings),
        samesite="lax",
    )
    return response


@router.get(
    "/auth/callback",
    summary="Finish signing in with GitHub",
    status_code=302,
    response_class=RedirectResponse,
    responses={400: PROBLEM_RESPONSES[400], 503: PROBLEM_RESPONSES[503]},
)
def callback(
    request: Request,
    settings: SettingsDep,
    engine: EngineDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Check the `state`, trade the code for a token, learn who it is and start a session."""
    expected = request.cookies.get(STATE_COOKIE)
    if not state or not expected or not tokens_match(state, expected):
        raise ApiError(
            400,
            "invalid-state",
            "Sign-in could not be verified",
            "The sign-in attempt did not start here, or took too long. Try again.",
        )
    if error or not code:
        return _back_to_dashboard(settings, "denied")

    oauth = oauth_of(request)
    cipher = cipher_of(request)
    try:
        token = oauth.exchange_code(code)
        user = oauth.fetch_user(token.value)
    except OAuthError as exc:
        log.warning("sign_in_failed", reason=str(exc))
        return _back_to_dashboard(settings, "failed")

    now = now_of(request)
    lifetime = (
        min(timedelta(seconds=token.expires_in), MAX_SESSION) if token.expires_in else MAX_SESSION
    )
    session_id = new_token()
    repository = SessionRepository(engine)
    repository.prune_expired(now)
    repository.create(
        StoredSession(
            id_hash=hash_session_id(session_id),
            github_id=user.id,
            login=user.login,
            token_encrypted=cipher.encrypt(token.value.get_secret_value()),
            csrf_token=new_token(),
            created_at=now,
            expires_at=now + lifetime,
        )
    )
    log.info("signed_in", login=user.login)
    response = _back_to_dashboard(settings, None)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=int(lifetime.total_seconds()),
        path="/",
        httponly=True,
        secure=_secure(settings),
        samesite=settings.session_cookie_samesite,
    )
    return response


@router.post(
    "/auth/logout",
    summary="Sign out",
    status_code=204,
    responses={403: PROBLEM_RESPONSES[403]},
)
def logout(request: Request, session: OptionalSessionDep, settings: SettingsDep) -> Response:
    """End the session. Signing out when not signed in is not an error."""
    response = Response(status_code=204)
    if session is None:
        return response
    check_csrf(request, session, settings)
    SessionRepository(engine_of(request)).delete(session.id_hash)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=_secure(settings),
        samesite=settings.session_cookie_samesite,
    )
    return response


def _secure(settings: Settings) -> bool:
    return settings.api_public_url.startswith("https://")


def _back_to_dashboard(settings: Settings, problem: str | None) -> RedirectResponse:
    target = settings.dashboard_url.rstrip("/") + "/"
    if problem:
        target += f"?sign_in={problem}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    return response
