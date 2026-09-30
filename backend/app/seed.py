"""Build a fresh database from the synthetic data set in /data and run detection.

Usage:  python -m backend.app.seed
Set DEMO_PASSWORD in .env to choose the password for all demo accounts; otherwise
a random one is generated and printed once.
"""
from __future__ import annotations

import json
import random
import secrets
from datetime import date, timedelta

from .container import Container, build_container


def _read(container: Container, name: str):
    return json.loads((container.settings.data_dir / name).read_text(encoding="utf-8"))


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
            events.append((item["id"], rng.choice(people)["id"], rng.choice(local_clients)["id"],
                           (start + timedelta(days=offset)).isoformat()))
    return events


def build(password: str | None = None, verbose: bool = True, container: Container | None = None) -> dict:
    container = container or build_container()
    password = password or container.settings.demo_password
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)
    if len(password) < container.settings.min_password_length:
        raise SystemExit(f"DEMO_PASSWORD must be at least {container.settings.min_password_length} characters.")

    db_path = container.settings.db_path
    if db_path.exists():
        db_path.unlink()

    users = _read(container, "users.json")
    org = _read(container, "org.json")
    items = _read(container, "items.json")
    chats = _read(container, "chats.json")
    profiles = _read(container, "usage_profiles.json")

    with container.unit_of_work() as uow:
        uow.init_schema()
        for u in users:
            uow.users.add(u, container.hasher.hash(password) if u["active"] else None)
        for c in org["clients"]:
            uow.catalog.add_client(c)
        for pos, t in enumerate(org["topics"]):
            uow.catalog.add_topic(t, pos)
        for i in sorted(items, key=lambda x: x["supersedes_id"] is not None):
            uow.catalog.add_item(i)
        for i in items:
            if i["supersedes_id"]:
                uow.catalog.link_replacement(i["supersedes_id"], i["id"])
        known_subjects = [c["subject"] for i in items for c in i["claims"]]
        for i in items:
            for claim in container.extractor.extract(i, known_subjects):
                uow.catalog.add_claim(i["id"], claim)
        for m in chats:
            uow.catalog.add_chat(m)
        uow.catalog.add_usage(_usage_events(profiles, {i["id"]: i for i in items}, users, org["clients"]))
        summary = container.detection.run(uow, actor_id=None)

    if verbose:
        print(f"Database built at {db_path}")
        print(f"Detection ({summary['engine']}): {summary}")
        if generated:
            print(f"Generated demo password for all accounts: {password}")
            print("Set DEMO_PASSWORD in .env to choose your own.")
    return summary


if __name__ == "__main__":
    build()
