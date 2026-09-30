"""Lifecycle actions an owner can take to resolve an issue."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import detection
from .db import split

ACTIONS = {
    "conflict": {"keep", "accept"},
    "duplicate": {"keep", "accept"},
    "outdated": {"retire", "reconfirm"},
    "missing": {"assign", "accept"},
}


class ActionError(ValueError):
    pass


def _log(conn: sqlite3.Connection, actor: str, action: str, target: str, detail: str) -> None:
    conn.execute("INSERT INTO audit_log (at, actor_id, action, target, detail) VALUES (?,?,?,?,?)",
                 (detection.now_iso(), actor, action, target, detail))


def _set_state(conn, item_id: str, state: str, actor: str, replaced_by: str | None = None) -> None:
    today = date.today().isoformat()
    conn.execute("UPDATE items SET state=?, state_changed_at=?, replaced_by_id=COALESCE(?, replaced_by_id) "
                 "WHERE id=?", (state, today, replaced_by, item_id))
    _log(conn, actor, f"item.{state}", item_id, f"replaced by {replaced_by}" if replaced_by else "")


def apply(conn: sqlite3.Connection, issue: dict, user: dict, action: str,
          keep_item_id: str | None, note: str | None) -> dict:
    if action not in ACTIONS[issue["type"]]:
        raise ActionError(f"Action '{action}' is not valid for a {issue['type']} issue.")
    if issue["state"] in ("resolved", "accepted"):
        raise ActionError("This issue is already closed.")

    actor = user["id"]
    item_ids = issue["item_ids"] if isinstance(issue["item_ids"], list) else split(issue["item_ids"])
    note = (note or "").strip()[:500]

    if action == "keep":
        if keep_item_id not in item_ids:
            raise ActionError("keep_item_id must be one of the items in this issue.")
        kept = conn.execute("SELECT title, version FROM items WHERE id=?", (keep_item_id,)).fetchone()
        for other in item_ids:
            if other != keep_item_id:
                _set_state(conn, other, "deprecated" if issue["type"] == "conflict" else "retired",
                           actor, replaced_by=keep_item_id)
        resolution = f"Kept “{kept['title']}” v{kept['version']} as the single source of truth."
        state = "resolved"
    elif action == "retire":
        _set_state(conn, item_ids[0], "retired", actor)
        resolution = "Retired: removed from circulation."
        state = "resolved"
    elif action == "reconfirm":
        item = conn.execute("SELECT i.*, u.active AS owner_active FROM items i JOIN users u ON u.id=i.owner_id "
                            "WHERE i.id=?", (item_ids[0],)).fetchone()
        if item["state"] != "active":
            raise ActionError("Only an active item can be reconfirmed. Retire it instead.")
        new_review = (date.today() + timedelta(days=365)).isoformat()
        conn.execute("UPDATE items SET review_by=? WHERE id=?", (new_review, item["id"]))
        detail = f"reviewed, next review {new_review}"
        if not item["owner_active"]:
            conn.execute("UPDATE items SET owner_id=? WHERE id=?", (actor, item["id"]))
            detail += f", ownership taken over by {user['name']}"
        _log(conn, actor, "item.reconfirmed", item["id"], detail)
        resolution = f"Reconfirmed as correct; {detail}."
        state = "resolved"
    elif action == "assign":
        conn.execute("UPDATE issues SET state='assigned', assigned_to=? WHERE id=?", (actor, issue["id"]))
        _log(conn, actor, "issue.assigned", issue["id"], note)
        detection.run(conn, actor_id=actor)
        return {"state": "assigned"}
    else:  # accept
        if not note:
            raise ActionError("Explain why this is acceptable, so the next person understands.")
        resolution = f"Accepted: {note}"
        state = "accepted"

    if note and action != "accept":
        resolution += f" Note: {note}"
    conn.execute("UPDATE issues SET state=?, resolved_at=?, resolved_by=?, resolution=? WHERE id=?",
                 (state, detection.now_iso(), actor, resolution, issue["id"]))
    _log(conn, actor, f"issue.{state}", issue["id"], resolution)
    summary = detection.run(conn, actor_id=actor)
    return {"state": state, "resolution": resolution, "detection": summary}
