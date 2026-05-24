import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_lock = threading.Lock()


def db_path() -> str:
    return os.getenv("PROBE_DB_PATH", "./probe.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS components (
    component_id TEXT PRIMARY KEY,
    mode         TEXT NOT NULL DEFAULT 'trace',
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    trace_id     TEXT PRIMARY KEY,
    component_id TEXT NOT NULL,
    mode         TEXT,
    input_json   TEXT,
    output_text  TEXT,
    error        TEXT,
    duration_ms  REAL,
    timestamp    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_component_ts
    ON traces (component_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS shadow_results (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id               TEXT NOT NULL,
    component_id           TEXT NOT NULL,
    current_output         TEXT,
    candidate_output       TEXT,
    candidate_error        TEXT,
    candidate_duration_ms  REAL,
    evaluation             TEXT,
    timestamp              REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_component_ts
    ON shadow_results (component_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_shadow_trace
    ON shadow_results (trace_id);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    name        TEXT,
    kind        TEXT NOT NULL DEFAULT 'api',
    user_id     INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    expires_at  REAL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens (user_id);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _bootstrap_admin()


def _bootstrap_admin() -> None:
    """Create an initial admin from env vars if no such user exists yet."""
    from .security import hash_password

    username = os.getenv("CONTROL_ADMIN_USERNAME", "").strip()
    password = os.getenv("CONTROL_ADMIN_PASSWORD", "")
    if not username or not password:
        return
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is not None:
            return
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at)
            VALUES (?, ?, 'admin', 1, ?)
            """,
            (username, hash_password(password), time.time()),
        )
