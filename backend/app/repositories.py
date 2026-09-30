"""Data access layer: every SQL statement in the app lives here.

Services receive a UnitOfWork and talk to repositories; they never see SQL or a
connection. Every statement is a constant string with bound parameters; lists are
passed as one JSON parameter and expanded by SQLite's json_each, so no SQL is ever
assembled from values.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from . import db
from .domain import Snapshot, User, split
from .ports import Session

IN_LIST = "(SELECT value FROM json_each(?))"


def _json(values: Iterable[str]) -> str:
    return json.dumps(list(values))


def _user(row: sqlite3.Row) -> User:
    return User(id=row["id"], name=row["name"], role=row["role"], team=row["team"],
                scopes=tuple(split(row["scopes"])), active=bool(row["active"]))


def _issue(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["details"] = json.loads(out["details"] or "{}")
    out["item_ids"] = split(out["item_ids"])
    return out


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_active(self, user_id: str | None) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        return _user(row) if row else None

    def password_hash(self, user_id: str) -> str | None:
        row = self.conn.execute("SELECT password_hash FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
        return row["password_hash"] if row else None

    def list_active(self) -> list[User]:
        rows = self.conn.execute("SELECT * FROM users WHERE active=1 ORDER BY role DESC, name")
        return [_user(r) for r in rows]

    def names(self) -> dict[str, str]:
        return {r["id"]: r["name"] for r in self.conn.execute("SELECT id, name FROM users")}

    def active_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM users WHERE active=1")}

    def add(self, u: dict, password_hash: str | None) -> None:
        self.conn.execute(
            "INSERT INTO users (id, name, role, team, scopes, active, left_at, password_hash) VALUES (?,?,?,?,?,?,?,?)",
            (u["id"], u["name"], u["role"], u["team"], ",".join(u["scopes"]), int(u["active"]),
             u.get("left_at"), password_hash))


class CatalogRepository:
    """Knowledge items, claims, topics, clients, questions and usage."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- reads for detection
    def as_of(self) -> date:
        row = self.conn.execute(
            "SELECT MAX(d) AS d FROM (SELECT MAX(created_at) AS d FROM usage_events "
            "UNION ALL SELECT MAX(created_at) FROM chats)").fetchone()
        return date.fromisoformat(row["d"][:10]) if row and row["d"] else date.today()

    def snapshot(self) -> Snapshot:
        claims: dict[str, list[dict]] = defaultdict(list)
        for r in self.conn.execute("SELECT * FROM claims"):
            claims[r["item_id"]].append(dict(r))
        return Snapshot(
            items={r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM items")},
            claims=claims,
            users={r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM users")},
            topics=[dict(r) for r in self.conn.execute("SELECT * FROM topics ORDER BY position")],
            chats=[dict(r) for r in self.conn.execute("SELECT * FROM chats ORDER BY created_at")],
            usage=[dict(r) for r in self.conn.execute("SELECT * FROM usage_events")],
            as_of=self.as_of(),
        )

    # ---- reads for views
    def topics(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT id, label, position FROM topics ORDER BY position")]

    def items_for_map(self, scopes: tuple[str, ...]) -> list[dict]:
        items = [dict(r) for r in self.conn.execute(
            "SELECT i.id, i.title, i.version, i.state, i.state_changed_at, i.owner_id, u.name AS owner_name, "
            "u.active AS owner_active, i.country, i.topic, i.source, i.created_at, i.review_by, "
            "i.supersedes_id, i.replaced_by_id FROM items i JOIN users u ON u.id = i.owner_id "
            "WHERE i.country IN " + IN_LIST, (_json(scopes),))]
        usage = Counter(r["item_id"] for r in self.conn.execute(
            "SELECT e.item_id FROM usage_events e JOIN items i ON i.id = e.item_id "
            "WHERE i.country IN " + IN_LIST, (_json(scopes),)))
        for item in items:
            item["uses"] = usage.get(item["id"], 0)
        return items

    def item_with_owner(self, item_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT i.*, u.name AS owner_name, u.active AS owner_active FROM items i "
            "JOIN users u ON u.id = i.owner_id WHERE i.id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def items_by_ids(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        return [dict(r) for r in self.conn.execute("SELECT * FROM items WHERE id IN " + IN_LIST, (_json(ids),))]

    def item_brief(self, item_id: str | None) -> dict | None:
        if not item_id:
            return None
        row = self.conn.execute("SELECT id, title, version, country FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def item_countries(self, ids: list[str]) -> set[str]:
        return {i["country"] for i in self.items_by_ids(ids)}

    def claims(self, item_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT subject, value, text FROM claims WHERE item_id=?", (item_id,))]

    def usage_by_month(self, item_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT substr(created_at,1,7) AS month, COUNT(*) AS uses FROM usage_events "
            "WHERE item_id=? GROUP BY month ORDER BY month", (item_id,))]

    def recent_client_ids(self, item_id: str, limit: int = 25) -> list[str]:
        return [r["client_id"] for r in self.conn.execute(
            "SELECT client_id FROM usage_events WHERE item_id=? ORDER BY created_at DESC LIMIT ?", (item_id, limit))]

    def client_names(self, scopes: tuple[str, ...]) -> dict[str, str]:
        return {r["id"]: r["name"] for r in self.conn.execute(
            "SELECT id, name FROM clients WHERE country IN " + IN_LIST, (_json(scopes),))}

    # ---- lifecycle writes
    def set_state(self, item_id: str, state: str, day: str, replaced_by: str | None = None) -> None:
        self.conn.execute("UPDATE items SET state=?, state_changed_at=?, replaced_by_id=COALESCE(?, replaced_by_id) "
                          "WHERE id=?", (state, day, replaced_by, item_id))

    def set_review(self, item_id: str, review_by: str) -> None:
        self.conn.execute("UPDATE items SET review_by=? WHERE id=?", (review_by, item_id))

    def set_owner(self, item_id: str, owner_id: str) -> None:
        self.conn.execute("UPDATE items SET owner_id=? WHERE id=?", (owner_id, item_id))

    # ---- seeding
    def add_client(self, c: dict) -> None:
        self.conn.execute("INSERT INTO clients (id, name, country) VALUES (?,?,?)", (c["id"], c["name"], c["country"]))

    def add_topic(self, t: dict, position: int) -> None:
        self.conn.execute("INSERT INTO topics (id, label, keywords, position) VALUES (?,?,?,?)",
                          (t["id"], t["label"], t.get("keywords", ""), position))

    def add_item(self, i: dict) -> None:
        self.conn.execute(
            "INSERT INTO items (id, title, version, state, state_changed_at, owner_id, country, topic, source, "
            "created_at, valid_from, review_by, supersedes_id, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i["id"], i["title"], i["version"], i["state"], i.get("state_changed_at"), i["owner_id"], i["country"],
             i["topic"], i["source"], i["created_at"], i["valid_from"], i["review_by"], i["supersedes_id"], i["body"]))

    def link_replacement(self, old_id: str, new_id: str) -> None:
        self.conn.execute("UPDATE items SET replaced_by_id=? WHERE id=?", (new_id, old_id))

    def add_claim(self, item_id: str, c: dict) -> None:
        self.conn.execute("INSERT INTO claims (item_id, subject, value, text) VALUES (?,?,?,?)",
                          (item_id, c["subject"], c["value"], c["text"]))

    def add_chat(self, m: dict) -> None:
        self.conn.execute(
            "INSERT INTO chats (id, author_id, channel, country, client_id, created_at, text, cites) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (m["id"], m["author_id"], m["channel"], m["country"], m.get("client_id"), m["created_at"],
             m["text"], ",".join(m.get("cites", []))))

    def add_usage(self, events: list[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO usage_events (item_id, user_id, client_id, created_at) VALUES (?,?,?,?)", events)


class SessionRepository:
    """Implements ports.SessionStore on SQLite. Ids arrive already hashed."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, session_id: str, user_id: str, created_at: str, expires_at: str) -> None:
        self.conn.execute("INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                          (session_id, user_id, created_at, expires_at))
        # Housekeeping: drop sessions that ended more than a day ago.
        cutoff = (datetime.fromisoformat(created_at) - timedelta(days=1)).isoformat()
        self.conn.execute("DELETE FROM sessions WHERE expires_at < ?", (cutoff,))

    def get_active(self, session_id: str, now: str) -> Session | None:
        row = self.conn.execute(
            "SELECT id, user_id, expires_at FROM sessions WHERE id=? AND revoked_at IS NULL AND expires_at > ?",
            (session_id, now)).fetchone()
        return Session(row["id"], row["user_id"], row["expires_at"]) if row else None

    def revoke(self, session_id: str, at: str) -> None:
        self.conn.execute("UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (at, session_id))

    def revoke_all_for_user(self, user_id: str, at: str) -> None:
        self.conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (at, user_id))


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(self, at: str, actor_id: str | None, action: str, target: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO audit_log (at, actor_id, action, target, detail) VALUES (?,?,?,?,?)",
                          (at, actor_id, action, target, detail))

    def history(self, targets: list[str]) -> list[dict]:
        if not targets:
            return []
        return [dict(r) for r in self.conn.execute(
            "SELECT at, actor_id, action, target, detail FROM audit_log "
            "WHERE action NOT LIKE 'auth.%' AND target IN " + IN_LIST + " ORDER BY id", (_json(targets),))]


class IssueRepository:
    def __init__(self, conn: sqlite3.Connection, audit: AuditRepository):
        self.conn = conn
        self.audit = audit

    def get(self, issue_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        return _issue(row) if row else None

    def in_scopes(self, scopes: tuple[str, ...]) -> list[dict]:
        return [_issue(r) for r in self.conn.execute(
            "SELECT * FROM issues WHERE country IN " + IN_LIST + " ORDER BY pressure DESC", (_json(scopes),))]

    def all(self) -> list[dict]:
        return [_issue(r) for r in self.conn.execute("SELECT * FROM issues")]

    def open_touching(self, country: str, item_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM issues WHERE state IN ('detected','assigned') AND country=?", (country,))
        return [_issue(r) for r in rows if item_id in split(r["item_ids"])]

    def assign_if_available(self, issue_id: str, user_id: str) -> bool:
        """Atomically assign an open issue that is unassigned (or already yours). False if not."""
        cur = self.conn.execute(
            "UPDATE issues SET state='assigned', assigned_to=? WHERE id=? AND state IN ('detected','assigned') "
            "AND (assigned_to IS NULL OR assigned_to=?)", (user_id, issue_id, user_id))
        return cur.rowcount == 1

    def close_if_open(self, issue_id: str, state: str, at: str, by: str | None, resolution: str) -> bool:
        """Atomically close an issue that is still open. False if someone else closed it first."""
        cur = self.conn.execute(
            "UPDATE issues SET state=?, resolved_at=?, resolved_by=?, resolution=? "
            "WHERE id=? AND state IN ('detected','assigned')", (state, at, by, resolution, issue_id))
        return cur.rowcount == 1

    def set_resolution(self, issue_id: str, resolution: str) -> None:
        self.conn.execute("UPDATE issues SET resolution=? WHERE id=?", (resolution, issue_id))

    def sync(self, candidates: list[dict], actor_id: str | None, stamp: str) -> None:
        """Upsert detected candidates and apply the issue lifecycle:
        new -> detected, resolved but detected again -> reopened, open but gone -> cleared."""
        existing = {r["id"]: dict(r) for r in self.conn.execute("SELECT id, state, title FROM issues")}
        seen = set()
        for c in candidates:
            seen.add(c["id"])
            values = (c["type"], c["country"], c["topic"], c["title"], c["explanation"], json.dumps(c["details"]),
                      ",".join(c["item_ids"]), c["arose_at"], c["pressure"], c["uses"], c["clients"], c["people"])
            prev = existing.get(c["id"])
            if prev is None:
                self.conn.execute(
                    "INSERT INTO issues (type, country, topic, title, explanation, details, item_ids, arose_at, "
                    "pressure, uses, clients, people, id, detected_at, state) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'detected')", (*values, c["id"], stamp))
                self.audit.log(stamp, actor_id, "issue.detected", c["id"], c["title"])
                continue
            self.conn.execute(
                "UPDATE issues SET type=?, country=?, topic=?, title=?, explanation=?, details=?, item_ids=?, "
                "arose_at=?, pressure=?, uses=?, clients=?, people=? WHERE id=?", (*values, c["id"]))
            if prev["state"] == "resolved":
                self.conn.execute("UPDATE issues SET state='detected', resolved_at=NULL, resolved_by=NULL, "
                                  "resolution=NULL WHERE id=?", (c["id"],))
                self.audit.log(stamp, actor_id, "issue.reopened", c["id"], "Condition detected again")
        for issue_id, prev in existing.items():
            if issue_id not in seen and prev["state"] in ("detected", "assigned"):
                self.close_if_open(issue_id, "resolved", stamp, None, "Condition cleared after a lifecycle change.")
                self.audit.log(stamp, actor_id, "issue.cleared", issue_id, prev["title"])


class UnitOfWork:
    """One connection and transaction per request (or per seed run), shared by all repositories."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def __enter__(self) -> "UnitOfWork":
        self.conn = db.connect(self.db_path)
        self.users = UserRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.catalog = CatalogRepository(self.conn)
        self.audit = AuditRepository(self.conn)
        self.issues = IssueRepository(self.conn, self.audit)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()

    def init_schema(self) -> None:
        db.init(self.conn)

    def commit(self) -> None:
        """Persist what happened so far, e.g. an audit entry for a request that is about to fail."""
        self.conn.commit()

    def begin_write(self) -> None:
        """Take the write lock up front, so concurrent state changes are serialised."""
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
