"""Build a fresh database from the synthetic data set in /data and run detection.

Usage:  python -m backend.app.seed
Set DEMO_PASSWORD in .env to choose the password for all demo accounts; otherwise
a random one is generated and printed once.
"""
from __future__ import annotations

import json
import os
import random
import secrets
from datetime import date, timedelta

from . import config, db, detection, llm
from .security import hash_password


def _read(name: str):
    return json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))


def _usage_events(profiles: dict, items: dict, users: list[dict], clients: list[dict]) -> list[tuple]:
    rng = random.Random(42)
    consultants = [u for u in users if u["role"] == "consultant"]
    events = []
    for p in profiles["profiles"]:
        item = items[p["item"]]
        people = [u for u in consultants if item["country"] in u["scopes"]]
        local_clients = [c for c in clients if c["country"] == item["country"]]
        start, end = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
        span = (end - start).days
        for n in range(p["count"]):
            # Skew towards recent dates so pressure reflects current use.
            offset = int(span * (1 - rng.random() ** 1.6)) if n else span
            day = start + timedelta(days=offset)
            events.append((item["id"], rng.choice(people)["id"], rng.choice(local_clients)["id"],
                           day.isoformat()))
    return events


def build(password: str | None = None, verbose: bool = True) -> dict:
    password = password or os.environ.get("DEMO_PASSWORD")
    generated = not password
    if generated:
        password = secrets.token_urlsafe(9)

    if config.DB_PATH.exists():
        config.DB_PATH.unlink()

    users = _read("users.json")
    org = _read("org.json")
    items = _read("items.json")
    chats = _read("chats.json")
    profiles = _read("usage_profiles.json")
    items_by_id = {i["id"]: i for i in items}

    use_llm = llm.available()
    with db.session() as conn:
        db.init(conn)
        for u in users:
            conn.execute(
                "INSERT INTO users (id, name, role, team, scopes, active, left_at, password_hash) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (u["id"], u["name"], u["role"], u["team"], ",".join(u["scopes"]), int(u["active"]),
                 u.get("left_at"), hash_password(password) if u["active"] else None))
        for c in org["clients"]:
            conn.execute("INSERT INTO clients (id, name, country) VALUES (?,?,?)",
                         (c["id"], c["name"], c["country"]))
        for pos, t in enumerate(org["topics"]):
            conn.execute("INSERT INTO topics (id, label, keywords, position) VALUES (?,?,?,?)",
                         (t["id"], t["label"], t.get("keywords", ""), pos))
        for i in sorted(items, key=lambda x: x["supersedes_id"] is not None):
            conn.execute(
                "INSERT INTO items (id, title, version, state, state_changed_at, owner_id, country, topic, "
                "source, created_at, valid_from, review_by, supersedes_id, body) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i["id"], i["title"], i["version"], i["state"], i.get("state_changed_at"), i["owner_id"],
                 i["country"], i["topic"], i["source"], i["created_at"], i["valid_from"], i["review_by"],
                 i["supersedes_id"], i["body"]))
        for i in items:
            if i["supersedes_id"]:
                conn.execute("UPDATE items SET replaced_by_id=? WHERE id=?", (i["id"], i["supersedes_id"]))

        known_subjects = [c["subject"] for i in items for c in i["claims"]]
        for i in items:
            claims = (llm.extract_claims(i, known_subjects) if use_llm else None) or i["claims"]
            for c in claims:
                conn.execute("INSERT INTO claims (item_id, subject, value, text) VALUES (?,?,?,?)",
                             (i["id"], c["subject"], c["value"], c["text"]))
        for m in chats:
            conn.execute(
                "INSERT INTO chats (id, author_id, channel, country, client_id, created_at, text, cites) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (m["id"], m["author_id"], m["channel"], m["country"], m.get("client_id"), m["created_at"],
                 m["text"], ",".join(m.get("cites", []))))
        conn.executemany("INSERT INTO usage_events (item_id, user_id, client_id, created_at) VALUES (?,?,?,?)",
                         _usage_events(profiles, items_by_id, users, org["clients"]))
        summary = detection.run(conn, actor_id=None)

    if verbose:
        print(f"Database built at {config.DB_PATH}")
        print(f"Detection ({summary['engine']}): {summary}")
        if generated:
            print(f"Generated demo password for all accounts: {password}")
            print("Set DEMO_PASSWORD in .env to choose your own.")
    return summary


if __name__ == "__main__":
    build()
