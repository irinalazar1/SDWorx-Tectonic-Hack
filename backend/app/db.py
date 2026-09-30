"""SQLite schema and connection factory. Only the repositories module uses this."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'consultant')),
    team TEXT NOT NULL,
    scopes TEXT NOT NULL,              -- comma separated country codes
    active INTEGER NOT NULL DEFAULT 1,
    left_at TEXT,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL
);

-- A knowledge item is managed like a product specification: versioned,
-- owned, scoped, and moved through a lifecycle.
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('draft', 'active', 'deprecated', 'retired')),
    state_changed_at TEXT,
    owner_id TEXT NOT NULL REFERENCES users(id),
    country TEXT NOT NULL,
    topic TEXT NOT NULL REFERENCES topics(id),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    review_by TEXT,
    supersedes_id TEXT REFERENCES items(id),
    replaced_by_id TEXT REFERENCES items(id),
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL REFERENCES users(id),
    channel TEXT NOT NULL,
    country TEXT NOT NULL,
    client_id TEXT REFERENCES clients(id),
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    cites TEXT NOT NULL DEFAULT ''     -- comma separated item ids
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    client_id TEXT NOT NULL REFERENCES clients(id),
    created_at TEXT NOT NULL
);

-- Every detected weak spot is itself a managed object with a lifecycle:
-- detected -> assigned -> resolved | accepted
CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,               -- stable fingerprint of type + items
    type TEXT NOT NULL CHECK (type IN ('conflict', 'duplicate', 'outdated', 'missing')),
    country TEXT NOT NULL,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    explanation TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',  -- JSON
    item_ids TEXT NOT NULL DEFAULT '',
    arose_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'detected'
        CHECK (state IN ('detected', 'assigned', 'resolved', 'accepted')),
    assigned_to TEXT REFERENCES users(id),
    pressure INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    clients INTEGER NOT NULL DEFAULT 0,
    people INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    resolved_by TEXT REFERENCES users(id),
    resolution TEXT
);

-- Server-side sessions. Only a SHA-256 of the session id is stored, so a leaked
-- database cannot be used to hijack sessions.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
