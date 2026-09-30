"""FastAPI dependency providers: how the API layer receives its collaborators.

Routes never construct services or security components; they declare what they need
with `Depends(...)`. Everything resolves from the Container stored on the app, so a test
can build the app with a different container, or override any single provider with
`app.dependency_overrides[provider] = fake`.

Authentication uses an HttpOnly, SameSite=Strict session cookie (not readable by
JavaScript). State-changing requests must also carry the session's CSRF token in the
`X-CSRF-Token` header.
"""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request

from ..container import Container
from ..domain import AuthenticationFailed, Forbidden, User
from ..repositories import UnitOfWork
from ..security import AccessPolicy, AuthService, Principal
from ..services import DirectoryService, IssueService, ItemService
from ..settings import Settings

CSRF_HEADER = "X-CSRF-Token"


def session_cookie_name(settings: Settings) -> str:
    # The __Host- prefix makes browsers enforce Secure, Path=/ and no Domain attribute.
    return "__Host-fl_session" if settings.cookie_secure else "fl_session"


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_settings(container: Container = Depends(get_container)) -> Settings:
    return container.settings


def get_uow(container: Container = Depends(get_container)) -> Iterator[UnitOfWork]:
    with container.unit_of_work() as uow:
        yield uow


# ---- security
def get_auth_service(container: Container = Depends(get_container)) -> AuthService:
    return container.auth


def get_policy(container: Container = Depends(get_container)) -> AccessPolicy:
    return container.policy


def get_principal(request: Request, auth: AuthService = Depends(get_auth_service),
                  uow: UnitOfWork = Depends(get_uow), settings: Settings = Depends(get_settings)) -> Principal:
    try:
        return auth.authenticate(uow, request.cookies.get(session_cookie_name(settings)))
    except AuthenticationFailed as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    return principal.user


def require_csrf(request: Request, principal: Principal = Depends(get_principal),
                 auth: AuthService = Depends(get_auth_service)) -> Principal:
    if not auth.csrf_valid(principal, request.headers.get(CSRF_HEADER)):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
    return principal


def require_owner(user: User = Depends(get_current_user), policy: AccessPolicy = Depends(get_policy)) -> User:
    try:
        policy.ensure_owner(user)
    except Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return user


# ---- services
def get_issue_service(container: Container = Depends(get_container)) -> IssueService:
    return container.issues


def get_item_service(container: Container = Depends(get_container)) -> ItemService:
    return container.items


def get_directory_service(container: Container = Depends(get_container)) -> DirectoryService:
    return container.directory
