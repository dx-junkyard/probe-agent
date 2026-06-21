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

CREATE TABLE IF NOT EXISTS repository_configs (
    system_id       INTEGER PRIMARY KEY,
    repo_path       TEXT NOT NULL,
    include_patterns TEXT NOT NULL DEFAULT '[]',
    exclude_patterns TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    repo_path       TEXT NOT NULL DEFAULT '',
    commit_sha      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'indexing',
    file_count      INTEGER NOT NULL DEFAULT 0,
    total_size      INTEGER NOT NULL DEFAULT 0,
    error_summary   TEXT,
    created_at      REAL NOT NULL,
    completed_at    REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_system
    ON repository_snapshots (system_id, id DESC);

CREATE TABLE IF NOT EXISTS snapshot_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL,
    path            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    content_hash    TEXT,
    content         BLOB NOT NULL DEFAULT X'',
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshot_files_snapshot
    ON snapshot_files (snapshot_id);

CREATE TABLE IF NOT EXISTS intelligence_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    snapshot_id     INTEGER NOT NULL,
    run_type        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    decision_method TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_details   TEXT,
    is_mock         INTEGER NOT NULL DEFAULT 0,
    started_at      REAL NOT NULL,
    completed_at    REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intelligence_runs_system
    ON intelligence_runs (system_id, id DESC);

CREATE TABLE IF NOT EXISTS system_profile_drafts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    intelligence_run_id  INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    name                 TEXT NOT NULL DEFAULT '',
    purpose              TEXT NOT NULL DEFAULT '',
    target_users         TEXT NOT NULL DEFAULT '[]',
    stakeholder_value    TEXT NOT NULL DEFAULT '',
    constraints          TEXT NOT NULL DEFAULT '[]',
    success_criteria     TEXT NOT NULL DEFAULT '[]',
    is_mock              INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_drafts_system
    ON system_profile_drafts (system_id, id DESC);

CREATE TABLE IF NOT EXISTS feature_drafts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    intelligence_run_id  INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    feature_id           TEXT NOT NULL,
    name                 TEXT NOT NULL,
    summary              TEXT NOT NULL DEFAULT '',
    user_value           TEXT NOT NULL DEFAULT '',
    success_criteria     TEXT NOT NULL DEFAULT '[]',
    risks                TEXT NOT NULL DEFAULT '[]',
    decision_method      TEXT NOT NULL DEFAULT 'reasoning_llm',
    is_mock              INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feature_drafts_system
    ON feature_drafts (system_id, id DESC);

CREATE TABLE IF NOT EXISTS draft_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    draft_type      TEXT NOT NULL,
    draft_id        INTEGER NOT NULL,
    path            TEXT NOT NULL,
    start_line      INTEGER NOT NULL DEFAULT 0,
    end_line        INTEGER NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_evidence_draft
    ON draft_evidence (draft_type, draft_id);

CREATE TABLE IF NOT EXISTS code_symbols (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    path            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    kind            TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    decorators      TEXT NOT NULL DEFAULT '[]',
    imports         TEXT NOT NULL DEFAULT '[]',
    docstring       TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0,
    is_pydantic_model INTEGER NOT NULL DEFAULT 0,
    route_path      TEXT,
    route_method    TEXT,
    component_id    TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_code_symbols_snapshot_name
    ON code_symbols (snapshot_id, qualified_name, path);

CREATE INDEX IF NOT EXISTS idx_code_symbols_snapshot
    ON code_symbols (snapshot_id);

CREATE INDEX IF NOT EXISTS idx_code_symbols_system
    ON code_symbols (system_id, snapshot_id);

CREATE TABLE IF NOT EXISTS symbol_index_warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    path            TEXT NOT NULL,
    message         TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbol_warnings_snapshot
    ON symbol_index_warnings (snapshot_id);

CREATE TABLE IF NOT EXISTS feature_code_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    snapshot_id     INTEGER NOT NULL,
    intelligence_run_id INTEGER NOT NULL,
    feature_id      TEXT NOT NULL,
    symbol_id       INTEGER NOT NULL,
    relation_reason TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.0,
    source          TEXT NOT NULL DEFAULT 'reasoning_llm',
    review_status   TEXT NOT NULL DEFAULT 'proposed',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (symbol_id) REFERENCES code_symbols (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feature_code_links_system
    ON feature_code_links (system_id, snapshot_id);

CREATE INDEX IF NOT EXISTS idx_feature_code_links_feature
    ON feature_code_links (system_id, feature_id);

CREATE INDEX IF NOT EXISTS idx_feature_code_links_run
    ON feature_code_links (intelligence_run_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_code_links_run_symbol
    ON feature_code_links (intelligence_run_id, feature_id, symbol_id);

CREATE TABLE IF NOT EXISTS probe_plans (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    intelligence_run_id  INTEGER NOT NULL,
    feature_id           TEXT NOT NULL,
    objective            TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'proposed',
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_probe_plans_system
    ON probe_plans (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_probe_plans_feature
    ON probe_plans (system_id, feature_id);

CREATE TABLE IF NOT EXISTS probe_points (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id              INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    component_id         TEXT NOT NULL,
    feature_id           TEXT NOT NULL,
    path                 TEXT NOT NULL,
    symbol               TEXT NOT NULL,
    line_start           INTEGER NOT NULL,
    line_end             INTEGER NOT NULL,
    reason               TEXT NOT NULL,
    recommended_mode     TEXT NOT NULL DEFAULT 'trace',
    side_effect_risk     TEXT NOT NULL DEFAULT 'low',
    replayability        TEXT NOT NULL DEFAULT '',
    denylist_hit         TEXT,
    status               TEXT NOT NULL DEFAULT 'proposed',
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES probe_plans (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_probe_points_plan
    ON probe_points (plan_id);

CREATE INDEX IF NOT EXISTS idx_probe_points_system
    ON probe_points (system_id, plan_id);

CREATE TABLE IF NOT EXISTS probe_patches (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id              INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    commit_sha           TEXT NOT NULL,
    diff                 TEXT NOT NULL DEFAULT '',
    worktree_path        TEXT,
    skipped              TEXT NOT NULL DEFAULT '[]',
    status               TEXT NOT NULL DEFAULT 'generated',
    error                TEXT,
    cleanup_state        TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error        TEXT,
    created_at           REAL NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES probe_plans (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_probe_patches_plan
    ON probe_patches (plan_id);

CREATE TABLE IF NOT EXISTS validation_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_id             INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    variant              TEXT NOT NULL,
    worktree_path        TEXT NOT NULL,
    overall_success      INTEGER NOT NULL DEFAULT 0,
    total_duration_ms    REAL NOT NULL DEFAULT 0.0,
    trace_received       INTEGER,
    trace_status         TEXT NOT NULL DEFAULT 'not_checked',
    network_isolation    TEXT NOT NULL DEFAULT 'not_requested',
    cleanup_state        TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error        TEXT,
    error                TEXT,
    created_at           REAL NOT NULL,
    FOREIGN KEY (patch_id) REFERENCES probe_patches (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_validation_runs_patch
    ON validation_runs (patch_id);

CREATE TABLE IF NOT EXISTS validation_commands (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               INTEGER NOT NULL,
    command              TEXT NOT NULL,
    exit_code            INTEGER NOT NULL,
    duration_ms          REAL NOT NULL DEFAULT 0.0,
    stdout               TEXT NOT NULL DEFAULT '',
    stderr               TEXT NOT NULL DEFAULT '',
    stdout_truncated     INTEGER NOT NULL DEFAULT 0,
    stderr_truncated     INTEGER NOT NULL DEFAULT 0,
    timed_out            INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES validation_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_validation_commands_run
    ON validation_commands (run_id);

CREATE TABLE IF NOT EXISTS experiments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    feature_id           TEXT NOT NULL,
    objective            TEXT NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    baseline_commit      TEXT NOT NULL,
    config_revision      TEXT NOT NULL,
    execution_config     TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'draft',
    error                TEXT,
    human_decision       TEXT NOT NULL DEFAULT 'undecided',
    human_decision_variant_key TEXT,
    human_decision_note  TEXT NOT NULL DEFAULT '',
    created_at           REAL NOT NULL,
    started_at           REAL,
    completed_at         REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_experiments_system
    ON experiments (system_id, id DESC);

CREATE TABLE IF NOT EXISTS experiment_variants (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id        INTEGER NOT NULL,
    variant_key          TEXT NOT NULL,
    label                TEXT NOT NULL,
    is_baseline          INTEGER NOT NULL DEFAULT 0,
    patch_text           TEXT NOT NULL DEFAULT '',
    patch_hash           TEXT NOT NULL,
    source               TEXT NOT NULL DEFAULT 'manual',
    risk_note            TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'planned',
    error                TEXT,
    workspace_path       TEXT,
    cleanup_state        TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error        TEXT,
    metrics_json         TEXT NOT NULL DEFAULT '{}',
    artifacts_json       TEXT NOT NULL DEFAULT '{}',
    started_at           REAL,
    completed_at         REAL,
    FOREIGN KEY (experiment_id) REFERENCES experiments (id) ON DELETE CASCADE,
    UNIQUE (experiment_id, variant_key)
);

CREATE INDEX IF NOT EXISTS idx_experiment_variants_experiment
    ON experiment_variants (experiment_id, id);

CREATE TABLE IF NOT EXISTS experiment_commands (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id           INTEGER NOT NULL,
    phase                TEXT NOT NULL,
    command              TEXT NOT NULL,
    exit_code            INTEGER NOT NULL,
    duration_ms          REAL NOT NULL DEFAULT 0.0,
    stdout               TEXT NOT NULL DEFAULT '',
    stderr               TEXT NOT NULL DEFAULT '',
    stdout_truncated     INTEGER NOT NULL DEFAULT 0,
    stderr_truncated     INTEGER NOT NULL DEFAULT 0,
    timed_out            INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (variant_id) REFERENCES experiment_variants (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_experiment_commands_variant
    ON experiment_commands (variant_id, id);

CREATE TABLE IF NOT EXISTS experiment_analyses (
    experiment_id        INTEGER PRIMARY KEY,
    status               TEXT NOT NULL DEFAULT 'pending',
    provider             TEXT,
    model                TEXT,
    prompt_version       TEXT,
    schema_version       TEXT,
    decision_method      TEXT,
    narrative            TEXT,
    recommendation_variant_key TEXT,
    recommendation_reason TEXT,
    risks_json           TEXT NOT NULL DEFAULT '[]',
    error                TEXT,
    created_at           REAL,
    FOREIGN KEY (experiment_id) REFERENCES experiments (id) ON DELETE CASCADE
);
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
        if "content" not in _columns(conn, "snapshot_files"):
            conn.execute(
                "ALTER TABLE snapshot_files ADD COLUMN content BLOB NOT NULL DEFAULT X''"
            )
        if "repo_path" not in _columns(conn, "repository_snapshots"):
            conn.execute(
                "ALTER TABLE repository_snapshots "
                "ADD COLUMN repo_path TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                """
                UPDATE repository_snapshots
                SET repo_path = COALESCE(
                    (SELECT repo_path FROM repository_configs
                     WHERE repository_configs.system_id = repository_snapshots.system_id),
                    ''
                )
                WHERE repo_path = ''
                """
            )
        if "imports" not in _columns(conn, "code_symbols"):
            conn.execute(
                "ALTER TABLE code_symbols ADD COLUMN imports TEXT NOT NULL DEFAULT '[]'"
            )
        if "component_id" not in _columns(conn, "code_symbols"):
            conn.execute("ALTER TABLE code_symbols ADD COLUMN component_id TEXT")
        validation_columns = _columns(conn, "validation_runs")
        if "trace_received" not in validation_columns:
            conn.execute("ALTER TABLE validation_runs ADD COLUMN trace_received INTEGER")
        if "trace_status" not in validation_columns:
            conn.execute(
                "ALTER TABLE validation_runs ADD COLUMN trace_status TEXT NOT NULL DEFAULT 'not_checked'"
            )
        if "network_isolation" not in validation_columns:
            conn.execute(
                "ALTER TABLE validation_runs ADD COLUMN network_isolation TEXT NOT NULL DEFAULT 'not_requested'"
            )
        if "cleanup_state" not in validation_columns:
            conn.execute(
                "ALTER TABLE validation_runs ADD COLUMN cleanup_state TEXT NOT NULL DEFAULT 'not_attempted'"
            )
        if "cleanup_error" not in validation_columns:
            conn.execute("ALTER TABLE validation_runs ADD COLUMN cleanup_error TEXT")
        patch_columns = _columns(conn, "probe_patches")
        if "cleanup_state" not in patch_columns:
            conn.execute(
                "ALTER TABLE probe_patches "
                "ADD COLUMN cleanup_state TEXT NOT NULL DEFAULT 'not_attempted'"
            )
        if "cleanup_error" not in patch_columns:
            conn.execute("ALTER TABLE probe_patches ADD COLUMN cleanup_error TEXT")
        experiment_columns = _columns(conn, "experiments")
        if "human_decision_variant_key" not in experiment_columns:
            conn.execute(
                "ALTER TABLE experiments "
                "ADD COLUMN human_decision_variant_key TEXT"
            )
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
