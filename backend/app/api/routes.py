"""HTTP routes. Each handler validates input, calls one service method, and returns its result.
Domain errors are mapped to HTTP status codes in `main.py`."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from ..domain import ResolveCommand, User
from ..repositories import UnitOfWork
from ..security import AuthService, Principal
from ..services import DirectoryService, IssueService, ItemService
from ..settings import Settings
from .deps import (get_auth_service, get_current_user, get_directory_service, get_issue_service, get_item_service,
                   get_principal, get_settings, get_uow, require_csrf, require_owner, session_cookie_name)

router = APIRouter(prefix="/api")

# Ids are validated before they reach any service: anything else is rejected with 422.
IssueId = Annotated[str, Path(pattern=r"^(con|dup|out|mis)-[0-9a-f]{10}$")]
ItemId = Annotated[str, Path(pattern=r"^KB-[A-Z]{2}-\d{3}$")]
USERNAME_PATTERN = r"^[A-Za-z0-9._@-]{1,64}$"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)


class ResolveIn(BaseModel):
    action: Literal["keep", "accept", "retire", "reconfirm", "assign"]
    keep_item_id: str | None = Field(default=None, max_length=16, pattern=r"^KB-[A-Z]{2}-\d{3}$")
    note: str | None = Field(default=None, max_length=500)


def _session_payload(user: User, csrf_token: str) -> dict:
    return {"user": user.public(), "csrf_token": csrf_token}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, auth: AuthService = Depends(get_auth_service),
          uow: UnitOfWork = Depends(get_uow), settings: Settings = Depends(get_settings)):
    client = request.client.host if request.client else "unknown"
    result = auth.login(uow, body.username, body.password, client)
    response.set_cookie(session_cookie_name(settings), result.token, max_age=settings.session_ttl_seconds,
                        httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    return _session_payload(result.user, result.csrf_token)


@router.post("/logout")
def logout(response: Response, principal: Principal = Depends(require_csrf),
           auth: AuthService = Depends(get_auth_service), uow: UnitOfWork = Depends(get_uow),
           settings: Settings = Depends(get_settings)):
    auth.logout(uow, principal)
    response.delete_cookie(session_cookie_name(settings), path="/", secure=settings.cookie_secure,
                           httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(get_principal)):
    return _session_payload(principal.user, principal.csrf_token)


@router.get("/demo-users")
def demo_users(directory: DirectoryService = Depends(get_directory_service), uow: UnitOfWork = Depends(get_uow),
               settings: Settings = Depends(get_settings)):
    """Account names for the demo sign-in screen. Only exists when DEMO_MODE is on."""
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    return directory.demo_users(uow)


@router.get("/map")
def map_view(user: User = Depends(get_current_user), issues: IssueService = Depends(get_issue_service),
             uow: UnitOfWork = Depends(get_uow)):
    return issues.map_view(uow, user)


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: IssueId, user: User = Depends(get_current_user),
                 issues: IssueService = Depends(get_issue_service), uow: UnitOfWork = Depends(get_uow)):
    return issues.detail(uow, user, issue_id)


@router.post("/issues/{issue_id}/resolve", dependencies=[Depends(require_csrf)])
def resolve_issue(issue_id: IssueId, body: ResolveIn, user: User = Depends(require_owner),
                  issues: IssueService = Depends(get_issue_service), uow: UnitOfWork = Depends(get_uow)):
    return issues.resolve(uow, user, issue_id, ResolveCommand(body.action, body.keep_item_id, body.note))


@router.get("/items/{item_id}")
def item_detail(item_id: ItemId, user: User = Depends(get_current_user),
                items: ItemService = Depends(get_item_service), uow: UnitOfWork = Depends(get_uow)):
    return items.detail(uow, user, item_id)
