"""Fault Lines API + static frontend."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import actions, config, db, detection
from .db import split
from .security import issue_token, read_token, throttle, verify_password

app = FastAPI(title="Fault Lines", docs_url=None, redoc_url=None, openapi_url=None)
bearer = HTTPBearer(auto_error=False)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------- auth
def get_conn():
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    user_id = read_token(creds.credentials) if creds else None
    row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone() if user_id else None
    if row is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    user = dict(row)
    user["scopes"] = split(user["scopes"])
    user.pop("password_hash", None)
    return user


def require_owner(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only knowledge owners can do this")
    return user


def _in_scope(user: dict, country: str) -> bool:
    return country in user["scopes"]


def _placeholders(values: list[str]) -> str:
    return ",".join("?" * len(values))


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ResolveIn(BaseModel):
    action: Literal["keep", "accept", "retire", "reconfirm", "assign"]
    keep_item_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)


@app.post("/api/login")
def login(body: LoginIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    client = request.client.host if request.client else "unknown"
    keys = (f"user:{body.username}", f"ip:{client}")
    if throttle.blocked(*keys):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (body.username,)).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        throttle.fail(*keys)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    throttle.reset(*keys)
    return {"token": issue_token(row["id"]),
            "user": {"id": row["id"], "name": row["name"], "role": row["role"],
                     "team": row["team"], "scopes": split(row["scopes"])}}


@app.get("/api/demo-users")
def demo_users(conn: sqlite3.Connection = Depends(get_conn)):
    """Account names for the demo sign-in screen (no credentials)."""
    rows = conn.execute("SELECT id, name, role, team, scopes FROM users WHERE active=1 ORDER BY role DESC, name")
    return [{**dict(r), "scopes": split(r["scopes"])} for r in rows]


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


# --------------------------------------------------------------------------- views
def _issue_out(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["details"] = json.loads(out["details"] or "{}")
    out["item_ids"] = split(out["item_ids"])
    return out


@app.get("/api/map")
def map_view(user: dict = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)):
    scopes = user["scopes"]
    ph = _placeholders(scopes)
    topics = [dict(r) for r in conn.execute("SELECT id, label, position FROM topics ORDER BY position")]
    items = [dict(r) for r in conn.execute(
        f"SELECT i.id, i.title, i.version, i.state, i.state_changed_at, i.owner_id, u.name AS owner_name, "
        f"u.active AS owner_active, i.country, i.topic, i.source, i.created_at, i.review_by, "
        f"i.supersedes_id, i.replaced_by_id FROM items i JOIN users u ON u.id = i.owner_id "
        f"WHERE i.country IN ({ph})", scopes)]
    usage = Counter(r["item_id"] for r in conn.execute(
        f"SELECT e.item_id FROM usage_events e JOIN items i ON i.id=e.item_id WHERE i.country IN ({ph})", scopes))
    for item in items:
        item["uses"] = usage.get(item["id"], 0)
    issues = [_issue_out(r) for r in conn.execute(
        f"SELECT * FROM issues WHERE country IN ({ph}) ORDER BY pressure DESC", scopes)]
    first = min((i["created_at"] for i in items), default=None)
    return {"as_of": detection.as_of_date(conn).isoformat(), "start": first,
            "topics": topics, "items": items, "issues": issues}


def _load_issue(conn, issue_id: str, user: dict) -> dict:
    row = conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    # Out-of-scope issues answer exactly like missing ones, so ids cannot be probed.
    if row is None or not _in_scope(user, row["country"]):
        raise HTTPException(status_code=404, detail="Issue not found")
    return _issue_out(row)


@app.get("/api/issues/{issue_id}")
def issue_detail(issue_id: str, user: dict = Depends(current_user),
                 conn: sqlite3.Connection = Depends(get_conn)):
    issue = _load_issue(conn, issue_id, user)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    active_users = {r["id"] for r in conn.execute("SELECT id FROM users WHERE active=1")}
    clients = {r["id"]: r["name"] for r in conn.execute(
        f"SELECT id, name FROM clients WHERE country IN ({_placeholders(user['scopes'])})", user["scopes"])}

    items = []
    ids = issue["item_ids"]
    if ids:
        for r in conn.execute(f"SELECT * FROM items WHERE id IN ({_placeholders(ids)})", ids):
            item = dict(r)
            item["owner_name"] = names.get(item["owner_id"])
            item["owner_active"] = item["owner_id"] in active_users
            item["claims"] = [dict(c) for c in conn.execute(
                "SELECT subject, value, text FROM claims WHERE item_id=?", (item["id"],))]
            item["usage_by_month"] = [dict(u) for u in conn.execute(
                "SELECT substr(created_at,1,7) AS month, COUNT(*) AS uses FROM usage_events "
                "WHERE item_id=? GROUP BY month ORDER BY month", (item["id"],))]
            item["recent_clients"] = sorted({clients.get(u["client_id"]) for u in conn.execute(
                "SELECT client_id FROM usage_events WHERE item_id=? ORDER BY created_at DESC LIMIT 25",
                (item["id"],))} - {None})
            items.append(item)
        items.sort(key=lambda i: ids.index(i["id"]))

    for q in issue["details"].get("questions", []):
        q["author_name"] = names.get(q["author_id"])
        q["client_name"] = clients.get(q["client_id"])
    ref = issue["details"].get("reference_item")
    if ref:
        r = conn.execute("SELECT id, title, country FROM items WHERE id=?", (ref,)).fetchone()
        issue["details"]["reference"] = dict(r) if r else None
    issue["assigned_to_name"] = names.get(issue["assigned_to"])
    issue["resolved_by_name"] = names.get(issue["resolved_by"])
    issue["items"] = items
    issue["history"] = [
        {**dict(r), "actor_name": names.get(r["actor_id"], "Fault Lines")}
        for r in conn.execute(
            f"SELECT at, actor_id, action, target, detail FROM audit_log WHERE target=? "
            f"{'OR target IN (' + _placeholders(ids) + ')' if ids else ''} ORDER BY id",
            (issue_id, *ids))]
    issue["allowed_actions"] = (sorted(actions.ACTIONS[issue["type"]])
                                if user["role"] == "owner" and issue["state"] in ("detected", "assigned")
                                else [])
    return issue


@app.post("/api/issues/{issue_id}/resolve")
def resolve_issue(issue_id: str, body: ResolveIn, user: dict = Depends(require_owner),
                  conn: sqlite3.Connection = Depends(get_conn)):
    issue = _load_issue(conn, issue_id, user)
    item_countries = {r["country"] for r in conn.execute(
        f"SELECT country FROM items WHERE id IN ({_placeholders(issue['item_ids'])})", issue["item_ids"])} \
        if issue["item_ids"] else set()
    if not all(_in_scope(user, c) for c in item_countries):
        raise HTTPException(status_code=403, detail="You do not own knowledge in this scope")
    try:
        return actions.apply(conn, issue, user, body.action, body.keep_item_id, body.note)
    except actions.ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/items/{item_id}")
def item_detail(item_id: str, user: dict = Depends(current_user),
                conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT i.*, u.name AS owner_name, u.active AS owner_active FROM items i "
                       "JOIN users u ON u.id=i.owner_id WHERE i.id=?", (item_id,)).fetchone()
    if row is None or not _in_scope(user, row["country"]):
        raise HTTPException(status_code=404, detail="Item not found")
    item = dict(row)
    item["claims"] = [dict(c) for c in conn.execute(
        "SELECT subject, value, text FROM claims WHERE item_id=?", (item_id,))]
    # Trust badges: every open issue that touches this item.
    item["open_issues"] = [
        {"id": r["id"], "type": r["type"], "title": r["title"], "pressure": r["pressure"]}
        for r in conn.execute("SELECT * FROM issues WHERE state IN ('detected','assigned') AND country=?",
                              (item["country"],))
        if item_id in split(r["item_ids"])]
    newer = conn.execute("SELECT id, title, version FROM items WHERE id=?", (item["replaced_by_id"],)).fetchone() \
        if item["replaced_by_id"] else None
    item["replaced_by"] = dict(newer) if newer else None
    return item


@app.post("/api/detect")
def rerun_detection(user: dict = Depends(require_owner), conn: sqlite3.Connection = Depends(get_conn)):
    return detection.run(conn, actor_id=user["id"])


@app.exception_handler(sqlite3.Error)
def db_error(_: Request, __: sqlite3.Error):
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
