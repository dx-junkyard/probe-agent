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
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS systems (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    environment   TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    owner_user_id INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    name        TEXT,
    kind        TEXT NOT NULL DEFAULT 'api',
    user_id     INTEGER NOT NULL,
    system_id   INTEGER,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    expires_at  REAL,
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (system_id) REFERENCES systems (id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_system ON api_tokens (system_id);

CREATE TABLE IF NOT EXISTS components (
    system_id    INTEGER NOT NULL,
    component_id TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'trace',
    updated_at   REAL NOT NULL,
    PRIMARY KEY (system_id, component_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS traces (
    system_id    INTEGER NOT NULL,
    trace_id     TEXT NOT NULL,
    component_id TEXT NOT NULL,
    mode         TEXT,
    input_json   TEXT,
    output_text  TEXT,
    error        TEXT,
    duration_ms  REAL,
    timestamp    REAL NOT NULL,
    PRIMARY KEY (system_id, trace_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_traces_component_ts
    ON traces (system_id, component_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS shadow_results (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id              INTEGER NOT NULL,
    trace_id               TEXT NOT NULL,
    component_id           TEXT NOT NULL,
    current_output         TEXT,
    candidate_output       TEXT,
    candidate_error        TEXT,
    candidate_duration_ms  REAL,
    evaluation             TEXT,
    timestamp              REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shadow_component_ts
    ON shadow_results (system_id, component_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_shadow_trace
    ON shadow_results (system_id, trace_id);

CREATE TABLE IF NOT EXISTS system_profile (
    system_id         INTEGER PRIMARY KEY,
    name              TEXT,
    purpose           TEXT,
    target_users      TEXT,
    stakeholder_value TEXT,
    constraints       TEXT,
    success_criteria  TEXT,
    created_at        REAL,
    updated_at        REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS component_profiles (
    system_id      INTEGER NOT NULL,
    component_id    TEXT NOT NULL,
    purpose         TEXT,
    responsibility  TEXT,
    expected_input  TEXT,
    expected_output TEXT,
    failure_impact  TEXT,
    notes           TEXT,
    created_at      REAL,
    updated_at      REAL,
    PRIMARY KEY (system_id, component_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluation_criteria (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    component_id   TEXT NOT NULL,
    name           TEXT NOT NULL,
    description    TEXT,
    criterion_type TEXT NOT NULL,
    expected_value TEXT,
    weight         REAL NOT NULL DEFAULT 1.0,
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_criteria_component
    ON evaluation_criteria (system_id, component_id);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    trace_id       TEXT NOT NULL,
    component_id   TEXT NOT NULL,
    criterion_id   INTEGER NOT NULL,
    status         TEXT NOT NULL,
    score          REAL,
    reason         TEXT,
    actual_output  TEXT,
    expected_value TEXT,
    created_at     REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_eval_results_trace
    ON evaluation_results (system_id, trace_id);

CREATE INDEX IF NOT EXISTS idx_eval_results_component
    ON evaluation_results (system_id, component_id);

CREATE TABLE IF NOT EXISTS generation_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id          INTEGER NOT NULL,
    component_id        TEXT NOT NULL,
    trace_id            TEXT NOT NULL,
    objective           TEXT NOT NULL,
    input_json          TEXT,
    current_output      TEXT,
    generated_code      TEXT NOT NULL,
    generation_notes    TEXT,
    candidate_output    TEXT,
    execution_error     TEXT,
    llm_verdict         TEXT NOT NULL DEFAULT 'unknown',
    llm_reason          TEXT,
    llm_risks           TEXT,
    llm_recommendation  TEXT,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_generation_runs_trace
    ON generation_runs (system_id, trace_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_generation_runs_component
    ON generation_runs (system_id, component_id, id DESC);
"""


_SCOPED_TABLES = [
    "components",
    "traces",
    "shadow_results",
    "system_profile",
    "component_profiles",
    "evaluation_criteria",
    "evaluation_results",
]


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_legacy_system(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM systems WHERE name = 'Legacy System' AND owner_user_id IS NULL"
    ).fetchone()
    if row is not None:
        return row["id"]
    now = time.time()
    cur = conn.execute(
        """
        INSERT INTO systems
            (name, environment, description, owner_user_id, created_at, updated_at)
        VALUES ('Legacy System', 'legacy',
                'Automatically created for data that predates system isolation.',
                NULL, ?, ?)
        """,
        (now, now),
    )
    return cur.lastrowid


def _migrate_to_system_scope(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    old_scoped = "components" in existing and "system_id" not in _columns(conn, "components")
    old_tokens = "api_tokens" in existing and "system_id" not in _columns(conn, "api_tokens")
    if not old_scoped and not old_tokens:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    if old_scoped:
        for table in _SCOPED_TABLES:
            if table in existing:
                conn.execute(f"ALTER TABLE {table} RENAME TO _old_{table}")
    if old_tokens:
        conn.execute("ALTER TABLE api_tokens RENAME TO _old_api_tokens")

    conn.executescript(SCHEMA)
    legacy_id = _ensure_legacy_system(conn)

    if old_tokens:
        conn.execute(
            """
            INSERT INTO api_tokens
                (id, token_hash, name, kind, user_id, system_id, revoked,
                 created_at, expires_at)
            SELECT id, token_hash, name, kind, user_id,
                   CASE WHEN kind = 'api' THEN ? ELSE NULL END,
                   revoked, created_at, expires_at
            FROM _old_api_tokens
            """,
            (legacy_id,),
        )

    if old_scoped:
        conn.execute(
            """
            INSERT INTO components (system_id, component_id, mode, updated_at)
            SELECT ?, component_id, mode, updated_at FROM _old_components
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO traces
                (system_id, trace_id, component_id, mode, input_json, output_text,
                 error, duration_ms, timestamp)
            SELECT ?, trace_id, component_id, mode, input_json, output_text,
                   error, duration_ms, timestamp
            FROM _old_traces
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO shadow_results
                (id, system_id, trace_id, component_id, current_output,
                 candidate_output, candidate_error, candidate_duration_ms,
                 evaluation, timestamp)
            SELECT id, ?, trace_id, component_id, current_output,
                   candidate_output, candidate_error, candidate_duration_ms,
                   evaluation, timestamp
            FROM _old_shadow_results
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO system_profile
                (system_id, name, purpose, target_users, stakeholder_value,
                 constraints, success_criteria, created_at, updated_at)
            SELECT ?, name, purpose, target_users, stakeholder_value,
                   constraints, success_criteria, created_at, updated_at
            FROM _old_system_profile
            LIMIT 1
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO component_profiles
                (system_id, component_id, purpose, responsibility, expected_input,
                 expected_output, failure_impact, notes, created_at, updated_at)
            SELECT ?, component_id, purpose, responsibility, expected_input,
                   expected_output, failure_impact, notes, created_at, updated_at
            FROM _old_component_profiles
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO evaluation_criteria
                (id, system_id, component_id, name, description, criterion_type,
                 expected_value, weight, enabled, created_at, updated_at)
            SELECT id, ?, component_id, name, description, criterion_type,
                   expected_value, weight, enabled, created_at, updated_at
            FROM _old_evaluation_criteria
            """,
            (legacy_id,),
        )
        conn.execute(
            """
            INSERT INTO evaluation_results
                (id, system_id, trace_id, component_id, criterion_id, status,
                 score, reason, actual_output, expected_value, created_at)
            SELECT id, ?, trace_id, component_id, criterion_id, status,
                   score, reason, actual_output, expected_value, created_at
            FROM _old_evaluation_results
            """,
            (legacy_id,),
        )

    for table in _SCOPED_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS _old_{table}")
    conn.execute("DROP TABLE IF EXISTS _old_api_tokens")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    with get_conn() as conn:
        _migrate_to_system_scope(conn)
        conn.executescript(SCHEMA)
        _ensure_legacy_system(conn)
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
