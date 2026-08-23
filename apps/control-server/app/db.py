import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .intelligence_run_types import install_intelligence_run_type_guards

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# ``_lock`` is a plain, NON-reentrant Lock and it is held for the whole lifetime
# of the connection yielded by get_conn(). The Control Server runs as a single
# uvicorn worker, so that lock is effectively process-wide: while it is held, no
# other request can touch the database.
#
# Two consequences, both enforced by _open_depth below:
#
# 1. Re-entering get_conn() on a thread that already holds a connection is a
#    permanent self-deadlock -- the thread waits on a lock only it can release,
#    and every other request piles up behind it until the process is restarted.
#    The most common way to hit this is indirect: an LLM client call, which
#    consumes System quota through resource_limits.consume_llm_execution() and
#    therefore opens its own connection.
# 2. Even without re-entry, holding the connection across an external call (an
#    LLM round trip, a subprocess) stalls every other request for its duration.
#
# The rule is therefore: never call get_conn() -- directly or transitively --
# while another connection is open on the same thread, and never keep one open
# across an external call. Read what is needed, close the connection, do the
# external work, then reopen to persist. The canonical example is
# routes/interview.py::run_runtime_reality_check.
_open_depth = threading.local()


class DatabaseReentrancyError(RuntimeError):
    """get_conn() was re-entered while this thread already held a connection.

    Raised instead of deadlocking, so the offending call stack is visible in
    the traceback rather than the process hanging forever.
    """


def db_path() -> str:
    return os.getenv("PROBE_DB_PATH", "./probe.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connection_is_open() -> bool:
    """True when this thread already holds a get_conn() connection."""
    return getattr(_open_depth, "value", 0) > 0


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    if connection_is_open():
        raise DatabaseReentrancyError(
            "get_conn() was called while this thread already holds a database "
            "connection. The connection lock is process-wide and not reentrant, "
            "so this would deadlock the whole server permanently. Close the "
            "outer connection before doing this work -- in particular, never "
            "call an LLM client inside a `with get_conn()` block: consuming "
            "System quota opens its own connection."
        )
    with _lock:
        _open_depth.value = 1
        conn = _connect()
        try:
            yield conn
        finally:
            _open_depth.value = 0
            conn.close()


_SOLUTION_DESIGN_OPTION_DDL = """
CREATE TABLE IF NOT EXISTS solution_design_option (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_design_id   INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    option_key           TEXT NOT NULL,
    option_order         INTEGER NOT NULL,
    title                TEXT NOT NULL DEFAULT '',
    approach             TEXT NOT NULL DEFAULT '',
    tradeoffs            TEXT NOT NULL DEFAULT '',
    risks                TEXT NOT NULL DEFAULT '',
    content_digest       TEXT NOT NULL,
    authored_by_kind     TEXT NOT NULL DEFAULT 'developer'
                             CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id  INTEGER,
    created_by           TEXT,
    created_at           REAL NOT NULL,
    superseded_by_id     INTEGER,
    schema_version       TEXT NOT NULL DEFAULT 'solution-design-option-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (solution_design_id) REFERENCES solution_design (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES solution_design_option (id) ON DELETE SET NULL
);

-- The uniqueness of an option_key holds over the CURRENT row only, which is
-- why it is a partial index here and not a table-level UNIQUE. An unqualified
-- UNIQUE (solution_design_id, option_key) contradicts the append-only rule
-- this table is built on: correcting an option inserts a new row and marks the
-- old one superseded, so the second INSERT would collide with the very row it
-- is replacing and the correction could never be recorded at all. It also took
-- SolutionLinkStaleReason.design_changed with it -- that reason is only
-- reachable once an option row HAS been superseded, so an unqualified
-- constraint made a documented finite value permanently unreachable rather
-- than merely unused. Same idiom as ux_evolution_node_event_idempotency above.
CREATE UNIQUE INDEX IF NOT EXISTS ux_solution_design_option_current
    ON solution_design_option (solution_design_id, option_key)
    WHERE superseded_by_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_solution_design_option_design
    ON solution_design_option (solution_design_id, option_order);
"""


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
    -- Replay capture (Issue #242 Phase A / #243), all additive. NULL means
    -- the trace predates Phase A or its component is not opted into replay
    -- capture; old rows are never bulk-reclassified.
    input_capture_json  TEXT,
    replayability       TEXT,
    replay_reasons_json TEXT,
    -- Issue #290 Finding 5: optional deployment provenance reported by the
    -- SDK (PROBE_ENVIRONMENT / PROBE_GIT_SHA). NULL when the caller never
    -- set the env var; never backfilled or inferred.
    environment  TEXT,
    git_sha      TEXT,
    -- Issue #367: audit summary of the redaction the Control Server itself
    -- applied at ingestion ({"redacted": true, "rules": [...],
    -- "fields": [...]}). NULL means this row's payload needed no
    -- server-side redaction -- NOT that redaction did not run, and NOT that
    -- the SDK found nothing (the SDK masks before sending and does not
    -- report what it masked).
    redaction_json TEXT,
    PRIMARY KEY (system_id, trace_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_traces_component_ts
    ON traces (system_id, component_id, timestamp DESC);

-- Issue #370: per-System freshness thresholds. probe-agent cannot know a
-- system's expected traffic rate, so the defaults in state_facts.py only
-- separate "clearly live" from "clearly not"; a System that knows its own
-- cadence narrows them here. Absent row = documented defaults.
CREATE TABLE IF NOT EXISTS connectivity_freshness_policy (
    system_id             INTEGER PRIMARY KEY,
    delayed_after_seconds REAL NOT NULL,
    stale_after_seconds   REAL NOT NULL,
    updated_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

-- Issue #273: durable, System-isolated resource counters and observations.
CREATE TABLE IF NOT EXISTS llm_daily_usage (
    system_id       INTEGER NOT NULL,
    usage_date      TEXT NOT NULL,
    execution_count INTEGER NOT NULL DEFAULT 0,
    updated_at      REAL NOT NULL,
    PRIMARY KEY (system_id, usage_date),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_quota_status (
    system_id       INTEGER PRIMARY KEY,
    trace_rows      INTEGER NOT NULL DEFAULT 0,
    trace_bytes     INTEGER NOT NULL DEFAULT 0,
    rejected_reason TEXT,
    rejected_at     REAL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sdk_transport_observations (
    system_id      INTEGER NOT NULL,
    trace_id       TEXT NOT NULL,
    dropped_count  INTEGER NOT NULL DEFAULT 0,
    failure_count  INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    observed_at    REAL NOT NULL,
    PRIMARY KEY (system_id, trace_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id, trace_id)
        REFERENCES traces (system_id, trace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sdk_transport_observations_system
    ON sdk_transport_observations (system_id, observed_at DESC);

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

-- Trace lineage (Issue #145, Phase 1). Optional correlation metadata is kept
-- out of traces.input_json in dedicated, indexed tables. Backward compatible:
-- traces without lineage simply have no rows here.
CREATE TABLE IF NOT EXISTS trace_spans (
    system_id      INTEGER NOT NULL,
    trace_id       TEXT NOT NULL,
    component_id   TEXT NOT NULL,
    span_id        TEXT,
    parent_span_id TEXT,
    flow_id        TEXT,
    correlation_id TEXT,
    timestamp      REAL NOT NULL,
    PRIMARY KEY (system_id, trace_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_correlation
    ON trace_spans (system_id, correlation_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_trace_spans_flow
    ON trace_spans (system_id, flow_id, timestamp);

CREATE TABLE IF NOT EXISTS trace_entities (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id    INTEGER NOT NULL,
    trace_id     TEXT NOT NULL,
    component_id TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'related',
    timestamp    REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_entities_lookup
    ON trace_entities (system_id, entity_type, entity_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_trace_entities_trace
    ON trace_entities (system_id, trace_id);

-- Declarative projections (Issue #146, Phase 2). Stores only the bounded,
-- structured slice produced by a projection spec — never the raw payload.
-- phase is 'input' | 'output' here; 'shadow_current' / 'shadow_candidate'
-- are added by Issue #150 (Phase 5).
CREATE TABLE IF NOT EXISTS trace_projections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    trace_id        TEXT NOT NULL,
    component_id    TEXT NOT NULL,
    projection_name TEXT NOT NULL,
    phase           TEXT NOT NULL,
    data_json       TEXT NOT NULL,
    data_hash       TEXT,
    truncated       INTEGER NOT NULL DEFAULT 0,
    extract_error   TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (system_id, trace_id, component_id, projection_name, phase),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_projections_trace
    ON trace_projections (system_id, trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_projections_component
    ON trace_projections (system_id, component_id, created_at DESC);

-- Trace analyzers (Issue #148, Phase 4a). Saved, reviewable, read-only views
-- over trace_projections. LLM-proposal columns (provider/model/prompt_version/
-- schema_version) are written by Issue #149; the table (audit contract) is
-- owned here.
CREATE TABLE IF NOT EXISTS trace_analyzers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    intent          TEXT NOT NULL DEFAULT '',
    spec_json       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'trace_projections',
    review_status   TEXT NOT NULL DEFAULT 'proposed',
    decision_method TEXT NOT NULL DEFAULT 'manual',
    provider        TEXT,
    model           TEXT,
    prompt_version  TEXT,
    schema_version  TEXT,
    is_mock         INTEGER NOT NULL DEFAULT 0,
    -- The human review decision is its own audit record (Principle 7):
    -- decision_method above describes who AUTHORED the spec (manual /
    -- reasoning_llm); review_decision_method records that the approve/reject
    -- decision was made by a human ('manual'), never by the LLM.
    reviewed_at            REAL,
    review_decision_method TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_analyzers_system
    ON trace_analyzers (system_id, id DESC);

CREATE TABLE IF NOT EXISTS trace_analysis_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     INTEGER NOT NULL,
    analyzer_id   INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    result_json   TEXT,
    error_details TEXT,
    row_count     INTEGER,
    started_at    REAL NOT NULL,
    completed_at  REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (analyzer_id) REFERENCES trace_analyzers (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_analysis_runs_analyzer
    ON trace_analysis_runs (system_id, analyzer_id, id DESC);

-- Retention policies + audit (Issue #152). Explicit, per-target settings for
-- lineage/projection/analyzer-run data. Default behaviour with no rows is
-- "never delete". target_table is one of trace_spans / trace_entities /
-- trace_projections / trace_analysis_runs.
CREATE TABLE IF NOT EXISTS retention_policies (
    system_id    INTEGER NOT NULL,
    target_table TEXT NOT NULL,
    max_age_days REAL,
    max_count    INTEGER,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (system_id, target_table),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS retention_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     INTEGER NOT NULL,
    target_table  TEXT NOT NULL,
    deleted_count INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    executed_at   REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_retention_audit_system
    ON retention_audit (system_id, id DESC);

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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    repo_path           TEXT NOT NULL DEFAULT '',
    commit_sha          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'indexing',
    file_count          INTEGER NOT NULL DEFAULT 0,
    total_size          INTEGER NOT NULL DEFAULT 0,
    indexed_size        INTEGER NOT NULL DEFAULT 0,
    metadata_only_count INTEGER NOT NULL DEFAULT 0,
    warnings            TEXT NOT NULL DEFAULT '[]',
    error_summary       TEXT,
    created_at          REAL NOT NULL,
    completed_at        REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_system
    ON repository_snapshots (system_id, id DESC);

-- Explicit repository refresh jobs (Issue #277). The target repository is
-- read-only; jobs persist orchestration state and generated DB artifacts only.
CREATE TABLE IF NOT EXISTS repository_resync_jobs (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                INTEGER NOT NULL,
    snapshot_id              INTEGER,
    status                   TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'snapshotting', 'indexing', 'completed',
                          'snapshot_failed', 'index_failed')),
    error                    TEXT,
    stale_capability_count   INTEGER NOT NULL DEFAULT 0,
    created_at               REAL NOT NULL,
    started_at               REAL,
    completed_at             REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_repository_resync_jobs_system
    ON repository_resync_jobs (system_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_repository_resync_jobs_active_system
    ON repository_resync_jobs (system_id)
    WHERE status IN ('queued', 'snapshotting', 'indexing');

CREATE TABLE IF NOT EXISTS snapshot_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id      INTEGER NOT NULL,
    path             TEXT NOT NULL,
    source_type      TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    content_hash     TEXT,
    content          BLOB NOT NULL DEFAULT X'',
    inclusion_status TEXT NOT NULL DEFAULT 'indexed',
    exclusion_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshot_files_snapshot
    ON snapshot_files (snapshot_id);

CREATE TABLE IF NOT EXISTS intelligence_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    -- Nullable since Issue #149: reasoning runs that are not tied to a
    -- repository snapshot (e.g. analyzer proposals over runtime traces).
    snapshot_id     INTEGER,
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
    symbol_source_hash TEXT,
    symbol_body_hash   TEXT,
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

-- Source-anchored explanation metadata (Issue #54).  Author-written facts
-- copied verbatim from docstrings of a pinned snapshot.  Kept separate from
-- reasoning-model interpretations; origin is always 'source_authored'.
CREATE TABLE IF NOT EXISTS symbol_source_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    symbol_id       INTEGER NOT NULL,
    path            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    role            TEXT,
    capability      TEXT,
    element_type    TEXT,
    system_purpose  TEXT,
    operation_kind  TEXT,
    consumers       TEXT NOT NULL DEFAULT '[]',
    state_effects   TEXT NOT NULL DEFAULT '[]',
    probe_value     TEXT,
    raw_block       TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'source_authored',
    explanation_hash TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (symbol_id) REFERENCES code_symbols (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbol_source_metadata_snapshot
    ON symbol_source_metadata (snapshot_id);

CREATE INDEX IF NOT EXISTS idx_symbol_source_metadata_symbol
    ON symbol_source_metadata (symbol_id);

CREATE INDEX IF NOT EXISTS idx_symbol_source_metadata_system
    ON symbol_source_metadata (system_id, snapshot_id);

-- Explanation-to-source dependency anchors (Issue #55).  Each source-authored
-- explanation records the deterministic provenance it depends on: the file,
-- the symbol span, and the three hash types.  Downstream drift features compare
-- these hashes against a newer snapshot.  Hash equality is only a change
-- signal, never proof of semantic equality.
CREATE TABLE IF NOT EXISTS explanation_source_anchors (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id        INTEGER NOT NULL,
    system_id          INTEGER NOT NULL,
    metadata_id        INTEGER NOT NULL,
    symbol_id          INTEGER NOT NULL,
    path               TEXT NOT NULL,
    qualified_name     TEXT NOT NULL,
    start_line         INTEGER NOT NULL,
    end_line           INTEGER NOT NULL,
    file_content_hash  TEXT,
    symbol_source_hash TEXT,
    symbol_body_hash   TEXT,
    explanation_hash   TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (metadata_id) REFERENCES symbol_source_metadata (id) ON DELETE CASCADE,
    FOREIGN KEY (symbol_id) REFERENCES code_symbols (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_explanation_anchors_snapshot
    ON explanation_source_anchors (snapshot_id);

CREATE INDEX IF NOT EXISTS idx_explanation_anchors_system
    ON explanation_source_anchors (system_id, snapshot_id);

-- Source-backed capability hierarchy (Issue #56). One row per hierarchy node,
-- discriminated by node_type (purpose|capability|element|supporting) and linked
-- to its parent via parent_id. Each node records its provenance (source anchor,
-- hashes, provenance_kind, decision_method, provider/model) so source-authored
-- explanation, deterministic structural fact, and reasoning interpretation stay
-- separable. Scoped by system and repository snapshot.
CREATE TABLE IF NOT EXISTS capability_hierarchy_nodes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id               INTEGER NOT NULL,
    snapshot_id             INTEGER NOT NULL,
    intelligence_run_id     INTEGER NOT NULL,
    parent_id               INTEGER,
    node_type               TEXT NOT NULL,
    name                    TEXT NOT NULL DEFAULT '',
    summary                 TEXT NOT NULL DEFAULT '',
    capability_key          TEXT,
    element_role            TEXT,
    operation_kind          TEXT,
    probe_value             TEXT,
    supporting_kind         TEXT,
    classification          TEXT,
    symbol_id               INTEGER,
    entrypoint_id           INTEGER,
    feature_id              TEXT,
    system_profile_draft_id INTEGER,
    path                    TEXT,
    qualified_name          TEXT,
    start_line              INTEGER,
    end_line                INTEGER,
    file_content_hash       TEXT,
    symbol_source_hash      TEXT,
    explanation_hash        TEXT,
    provenance_kind         TEXT NOT NULL DEFAULT 'structural',
    decision_method         TEXT NOT NULL DEFAULT 'deterministic',
    provider                TEXT,
    model                   TEXT,
    created_at              REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES capability_hierarchy_nodes (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_capability_hierarchy_run
    ON capability_hierarchy_nodes (intelligence_run_id);

CREATE INDEX IF NOT EXISTS idx_capability_hierarchy_system
    ON capability_hierarchy_nodes (system_id, snapshot_id);

-- Explanation refresh proposals (Issue #59). When a source-backed explanation
-- drifts (#57), a reasoning model proposes updated wording/metadata. Each row
-- is a reviewable SUGGESTION only: probe-agent never edits the target source
-- repository, and a developer must apply the change to the source by hand. The
-- run audit (provider/model/prompt/schema/status/error) lives in
-- intelligence_runs; this table stores the proposal payload and the captured
-- vs. current source provenance it was generated from. Scoped by system.
CREATE TABLE IF NOT EXISTS explanation_refresh_proposals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    intelligence_run_id   INTEGER NOT NULL,
    base_snapshot_id      INTEGER NOT NULL,
    target_snapshot_id    INTEGER NOT NULL,
    node_id               INTEGER,
    node_type             TEXT NOT NULL DEFAULT '',
    name                  TEXT NOT NULL DEFAULT '',
    entrypoint_type       TEXT,
    entrypoint_id         TEXT,
    path                  TEXT,
    qualified_name        TEXT,
    drift_status          TEXT NOT NULL DEFAULT '',
    drift_reason          TEXT NOT NULL DEFAULT '',
    changed_hashes        TEXT NOT NULL DEFAULT '[]',
    old_explanation       TEXT NOT NULL DEFAULT '',
    proposed_explanation  TEXT,
    proposed_metadata     TEXT,
    summary_of_changes    TEXT,
    confidence            REAL,
    captured_file_content_hash  TEXT,
    captured_symbol_source_hash TEXT,
    captured_explanation_hash   TEXT,
    current_file_content_hash   TEXT,
    current_symbol_source_hash  TEXT,
    current_explanation_hash    TEXT,
    status                TEXT NOT NULL DEFAULT 'proposed',
    is_mock               INTEGER NOT NULL DEFAULT 0,
    provider              TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    decision_method       TEXT NOT NULL DEFAULT 'reasoning_llm',
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refresh_proposals_system
    ON explanation_refresh_proposals (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_proposals_run
    ON explanation_refresh_proposals (intelligence_run_id);

CREATE TABLE IF NOT EXISTS code_entrypoints (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id               INTEGER NOT NULL,
    snapshot_id             INTEGER NOT NULL,
    entrypoint_type         TEXT NOT NULL,
    entrypoint_id           TEXT NOT NULL,
    category                TEXT NOT NULL,
    label                   TEXT NOT NULL,
    operation               TEXT,
    framework               TEXT,
    handler_symbol_id       INTEGER,
    handler_path            TEXT NOT NULL,
    handler_qualified_name  TEXT NOT NULL,
    line_start              INTEGER NOT NULL,
    line_end                INTEGER NOT NULL,
    route_method            TEXT,
    route_path              TEXT,
    confidence              REAL NOT NULL DEFAULT 1.0,
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    source                  TEXT NOT NULL DEFAULT 'deterministic',
    pattern_id              INTEGER,
    created_at              REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (handler_symbol_id) REFERENCES code_symbols (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_code_entrypoints_unique
    ON code_entrypoints (snapshot_id, entrypoint_type, entrypoint_id);

CREATE INDEX IF NOT EXISTS idx_code_entrypoints_system
    ON code_entrypoints (system_id, snapshot_id);

CREATE TABLE IF NOT EXISTS code_entrypoint_patterns (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id               INTEGER NOT NULL,
    snapshot_id             INTEGER NOT NULL,
    intelligence_run_id     INTEGER NOT NULL,
    file_glob               TEXT NOT NULL,
    regex                   TEXT NOT NULL,
    method_group            TEXT,
    path_group              TEXT,
    method_constant         TEXT,
    framework               TEXT NOT NULL,
    language                TEXT NOT NULL,
    reason                  TEXT NOT NULL,
    confidence              REAL NOT NULL DEFAULT 0.0,
    match_count             INTEGER NOT NULL DEFAULT 0,
    examples_json           TEXT NOT NULL DEFAULT '[]',
    created_at              REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_entrypoint_patterns_system
    ON code_entrypoint_patterns (system_id, snapshot_id);

CREATE INDEX IF NOT EXISTS idx_code_entrypoint_patterns_run
    ON code_entrypoint_patterns (intelligence_run_id);

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
    origin               TEXT NOT NULL DEFAULT 'manual',
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
    apply_status         TEXT NOT NULL DEFAULT 'not_applied',
    apply_error          TEXT,
    applied_at           REAL,
    applied_by_user_id   INTEGER,
    created_at           REAL NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES probe_plans (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (applied_by_user_id) REFERENCES users (id) ON DELETE SET NULL
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

-- Canonical lineage for an adopted improvement that is ready to enter the
-- existing approval-gated GitHub publish workflow.  ``patch_id`` is the
-- transport artifact consumed by publish_jobs; experiment_id + variant_id
-- are the semantic identity.  Overview must join through this table and may
-- never infer lineage from System-wide timestamps or existence checks.
CREATE TABLE IF NOT EXISTS improvement_publish_artifacts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    experiment_id        INTEGER NOT NULL,
    variant_id           INTEGER NOT NULL,
    patch_id             INTEGER NOT NULL,
    status               TEXT NOT NULL DEFAULT 'ready',
    error                TEXT,
    created_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (experiment_id) REFERENCES experiments (id) ON DELETE CASCADE,
    FOREIGN KEY (variant_id) REFERENCES experiment_variants (id) ON DELETE CASCADE,
    FOREIGN KEY (patch_id) REFERENCES probe_patches (id) ON DELETE CASCADE,
    UNIQUE (experiment_id, variant_id),
    UNIQUE (patch_id)
);

CREATE INDEX IF NOT EXISTS idx_improvement_publish_artifacts_system
    ON improvement_publish_artifacts (system_id, id DESC);

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

CREATE TABLE IF NOT EXISTS workspaces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     INTEGER NOT NULL,
    title         TEXT NOT NULL,
    focus         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    summary       TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspaces_system
    ON workspaces (system_id, id DESC);

CREATE TABLE IF NOT EXISTS workspace_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id      INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    context_metadata  TEXT NOT NULL DEFAULT '{}',
    created_at        REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_messages_workspace
    ON workspace_messages (workspace_id, id);

CREATE TABLE IF NOT EXISTS workspace_context_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL,
    system_id     INTEGER NOT NULL,
    item_type     TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_context_items_workspace
    ON workspace_context_items (workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_context_items_unique
    ON workspace_context_items (workspace_id, item_type, item_id);

CREATE TABLE IF NOT EXISTS workspace_proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL,
    system_id     INTEGER NOT NULL,
    message_id    INTEGER,
    proposal_type TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'proposed',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES workspace_messages (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_proposals_workspace
    ON workspace_proposals (workspace_id, id);

CREATE TABLE IF NOT EXISTS workspace_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id         INTEGER NOT NULL,
    workspace_id        INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    decision            TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    decided_by_user_id  INTEGER,
    created_at          REAL NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES workspace_proposals (id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_decisions_proposal
    ON workspace_decisions (proposal_id, id DESC);

CREATE TABLE IF NOT EXISTS workspace_proposal_drafts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id        INTEGER NOT NULL,
    proposal_id         INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    draft_type          TEXT NOT NULL,
    target_screen       TEXT NOT NULL,
    payload             TEXT NOT NULL DEFAULT '{}',
    missing_fields      TEXT NOT NULL DEFAULT '[]',
    created_by_user_id  INTEGER,
    created_at          REAL NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_id) REFERENCES workspace_proposals (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_proposal_drafts_proposal
    ON workspace_proposal_drafts (proposal_id);

-- System-understanding interview persistence (Issue #67). This is the #35
-- analogue for the #66 conversational metadata/probe authoring flow: a pure
-- persistence + CRUD layer with no LLM calls and no worktree writes. A session
-- is bound to one system and one pinned repository snapshot; messages are the
-- ordered conversation turns; proposals are one row per proposed symbol holding
-- both the proposed `probe-agent:` docstring metadata block (#54 vocabulary)
-- and the associated probe-plan fields (#25 model). Reasoning-run audit
-- metadata lives in the shared intelligence_runs store and is referenced from
-- messages/proposals via intelligence_run_id rather than duplicated here.
CREATE TABLE IF NOT EXISTS interview_session (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    title                TEXT NOT NULL DEFAULT '',
    focus                TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'open',
    stage                TEXT NOT NULL DEFAULT 'understanding_initialized',
    current_understanding TEXT,
    gap_analysis         TEXT,
    open_questions       TEXT,
    user_intent          TEXT,
    last_error           TEXT,
    -- Issue #123: manual confirmation that unlocks proposal generation when
    -- no structured understanding could be built (zero-base interview).
    understanding_confirmed_at REAL,
    understanding_confirmed_by TEXT,
    materialization_diff TEXT,
    materialization_ref  TEXT,
    materialized_at      REAL,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_session_system
    ON interview_session (system_id, id DESC);

CREATE TABLE IF NOT EXISTS interview_message (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    role                TEXT NOT NULL,
    content             TEXT NOT NULL,
    intelligence_run_id INTEGER,
    created_at          REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_message_session
    ON interview_message (session_id, id);

CREATE TABLE IF NOT EXISTS interview_proposal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    snapshot_id         INTEGER NOT NULL,
    message_id          INTEGER,
    intelligence_run_id INTEGER NOT NULL,
    symbol_id           INTEGER,
    path                TEXT NOT NULL,
    qualified_name      TEXT NOT NULL,
    -- #54 docstring metadata block: free-text fields.
    md_role             TEXT,
    md_capability       TEXT,
    md_system_purpose   TEXT,
    md_probe_value      TEXT,
    -- #54 docstring metadata block: finite-vocabulary fields (validated in API).
    md_element_type     TEXT,
    md_operation_kind   TEXT,
    md_consumers        TEXT NOT NULL DEFAULT '[]',
    md_state_effects    TEXT NOT NULL DEFAULT '[]',
    -- #25 probe-plan fields for the same symbol.
    feature_id          TEXT NOT NULL DEFAULT '',
    objective           TEXT NOT NULL DEFAULT '',
    probe_reason        TEXT NOT NULL DEFAULT '',
    recommended_mode    TEXT NOT NULL DEFAULT 'trace',
    side_effect_risk    TEXT NOT NULL DEFAULT 'low',
    replayability       TEXT NOT NULL DEFAULT 'safe',
    -- Provenance: link to understanding graph node and capability scope.
    graph_node_id       TEXT,
    capability_name     TEXT,
    evidence_summary    TEXT,
    proposal_confidence REAL,
    -- Audit + per-item approval. decision_method is the Principle 7 enum;
    -- newly stored proposals are reasoning_llm (this issue never sets manual).
    decision_method     TEXT NOT NULL DEFAULT 'reasoning_llm',
    approval_state      TEXT NOT NULL DEFAULT 'proposed',
    is_mock             INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES interview_message (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (symbol_id) REFERENCES code_symbols (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_proposal_session
    ON interview_proposal (session_id, id);

CREATE INDEX IF NOT EXISTS idx_interview_proposal_system
    ON interview_proposal (system_id, session_id);

CREATE TABLE IF NOT EXISTS interview_snapshot_rebase (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                      INTEGER NOT NULL,
    system_id                       INTEGER NOT NULL,
    from_snapshot_id                INTEGER NOT NULL,
    to_snapshot_id                  INTEGER NOT NULL,
    actor                           TEXT NOT NULL DEFAULT '',
    proposals_preserved             INTEGER NOT NULL DEFAULT 0,
    proposals_marked_needs_review   INTEGER NOT NULL DEFAULT 0,
    proposals_missing_source        INTEGER NOT NULL DEFAULT 0,
    proposals_changed_source        INTEGER NOT NULL DEFAULT 0,
    details_json                    TEXT NOT NULL DEFAULT '{}',
    created_at                      REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (from_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (to_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_snapshot_rebase_session
    ON interview_snapshot_rebase (session_id, id DESC);

-- Issue #70: per-item approval gate with manual decision record.
-- Each decision is a separate row that references — but does not overwrite —
-- the original reasoning_llm proposal. For edits, the developer-corrected
-- metadata and probe-plan values are stored here. decision_method is always
-- 'manual' for rows in this table.
CREATE TABLE IF NOT EXISTS interview_proposal_decision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id         INTEGER NOT NULL,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    decision            TEXT NOT NULL,
    decision_method     TEXT NOT NULL DEFAULT 'manual',
    actor               TEXT NOT NULL DEFAULT '',
    -- Edited metadata (populated only for decision='edited').
    edited_md_role             TEXT,
    edited_md_capability       TEXT,
    edited_md_system_purpose   TEXT,
    edited_md_probe_value      TEXT,
    edited_md_element_type     TEXT,
    edited_md_operation_kind   TEXT,
    edited_md_consumers        TEXT,
    edited_md_state_effects    TEXT,
    -- Edited probe-plan (populated only for decision='edited').
    edited_feature_id          TEXT,
    edited_objective           TEXT,
    edited_probe_reason        TEXT,
    edited_recommended_mode    TEXT,
    edited_side_effect_risk    TEXT,
    edited_replayability       TEXT,
    -- Denylist re-check result for edits.
    denylist_hit        TEXT,
    decided_at          REAL NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES interview_proposal (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_proposal_decision_proposal
    ON interview_proposal_decision (proposal_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_interview_proposal_decision_session
    ON interview_proposal_decision (session_id);

-- Structured interview Q&A (Issue #129). Each row is one question; answering
-- a question the first time sets answer_text/status on the same row, but
-- *correcting* an existing answer never overwrites it — it inserts a new row
-- with the corrected answer and links the old row forward via
-- superseded_by_id, keeping every prior answer auditable (Principle 7).
-- question_category/question_source/status are explicit finite sets
-- (Principle 6, deterministic). hypothesis/evidence_refs are populated by
-- Issue #130's evidence-backed dialogue turns; both are nullable because
-- not every question carries a hypothesis or read evidence.
CREATE TABLE IF NOT EXISTS interview_qa (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    question_text       TEXT NOT NULL,
    question_category   TEXT NOT NULL DEFAULT 'general',
    question_source     TEXT NOT NULL DEFAULT 'dialogue',
    hypothesis          TEXT,
    evidence_refs       TEXT,
    -- Issue #135: raw aggregated trace facts + declared-metadata provenance
    -- for question_source = 'runtime' rows only (JSON object). NULL for all
    -- other sources; kept separate from evidence_refs (code line ranges).
    runtime_evidence    TEXT,
    answer_text         TEXT,
    -- Issue #309: explicit answer action provenance for the deterministic
    -- unknown-selection rate. NULL means the row predates this measurement
    -- field (or has never been answered); it is never guessed from free
    -- text. 0/1 is written by both normal Q&A answer paths.
    answer_unknown      INTEGER,
    status              TEXT NOT NULL DEFAULT 'open',
    answered_by         TEXT,
    superseded_by_id    INTEGER,
    created_at          REAL NOT NULL,
    answered_at         REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES interview_qa (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_qa_session
    ON interview_qa (session_id, id);

CREATE INDEX IF NOT EXISTS idx_interview_qa_system
    ON interview_qa (system_id, session_id);

CREATE INDEX IF NOT EXISTS idx_interview_qa_current
    ON interview_qa (session_id, superseded_by_id);

-- Interview UX measurement events (Issue #309). Server-owned interview
-- state remains the primary source for metrics; this append-only table is
-- only for UI interactions which cannot be reconstructed from domain rows
-- (review abandonment, evidence expansion, unchanged-item reconfirmation).
-- event_type/target_kind are finite and cross-validated by the API. No free
-- text or page content is accepted, and nothing is sent to an external
-- analytics service. event_key makes browser retries idempotent per System.
CREATE TABLE IF NOT EXISTS interview_metric_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key   TEXT NOT NULL,
    system_id   INTEGER NOT NULL,
    session_id  INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id   INTEGER NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE (system_id, event_key),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_metric_event_system
    ON interview_metric_event (system_id, event_type, recorded_at);

CREATE INDEX IF NOT EXISTS idx_interview_metric_event_session
    ON interview_metric_event (session_id, recorded_at);

-- Understanding revisions (Issue #136). One row per successful
-- update-understanding call — appended, never overwritten — so the
-- Dashboard can show what changed since the previous revision. Linked to
-- the understanding_review intelligence_runs row that produced it
-- (Principle 7). Diffing is computed on demand from these rows; no diff
-- result is stored (always reproducible, Principle 6).
CREATE TABLE IF NOT EXISTS understanding_revision (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    snapshot_id             INTEGER NOT NULL,
    intelligence_run_id     INTEGER,
    current_understanding   TEXT,
    gap_analysis            TEXT,
    created_at              REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_revision_session
    ON understanding_revision (session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_understanding_revision_system
    ON understanding_revision (system_id, session_id);

-- Canonical, manually-confirmed capability composition (Issue #312).
--
-- The reasoning-model ``current_understanding`` JSON remains the proposal
-- and display snapshot.  These sidecar tables are the authoritative,
-- append-only identity/composition history used for deterministic cascade
-- decisions.  Entity identity is independent of the displayed name, and
-- relations are many-to-many so one lower-level function can support more
-- than one Core Capability.
CREATE TABLE IF NOT EXISTS understanding_capability_entity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id   INTEGER NOT NULL,
    entity_kind TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_understanding_capability_entity_system
    ON understanding_capability_entity (system_id, entity_kind, id);

CREATE TABLE IF NOT EXISTS understanding_capability_confirmation (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    session_id            INTEGER NOT NULL,
    base_confirmation_id  INTEGER,
    source_revision_id    INTEGER,
    source_revision_at    REAL,
    composition_digest    TEXT NOT NULL,
    request_digest        TEXT,
    decided_by            TEXT NOT NULL,
    decided_by_user_id    INTEGER,
    decision_method       TEXT NOT NULL DEFAULT 'manual',
    created_at            REAL NOT NULL,
    UNIQUE (system_id, session_id, source_revision_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (base_confirmation_id) REFERENCES understanding_capability_confirmation (id) ON DELETE SET NULL,
    FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    -- Revision retention must never erase a confirmed composition.
    FOREIGN KEY (source_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_capability_confirmation_session
    ON understanding_capability_confirmation (system_id, session_id, id DESC);

CREATE TABLE IF NOT EXISTS understanding_capability_entity_version (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    confirmation_id   INTEGER NOT NULL,
    entity_id         INTEGER NOT NULL,
    entity_kind       TEXT NOT NULL,
    name              TEXT NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    semantic_digest   TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    created_at        REAL NOT NULL,
    UNIQUE (confirmation_id, entity_id),
    UNIQUE (confirmation_id, entity_kind, name),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_id) REFERENCES understanding_capability_confirmation (id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES understanding_capability_entity (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_understanding_capability_entity_version_lookup
    ON understanding_capability_entity_version
       (system_id, confirmation_id, entity_kind, name);

-- Stable identity of one directed support relation.  Reusing the same
-- endpoint entity ids reuses this row even if either display name changes.
CREATE TABLE IF NOT EXISTS understanding_capability_relation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    supported_entity_id INTEGER NOT NULL,
    supporting_entity_id INTEGER NOT NULL,
    relation_kind       TEXT NOT NULL DEFAULT 'supports',
    created_at          REAL NOT NULL,
    UNIQUE (system_id, supported_entity_id, supporting_entity_id, relation_kind),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (supported_entity_id) REFERENCES understanding_capability_entity (id) ON DELETE RESTRICT,
    FOREIGN KEY (supporting_entity_id) REFERENCES understanding_capability_entity (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_understanding_capability_relation_system
    ON understanding_capability_relation (system_id, supported_entity_id, supporting_entity_id);

CREATE TABLE IF NOT EXISTS understanding_capability_relation_version (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    confirmation_id   INTEGER NOT NULL,
    relation_id       INTEGER NOT NULL,
    role              TEXT NOT NULL DEFAULT '',
    scope             TEXT NOT NULL DEFAULT '',
    semantic_digest   TEXT NOT NULL,
    created_at        REAL NOT NULL,
    UNIQUE (confirmation_id, relation_id),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_id) REFERENCES understanding_capability_confirmation (id) ON DELETE CASCADE,
    FOREIGN KEY (relation_id) REFERENCES understanding_capability_relation (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_understanding_capability_relation_version_lookup
    ON understanding_capability_relation_version (system_id, confirmation_id, relation_id);

-- Evidence actually read during pass 1 of the interview dialogue turn
-- (Issue #137). One row per snippet read from the pinned snapshot,
-- regardless of whether the resulting question cited it — a raw fact kept
-- separate from interview_qa.evidence_refs (which only holds cited spans).
-- Snippet content itself is never persisted (size/confidentiality).
CREATE TABLE IF NOT EXISTS intelligence_run_evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    intelligence_run_id INTEGER NOT NULL,
    path                TEXT NOT NULL,
    start_line          INTEGER NOT NULL,
    end_line            INTEGER NOT NULL,
    char_count          INTEGER NOT NULL DEFAULT 0,
    truncated           INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intelligence_run_evidence_run
    ON intelligence_run_evidence (intelligence_run_id);

CREATE INDEX IF NOT EXISTS idx_intelligence_run_evidence_system
    ON intelligence_run_evidence (system_id, intelligence_run_id);

-- Understanding graph snapshots (Issue #79). Persists merged documentation
-- claim graphs for a system. Each snapshot records the full graph JSON,
-- source hash, claim count, and confidence summary.
CREATE TABLE IF NOT EXISTS understanding_graph_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    snapshot_id         INTEGER,
    graph_json          TEXT NOT NULL,
    source_hash         TEXT NOT NULL DEFAULT '',
    claim_count         INTEGER NOT NULL DEFAULT 0,
    confidence_summary  TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_graph_system
    ON understanding_graph_snapshots (system_id, id DESC);

CREATE TABLE IF NOT EXISTS system_understanding_builds (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    snapshot_id       INTEGER,
    status            TEXT NOT NULL DEFAULT 'queued',
    current_step      TEXT,
    error             TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    heartbeat_at      REAL,
    started_at        REAL,
    completed_at      REAL,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_builds_system
    ON system_understanding_builds (system_id, id DESC);

-- Step-level orchestration for System Understanding builds (Issue #109).
-- One row per (build, step). Deterministic status vocabulary:
-- pending / running / completed / failed / blocked / cancelled.
-- artifact_provenance stores deterministic facts about what the step
-- produced or reused (intelligence_run_id, row counts, graph snapshot id).
CREATE TABLE IF NOT EXISTS system_understanding_build_steps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id          INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    snapshot_id       INTEGER,
    step              TEXT NOT NULL,
    depends_on        TEXT NOT NULL DEFAULT '[]',
    status            TEXT NOT NULL DEFAULT 'pending',
    reused_existing   INTEGER NOT NULL DEFAULT 0,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    artifact_provenance TEXT NOT NULL DEFAULT '{}',
    heartbeat_at      REAL,
    started_at        REAL,
    completed_at      REAL,
    created_at        REAL NOT NULL,
    FOREIGN KEY (build_id) REFERENCES system_understanding_builds (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    UNIQUE (build_id, step)
);

-- One row per worker execution of a build job (Issue #109): the initial
-- enqueue and every retry/resume each get their own run. The run id is the
-- externally referenceable identifier returned by the build endpoint next to
-- the job id; its status mirrors the job outcome for that execution.
CREATE TABLE IF NOT EXISTS system_understanding_build_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id      INTEGER NOT NULL,
    system_id     INTEGER NOT NULL,
    trigger       TEXT NOT NULL DEFAULT 'build',
    status        TEXT NOT NULL DEFAULT 'running',
    started_at    REAL,
    completed_at  REAL,
    created_at    REAL NOT NULL,
    FOREIGN KEY (build_id) REFERENCES system_understanding_builds (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_understanding_build_runs_build
    ON system_understanding_build_runs (build_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_understanding_build_steps_build
    ON system_understanding_build_steps (build_id);

CREATE INDEX IF NOT EXISTS idx_understanding_build_steps_system
    ON system_understanding_build_steps (system_id, id DESC);

-- Chunk-level LLM tasks for the claim_scan step (Issue #109). Each row is one
-- documentation chunk scan with unified retry/backoff accounting. Completed
-- results are kept (result_json) so a retry only re-scans failed chunks, a
-- later build for the same snapshot can reuse results by content hash, and
-- (Issue #195) a build against a *new* snapshot can also reuse results for
-- chunks whose path + content_hash + prompt/schema version are unchanged,
-- so only added/changed documentation is re-scanned by the LLM.
-- chunk_start_line records where the chunk started in its snapshot: reused
-- result_json embeds absolute evidence line numbers, so a cross-snapshot
-- reuse of a chunk that shifted position must offset those lines by the
-- start-line delta to keep evidence resolvable against the new snapshot.
CREATE TABLE IF NOT EXISTS system_understanding_llm_tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id           INTEGER NOT NULL,
    step_id            INTEGER NOT NULL,
    system_id          INTEGER NOT NULL,
    snapshot_id        INTEGER,
    task_type          TEXT NOT NULL DEFAULT 'claim_scan_chunk',
    chunk_id           TEXT NOT NULL,
    chunk_content_hash TEXT NOT NULL DEFAULT '',
    chunk_path         TEXT NOT NULL DEFAULT '',
    chunk_start_line   INTEGER,
    prompt_version     TEXT NOT NULL DEFAULT '',
    schema_version     TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'pending',
    attempts           INTEGER NOT NULL DEFAULT 0,
    max_attempts       INTEGER NOT NULL DEFAULT 3,
    reused_existing    INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    result_json        TEXT,
    started_at         REAL,
    completed_at       REAL,
    created_at         REAL NOT NULL,
    FOREIGN KEY (build_id) REFERENCES system_understanding_builds (id) ON DELETE CASCADE,
    FOREIGN KEY (step_id) REFERENCES system_understanding_build_steps (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (build_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_understanding_llm_tasks_build
    ON system_understanding_llm_tasks (build_id, status);

CREATE INDEX IF NOT EXISTS idx_understanding_llm_tasks_system
    ON system_understanding_llm_tasks (system_id, snapshot_id, chunk_content_hash);

-- Supports the cross-snapshot completed-result reuse lookup (Issue #195),
-- which matches on content identity rather than snapshot_id.
CREATE INDEX IF NOT EXISTS idx_understanding_llm_tasks_reuse
    ON system_understanding_llm_tasks
        (system_id, chunk_content_hash, chunk_path, prompt_version, schema_version, status);

-- Issue drafts (Issue #107). probe-agent is the source of truth for issue
-- drafts generated from System Understanding gaps (and, later, interviews /
-- probe proposals). The draft body is a deterministic Markdown rendering of an
-- already-derived gap (its title, evidence, and pinned snapshot), not an
-- open-ended inference. External issue trackers are not integrated; the user
-- registers the URL of an issue they created elsewhere. status vocabulary:
-- draft / copied / external_created / closed / rejected.
CREATE TABLE IF NOT EXISTS issue_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     INTEGER NOT NULL,
    snapshot_id   INTEGER,
    commit_sha    TEXT,
    source_type   TEXT NOT NULL DEFAULT 'system_understanding_gap',
    source_key    TEXT,
    gap_type      TEXT,
    severity      TEXT,
    node_name     TEXT,
    title         TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',
    external_url  TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_issue_drafts_system
    ON issue_drafts (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_issue_drafts_source
    ON issue_drafts (system_id, source_key);

-- Gap count history (Issue #203). One row per (build, gap_type) recorded
-- when a System Understanding build job settles as completed or partial
-- (never failed/cancelled). Read alongside the existing per-request gap
-- computation (`_collect_gaps` / `_compute_gap_summary`) to show a
-- before/after trend across builds without re-deriving history. A build with
-- zero gaps of a given type has no row for that type. When a build has no
-- open gaps at all, one reserved ``__no_open_gaps__`` marker row preserves
-- the build boundary; loaders exclude that marker from user-visible types.
CREATE TABLE IF NOT EXISTS system_understanding_gap_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id   INTEGER NOT NULL,
    snapshot_id INTEGER,
    build_id    INTEGER NOT NULL,
    gap_type    TEXT NOT NULL,
    count       INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (build_id) REFERENCES system_understanding_builds (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_su_gap_history_system
    ON system_understanding_gap_history (system_id, build_id DESC);

-- Human triage lifecycle for docs-code gaps (Issue #276). Rows are
-- append-only audit decisions. ``gap_key`` is the snapshot-stable,
-- human-readable locator; ``content_fingerprint`` is stored separately so a
-- dismissed gap can deterministically reopen when its semantic content
-- changes. Automatic reopen rows use decision_method=deterministic; every
-- human transition records the user and decision_method=manual.
CREATE TABLE IF NOT EXISTS gap_triage_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    snapshot_id         INTEGER,
    gap_key             TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    status              TEXT NOT NULL
        CHECK (status IN ('open', 'acknowledged', 'dismissed', 'resolved')),
    decided_by_user_id  INTEGER,
    decision_method     TEXT NOT NULL
        CHECK (decision_method IN ('manual', 'deterministic')),
    note                TEXT,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CHECK (
        (decision_method = 'manual' AND decided_by_user_id IS NOT NULL)
        OR decision_method = 'deterministic'
    )
);

CREATE INDEX IF NOT EXISTS idx_gap_triage_system_key
    ON gap_triage_decisions (system_id, gap_key, id DESC);

-- Probe Patterns (Issue #168). A pattern is a reusable observation unit that
-- survives pre-release probe removal: what feature it observes, which probe
-- points it carried, and the pinned snapshot/commit it was captured from.
-- Reconciliation against a newer snapshot is persisted separately so users
-- can see how the implementation moved since the pattern was saved.
CREATE TABLE IF NOT EXISTS probe_patterns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    name                TEXT NOT NULL,
    feature_id          TEXT NOT NULL DEFAULT '',
    capability          TEXT NOT NULL DEFAULT '',
    objective           TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    origin              TEXT NOT NULL DEFAULT 'manual',
    source_plan_id      INTEGER,
    source_snapshot_id  INTEGER,
    source_commit_sha   TEXT NOT NULL DEFAULT '',
    superseded_by_id    INTEGER,
    last_used_at        REAL,
    last_reconciled_at  REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (source_plan_id) REFERENCES probe_plans (id) ON DELETE SET NULL,
    FOREIGN KEY (source_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES probe_patterns (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_probe_patterns_system
    ON probe_patterns (system_id, id DESC);

-- Points carry the structural facts captured at save time (signature and
-- source/body hashes from the pinned snapshot) so reconciliation can decide
-- exact_match / changed_signature deterministically without re-reading the
-- old repository state.
CREATE TABLE IF NOT EXISTS probe_pattern_points (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    component_id        TEXT NOT NULL DEFAULT '',
    path                TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    line_start          INTEGER NOT NULL DEFAULT 0,
    line_end            INTEGER NOT NULL DEFAULT 0,
    reason              TEXT NOT NULL DEFAULT '',
    recommended_mode    TEXT NOT NULL DEFAULT 'trace',
    side_effect_risk    TEXT NOT NULL DEFAULT 'low',
    replayability       TEXT NOT NULL DEFAULT '',
    signature           TEXT NOT NULL DEFAULT '',
    symbol_source_hash  TEXT,
    symbol_body_hash    TEXT,
    docstring           TEXT,
    status              TEXT NOT NULL DEFAULT 'saved',
    removed_at          REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (pattern_id) REFERENCES probe_patterns (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_probe_pattern_points_pattern
    ON probe_pattern_points (pattern_id);

-- Append-only lifecycle history so "what happened to this pattern and when"
-- (saved, removed before release, reconciled, re-planned) stays auditable.
CREATE TABLE IF NOT EXISTS probe_pattern_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    event_type          TEXT NOT NULL,
    detail              TEXT NOT NULL DEFAULT '{}',
    created_by_user_id  INTEGER,
    created_at          REAL NOT NULL,
    FOREIGN KEY (pattern_id) REFERENCES probe_patterns (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_probe_pattern_events_pattern
    ON probe_pattern_events (pattern_id, id DESC);

CREATE TABLE IF NOT EXISTS probe_pattern_reconciliations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id           INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    commit_sha           TEXT NOT NULL,
    intelligence_run_id  INTEGER,
    status               TEXT NOT NULL DEFAULT 'completed',
    error                TEXT,
    summary_json         TEXT NOT NULL DEFAULT '{}',
    created_at           REAL NOT NULL,
    FOREIGN KEY (pattern_id) REFERENCES probe_patterns (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_probe_pattern_reconciliations_pattern
    ON probe_pattern_reconciliations (pattern_id, id DESC);

-- Per-point reconcile classification. decision_method records whether the
-- classification was a deterministic structural check or a reasoning-model
-- proposal; user_decision records the developer's manual call on it.
CREATE TABLE IF NOT EXISTS probe_pattern_reconcile_points (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_id   INTEGER NOT NULL,
    pattern_point_id    INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    classification      TEXT NOT NULL,
    decision_method     TEXT NOT NULL DEFAULT 'deterministic',
    target_path         TEXT,
    target_symbol       TEXT,
    target_line_start   INTEGER,
    target_line_end     INTEGER,
    confidence          REAL NOT NULL DEFAULT 0.0,
    explanation         TEXT NOT NULL DEFAULT '',
    hypothesis          TEXT NOT NULL DEFAULT '',
    question            TEXT NOT NULL DEFAULT '',
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    denylist_hit        TEXT,
    body_changed        INTEGER NOT NULL DEFAULT 0,
    user_decision       TEXT NOT NULL DEFAULT 'pending',
    decided_at          REAL,
    investigation_json  TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (reconciliation_id) REFERENCES probe_pattern_reconciliations (id) ON DELETE CASCADE,
    FOREIGN KEY (pattern_point_id) REFERENCES probe_pattern_points (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_probe_pattern_reconcile_points_rec
    ON probe_pattern_reconcile_points (reconciliation_id);

-- Reviewable pre-release removal diffs. Mirrors probe_patches' explicit apply
-- boundary: generated in an isolated worktree, applied to the target working
-- tree only after commit-sha confirmation against a clean tree.
CREATE TABLE IF NOT EXISTS probe_removal_patches (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id           INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    snapshot_id          INTEGER NOT NULL,
    commit_sha           TEXT NOT NULL,
    diff                 TEXT NOT NULL DEFAULT '',
    point_ids            TEXT NOT NULL DEFAULT '[]',
    skipped              TEXT NOT NULL DEFAULT '[]',
    status               TEXT NOT NULL DEFAULT 'generated',
    error                TEXT,
    cleanup_state        TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error        TEXT,
    apply_status         TEXT NOT NULL DEFAULT 'not_applied',
    apply_error          TEXT,
    applied_at           REAL,
    applied_by_user_id   INTEGER,
    created_at           REAL NOT NULL,
    FOREIGN KEY (pattern_id) REFERENCES probe_patterns (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (applied_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_probe_removal_patches_pattern
    ON probe_removal_patches (pattern_id, id DESC);

-- GitHub App installation allowlist (Issue #222).  An Installation Access
-- Token grants access to the GitHub account, rather than a probe-agent
-- System, so an installation must be registered by an administrator and
-- explicitly assigned to each System that may use it.  Tokens are never
-- persisted here.
CREATE TABLE IF NOT EXISTS github_installations (
    installation_id        INTEGER PRIMARY KEY,
    github_account_login   TEXT NOT NULL,
    github_account_type    TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'active',
    registered_by_user_id  INTEGER,
    verified_at            TEXT NOT NULL,
    disabled_by_user_id    INTEGER,
    disabled_at            TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    FOREIGN KEY (registered_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (disabled_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS github_installation_systems (
    installation_id      INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    assigned_by_user_id  INTEGER,
    created_at           TEXT NOT NULL,
    PRIMARY KEY (installation_id, system_id),
    FOREIGN KEY (installation_id) REFERENCES github_installations (installation_id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_github_installation_systems_system
    ON github_installation_systems (system_id, installation_id);

-- GitHub App connection persistence (Issue #216, sub-task 1). Records which
-- remote repository a System is connected to for the publish workflow and
-- through which GitHub App installation, so a later repository manager /
-- publish job can look up the installation without re-asking the user.
-- The Installation Access Token itself is short-lived and is never stored
-- here or anywhere else (Principle 5/8) -- only this structural connection
-- metadata is. Soft-deleted (status='disconnected') rather than physically
-- deleted for audit; the partial unique index below only constrains
-- non-disconnected rows so the same (system, owner, repo) can be
-- reconnected as a fresh row.
CREATE TABLE IF NOT EXISTS github_connections (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    api_base_url         TEXT NOT NULL,
    web_base_url         TEXT NOT NULL,
    owner                TEXT NOT NULL,
    repo                 TEXT NOT NULL,
    clone_url            TEXT NOT NULL,
    installation_id      INTEGER NOT NULL,
    default_branch       TEXT,
    credential_type      TEXT NOT NULL DEFAULT 'github_app',
    status               TEXT NOT NULL DEFAULT 'pending',
    last_error           TEXT,
    -- Set by the repo manager's sync endpoint (Issue #216 sub-task 2) after
    -- `ensure_mirror` + resolving the default branch's local commit SHA.
    last_synced_at          TEXT,
    last_synced_commit_sha  TEXT,
    created_by_user_id   INTEGER,
    updated_by_user_id   INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_github_connections_active_unique
    ON github_connections (system_id, owner, repo)
    WHERE status != 'disconnected';

CREATE INDEX IF NOT EXISTS idx_github_connections_system
    ON github_connections (system_id, id DESC);

-- Publish job state machine (Issue #216, sub-task 3): commit/push/PR
-- creation for an approved probe patch against a connected GitHub
-- repository. Mirrors probe_patches' explicit-apply-boundary spirit: a
-- prepare phase (authenticating -> fetching -> checking_out ->
-- applying_patch -> validating) stops at awaiting_approval, and only an
-- explicit human approval starts the publish phase (committing -> pushing
-- -> creating_pr -> completed). `status` is a finite, ordered set enforced
-- in app/publish_job.py; `error` is always sanitized (github_app._sanitize)
-- before persistence -- an installation token must never reach this table.
-- `base_commit_sha` / `branch_name` / `commit_sha` / `pr_url` / `pr_number`
-- are raw deterministic facts recorded as the job progresses;
-- `validation_summary` is a structural JSON summary of validation_runs rows
-- read at the `validating` step (not a re-interpretation).
CREATE TABLE IF NOT EXISTS publish_jobs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id              INTEGER NOT NULL,
    connection_id          INTEGER NOT NULL,
    patch_id               INTEGER NOT NULL,
    snapshot_id            INTEGER NOT NULL,
    base_branch            TEXT NOT NULL,
    base_commit_sha        TEXT,
    branch_name            TEXT,
    commit_sha             TEXT,
    pr_url                 TEXT,
    pr_number              INTEGER,
    status                 TEXT NOT NULL DEFAULT 'pending',
    error                  TEXT,
    validation_summary     TEXT,
    requested_by_user_id   INTEGER,
    approved_by_user_id    INTEGER,
    created_at             REAL NOT NULL,
    updated_at             REAL NOT NULL,
    approved_at            REAL,
    completed_at           REAL,
    heartbeat_at           REAL,
    cleanup_state          TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error          TEXT,
    retry_count            INTEGER NOT NULL DEFAULT 0,
    last_attempt_at        REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (connection_id) REFERENCES github_connections (id) ON DELETE CASCADE,
    FOREIGN KEY (patch_id) REFERENCES probe_patches (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_system
    ON publish_jobs (system_id, id DESC);

-- Cross-process lease on a connection's retry/reconcile work (Issue #226):
-- `repo_manager.connection_lock` is an in-process RLock only, so it cannot
-- prevent two separate server processes/replicas from both reconciling the
-- same connection at once. One row per connection; a live (unexpired) row
-- for a different owner blocks acquisition (see
-- `app/publish_job.py::_acquire_connection_lease`). `owner` is a structural
-- process/thread identifier only, never a secret.
CREATE TABLE IF NOT EXISTS publish_connection_leases (
    connection_id INTEGER PRIMARY KEY,
    owner         TEXT NOT NULL,
    acquired_at   REAL NOT NULL,
    expires_at    REAL NOT NULL
);

-- Append-only audit trail for the GitHub publish workflow (Issue #227:
-- connection disconnect / auto-cancel; Issue #226 is expected to extend
-- this same table with publish_jobs status-transition events rather than
-- adding a parallel one). Written via
-- `app/publish_audit.py::record_publish_audit_event`, which takes an
-- already-open connection so the audit row lands inside the caller's own
-- transaction. `detail` is a small JSON object of structural facts only
-- (job ids, counts, a fixed reason string, a cleanup_state value) -- never
-- an installation token, JWT, private key, or filesystem path
-- (Principle 5/8).
CREATE TABLE IF NOT EXISTS publish_audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    connection_id  INTEGER,
    job_id         INTEGER,
    event_type     TEXT NOT NULL,
    actor_user_id  INTEGER,
    detail         TEXT,          -- JSON
    created_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publish_audit_events_system
    ON publish_audit_events (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_publish_audit_events_job
    ON publish_audit_events (job_id, id DESC);

-- Append-only audit trail for one-time auth startup operations (Issue #225),
-- currently just the env-var admin bootstrap. `detail` is a small JSON
-- object of structural facts only -- never a password, password hash, or
-- token (Principle 5/8 secret-hygiene rule extends here).
CREATE TABLE IF NOT EXISTS auth_audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    username    TEXT,
    detail      TEXT,          -- JSON, never a password/hash/token
    created_at  REAL NOT NULL
);

-- Replay engine (Issue #242 Phase B / #244). System-scoped.
--
-- replay_approvals is the human replay-approval gate this phase's acceptance
-- criteria require (a persisted `decision_method: manual` record that
-- POST /replay-runs enforces). The issue's DB-ownership list names only the
-- three replay_* tables below; this table is the approval-gate persistence
-- for Phase B itself, not a speculative later-phase table.
-- risk_context_json is the deterministic risk context (persisted probe plan
-- point labels + the fixed Principle-4 warning) shown at approval time.
CREATE TABLE IF NOT EXISTS replay_approvals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    component_id        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'approved',  -- 'approved' | 'revoked'
    reason              TEXT NOT NULL DEFAULT '',
    approved_by_user_id INTEGER,
    decision_method     TEXT NOT NULL DEFAULT 'manual',
    risk_context_json   TEXT,
    created_at          REAL NOT NULL,
    revoked_at          REAL,
    revoked_by_user_id  INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (revoked_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_approvals_component
    ON replay_approvals (system_id, component_id, id DESC);

-- A Replay Set is an ordered selection of captured trace inputs for one
-- component. trace_ids_json is a JSON array capped at 50 entries
-- (MAX_REPLAY_SET_SIZE, enforced at the API); source is finite
-- ('manual' | 'analyzer_run').
CREATE TABLE IF NOT EXISTS replay_sets (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id              INTEGER NOT NULL,
    component_id           TEXT NOT NULL,
    name                   TEXT NOT NULL DEFAULT '',
    trace_ids_json         TEXT NOT NULL DEFAULT '[]',
    source                 TEXT NOT NULL DEFAULT 'manual',
    source_analyzer_run_id INTEGER,
    created_at             REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (source_analyzer_run_id)
        REFERENCES trace_analysis_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_sets_system
    ON replay_sets (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_replay_sets_component
    ON replay_sets (system_id, component_id, id DESC);

-- One synchronous replay execution of a Replay Set against the pinned
-- snapshot's real implementation in an isolated sandboxed worktree. Audit
-- fields (Principle 7): commit_sha, resolved symbol, trace_set_hash (sha256
-- over the ordered trace ids + each trace's input payload), sandbox config
-- (timeout / network isolation / harness version / env keys), approval
-- linkage, timestamps, failure details, and worktree cleanup state.
CREATE TABLE IF NOT EXISTS replay_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    replay_set_id         INTEGER NOT NULL,
    component_id          TEXT NOT NULL,
    snapshot_id           INTEGER NOT NULL,
    commit_sha            TEXT NOT NULL,
    symbol_path           TEXT NOT NULL,
    symbol_qualified_name TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'failed'
    error                 TEXT,
    trace_set_hash        TEXT NOT NULL,
    sandbox_config_json   TEXT NOT NULL DEFAULT '{}',
    approval_id           INTEGER,
    workspace_path        TEXT,
    cleanup_state         TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error         TEXT,
    created_at            REAL NOT NULL,
    started_at            REAL,
    completed_at          REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_set_id) REFERENCES replay_sets (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES replay_approvals (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_runs_system
    ON replay_runs (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_replay_runs_set
    ON replay_runs (system_id, replay_set_id, id DESC);

-- Per-trace deterministic comparison of replay output vs recorded output.
-- case_status is finite ('match' | 'mismatch' | 'error' | 'skipped');
-- input_source is finite ('structured' | 'repr_partial', NULL for skipped
-- cases without an executable input); skip_reason is finite
-- ('unreplayable_capture' | 'repr_parse_failed' | 'undecodable_input' |
-- 'trace_missing'). recorded_error stores the recorded error's FIRST LINE
-- ("Type: msg") — the deterministic comparison basis; the full recorded
-- error (with traceback) stays on the traces row. comparison_mode is fixed
-- 'repr' in Phase B; output_truncated notes that repr equality on truncated
-- values is prefix-bounded.
CREATE TABLE IF NOT EXISTS replay_case_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    replay_run_id    INTEGER NOT NULL,
    trace_id         TEXT NOT NULL,
    position         INTEGER NOT NULL,
    case_status      TEXT NOT NULL,
    input_source     TEXT,
    skip_reason      TEXT,
    replay_output    TEXT,
    replay_error     TEXT,
    recorded_output  TEXT,
    recorded_error   TEXT,
    duration_ms      REAL,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    comparison_mode  TEXT NOT NULL DEFAULT 'repr',
    created_at       REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_replay_case_results_run
    ON replay_case_results (replay_run_id, position);
CREATE INDEX IF NOT EXISTS idx_replay_case_results_system
    ON replay_case_results (system_id, replay_run_id);

-- Replay variants (Issue #242 Phase C / #245). A "variant replay run" is a
-- normal Phase B replay_runs row (baseline: same snapshot/symbol/approval-
-- gate/trace_set_hash resolution, same replay_case_results baseline-vs-
-- recorded classification) that ALSO gets one or more patched variants
-- replayed in the SAME run against the SAME Replay Set + sandbox config.
-- replay_variants hangs off that replay_runs row via replay_run_id; the
-- baseline itself gets a row too (variant_key='baseline', is_baseline=1,
-- patch_text='', apply_status='not_applicable') purely so one query lists
-- everything the run covers (mirrors experiment_variants' own baseline row).
-- variant_key is finite ('baseline' | 'variant-N'); source is finite
-- ('manual' | 'pasted' | 'llm_draft'); apply_status is finite
-- ('applied' | 'invalid_patch' | 'not_applicable'). Each variant is applied
-- and executed in its OWN independent worktree (workspace_path/cleanup_*
-- below), so one variant's bad patch or timeout never touches the baseline
-- or any other variant -- see app/replay_runner.py's execute_harness
-- patch_text parameter and app/replay_variants.py's classification.
CREATE TABLE IF NOT EXISTS replay_variants (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    replay_run_id  INTEGER NOT NULL,
    variant_key    TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    is_baseline    INTEGER NOT NULL DEFAULT 0,
    patch_text     TEXT NOT NULL DEFAULT '',
    patch_hash     TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'manual',
    apply_status   TEXT NOT NULL DEFAULT 'not_applicable',
    apply_error    TEXT,
    status         TEXT NOT NULL DEFAULT 'running',  -- 'running'|'completed'|'failed'
    error          TEXT,
    workspace_path TEXT,
    cleanup_state  TEXT NOT NULL DEFAULT 'not_attempted',
    cleanup_error  TEXT,
    created_at     REAL NOT NULL,
    started_at     REAL,
    completed_at   REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_replay_variants_run
    ON replay_variants (replay_run_id, id);
CREATE INDEX IF NOT EXISTS idx_replay_variants_system
    ON replay_variants (system_id, replay_run_id);

-- Per-trace baseline-replay-vs-candidate-replay comparison for one variant
-- (Issue #245). Keep Phase B's replay_case_results as the baseline-vs-
-- RECORDED record; this table holds baseline-REPLAY-vs-candidate instead,
-- so it also carries replay_run_id (joinable back to replay_case_results by
-- replay_run_id + trace_id + position for the originally-recorded output/
-- error, without duplicating those columns here).
--
-- case_status is the finite 7-member set documented in
-- app/replay_variants.py's module docstring (match / diff / candidate_error
-- / error_to_success / error_to_same_error / error_to_different_error /
-- skipped). comparison_mode is finite ('structured' | 'repr') and NULL
-- when the classification did not depend on an output-equality mode
-- (candidate_error / error_to_* / skipped). field_diffs_json is only
-- populated for a 'diff' produced in 'structured' mode (changed top-level
-- field names). recorded_error here is the BASELINE REPLAY's own error
-- first line (this run's baseline execution, not the historical production
-- trace -- that stays on replay_case_results.recorded_error, reachable via
-- the replay_run_id + trace_id join above); candidate_error is the
-- candidate's error first line. duration_delta_ms is candidate duration
-- minus baseline duration for this run.
CREATE TABLE IF NOT EXISTS replay_variant_case_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    replay_variant_id INTEGER NOT NULL,
    replay_run_id     INTEGER NOT NULL,
    trace_id          TEXT NOT NULL,
    position          INTEGER NOT NULL,
    case_status       TEXT NOT NULL,
    comparison_mode   TEXT,
    baseline_output   TEXT,
    candidate_output  TEXT,
    candidate_error   TEXT,
    recorded_error    TEXT,
    duration_ms       REAL,
    duration_delta_ms REAL,
    field_diffs_json  TEXT,
    output_truncated  INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_variant_id) REFERENCES replay_variants (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_replay_variant_case_results_variant
    ON replay_variant_case_results (replay_variant_id, position);
CREATE INDEX IF NOT EXISTS idx_replay_variant_case_results_system
    ON replay_variant_case_results (system_id, replay_variant_id);

-- LLM candidate-draft provenance for Replay variants (Issue #245). Mirrors
-- the established intelligence_runs + per-feature-draft-table pattern used
-- throughout #23-#26 (e.g. system_profile_drafts): the audit record
-- (provider/model/prompt_version/schema_version/decision_method/is_mock/
-- status/error/timestamps) lives in intelligence_runs
-- (run_type='replay_variant_draft'); this table holds only the draft's own
-- content (deterministically spliced patch_text, generated_code, and the
-- context it was drafted from), kept separate from raw deterministic replay
-- results per the CLAUDE.md storage-separation rule. A draft is proposed
-- standalone (no replay_run_id -- it has not been run as a variant yet);
-- the caller copies patch_text into POST /replay-variant-runs to try it.
CREATE TABLE IF NOT EXISTS replay_variant_drafts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    intelligence_run_id   INTEGER NOT NULL,
    replay_set_id         INTEGER NOT NULL,
    component_id          TEXT NOT NULL,
    trace_id              TEXT NOT NULL,
    objective             TEXT NOT NULL DEFAULT '',
    snapshot_id           INTEGER NOT NULL,
    symbol_path           TEXT NOT NULL,
    symbol_qualified_name TEXT NOT NULL,
    generated_code        TEXT NOT NULL DEFAULT '',
    patch_text            TEXT NOT NULL DEFAULT '',
    patch_hash            TEXT NOT NULL DEFAULT '',
    notes                 TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'proposed',  -- 'proposed'|'failed'
    error                 TEXT,
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_set_id) REFERENCES replay_sets (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_replay_variant_drafts_system
    ON replay_variant_drafts (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_replay_variant_drafts_set
    ON replay_variant_drafts (system_id, replay_set_id, id DESC);

-- Review-only regression-test scaffolds generated from one completed replay
-- variant case (Issue #246). The reasoning audit/provenance lives in
-- intelligence_runs; this table persists the generated content and exact raw
-- replay context identifiers. Nothing here is ever written to the target repo.
CREATE TABLE IF NOT EXISTS replay_regression_scaffolds (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    intelligence_run_id INTEGER NOT NULL,
    replay_run_id       INTEGER NOT NULL,
    replay_variant_id   INTEGER NOT NULL,
    replay_set_id       INTEGER NOT NULL,
    trace_id            TEXT NOT NULL,
    snapshot_id         INTEGER NOT NULL,
    scaffold_text       TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'proposed',
    error               TEXT,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_variant_id) REFERENCES replay_variants (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_set_id) REFERENCES replay_sets (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_replay_regression_scaffolds_system
    ON replay_regression_scaffolds (system_id, id DESC);

-- =========================================================================
-- AI Candidate Studio (Issue #252)
--
-- A conversation-oriented wrapper over the EXISTING isolated-Replay
-- infrastructure (#242 Phase B/C): a CandidateSession groups a component, a
-- pinned baseline snapshot, a Replay Set (the evaluation inputs), and the
-- chat; every time a patch is actually generated an IMMUTABLE CandidateVersion
-- is created (chat messages alone never create versions). Nothing here adds a
-- new judgement, execution, or comparison path -- proposal generation reuses
-- the reasoning-model candidate prompt + deterministic splice->diff from
-- app/candidate_studio.py (built on replay_draft's mechanism), replay reuses
-- POST /replay-variant-runs verbatim (same approval gate, network-off
-- worktree sandbox, always-cleanup, finite diff matrix), and promotion reuses
-- the variant experiment-payload shape. The LLM never adopts/merges/deploys
-- anything: promotion only hands a reviewed patch to the existing Experiment
-- creation flow (Principle 7).
-- =========================================================================
CREATE TABLE IF NOT EXISTS candidate_sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    component_id          TEXT NOT NULL,
    snapshot_id           INTEGER NOT NULL,
    commit_sha            TEXT NOT NULL,
    symbol_path           TEXT NOT NULL,
    symbol_qualified_name TEXT NOT NULL,
    replay_set_id         INTEGER NOT NULL,
    objective             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'archived'
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_set_id) REFERENCES replay_sets (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidate_sessions_system
    ON candidate_sessions (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_candidate_sessions_component
    ON candidate_sessions (system_id, component_id, id DESC);

-- One immutable candidate version per generated patch. parent_version_id
-- makes additional instructions branch off the selected version (a tree per
-- session). The reasoning-model provenance lives in intelligence_runs
-- (run_type='candidate_studio_proposal'); this table holds the structured
-- proposal content (summary / assumptions / changed_symbols / risks /
-- suggested_tests) plus the deterministically spliced patch, kept separate
-- from raw replay results (CLAUDE.md storage-separation rule). status is
-- finite: the generate job lifecycle terminal state
-- ('proposed' = patch generated & validated | 'failed' = LLM/patch/scope/
-- validation failure, fail-closed). replay_status is finite
-- ('not_run' | 'running' | 'completed' | 'failed'); replay_run_id / candidate
-- variant point at the reused replay_variant_run when replayed. promoted_at
-- records that the reviewed patch was handed to the Experiment flow (never an
-- auto-adoption).
CREATE TABLE IF NOT EXISTS candidate_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    session_id          INTEGER NOT NULL,
    parent_version_id   INTEGER,
    version_number      INTEGER NOT NULL,
    intelligence_run_id INTEGER NOT NULL,
    instruction         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'failed',  -- 'proposed' | 'failed'
    summary             TEXT NOT NULL DEFAULT '',
    assumptions_json    TEXT NOT NULL DEFAULT '[]',
    changed_symbols_json TEXT NOT NULL DEFAULT '[]',
    risks_json          TEXT NOT NULL DEFAULT '[]',
    suggested_tests_json TEXT NOT NULL DEFAULT '[]',
    generated_code      TEXT NOT NULL DEFAULT '',
    patch_text          TEXT NOT NULL DEFAULT '',
    patch_hash          TEXT NOT NULL DEFAULT '',
    error               TEXT,
    replay_status       TEXT NOT NULL DEFAULT 'not_run',
    replay_run_id       INTEGER,
    replay_variant_id   INTEGER,
    promoted_at         REAL,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES candidate_sessions (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_version_id) REFERENCES candidate_versions (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_versions_session
    ON candidate_versions (session_id, version_number);
CREATE INDEX IF NOT EXISTS idx_candidate_versions_system
    ON candidate_versions (system_id, session_id, id DESC);

-- Conversation turns. role is finite ('user' | 'assistant'); an assistant
-- turn may reference the version its request produced (version_id), but a
-- message never itself creates a version -- only POST .../generate does.
-- The assistant's "understood conditions" echo is DETERMINISTIC display text
-- (Principle 6), not an inference; the LLM's actual understanding is surfaced
-- inside a CandidateVersion's proposal (summary / assumptions).
CREATE TABLE IF NOT EXISTS candidate_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id   INTEGER NOT NULL,
    session_id  INTEGER NOT NULL,
    role        TEXT NOT NULL,  -- 'user' | 'assistant'
    content     TEXT NOT NULL DEFAULT '',
    version_id  INTEGER,
    created_at  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES candidate_sessions (id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES candidate_versions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_messages_session
    ON candidate_messages (session_id, id);
CREATE INDEX IF NOT EXISTS idx_candidate_messages_system
    ON candidate_messages (system_id, session_id);

-- Issue #94/#275: append-only audit of human "confirmed" decisions
-- reconciling the manual system_profile purpose against the AI/source-
-- derived purpose view. Never UPDATE/DELETE a row; the latest row per
-- system is the current confirmation, and staleness is computed at read
-- time by comparing the stored snapshot/manual/ai values against the
-- current ones (see state_facts.purpose_confirmation_staleness).
CREATE TABLE IF NOT EXISTS system_purpose_confirmations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    snapshot_id         INTEGER NOT NULL,
    understanding_build_id INTEGER,
    decided_by_user_id  INTEGER,
    manual_purpose      TEXT NOT NULL,
    manual_profile_name TEXT,
    ai_purpose_name     TEXT,
    ai_purpose_summary  TEXT,
    ai_source           TEXT,
    ai_provenance_kind  TEXT,
    note                TEXT,
    decision_method     TEXT NOT NULL DEFAULT 'manual',
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (understanding_build_id) REFERENCES system_understanding_builds (id) ON DELETE SET NULL,
    FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_system_purpose_confirmations_system
    ON system_purpose_confirmations (system_id, id DESC);

-- Intent Brief (Issue #284): user intent (goal/pain/success_criteria/
-- priority/constraints/non_goals) kept structurally separate from
-- implementation-fact understanding. field/status are finite sets validated
-- by the API, not a DB CHECK constraint (kept consistent with the rest of
-- this schema). origin='user' rows are authored/confirmed by a human
-- directly (decision_method='manual'); origin='ai_proposed' rows come from
-- the reasoning-model propose endpoint (decision_method='reasoning_llm',
-- status='proposed') and NEVER become 'confirmed' except through the
-- explicit confirm/correct user endpoints (Principle 2). Corrections never
-- overwrite: a new row is inserted and the old row's superseded_by_id is set
-- (revision history), mirroring interview_qa's answer/correction pattern.
CREATE TABLE IF NOT EXISTS interview_intent_item (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    field               TEXT NOT NULL,
    value_text          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'proposed',
    origin              TEXT NOT NULL,
    source_statement    TEXT,
    decision_method     TEXT NOT NULL DEFAULT 'manual',
    intelligence_run_id INTEGER,
    is_mock             INTEGER NOT NULL DEFAULT 0,
    superseded_by_id    INTEGER,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES interview_intent_item (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_intent_item_session
    ON interview_intent_item (session_id, superseded_by_id);

CREATE INDEX IF NOT EXISTS idx_interview_intent_item_system
    ON interview_intent_item (system_id, session_id);

CREATE INDEX IF NOT EXISTS idx_interview_intent_item_field
    ON interview_intent_item (session_id, field, superseded_by_id);

-- Inquiry lifecycle (Issue #285): when a developer has a doubt about a
-- confirmation item (a Q&A question, an Intent Brief item, or -- from Issue
-- #287 onward -- a review item), the original item is held pending and a
-- separate Inquiry conversation starts. Resolving the Inquiry ("疑問は解消
-- した") is strictly separate from answering/confirming the origin item:
-- creating, messaging, and resolving/holding/cancelling an Inquiry never
-- writes to interview_qa / interview_intent_item. origin_kind/origin_id
-- identify the item under discussion; 'review_item' is accepted now even
-- though no reviewing table exists yet (#287 is what starts writing those
-- rows) so the finite set does not need to change later. held_draft is the
-- user's unconfirmed answer draft at the moment they opened the Inquiry,
-- opaque JSON round-tripped back to the dashboard so it can restore the
-- input when the developer returns to the original item -- the server never
-- interprets it, and resolving never submits it as an answer (Principle 2:
-- only an explicit user action on the origin item's own endpoint counts).
-- Premise bundle (Issue #308 / #320): the immutable facts an Inquiry's
-- conversation was reasoned against, captured once at creation and never
-- rebased. premise_snapshot_id is the snapshot answer generation is pinned
-- to, so a follow-up message keeps using it even after the session's own
-- snapshot advances; premise_revision_id is the Understanding Revision the
-- origin review item was built from. Both are audit references (ON DELETE
-- SET NULL under retention); the hash/digest columns are the comparable
-- MEANING of the premise and survive that retention as standalone audit
-- facts. premise_review_subject_id (Issue #321) is the stable discussion-
-- point identity used to find the current successor item after a rebuild
-- deleted the original physical row. All columns are NULL for rows written
-- before this migration -- a past snapshot/revision/hash is never guessed --
-- and, apart from the snapshot/version/captured_at trio, for the qa/intent
-- origins that v1 does not auto-track.
--
-- premise_evaluation / premise_successor_item_id / superseded_at (Issue
-- #323) are the result side: the finite verdict of the last premise
-- evaluation, the unique current successor item when there is exactly one,
-- and the moment the Inquiry became 'superseded' -- kept separate from
-- closed_at so the original resolved timestamp is never overwritten.
CREATE TABLE IF NOT EXISTS interview_inquiry (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                INTEGER NOT NULL,
    system_id                 INTEGER NOT NULL,
    origin_kind               TEXT NOT NULL,
    origin_id                 INTEGER NOT NULL,
    held_draft                TEXT,
    status                    TEXT NOT NULL DEFAULT 'open',
    status_reason             TEXT,
    premise_snapshot_id       INTEGER,
    premise_revision_id       INTEGER,
    premise_review_subject_id TEXT,
    premise_content_hash      TEXT,
    premise_capability_digest TEXT,
    premise_intent_digest     TEXT,
    premise_tracking_version  TEXT,
    premise_captured_at       REAL,
    premise_evaluation        TEXT,
    premise_successor_item_id INTEGER,
    superseded_at             REAL,
    created_at                REAL NOT NULL,
    updated_at                REAL NOT NULL,
    closed_at                 REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (premise_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_successor_item_id) REFERENCES alignment_item (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_inquiry_session
    ON interview_inquiry (session_id, id);

CREATE INDEX IF NOT EXISTS idx_interview_inquiry_system
    ON interview_inquiry (system_id, session_id);

-- One row per turn in the Inquiry side-conversation. detail is populated on
-- assistant messages only: {key_points, evidence, uncertainty} for
-- progressive disclosure in the UI (the conclusion is the message content
-- itself, shown first; "根拠を見る" expands detail). intelligence_run_id
-- links to the intelligence_runs audit row that produced the message
-- (Principle 7); NULL for user messages and for the fixed-template
-- "insufficient information" assistant message (never LLM output).
CREATE TABLE IF NOT EXISTS interview_inquiry_message (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    role                TEXT NOT NULL,
    content             TEXT NOT NULL,
    detail              TEXT,
    intelligence_run_id INTEGER,
    is_mock             INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    FOREIGN KEY (inquiry_id) REFERENCES interview_inquiry (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_inquiry_message_inquiry
    ON interview_inquiry_message (inquiry_id, id);

CREATE INDEX IF NOT EXISTS idx_interview_inquiry_message_system
    ON interview_inquiry_message (system_id, inquiry_id);

-- Audit trail for every Inquiry status change (Principle 7): which
-- unresolved/hold/cancel/resolve/resume/reopen-doubt transition happened,
-- who did it, and why. Kept as its own append-only table rather than
-- overloading interview_inquiry.status_reason (which only ever reflects the
-- *current* status) so a full history survives multiple hold/resume cycles.
CREATE TABLE IF NOT EXISTS interview_inquiry_transition (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id  INTEGER NOT NULL,
    system_id   INTEGER NOT NULL,
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    actor       TEXT,
    reason      TEXT,
    created_at  REAL NOT NULL,
    FOREIGN KEY (inquiry_id) REFERENCES interview_inquiry (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_inquiry_transition_inquiry
    ON interview_inquiry_transition (inquiry_id, id);

-- Joint Understanding sessions (Epic #328 Phase A / Issue #329). What
-- 「わからない」 STARTS instead of ending: a shared workspace where
-- investigation findings, translated explanations, and the developer's own
-- judgements accumulate in separate provenances until the developer picks a
-- next action and closes the session with an explicitly typed outcome.
--
-- Boundary this table set exists to keep (Epic #328: 「わからない」という入力
-- を開発者の意図として混入させない): NOTHING here ever writes the origin
-- confirmation item. interview_qa.answer_text/status,
-- interview_intent_item.value_text/status, and alignment_item.user_decision/
-- status are read-only from this feature's point of view -- unlike Issue
-- #287's Inquiry integration, a Joint Understanding session does not even
-- mirror an 'inquiry'-style status onto alignment_item (Phase D / #332 owns
-- the question of how these two flows integrate). question_text lives on the
-- session row only; it is never copied into an answer field and never
-- becomes a developer finding.
--
-- All three tables are System-scoped and every vocabulary column
-- (origin_kind/trigger/status/outcome/origin_role/claim_kind/action_kind/
-- decision_method) is validated against the finite sets in
-- app/joint_understanding.py before insert (Principle 6).
CREATE TABLE IF NOT EXISTS joint_understanding_session (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    origin_kind         TEXT NOT NULL,
    origin_id           INTEGER NOT NULL,
    trigger             TEXT NOT NULL,
    question_text       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    outcome             TEXT,
    outcome_reason      TEXT,
    -- Issue #332: which findings the recorded outcome rests on, and the
    -- premise state evaluated at close time ('fresh' | 'stale'). An outcome
    -- that adopts a hypothesis or records a decision must name its basis;
    -- a stale premise (the interview session moved to a newer snapshot than
    -- the one this session pinned) blocks adopt/decide entirely.
    outcome_finding_ids TEXT NOT NULL DEFAULT '[]',
    outcome_premise_state TEXT,
    -- Issue #337: the reason code behind outcome_premise_state, and WHO
    -- closed the session. A close is a manual decision, so the deciding
    -- human and their stated reason must both survive a reload -- an
    -- outcome with no recoverable decider is not an audit record.
    outcome_premise_reason TEXT,
    closed_by_actor_kind  TEXT,
    closed_by_user_id     INTEGER,
    closed_by_username    TEXT,
    -- The premise bundle (Issue #337), sharing Issue #308's column names
    -- because it is the same bundle: snapshot + pinned commit + origin
    -- revision + origin content hash + confirmed Capability scope digest
    -- (+ the linked Intent digest and the review-subject anchor where the
    -- origin has them). premise_snapshot_id alone was never enough -- an
    -- Intent correction or an Alignment rebuild moves the ground without
    -- moving the snapshot, and a NULL premise was previously read as a
    -- satisfied one. app/joint_premise.py evaluates them into the finite
    -- current | stale | missing | invalid verdict; a bundle that cannot be
    -- compared is 'invalid' and blocks the asserting outcomes.
    premise_snapshot_id INTEGER,
    premise_commit_sha  TEXT,
    premise_revision_id INTEGER,
    premise_content_hash TEXT,
    premise_capability_digest TEXT,
    premise_intent_digest TEXT,
    premise_review_subject_id TEXT,
    premise_tracking_version TEXT,
    premise_captured_at REAL,
    schema_version      TEXT NOT NULL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    closed_at           REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (premise_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_session_session
    ON joint_understanding_session (session_id, status);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_session_origin
    ON joint_understanding_session (system_id, origin_kind, origin_id);

-- Append-only. A correction is a NEW row carrying supersedes_finding_id;
-- existing rows are never UPDATEd or DELETEd, so an explanation can always
-- be traced back to the exact claim and evidence it came from even after it
-- was revised. origin_role/claim_kind are independent axes: WHO produced the
-- statement vs WHAT kind of statement it is (fact/inference/hypothesis/
-- unknown/conflict). Only origin_role='investigation' rows may carry
-- evidence_json/runtime_evidence_json -- a translation references findings
-- via supports_finding_ids and a developer statement is a judgement, not
-- snapshot evidence (app/joint_understanding.validate_finding).
CREATE TABLE IF NOT EXISTS joint_understanding_finding (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    origin_role             TEXT NOT NULL,
    claim_kind              TEXT NOT NULL,
    statement               TEXT NOT NULL,
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    runtime_evidence_json   TEXT NOT NULL DEFAULT '[]',
    supports_finding_ids    TEXT NOT NULL DEFAULT '[]',
    competing_explanations  TEXT NOT NULL DEFAULT '[]',
    refutation_conditions   TEXT NOT NULL DEFAULT '[]',
    next_investigation      TEXT,
    uncertainty             TEXT NOT NULL DEFAULT '',
    supersedes_finding_id   INTEGER,
    decision_method         TEXT NOT NULL,
    intelligence_run_id     INTEGER,
    is_mock                 INTEGER NOT NULL DEFAULT 0,
    -- Issue #337: provenance, on two axes that must not collapse into one.
    -- producer_kind is WHICH CODE PATH wrote the row (investigation_loop /
    -- translator / developer_api); actor_kind is WHETHER AN AUTHENTICATED
    -- HUMAN stands behind it. Both come from the route and the resolved
    -- Principal, never from the request body -- previously a body claiming
    -- origin_role='developer' was enough to record any caller's sentence as
    -- the human's own judgement, with decision_method='manual' attached.
    -- NULL on rows written before this contract; they report 'legacy',
    -- which is never assumed to be a human (app/joint_premise.py).
    producer_kind           TEXT,
    actor_kind              TEXT,
    actor_user_id           INTEGER,
    actor_username          TEXT,
    created_at              REAL NOT NULL,
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (supersedes_finding_id)
        REFERENCES joint_understanding_finding (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_finding_session
    ON joint_understanding_finding (joint_understanding_id, id);

-- The finite next understanding actions the developer chose, append-only.
-- decision_method is always 'manual': choosing an action is the human's own
-- record of what they want to do next, and it never approves, adopts, or
-- decides the origin item by itself.
CREATE TABLE IF NOT EXISTS joint_understanding_action (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    action_kind             TEXT NOT NULL,
    -- Free-text display label the caller may supply. Issue #337: this is NOT
    -- the identity. actor_kind/actor_user_id below come from the resolved
    -- Principal, so a request body can no longer name who acted.
    actor                   TEXT,
    actor_kind              TEXT,
    actor_user_id           INTEGER,
    actor_username          TEXT,
    note                    TEXT,
    decision_method         TEXT NOT NULL DEFAULT 'manual',
    created_at              REAL NOT NULL,
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_action_session
    ON joint_understanding_action (joint_understanding_id, id);

-- One provisionally adopted hypothesis (Issue #337).
--
-- `outcome='hypothesis_adopted'` is explicitly PROVISIONAL, but before this
-- table the only trace of it was the closed session's outcome label. Nothing
-- said WHICH hypothesis was adopted against WHICH premise, so nothing could
-- bring it back for re-confirmation when that premise later moved -- the
-- adoption quietly aged into something indistinguishable from a fact.
--
-- Every row is an immutable capture: the basis finding plus the premise
-- digests as they stood at adoption time. The lifecycle state
-- (provisional | reconfirmation_required | basis_withdrawn) is DERIVED from
-- comparing that capture against the current premise
-- (app/joint_premise.adoption_state), never stored -- the same discipline
-- Issue #349 applies to an unresolved blocking failure, and for the same
-- reason: a stored state can drift out of sync with the facts it describes.
CREATE TABLE IF NOT EXISTS joint_understanding_hypothesis_adoption (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    finding_id              INTEGER NOT NULL,
    adopted_by_actor_kind   TEXT NOT NULL,
    adopted_by_user_id      INTEGER,
    adopted_by_username     TEXT,
    adoption_reason         TEXT NOT NULL DEFAULT '',
    premise_snapshot_id     INTEGER,
    premise_commit_sha      TEXT,
    premise_revision_id     INTEGER,
    premise_content_hash    TEXT,
    premise_capability_digest TEXT,
    premise_intent_digest   TEXT,
    decision_method         TEXT NOT NULL DEFAULT 'manual',
    adopted_at              REAL NOT NULL,
    UNIQUE (joint_understanding_id, finding_id),
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (finding_id)
        REFERENCES joint_understanding_finding (id) ON DELETE CASCADE,
    FOREIGN KEY (adopted_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_snapshot_id)
        REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_revision_id)
        REFERENCES understanding_revision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_adoption_session
    ON joint_understanding_hypothesis_adoption (joint_understanding_id, id);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_adoption_system
    ON joint_understanding_hypothesis_adoption (system_id, adopted_at);

-- One iterative investigation round (Epic #328 Phase B / Issue #330).
-- Append-only audit of what a round actually did -- which candidates it
-- picked, which files it read, which it left unread, why it stopped -- plus
-- the state that must survive into the NEXT round and into a later RETRY:
-- search_leads / open_hypotheses / missing_evidence / read_paths. Restoring
-- those on retry is what keeps a re-run from starting over from the bare
-- question (Epic #328: 調査の再試行で、既に得た調査方針や検索手がかりを失わない).
-- stop_reason is only set on the round that ended the loop, and is one of
-- the finite investigation_loop.STOP_REASONS.
CREATE TABLE IF NOT EXISTS joint_understanding_investigation_round (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    round_index             INTEGER NOT NULL,
    status                  TEXT NOT NULL,
    stop_reason             TEXT,
    conclusion              TEXT NOT NULL DEFAULT '',
    search_leads            TEXT NOT NULL DEFAULT '[]',
    open_hypotheses         TEXT NOT NULL DEFAULT '[]',
    missing_evidence        TEXT NOT NULL DEFAULT '[]',
    read_paths              TEXT NOT NULL DEFAULT '[]',
    unread_candidates       TEXT NOT NULL DEFAULT '[]',
    pruned_findings         INTEGER NOT NULL DEFAULT 0,
    files_read              INTEGER NOT NULL DEFAULT 0,
    chars_read              INTEGER NOT NULL DEFAULT 0,
    llm_calls               INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds         REAL NOT NULL DEFAULT 0,
    intelligence_run_id     INTEGER,
    error_details           TEXT,
    -- Issue #339: the finite execution-failure class
    -- (app/investigation_loop.EXECUTION_FAILURE_CLASSES). NULL for a round
    -- that succeeded AND for one that ended in a research limitation -- a
    -- limitation is a real, evidence-backed result, not a broken run, and
    -- collapsing the two is what made "the system looked and could not tell"
    -- indistinguishable from "the system could not look".
    failure_class           TEXT,
    created_at              REAL NOT NULL,
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_round_session
    ON joint_understanding_investigation_round (joint_understanding_id, id);

-- One exploration source's contribution to one investigation round
-- (Issue #339).
--
-- The round row records what the round read in total. That is not enough to
-- check the boundary the added sources have to respect: "this round only read
-- the pinned revision" is a claim about EACH source, and a git-history source
-- that walked past the pinned commit would be invisible in a round-level
-- total. So every source records its own revision (the pinned commit for git
-- history, the snapshot id for the index/content/runtime sources), its own
-- budget consumption, and its own failure.
--
-- A failed source is recorded and skipped -- it never fails the round, and it
-- is never replaced by an unbounded fallback search (Issue #339: 予算超過時に
-- 無制限の fallback 探索を行わない). source_kind is validated against
-- app/snapshot_explorers.EXPLORATION_SOURCE_KINDS before insert.
CREATE TABLE IF NOT EXISTS joint_understanding_exploration_source (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id                INTEGER NOT NULL,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    source_kind             TEXT NOT NULL,
    revision                TEXT NOT NULL,
    candidates_found        INTEGER NOT NULL DEFAULT 0,
    queries_run             INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds         REAL NOT NULL DEFAULT 0,
    truncated               INTEGER NOT NULL DEFAULT 0,
    error_details           TEXT,
    created_at              REAL NOT NULL,
    FOREIGN KEY (round_id)
        REFERENCES joint_understanding_investigation_round (id) ON DELETE CASCADE,
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_exploration_round
    ON joint_understanding_exploration_source (round_id, id);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_exploration_source_kind
    ON joint_understanding_exploration_source (system_id, source_kind, created_at);

-- One translation pass (Epic #328 Phase C / Issue #331): the investigation's
-- findings restated in terms of purpose, user impact, the gap against that
-- purpose, system-wide consistency, and which decision changes what.
--
-- The translated SENTENCES are also stored as ordinary
-- joint_understanding_finding rows with origin_role='translation' (that is
-- the append-only spine, and it is what carries supports_finding_ids), so
-- this table holds the parts that are not a single statement: the summary,
-- the option comparison, what is still unknown, and whether the pass
-- concluded that a question actually has to go to the developer.
-- statements_json therefore stores each statement's translation finding id,
-- never a second copy of the sentence's provenance.
CREATE TABLE IF NOT EXISTS joint_understanding_translation (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    purpose_summary         TEXT NOT NULL DEFAULT '',
    statements_json         TEXT NOT NULL DEFAULT '[]',
    options_json            TEXT NOT NULL DEFAULT '[]',
    open_unknowns           TEXT NOT NULL DEFAULT '[]',
    decision_question       TEXT,
    ask_developer           INTEGER NOT NULL DEFAULT 0,
    intelligence_run_id     INTEGER,
    is_mock                 INTEGER NOT NULL DEFAULT 0,
    created_at              REAL NOT NULL,
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_translation_session
    ON joint_understanding_translation (joint_understanding_id, id);

-- Reflux of system-verified facts into the understanding surface
-- (Epic #328 Phase D / Issue #332).
--
-- The flow this replaces: an investigation result only reached System
-- Understanding if a HUMAN retyped it into an answer field. A reflux row
-- records that an investigation finding (origin_role='investigation',
-- claim_kind='fact') has been attached to the understanding surface WITH its
-- evidence and WITHOUT being recorded as anyone's answer:
-- decision_method is always 'reasoning_llm' here, never 'manual', and
-- nothing in this path writes interview_qa.answer_text/status,
-- interview_intent_item.value_text/status, or alignment_item.user_decision.
--
-- target_kind is a finite set of EXISTING structures (no third understanding
-- model is introduced):
--   'qa_investigation'  -- interview_qa.investigation_json/_run_id, the same
--                          slot Issue #286's route-and-investigate writes and
--                          which is by construction not the answer
--   'session_ledger'    -- this table alone, for origins with no such slot
--                          (intent / review_item / inquiry). Their built rows
--                          are owned by the Alignment/Understanding rebuild,
--                          so writing a fact into them would be silently
--                          overwritten -- the ledger keeps the fact readable
--                          and attributable instead.
CREATE TABLE IF NOT EXISTS joint_understanding_reflux (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    joint_understanding_id  INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    finding_id              INTEGER NOT NULL,
    target_kind             TEXT NOT NULL,
    target_id               INTEGER,
    statement               TEXT NOT NULL,
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    runtime_evidence_json   TEXT NOT NULL DEFAULT '[]',
    decision_method         TEXT NOT NULL DEFAULT 'reasoning_llm',
    intelligence_run_id     INTEGER,
    premise_snapshot_id     INTEGER,
    created_at              REAL NOT NULL,
    UNIQUE (joint_understanding_id, finding_id),
    FOREIGN KEY (joint_understanding_id)
        REFERENCES joint_understanding_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (finding_id)
        REFERENCES joint_understanding_finding (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_understanding_reflux_session
    ON joint_understanding_reflux (joint_understanding_id, id);

-- The shared verified-evidence feed (Issue #336).
--
-- Reflux (#332) attached a verified fact WITHOUT recording it as anyone's
-- answer, but for every origin except 'qa' the attachment was the
-- joint_understanding_reflux row itself -- and no rebuild read that table. The
-- fact was recorded, attributable, and invisible to every consumer that could
-- have used it, which is exactly the isolated third conversation Epic #328 set
-- out to remove. This table is the one place a verified fact is published to
-- and the one place a rebuild reads it from.
--
-- Provenance is its own: decision_method is always 'reasoning_llm' here, never
-- 'manual'. A rebuild has to be able to tell an investigated fact apart from a
-- developer's confirmed answer, which is why the feed is a separate prompt
-- input rather than being merged into the Q&A section.
--
-- content_digest is a publication digest over the source session, finding, and
-- the canonical semantic digest of the statement/evidence. Currency depends on
-- the source session's premise, so two independent investigations establishing
-- the same fact must remain distinct: an old source may become stale while a
-- newer source remains current. UNIQUE makes retrying the exact same
-- publication idempotent without conflating those two provenance records.
--
-- `superseded` is a manual/administrative override only. The ordinary currency
-- rules are evaluated at READ time (app/understanding_evidence_feed.
-- current_entries): a finding a later finding corrected, and a source session
-- whose Issue #337 premise verdict is no longer 'current', are both excluded
-- there. Neither is knowable when the fact is published, and an excluded entry
-- must stay readable as history rather than being deleted or rewritten.
CREATE TABLE IF NOT EXISTS understanding_evidence_feed (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id               INTEGER NOT NULL,
    session_id              INTEGER NOT NULL,
    source_kind             TEXT NOT NULL,
    source_id               INTEGER NOT NULL,
    finding_id              INTEGER NOT NULL,
    origin_kind             TEXT NOT NULL,
    origin_id               INTEGER NOT NULL,
    statement               TEXT NOT NULL,
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    runtime_evidence_json   TEXT NOT NULL DEFAULT '[]',
    decision_method         TEXT NOT NULL DEFAULT 'reasoning_llm',
    intelligence_run_id     INTEGER,
    premise_snapshot_id     INTEGER,
    premise_commit_sha      TEXT,
    content_digest          TEXT NOT NULL,
    superseded              INTEGER NOT NULL DEFAULT 0,
    schema_version          TEXT NOT NULL,
    created_at              REAL NOT NULL,
    UNIQUE (system_id, content_digest),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (finding_id)
        REFERENCES joint_understanding_finding (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL,
    FOREIGN KEY (premise_snapshot_id)
        REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_evidence_feed_system
    ON understanding_evidence_feed (system_id, session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_understanding_evidence_feed_source
    ON understanding_evidence_feed (source_kind, source_id);

-- Which rebuild used which verified fact (Issue #336).
--
-- Deliberately separate from human confirmation. A row here says the AI fed a
-- fact into a rebuild; interview_session.understanding_confirmed_at (and Issue
-- #312's Capability confirmation) says a human accepted the RESULT. Recording
-- both in one place would let "the AI used this" be read as "the developer
-- agreed with it" -- the conflation Epic #328 forbids between an investigation
-- fact and a developer's decision.
CREATE TABLE IF NOT EXISTS understanding_evidence_consumption (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id             INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    session_id              INTEGER NOT NULL,
    consumer_kind           TEXT NOT NULL,
    revision_id             INTEGER,
    intelligence_run_id     INTEGER,
    consumed_at             REAL NOT NULL,
    UNIQUE (evidence_id, consumer_kind, intelligence_run_id),
    FOREIGN KEY (evidence_id)
        REFERENCES understanding_evidence_feed (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (revision_id)
        REFERENCES understanding_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id)
        REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_understanding_evidence_consumption_evidence
    ON understanding_evidence_consumption (evidence_id, consumer_kind);

CREATE INDEX IF NOT EXISTS idx_understanding_evidence_consumption_session
    ON understanding_evidence_consumption (system_id, session_id, consumed_at);

-- Alignment Review / Review Queue (Issue #287). Contrasts confirmed/proposed
-- Intent Brief items (interview_intent_item, Issue #284) against the
-- evidence-backed Current System understanding (the latest
-- understanding_revision, Issue #136) to produce alignment items. Each item
-- carries its own claim + evidence (validated against the pinned snapshot,
-- same discipline as Issue #286's investigation evidence check) and a
-- DETERMINISTIC review classification (review_category/reason_code):
-- computed by a data-driven rule table over the reasoning model's finite
-- output fields (alignment_state/risk_flags/confidence/intent_field) --
-- classification itself is never a reasoning decision (Principle 6).
-- user_reason is a fixed Japanese template keyed by reason_code (never LLM
-- free text). Only review_category IN (must_review, batch_reviewable) ever
-- surfaces as an action-required card in the Review Queue; the rest are
-- collapsed/informational.
--
-- Rebuild semantics (POST .../alignment/build): a build DELETEs and
-- recreates only rows with status='open' AND user_decision IS NULL for the
-- session -- untouched suggestions with no user progress. Any row with a
-- different status (answered/corrected/held/inquiry) or a recorded
-- user_decision is never deleted or overwritten by a rebuild, regardless of
-- how the base revision changed (Principle 2 -- a rebuild must never lose a
-- human decision).
--
-- status='inquiry' (Issue #287's extension to the Issue #285 Inquiry
-- lifecycle) is set while an Inquiry with origin_kind='review_item' /
-- origin_id=<this row's id> is open, and reset to 'open' (never 'answered')
-- when that Inquiry resolves/holds/cancels -- the developer must still
-- explicitly answer via the item's own endpoint (Principle 2).
--
-- superseded (additive column, review-finding fix): set to 1 on a rebuild
-- for rows that were already in a TERMINAL status (answered/corrected) at
-- that time, so the fresh replacement row for the same contrast point is
-- distinguishable from stale history. held/inquiry rows are never marked
-- superseded (still in-flight). GET .../review-queue always excludes
-- superseded=1 rows in addition to filtering by review_category.
CREATE TABLE IF NOT EXISTS alignment_item (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    revision_id             INTEGER,
    snapshot_id             INTEGER NOT NULL,
    intent_item_id          INTEGER,
    intent_summary          TEXT,
    current_claim           TEXT NOT NULL,
    current_evidence        TEXT NOT NULL DEFAULT '[]',
    gap_summary             TEXT,
    proposed_interpretation TEXT,
    alignment_state         TEXT NOT NULL,
    risk_flags              TEXT NOT NULL DEFAULT '[]',
    confidence              TEXT NOT NULL,
    review_category         TEXT NOT NULL,
    reason_code             TEXT NOT NULL,
    user_reason             TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'open',
    user_decision           TEXT,
    -- Issue #313: the reviewed external policy used for deterministic
    -- classification. Existing rows are explicitly marked legacy rather
    -- than guessed to have been produced by a later YAML revision.
    policy_version          TEXT NOT NULL DEFAULT 'legacy-code-v1',
    policy_digest           TEXT,
    policy_rule_id          TEXT,
    -- Issue #310: this does not change deterministic classification.  It
    -- only makes an explicitly human-selected recheck target actionable.
    manual_recheck_required INTEGER NOT NULL DEFAULT 0,
    -- Issue #321: the stable discussion-point identity this physical row is
    -- one generation of, plus the auditable link to the previous
    -- generation. review_subject_id is a deterministic digest over
    -- structural anchors only (Intent field + confirmed Capability
    -- entity/relation ids -- app/inquiry_premise.py), NULL when the item has
    -- no stable anchor. subject_state is the finite lineage verdict
    -- (new/unchanged/changed/ambiguous/untrackable) and replaces_item_id the
    -- unique predecessor row, set only when that verdict is unambiguous.
    -- These are independent of content_hash/carried_over_from: those answer
    -- "is this byte-identical to a human-accepted row?", these answer "which
    -- earlier row was the same discussion point?".
    review_subject_id       TEXT,
    subject_state           TEXT,
    replaces_item_id        INTEGER,
    intelligence_run_id     INTEGER NOT NULL,
    is_mock                 INTEGER NOT NULL DEFAULT 0,
    created_at              REAL NOT NULL,
    updated_at              REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (intent_item_id) REFERENCES interview_intent_item (id) ON DELETE SET NULL,
    FOREIGN KEY (replaces_item_id) REFERENCES alignment_item (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE RESTRICT
);
-- NOTE: idx_alignment_item_review_subject is created in the migration block
-- (init_db), not here: this script also runs against pre-#321 databases,
-- where CREATE TABLE IF NOT EXISTS is a no-op and the indexed column does
-- not exist yet.

CREATE INDEX IF NOT EXISTS idx_alignment_item_session
    ON alignment_item (session_id, status);

CREATE INDEX IF NOT EXISTS idx_alignment_item_system
    ON alignment_item (system_id, session_id);

CREATE INDEX IF NOT EXISTS idx_alignment_item_review_queue
    ON alignment_item (session_id, review_category, status);

-- The exact confirmed capability composition used by an Alignment item and
-- the finite entity/relation ids the reasoning model cited from it (Issue
-- #312).  These are new sidecar tables instead of columns on alignment_item:
-- old databases gain them additively and legacy rows remain explicitly
-- unscoped rather than having identity inferred from names.
CREATE TABLE IF NOT EXISTS alignment_item_capability_scope (
    alignment_item_id INTEGER PRIMARY KEY,
    system_id         INTEGER NOT NULL,
    confirmation_id   INTEGER NOT NULL,
    change_kind       TEXT NOT NULL DEFAULT 'none',
    created_at        REAL NOT NULL,
    FOREIGN KEY (alignment_item_id) REFERENCES alignment_item (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_id) REFERENCES understanding_capability_confirmation (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_alignment_item_capability_scope_confirmation
    ON alignment_item_capability_scope (system_id, confirmation_id, alignment_item_id);

CREATE TABLE IF NOT EXISTS alignment_item_capability_dependency (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alignment_item_id INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    target_kind       TEXT NOT NULL,
    entity_id         INTEGER,
    relation_id       INTEGER,
    captured_digest   TEXT NOT NULL,
    created_at        REAL NOT NULL,
    CHECK (
        (target_kind = 'entity' AND entity_id IS NOT NULL AND relation_id IS NULL)
        OR
        (target_kind = 'relation' AND entity_id IS NULL AND relation_id IS NOT NULL)
    ),
    FOREIGN KEY (alignment_item_id) REFERENCES alignment_item (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES understanding_capability_entity (id) ON DELETE RESTRICT,
    FOREIGN KEY (relation_id) REFERENCES understanding_capability_relation (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_alignment_item_capability_dependency_entity
    ON alignment_item_capability_dependency (system_id, entity_id, alignment_item_id);

CREATE INDEX IF NOT EXISTS idx_alignment_item_capability_dependency_relation
    ON alignment_item_capability_dependency (system_id, relation_id, alignment_item_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alignment_item_capability_dependency_unique_entity
    ON alignment_item_capability_dependency (alignment_item_id, entity_id)
    WHERE target_kind = 'entity';

CREATE UNIQUE INDEX IF NOT EXISTS idx_alignment_item_capability_dependency_unique_relation
    ON alignment_item_capability_dependency (alignment_item_id, relation_id)
    WHERE target_kind = 'relation';

-- Issue #310: an Inquiry opened from a deterministically selected
-- no_review_required sample is an objection to the rule that classified the
-- item.  This is deliberately separate from the Inquiry conversation: it is
-- a compact, immutable audit fact keyed to the exact item/rule provenance,
-- not an interpretation of the user's free-text question.
CREATE TABLE IF NOT EXISTS alignment_rule_objection (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    session_id        INTEGER NOT NULL,
    alignment_item_id INTEGER NOT NULL UNIQUE,
    inquiry_id        INTEGER NOT NULL UNIQUE,
    reason_code       TEXT NOT NULL,
    policy_version    TEXT NOT NULL,
    policy_digest     TEXT,
    policy_rule_id    TEXT NOT NULL,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (alignment_item_id) REFERENCES alignment_item (id) ON DELETE CASCADE,
    FOREIGN KEY (inquiry_id) REFERENCES interview_inquiry (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alignment_rule_objection_system_rule
    ON alignment_rule_objection (system_id, reason_code, id);

-- A manual recheck target is an explicit, finite human action. The physical
-- item link is nullable so an in-flight target survives the rebuild DELETE
-- and can be rebound one-for-one to the same content in the same session.
-- Exact policy provenance prevents an objection to one reviewed rule version
-- from selecting a different version that happens to share a reason_code.
CREATE TABLE IF NOT EXISTS alignment_manual_recheck_target (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    session_id           INTEGER NOT NULL,
    alignment_item_id    INTEGER UNIQUE,
    reason_code          TEXT NOT NULL,
    policy_version       TEXT NOT NULL,
    policy_digest        TEXT NOT NULL DEFAULT '',
    policy_rule_id       TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    decision_method      TEXT NOT NULL DEFAULT 'manual',
    requested_by_user_id INTEGER,
    created_at           REAL NOT NULL,
    resolved_at          REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (alignment_item_id) REFERENCES alignment_item (id) ON DELETE SET NULL,
    FOREIGN KEY (requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_alignment_manual_recheck_target_pending
    ON alignment_manual_recheck_target (system_id, status, content_hash);

-- Automatic refresh job after an answer batch (Issue #288). One row per
-- refresh attempt; app/interview_refresh.py's request_refresh() dedupes so
-- at most one 'pending' and one 'updating' row exist per session at a time.
-- trigger_kind/status are explicit finite sets (Principle 6):
--   trigger_kind: qa_answer | intent_update | alignment_answer | nl_change_set
--   status:       pending | updating | updated | failed | stale
-- base_revision_id is the understanding_revision id at enqueue time (NULL
-- when the session has none yet); base_answer_marker is the enqueue
-- timestamp, the dedupe key input. result_revision_id identifies the
-- revision used by the resulting Alignment build (newly generated for
-- qa/alignment feedback, already persisted for intent/change-set updates);
-- intelligence_run_id is set only when this job generated an Understanding
-- review (Principle 7 audit lineage). error carries either a failure message
-- (status='failed') or a fixed informational note (status='updated' with
-- nothing new to apply, or status='stale' when superseded by a newer
-- completed job) -- never LLM free text (Principle 6/7).
CREATE TABLE IF NOT EXISTS interview_refresh_job (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    trigger_kind        TEXT NOT NULL,
    base_revision_id    INTEGER,
    base_answer_marker  REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    error               TEXT,
    intelligence_run_id INTEGER,
    result_revision_id  INTEGER,
    created_at          REAL NOT NULL,
    started_at          REAL,
    finished_at         REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (base_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL,
    FOREIGN KEY (result_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interview_refresh_job_session
    ON interview_refresh_job (session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_interview_refresh_job_system
    ON interview_refresh_job (system_id, session_id);

-- Natural-language bulk correction -> structured change set (Issue #289).
-- A developer's free-text correction covering multiple understanding items
-- is never applied directly to state (Principle 2/6): it is first turned
-- into a validated, structured, itemized change set by a reasoning LLM
-- (app/change_sets.py, prompt_version 'nl-change-set-v1'), previewed with
-- field-level diffs + deterministic impact, and only the items the
-- developer explicitly selects are ever applied. One row per submitted
-- correction text. base_revision_id pins the understanding_revision that
-- was current when this change set was proposed, so a later staleness
-- check (has the understanding moved on since?) has something concrete to
-- compare against for understanding_claim targets.
CREATE TABLE IF NOT EXISTS understanding_change_set (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    base_revision_id    INTEGER,
    source_text         TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'proposed',
    intelligence_run_id INTEGER NOT NULL,
    is_mock             INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (base_revision_id) REFERENCES understanding_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_understanding_change_set_session
    ON understanding_change_set (session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_understanding_change_set_system
    ON understanding_change_set (system_id, session_id);

-- One row per proposed field-level edit within a change set (Issue #289).
-- target_kind is a finite set (intent_item | understanding_claim) enforced
-- by construction: only the whitelisted (target_kind, field) pair for each
-- kind ever resolves (intent_item -> value_text, understanding_claim ->
-- summary); anything else -- including any attempt to address alignment
-- user_decision, evidence refs, confirmed-at audit fields, or alignment
-- classification, none of which are addressable target_kind values at all
-- -- is rejected with resolution_state='forbidden'. target_ref is JSON
-- ({"intent_item_id": ...} for intent_item, {"section": ..., "name": ...}
-- for understanding_claim) resolved deterministically against a finite
-- candidate list at proposal time (app/change_sets.py), never a reasoning
-- decision. resolution_state is re-validated (never re-interpreted) every
-- time it is read or applied: 'resolved' items are re-checked against the
-- CURRENT target for staleness (understanding moved on since
-- base_revision_id) and conflict (current value no longer matches the
-- recorded before_value); 'ambiguous'/'forbidden' are structural facts
-- fixed at creation and never change. applied/applied_at guard against
-- double-application when the same item_id is submitted to apply twice.
CREATE TABLE IF NOT EXISTS understanding_change_item (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    change_set_id       INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    target_kind         TEXT NOT NULL,
    target_ref          TEXT NOT NULL,
    field               TEXT NOT NULL,
    before_value        TEXT,
    after_value         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    resolution_state    TEXT NOT NULL,
    applied             INTEGER NOT NULL DEFAULT 0,
    applied_at          REAL,
    created_at          REAL NOT NULL,
    FOREIGN KEY (change_set_id) REFERENCES understanding_change_set (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_understanding_change_item_set
    ON understanding_change_item (change_set_id, id);

CREATE INDEX IF NOT EXISTS idx_understanding_change_item_system
    ON understanding_change_item (system_id, change_set_id);

-- Runtime Reality Check <-> Inquiry/Review Queue integration (Issue #290).
-- A developer's request to START capturing NEW runtime observation for a
-- component (as opposed to reading facts that already exist) is never
-- auto-started (Principle 5/8): it is only ever recorded here as a proposal,
-- and approving it (status='approved') does NOT itself flip any
-- components.mode policy row -- the response only points back at the
-- existing PUT /components/{component_id}/policy endpoint. status is a
-- finite set: proposed | approved | rejected | expired. decision_by/
-- decision_at are set only by the manual approve/reject endpoints
-- (decision_method='manual', Principle 2) -- never by investigation or any
-- other automatic code path.
CREATE TABLE IF NOT EXISTS runtime_observation_proposal (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    origin_inquiry_id       INTEGER,
    origin_alignment_item_id INTEGER,
    target_component        TEXT NOT NULL,
    purpose                 TEXT NOT NULL,
    expected_cost           TEXT,
    risk_note               TEXT,
    retention_note          TEXT,
    status                  TEXT NOT NULL DEFAULT 'proposed',
    decision_by             TEXT,
    decision_at             REAL,
    created_at              REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (origin_inquiry_id) REFERENCES interview_inquiry (id) ON DELETE SET NULL,
    FOREIGN KEY (origin_alignment_item_id) REFERENCES alignment_item (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_observation_proposal_session
    ON runtime_observation_proposal (session_id, status);

CREATE INDEX IF NOT EXISTS idx_runtime_observation_proposal_system
    ON runtime_observation_proposal (system_id, session_id);

-- Answerable knowledge areas / handoff (Issue #291). A developer picks which
-- knowledge areas they can answer NOW (no role inference, Principle 6); the
-- finite set is product_intent | domain_rule | operations | implementation |
-- security. Out-of-area questions are never hidden -- they are grouped
-- separately and can be deferred/held/handed off to another assignee.
--
-- origin_kind is 'qa' (interview_qa) or 'review_item' (alignment_item), a
-- finite set like Issue #285/#287's origin_kind. assignee/created_by/
-- answered_by are free-text actor names (no org auth system exists yet --
-- same convention as understanding_confirmed_by/answered_by elsewhere).
--
-- Creating a handoff never writes the assignee's eventual answer into the
-- origin row (interview_qa.answer_text / alignment_item.user_decision):
-- answer_text/answered_by/answered_at here are the ASSIGNEE's own answer,
-- held pending the ORIGINAL user's explicit confirmation via /return ->
-- the origin item's own existing answer endpoint (Principle 2 -- an
-- assignee's answer is never silently treated as the developer's own).
--
-- For origin_kind='qa': the origin interview_qa row's own `status` is left
-- untouched (do not overload its finite set); instead
-- interview_qa.handoff_id (additive column below) links to this row, and
-- the askable-view filter (routes/interview.py) treats a qa row with an
-- open handoff (status IN pending/answered) as "held via handoff" -- not
-- re-asked. For origin_kind='review_item': alignment_item.status is set to
-- 'held' (the same finite value /hold already uses) plus
-- alignment_item.handoff_id (additive column below) links to this row.
--
-- status is a finite set: pending | answered | returned | cancelled.
-- Transition table (enforced in routes/interview_handoff.py):
--   pending -> answered | cancelled
--   answered -> returned
--   anything else -> 409.
CREATE TABLE IF NOT EXISTS question_handoff (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL,
    system_id        INTEGER NOT NULL,
    origin_kind      TEXT NOT NULL,
    origin_id        INTEGER NOT NULL,
    assignee         TEXT NOT NULL,
    background       TEXT NOT NULL,
    needed_decision  TEXT NOT NULL,
    evidence         TEXT,
    due_note         TEXT,
    priority         TEXT NOT NULL DEFAULT 'normal',
    status           TEXT NOT NULL DEFAULT 'pending',
    answer_text      TEXT,
    answered_by      TEXT,
    answered_at      REAL,
    created_by       TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_handoff_session
    ON question_handoff (session_id, status);

CREATE INDEX IF NOT EXISTS idx_question_handoff_system
    ON question_handoff (system_id, session_id);

-- Probe Cell Fabric (Issue #297), Sub 1: Cell contract / Role Card / common
-- state schema (Issue #298). See app/cell_fabric.py for the Pydantic
-- contract layer and the "Probe Cell Fabric(Issue #297)" section of
-- docs/project-intelligence.md for the full epic design.
--
-- agent_role_cards is versioned and append-only per (system_id, role_key,
-- version): a new revision is always a new row (UNIQUE constraint below
-- enforces no duplicate version), never an UPDATE of an existing version's
-- content. Only `status` may be updated on an existing row (e.g. deprecate).
-- This is a distinct table from the existing API Role Card display model
-- (Issue #58) -- Agent Role Card declares a Cell's mission/scope/model
-- alias/tool policy/acceptance template/rubric ref, never mixed with it.
CREATE TABLE IF NOT EXISTS agent_role_cards (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    role_key                    TEXT NOT NULL,
    version                     TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'draft'
                                     CHECK (status IN ('draft', 'active', 'deprecated')),
    mission                     TEXT NOT NULL,
    scope_json                  TEXT NOT NULL DEFAULT '[]',
    out_of_scope_json           TEXT NOT NULL DEFAULT '[]',
    model_alias                 TEXT NOT NULL,
    tool_policy_json            TEXT NOT NULL DEFAULT '{}',
    acceptance_template_json    TEXT NOT NULL DEFAULT '[]',
    rubric_ref                  TEXT,
    changelog                   TEXT NOT NULL,
    schema_version              TEXT NOT NULL,
    decision_method             TEXT NOT NULL DEFAULT 'manual',
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (system_id, role_key, version)
);

CREATE INDEX IF NOT EXISTS idx_agent_role_cards_system_role
    ON agent_role_cards (system_id, role_key, id DESC);

-- cell_definitions: one logical Probe Cell per row. roster_json NULL means a
-- worker Cell; a non-null JSON array (possibly empty) means an orchestrator
-- Cell -- there is no separate "kind" column, matching the shared
-- cell_definition schema. role_card_id pins the Cell to one specific Role
-- Card VERSION row (not just a role_key), so a Role Card revision never
-- silently changes an already-bound Cell's behavior.
CREATE TABLE IF NOT EXISTS cell_definitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    cell_id         TEXT NOT NULL,
    roster_json     TEXT,
    role_card_id    INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'dormant'
                        CHECK (status IN ('active', 'dormant', 'retired')),
    mission         TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (role_card_id) REFERENCES agent_role_cards (id) ON DELETE RESTRICT,
    UNIQUE (system_id, cell_id)
);

CREATE INDEX IF NOT EXISTS idx_cell_definitions_system
    ON cell_definitions (system_id, id DESC);

-- Probe Cell Fabric (Issue #297), Sub 2: versioned Cell Binding and a
-- read-only Probe Cell pilot (Issue #299). See app/cell_binding.py for the
-- provenance/versioning/drift logic and the "Probe Cell Fabric(Issue #297)"
-- section of docs/project-intelligence.md for the full design.
--
-- cell_bindings rows are append-only VERSIONS: creating a new binding for a
-- Cell never UPDATEs the content of a prior version's row -- it inserts a
-- new row with version = max(version)+1 and marks the previous
-- active/stale/review_required row 'superseded' in the same transaction.
-- provenance is exactly one of probe_point_id (an approved Probe Point) or
-- probe_pattern_id (a Probe Pattern's saved point); both are nullable so
-- either source can be recorded, but application logic (app/cell_binding.py)
-- requires exactly one to be set.
CREATE TABLE IF NOT EXISTS cell_bindings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    version               INTEGER NOT NULL,
    snapshot_id           INTEGER NOT NULL,
    commit_sha            TEXT NOT NULL,
    path                  TEXT NOT NULL,
    qualified_symbol      TEXT NOT NULL,
    component_id          TEXT NOT NULL,
    probe_point_id        INTEGER,
    probe_pattern_id      INTEGER,
    feature_refs_json     TEXT NOT NULL DEFAULT '[]',
    capability_refs_json  TEXT NOT NULL DEFAULT '[]',
    entrypoint_refs_json  TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'stale', 'review_required', 'superseded')),
    status_reason         TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE RESTRICT,
    FOREIGN KEY (probe_point_id) REFERENCES probe_points (id) ON DELETE SET NULL,
    FOREIGN KEY (probe_pattern_id) REFERENCES probe_patterns (id) ON DELETE SET NULL,
    UNIQUE (system_id, cell_definition_id, version)
);

CREATE INDEX IF NOT EXISTS idx_cell_bindings_cell
    ON cell_bindings (system_id, cell_definition_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_cell_bindings_status
    ON cell_bindings (system_id, cell_definition_id, status);

-- cell_activations: an audit record of when a Cell was invoked (explicit
-- request or an aggregation-window trigger). This is NOT a per-trace LLM
-- call log -- Sub 2 never invokes an LLM; used_llm defaults to 0 and there
-- is no LLM execution path in this sub-issue at all (later subs may record
-- used_llm=1 once an orchestrator/worker execution path exists).
CREATE TABLE IF NOT EXISTS cell_activations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    trigger_kind          TEXT NOT NULL
                              CHECK (trigger_kind IN ('explicit', 'aggregation_window')),
    window_start          REAL,
    window_end            REAL,
    requested_by          TEXT,
    used_llm              INTEGER NOT NULL DEFAULT 0,
    intelligence_run_id   INTEGER,
    status                TEXT NOT NULL DEFAULT 'recorded'
                              CHECK (status IN ('recorded', 'completed', 'failed')),
    detail                TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL,
    completed_at          REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cell_activations_cell
    ON cell_activations (system_id, cell_definition_id, id DESC);
-- Issue #300 (Sub 3 of the Probe Cell Fabric epic, Issue #297): Goal/Task
-- ledger + delegate/report/escalate protocol. Purely deterministic -- no
-- reasoning-model call anywhere in this table group or in app/cell_tasks.py.
-- parent_goal_id NULL means a root goal; cycle rejection is enforced in
-- app/cell_tasks.py (walking the parent chain), not by SQLite.
CREATE TABLE IF NOT EXISTS cell_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    parent_goal_id  INTEGER,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_cell_id   INTEGER,
    status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'achieved', 'abandoned')),
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_goal_id) REFERENCES cell_goals (id) ON DELETE CASCADE,
    FOREIGN KEY (owner_cell_id) REFERENCES cell_definitions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cell_goals_system
    ON cell_goals (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_goals_parent
    ON cell_goals (system_id, parent_goal_id);

-- cell_tasks: exactly one owner Cell and one parent goal per task by
-- construction (no many-to-many membership table exists at Sub 3).
-- acceptance_json must be a non-empty JSON array; this is enforced at the
-- API/core layer via cell_fabric.TaskDelegation-style validation, not by a
-- SQLite CHECK (JSON array emptiness is not expressible there). UNIQUE
-- (system_id, idempotency_key) relies on SQLite treating distinct NULLs as
-- non-conflicting, so tasks created without an idempotency key never
-- collide with each other.
CREATE TABLE IF NOT EXISTS cell_tasks (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    goal_id               INTEGER NOT NULL,
    owner_cell_id         INTEGER NOT NULL,
    delegated_by_cell_id  INTEGER,
    title                 TEXT NOT NULL,
    acceptance_json       TEXT NOT NULL,
    context_refs_json     TEXT NOT NULL DEFAULT '[]',
    budget_json           TEXT,
    deadline              TEXT,
    priority              TEXT NOT NULL DEFAULT 'normal'
                              CHECK (priority IN ('low', 'normal', 'high')),
    status                TEXT NOT NULL DEFAULT 'todo'
                              CHECK (status IN ('todo', 'doing', 'review', 'done', 'failed', 'blocked')),
    retry_count           INTEGER NOT NULL DEFAULT 0,
    retry_limit           INTEGER NOT NULL DEFAULT 3,
    blocked_by_json       TEXT NOT NULL DEFAULT '[]',
    acceptance_met        INTEGER NOT NULL DEFAULT 0,
    evidence_json         TEXT NOT NULL DEFAULT '[]',
    returned_to_parent    INTEGER NOT NULL DEFAULT 0,
    idempotency_key       TEXT,
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (goal_id) REFERENCES cell_goals (id) ON DELETE CASCADE,
    FOREIGN KEY (owner_cell_id) REFERENCES cell_definitions (id) ON DELETE RESTRICT,
    FOREIGN KEY (delegated_by_cell_id) REFERENCES cell_definitions (id) ON DELETE SET NULL,
    UNIQUE (system_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_cell_tasks_system
    ON cell_tasks (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_tasks_goal
    ON cell_tasks (system_id, goal_id);
CREATE INDEX IF NOT EXISTS idx_cell_tasks_owner
    ON cell_tasks (system_id, owner_cell_id);
CREATE INDEX IF NOT EXISTS idx_cell_tasks_status
    ON cell_tasks (system_id, status);

-- Append-only audit of every task state transition -- retry / blocked /
-- unblocked / returned_to_parent are ordinary rows here, written in the same
-- transaction as the state change, never reconstructed after the fact.
CREATE TABLE IF NOT EXISTS cell_task_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id    INTEGER NOT NULL,
    task_id      INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    detail       TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES cell_tasks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cell_task_events_task
    ON cell_task_events (system_id, task_id, id DESC);

-- cell_reports: kind is schema-validated to digest|escalation only -- any
-- other free-form payload is rejected fail-closed at the API layer
-- (Pydantic extra="forbid"). fact_json / interpretation_json / ask_json stay
-- separate fields: raw evidence-backed facts are never mixed with
-- interpretation/ask text (Principle 7), even though this module calls no
-- reasoning model.
CREATE TABLE IF NOT EXISTS cell_reports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    cell_definition_id   INTEGER NOT NULL,
    task_id              INTEGER,
    kind                 TEXT NOT NULL CHECK (kind IN ('digest', 'escalation')),
    severity             TEXT CHECK (severity IS NULL OR severity IN ('sev1', 'sev2', 'sev3')),
    fact_json            TEXT NOT NULL DEFAULT '[]',
    interpretation_json  TEXT NOT NULL DEFAULT '[]',
    ask_json             TEXT NOT NULL DEFAULT '[]',
    idempotency_key      TEXT,
    created_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES cell_tasks (id) ON DELETE SET NULL,
    UNIQUE (system_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_cell_reports_system
    ON cell_reports (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_reports_cell
    ON cell_reports (system_id, cell_definition_id, id DESC);

-- cell_escalations: created automatically from an escalation-kind report in
-- the same transaction; never created independently of a report.
CREATE TABLE IF NOT EXISTS cell_escalations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    report_id            INTEGER NOT NULL,
    cell_definition_id   INTEGER NOT NULL,
    severity             TEXT NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3')),
    status               TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'acknowledged', 'resolved')),
    summary              TEXT NOT NULL,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (report_id) REFERENCES cell_reports (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cell_escalations_system
    ON cell_escalations (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_escalations_status
    ON cell_escalations (system_id, status, id DESC);

-- Probe Cell Fabric (Issue #297), Sub 4: 領域オーケストレーター (Issue #301).
-- See app/cell_orchestrator.py for guardrail validation, the deterministic
-- digest builder, and the reasoning triage; the "Probe Cell Fabric(Issue
-- #297)" section of docs/project-intelligence.md for the full epic design.
--
-- cell_roster_events: append-only audit of every roster change made through
-- the explicit PUT /cell-fabric/cells/{cell_id}/roster endpoint (creation is
-- NOT audited here). old_roster_json is NULL when the cell had no roster
-- before (a worker Cell becoming an orchestrator for the first time).
CREATE TABLE IF NOT EXISTS cell_roster_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    old_roster_json       TEXT,
    new_roster_json       TEXT NOT NULL,
    changed_by            TEXT,
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cell_roster_events_cell
    ON cell_roster_events (system_id, cell_definition_id, id DESC);

-- cell_triage_results: persisted reasoning triage output (individual vs
-- systemic vs upstream vs inconclusive), kept separate from the
-- deterministic digest_json snapshot it was computed from (Principle 7 --
-- raw facts and interpretation stay separate fields even within one row).
-- intelligence_run_id is NOT NULL: a row here is only ever written alongside
-- a completed intelligence_runs row in the same transaction -- on ANY
-- failure app/cell_orchestrator.py persists the failed run and writes no row
-- here at all (fail-closed, no heuristic classification).
CREATE TABLE IF NOT EXISTS cell_triage_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id               INTEGER NOT NULL,
    cell_definition_id      INTEGER NOT NULL,
    intelligence_run_id     INTEGER NOT NULL,
    digest_json             TEXT NOT NULL,
    classification          TEXT NOT NULL
                                CHECK (classification IN ('individual', 'systemic', 'upstream', 'inconclusive')),
    reasoning_summary       TEXT NOT NULL DEFAULT '',
    affected_cell_ids_json  TEXT NOT NULL DEFAULT '[]',
    proposed_ask            TEXT NOT NULL DEFAULT '',
    created_at              REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_cell_triage_results_cell
    ON cell_triage_results (system_id, cell_definition_id, id DESC);

-- Probe Cell Fabric (Issue #297), Sub 6: 品質サンプリング・独立監査・quality
-- floor (Issue #302). See app/cell_quality.py for the deterministic
-- stratified sampling, the deterministic verdict + fail-closed reasoning
-- explanation, the daily audit budget gate, and the quality-floor
-- suspend/resume logic; the "Probe Cell Fabric(Issue #297)" section of
-- docs/project-intelligence.md for the full epic design.
--
-- cell_quality_configs: one row per (system, Cell). sample_rate/audit_rate/
-- quality_floor are fractions in [0.0, 1.0]; strata_json is a JSON array of
-- {name, task_type?, risk?, rare} objects used for finite-field stratum
-- matching only (Principle 6) -- never similarity/keyword scoring.
CREATE TABLE IF NOT EXISTS cell_quality_configs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    sample_rate           REAL NOT NULL DEFAULT 0.05
                              CHECK (sample_rate >= 0.0 AND sample_rate <= 1.0),
    strata_json           TEXT NOT NULL DEFAULT '[]',
    audit_rate            REAL NOT NULL DEFAULT 0.1
                              CHECK (audit_rate >= 0.0 AND audit_rate <= 1.0),
    quality_floor         REAL NOT NULL DEFAULT 0.7
                              CHECK (quality_floor >= 0.0 AND quality_floor <= 1.0),
    floor_window          INTEGER NOT NULL DEFAULT 20 CHECK (floor_window >= 1),
    daily_audit_budget    INTEGER NOT NULL DEFAULT 50 CHECK (daily_audit_budget >= 0),
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    UNIQUE (system_id, cell_definition_id)
);

-- cell_quality_samples: deterministic stratified selection output. Every row
-- is idempotent (UNIQUE + INSERT OR IGNORE at the app layer): re-running
-- selection over the same window never duplicates a (system, Cell, target)
-- row. selection_seed is the exact stable-hash input string used to derive
-- the selection fraction, so a selection decision is always reproducible
-- and auditable from the row alone.
CREATE TABLE IF NOT EXISTS cell_quality_samples (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    config_id             INTEGER NOT NULL,
    stratum               TEXT NOT NULL DEFAULT '',
    target_kind           TEXT NOT NULL DEFAULT 'trace' CHECK (target_kind = 'trace'),
    -- traces.trace_id is TEXT (system_id + trace_id composite PK).
    target_id             TEXT NOT NULL,
    selection_seed        TEXT NOT NULL,
    selected_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (config_id) REFERENCES cell_quality_configs (id) ON DELETE RESTRICT,
    UNIQUE (system_id, cell_definition_id, target_kind, target_id)
);

CREATE INDEX IF NOT EXISTS idx_cell_quality_samples_cell
    ON cell_quality_samples (system_id, cell_definition_id, id DESC);

-- cell_quality_audits: the DETERMINISTIC verdict (Principle 6, evaluated
-- against the component's evaluation_criteria via app/evaluator.py) plus an
-- OPTIONAL fail-closed reasoning explanation (only attempted for 'fail'
-- verdicts; a failed/skipped explanation never blocks the deterministic
-- verdict from being persisted). auditor_model_alias is recorded verbatim
-- so a worker-alias vs auditor-alias mismatch is always visible in the row.
-- verbatim_example and explanation are separate fields from fact/verdict --
-- raw evidence-backed facts are never mixed with interpretation text
-- (Principle 7), even though the verdict itself is deterministic.
CREATE TABLE IF NOT EXISTS cell_quality_audits (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                 INTEGER NOT NULL,
    sample_id                 INTEGER NOT NULL,
    auditor_model_alias       TEXT NOT NULL,
    verdict                   TEXT NOT NULL
                                  CHECK (verdict IN ('pass', 'fail', 'no_criteria')),
    verdict_decision_method   TEXT NOT NULL DEFAULT 'deterministic',
    is_blind                  INTEGER NOT NULL DEFAULT 0,
    failed_criteria_json      TEXT NOT NULL DEFAULT '[]',
    verbatim_example          TEXT NOT NULL DEFAULT '',
    explanation               TEXT NOT NULL DEFAULT '',
    explanation_run_id        INTEGER,
    created_at                 REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (sample_id) REFERENCES cell_quality_samples (id) ON DELETE CASCADE,
    FOREIGN KEY (explanation_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cell_quality_audits_sample
    ON cell_quality_audits (system_id, sample_id, id DESC);

-- cell_intake_states: only the ONE Cell whose rolling pass rate breaches its
-- quality_floor is ever suspended -- every other Cell (in this System or any
-- other) is untouched. escalation_id points at the sev1 escalation created
-- (via app/cell_tasks.py's existing submit_report path) in the same logical
-- suspend operation; resume clears it back to NULL.
CREATE TABLE IF NOT EXISTS cell_intake_states (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER NOT NULL,
    intake_status         TEXT NOT NULL DEFAULT 'accepting'
                              CHECK (intake_status IN ('accepting', 'suspended')),
    reason                TEXT NOT NULL DEFAULT '',
    escalation_id         INTEGER,
    changed_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (escalation_id) REFERENCES cell_escalations (id) ON DELETE SET NULL,
    UNIQUE (system_id, cell_definition_id)
);

-- cell_quality_usage: System-scoped daily audit-invocation counter, mirroring
-- llm_daily_usage's (Issue #273) exact pattern -- one row per (system, UTC
-- day), incremented atomically before an audit runs. The unit is accepted
-- run_audit calls, not tokens or currency. Each Cell's own
-- cell_quality_configs.daily_audit_budget is the invocation ceiling compared
-- against this SHARED per-System counter (never a per-Cell counter row).
CREATE TABLE IF NOT EXISTS cell_quality_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id     INTEGER NOT NULL,
    day           TEXT NOT NULL,
    audits_used   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (system_id, day)
);
-- Probe Cell Fabric (Issue #297), Sub 5: Root Orchestrator と統合ダイジェスト
-- (Issue #303). See app/cell_root.py for the deterministic digest builder and
-- ask lifecycle, and the "Probe Cell Fabric(Issue #297)" section of
-- docs/project-intelligence.md for the full epic design.
--
-- cell_asks: a human-decidable Ask surfaced by the root digest, created from
-- (a) an open sev1/sev2 cell_escalations row or (b) a cell_triage_results row
-- with a non-empty proposed_ask. source_kind + source_id together identify
-- the originating row (report is reserved for a future source kind; this Sub
-- only ever writes 'escalation' or 'triage'). dedupe_key makes re-sync
-- idempotent (UNIQUE with system_id) -- re-running sync_asks_from_sources
-- never creates a duplicate row for the same source.
--
-- execution_approved ALWAYS stays 0 in this Sub: deciding an Ask ('accepted'
-- | 'held' | 'rejected') records decision_method='manual' and flows back into
-- the Goal/Task ledger (unblocking a blocked task, acknowledging the source
-- escalation), but it is a PROPOSAL-ACCEPT record, never an EXECUTION-APPROVE
-- record -- no policy change, candidate deploy, or patch apply is triggered
-- here. Execution approval is #304's and the existing #25/#216/#242/#252
-- gates' domain; this column exists so a later Sub can distinguish the two
-- without a schema change, but nothing in this Sub ever sets it to 1.
CREATE TABLE IF NOT EXISTS cell_asks (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    source_kind           TEXT NOT NULL CHECK (source_kind IN ('escalation', 'triage', 'report')),
    source_id             INTEGER NOT NULL,
    cell_definition_id    INTEGER,
    goal_id               INTEGER,
    task_id               INTEGER,
    ask_text              TEXT NOT NULL,
    severity              TEXT NOT NULL DEFAULT 'sev2' CHECK (severity IN ('sev1', 'sev2', 'sev3')),
    status                TEXT NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'accepted', 'held', 'rejected')),
    decision              TEXT NOT NULL DEFAULT '',
    decision_note         TEXT NOT NULL DEFAULT '',
    decision_method       TEXT NOT NULL DEFAULT '',
    decided_by            TEXT,
    decided_at            REAL,
    execution_approved    INTEGER NOT NULL DEFAULT 0,
    dedupe_key            TEXT NOT NULL,
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE SET NULL,
    FOREIGN KEY (goal_id) REFERENCES cell_goals (id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES cell_tasks (id) ON DELETE SET NULL,
    UNIQUE (system_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_cell_asks_system
    ON cell_asks (system_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_asks_status
    ON cell_asks (system_id, status, id DESC);

-- Probe Cell Fabric (Issue #297), Sub 7: 改善仮説・カナリア・shadow実行承認
-- ゲート (Issue #304). See app/cell_improvement.py for the lifecycle state
-- machine, the canary evidence gate, parent/human approval gates, rubric
-- ownership, the consecutive-rejection auto-suspend circuit breaker, and the
-- fail-closed reasoning hypothesis draft; the "Probe Cell Fabric(Issue #297)"
-- section of docs/project-intelligence.md for the full epic design.
--
-- cell_improvements: one row per improvement hypothesis. There is NO DELETE
-- endpoint anywhere in this module -- a rejected row is permanent history,
-- both for audit and because it is the deterministic input to the
-- consecutive-rejection circuit breaker. role_card_id is the card that was
-- PINNED to the Cell at the time this improvement was created (immutable
-- baseline for comparison, distinct from cell_definitions.role_card_id which
-- changes on adoption); NULL for target_kind='candidate_patch'.
-- canary_evidence_json holds ONLY refs into EXISTING Replay/Experiment/
-- Evaluation-Criteria infrastructure (replay_run:<id> / experiment:<id> /
-- evaluation:<id>) -- never a new execution record.
CREATE TABLE IF NOT EXISTS cell_improvements (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    cell_definition_id          INTEGER NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'observed'
                                    CHECK (status IN ('observed', 'proposed', 'canary_ready',
                                                       'canary_running', 'adopted', 'rejected',
                                                       'blocked')),
    target_kind                 TEXT NOT NULL CHECK (target_kind IN ('role_card', 'candidate_patch')),
    hypothesis                  TEXT NOT NULL DEFAULT '',
    expected_effect             TEXT NOT NULL DEFAULT '',
    risk                        TEXT NOT NULL DEFAULT '',
    rollback_plan               TEXT NOT NULL DEFAULT '',
    observed_facts_json         TEXT NOT NULL DEFAULT '[]',
    proposal_run_id             INTEGER,
    role_card_id                INTEGER,
    proposed_role_card_version  TEXT,
    canary_evidence_json        TEXT NOT NULL DEFAULT '[]',
    parent_cell_id              INTEGER,
    parent_approved_by          TEXT,
    parent_approved_at          REAL,
    human_approved_by           TEXT,
    human_approved_at           REAL,
    suspended                   INTEGER NOT NULL DEFAULT 0,
    suspension_reason           TEXT NOT NULL DEFAULT '',
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (cell_definition_id) REFERENCES cell_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL,
    FOREIGN KEY (role_card_id) REFERENCES agent_role_cards (id) ON DELETE SET NULL,
    FOREIGN KEY (parent_cell_id) REFERENCES cell_definitions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cell_improvements_cell
    ON cell_improvements (system_id, cell_definition_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cell_improvements_status
    ON cell_improvements (system_id, cell_definition_id, status);

-- cell_improvement_events: append-only audit trail -- rejected hypotheses
-- are never deleted, and there is no DELETE endpoint anywhere in this
-- module. This is also the deterministic input the consecutive-rejection
-- circuit breaker (app/cell_improvement.py::_consecutive_rejection_count)
-- reads to decide whether new-improvement creation is currently refused.
CREATE TABLE IF NOT EXISTS cell_improvement_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    improvement_id    INTEGER NOT NULL,
    event_type        TEXT NOT NULL CHECK (event_type IN (
                          'created', 'status_transition', 'parent_approval', 'human_approval',
                          'approvals_invalidated',
                          'shadow_proposed', 'live_shadow_approval_requested',
                          'live_shadow_approved', 'suspended', 'resumed', 'rolled_back'
                      )),
    from_status       TEXT,
    to_status         TEXT,
    actor             TEXT,
    detail            TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (improvement_id) REFERENCES cell_improvements (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cell_improvement_events_improvement
    ON cell_improvement_events (system_id, improvement_id, id ASC);

-- cell_shadow_decisions: a 'shadow_proposal' row and a
-- 'live_shadow_execution_approval' row are ALWAYS separate records with
-- separate statuses -- approving one never approves or performs the other.
-- Approving a 'live_shadow_execution_approval' writes NOTHING but this row
-- plus one cell_improvement_events row: no policy write, no candidate
-- deploy, anywhere in app/cell_improvement.py.
CREATE TABLE IF NOT EXISTS cell_shadow_decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    improvement_id    INTEGER NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('shadow_proposal', 'live_shadow_execution_approval')),
    status            TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'approved', 'rejected')),
    decided_by        TEXT,
    decided_at        REAL,
    decision_method   TEXT NOT NULL DEFAULT '',
    note              TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (improvement_id) REFERENCES cell_improvements (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cell_shadow_decisions_improvement
    ON cell_shadow_decisions (system_id, improvement_id, id DESC);

-- ---------------------------------------------------------------------------
-- State-driven System Interview workflow (Issue #349, implementing the
-- docs/system-interview-workflow-ux.md spec written by Issue #342/#343-#346).
--
-- These five tables are exactly the four persisted facts §8.1 declares
-- missing (A: diff review completion, B: system-process running/failure
-- records, C: reached_state + backward requests, D: acknowledgement of a
-- backward request) plus the manual audit record for OP-D14
-- (suspend/handoff/resume, which reuses the existing interview_session
-- `status` rather than adding a state fact of its own).
--
-- None of them add or relax a human gate: A and D are developer decisions
-- recorded with `decision_method = 'manual'`; B and C are system-recorded
-- progress facts. They exist so the displayed workflow state is derivable
-- from persisted facts alone (spec principle P9) and survives a reload.

-- Fact A: the developer explicitly recorded "I reviewed this diff".
-- `diff_materialized_at` is the identity of the reviewed diff (the
-- interview_session.materialized_at value at review time); a newly generated
-- diff gets a new timestamp, so a previous completion record never carries
-- over to it (spec §2.3 W6). Download/摘要表示 never write this row.
CREATE TABLE IF NOT EXISTS interview_diff_review (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           INTEGER NOT NULL,
    system_id            INTEGER NOT NULL,
    diff_materialized_at REAL NOT NULL,
    diff_digest          TEXT NOT NULL DEFAULT '',
    reviewed_by          TEXT NOT NULL DEFAULT '',
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method = 'manual'),
    note                 TEXT NOT NULL DEFAULT '',
    created_at           REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_diff_review_session
    ON interview_diff_review (session_id, id DESC);

-- Fact B: one row per run of a process that can put the screen into `W1`.
-- `process_kind` is a finite set (see app/interview_workflow.py
-- PROCESS_KINDS); `failure_class` and `target_state` are only set on a
-- failed run, and `target_state` is restricted to the spec's finite
-- blocking set W2 / W4 / W5. A blocking failure counts as unresolved until a
-- later run of the same kind succeeds -- derived, never stored, so a success
-- anywhere (manual retry or automatic refresh) resolves it identically.
CREATE TABLE IF NOT EXISTS interview_process_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL,
    system_id      INTEGER NOT NULL,
    process_kind   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'succeeded', 'failed')),
    failure_class  TEXT CHECK (failure_class IN ('blocking', 'degraded')),
    target_state   TEXT CHECK (target_state IN ('W2', 'W4', 'W5')),
    error          TEXT,
    started_at     REAL NOT NULL,
    heartbeat_at   REAL,
    finished_at    REAL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_process_run_session
    ON interview_process_run (session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_interview_process_run_status
    ON interview_process_run (session_id, status);

-- Fact C part 1: the session's current workflow checkpoint. NOT an all-time
-- monotonic maximum -- it moves backward only when the developer
-- acknowledges a backward request (fact D). Only ordered states (W2..W7) are
-- ever stored here; W0-A / W0-B / W1 carry no workflow position.
CREATE TABLE IF NOT EXISTS interview_workflow_checkpoint (
    session_id    INTEGER PRIMARY KEY,
    system_id     INTEGER NOT NULL,
    reached_state TEXT NOT NULL CHECK (
        reached_state IN ('W2', 'W3', 'W4', 'W5', 'W6', 'W7')
    ),
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

-- Fact C part 2: a recorded backward request. Created by the system when the
-- first-match rule table picks a candidate state EARLIER than reached_state.
-- While one is `pending`, the displayed state stays at reached_state
-- (spec §2.2 stage 2). `resolved` is the system-only outcome when the facts
-- move forward again on their own; `acknowledged` requires fact D.
CREATE TABLE IF NOT EXISTS interview_back_request (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    cause_kind      TEXT NOT NULL,
    candidate_state TEXT NOT NULL,
    reached_state   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'acknowledged', 'resolved')),
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_back_request_session
    ON interview_back_request (session_id, id DESC);

-- Fact D: the developer's explicit acknowledgement of ONE backward request.
-- Deliberately a separate table from fact C: "interrupt what I am doing and
-- go back" is a developer decision, not an observed progress fact, and an
-- acknowledgement is never reused for a later request (spec §2.2 stage 2-5).
CREATE TABLE IF NOT EXISTS interview_back_acknowledgement (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    back_request_id INTEGER NOT NULL UNIQUE,
    session_id      INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    decision_method TEXT NOT NULL DEFAULT 'manual'
                        CHECK (decision_method = 'manual'),
    created_at      REAL NOT NULL,
    FOREIGN KEY (back_request_id) REFERENCES interview_back_request (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

-- OP-D14 audit: suspending / handing off / resuming a session flips the
-- existing interview_session.status between 'closed' and 'open'. That is a
-- developer decision, so it carries a manual audit record. It adds NO new
-- state-decision fact -- rule row 3 reads `status` directly.
CREATE TABLE IF NOT EXISTS interview_session_status_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    system_id       INTEGER NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('close', 'reopen')),
    terminal_kind   TEXT CHECK (terminal_kind IN ('suspended', 'handoff')),
    reason          TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL DEFAULT '',
    decision_method TEXT NOT NULL DEFAULT 'manual'
                        CHECK (decision_method = 'manual'),
    created_at      REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_session_status_audit_session
    ON interview_session_status_audit (session_id, id DESC);

-- ---------------------------------------------------------------------------
-- Purpose Chain relation decisions (Issue #388, docs/purpose-chain.md §1.5).
--
-- The ONLY thing #388 persists. Elements and relations themselves are a pure
-- projection over existing rows (Intent Brief items, understanding_revision
-- claims) recomputed on every read -- this table exists solely to record a
-- human's `confirmed` / `rejected` decision about ONE relation, because that
-- decision cannot be re-derived: it is the developer's judgement, not a
-- structural fact.
--
-- Append-only, exactly like `interview_intent_item` / #308's premise bundle:
-- a correction inserts a NEW row and sets the prior row's `superseded_by_id`;
-- nothing is ever UPDATEd or DELETEd. `source_digest` / `target_digest`
-- capture `purpose_chain.element_digest()` for both endpoints AT DECISION
-- TIME, so a later content change can be detected (`recheck_state = 'stale'`)
-- WITHOUT invalidating the decision row itself -- the audit fact "a human
-- confirmed this relation, given these exact endpoint contents, at this
-- time" must survive every later edit, exactly as docs/purpose-chain.md
-- §1.5 states: "決定は上書きされず、digest が動いても削除されない".
--
-- `relation_id` is the STABLE string id `purpose_chain.py` derives
-- (`f"{relation_kind}:{source_element_id}->{target_element_id}"`), never a
-- row id -- Purpose Chain elements/relations are recomputed on every read and
-- carry no row identity of their own to reference.
CREATE TABLE IF NOT EXISTS purpose_relation_decision (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    session_id                  INTEGER NOT NULL,
    relation_id                 TEXT NOT NULL,
    relation_kind                TEXT NOT NULL,
    decision                    TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected')),
    rationale                   TEXT NOT NULL DEFAULT '',
    source_element_id           TEXT NOT NULL,
    target_element_id           TEXT NOT NULL,
    source_digest                TEXT NOT NULL,
    target_digest                TEXT NOT NULL,
    understanding_revision_id   INTEGER,
    intent_revision_id          INTEGER,
    snapshot_id                  INTEGER,
    decision_method              TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    decided_by                   TEXT,
    superseded_by_id             INTEGER,
    created_at                   REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES purpose_relation_decision (id) ON DELETE SET NULL
);

-- The lookup every read performs: "the current (non-superseded) decision for
-- THIS relation, in THIS session". System-scoped so a foreign session_id can
-- never surface another System's decision.
CREATE INDEX IF NOT EXISTS idx_purpose_relation_decision_lookup
    ON purpose_relation_decision (system_id, session_id, relation_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_purpose_relation_decision_session
    ON purpose_relation_decision (session_id, id DESC);

-- ---------------------------------------------------------------------------
-- Purpose Needs responses (Issue #389, docs/purpose-chain.md §2.6).
--
-- The developer's answer/defer/investigate to ONE derived Purpose Chain need
-- (`app/purpose_needs.py`). Needs themselves are never persisted -- they are
-- a pure projection recomputed from `purpose_chain.derive_purpose_chain` on
-- every read, exactly like elements/relations. This table exists solely to
-- record the developer's RESPONSE, because that cannot be re-derived: it is
-- the developer's own action, not a structural fact.
--
-- Append-only, the same discipline as `purpose_relation_decision` /
-- `interview_intent_item`: a later response to the same `need_id` inserts a
-- NEW row and sets the prior current row's `superseded_by_id`; nothing is
-- ever UPDATEd or DELETEd. `target_digest` captures
-- `purpose_needs.target_digest_for(...)` for the need's target AT RESPONSE
-- TIME, so `defer` and `unknown`/`investigate` can be told apart from a
-- STALE `defer`/`unknown` whose target has since changed
-- (`purpose_needs.apply_response_state` re-derives the need fresh on every
-- read and compares digests -- a `defer` never silently expires by itself,
-- it stops matching once the target's content actually moves).
--
-- `response_kind='confirm'|'correct'` never writes a second revision-chain
-- implementation here: the row only LINKS to the audit row the EXISTING
-- Intent Brief confirm/correct/create endpoints or
-- `purpose_chain.record_relation_decision` already created
-- (`linked_intent_item_id` / `linked_relation_decision_id`).
-- `response_kind='unknown'|'investigate'` opens a Joint Understanding
-- session with `trigger='purpose_need'` and links it
-- (`linked_joint_session_id`) -- see `app/joint_premise.py`'s
-- `origin_kind='purpose_need'` branch, which reads THIS table by
-- `origin_id` to resolve and re-derive that session's premise.
--
-- `need_id` is the STABLE string id `purpose_needs.py` derives
-- (`f"{need_code}:{target_id}"`), never a row id -- needs carry no row
-- identity of their own to reference, exactly like Purpose Chain relations.
CREATE TABLE IF NOT EXISTS purpose_need_response (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    session_id                  INTEGER NOT NULL,
    need_id                     TEXT NOT NULL,
    need_code                   TEXT NOT NULL,
    response_kind               TEXT NOT NULL
                                    CHECK (response_kind IN
                                        ('confirm', 'correct', 'unknown', 'defer', 'investigate')),
    value_text                  TEXT NOT NULL DEFAULT '',
    target_kind                 TEXT NOT NULL CHECK (target_kind IN ('element', 'relation')),
    target_id                   TEXT NOT NULL,
    target_digest               TEXT NOT NULL,
    decision_method              TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    responded_by                 TEXT,
    linked_intent_item_id        INTEGER,
    linked_relation_decision_id  INTEGER,
    linked_joint_session_id      INTEGER,
    superseded_by_id             INTEGER,
    created_at                   REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
    FOREIGN KEY (linked_intent_item_id) REFERENCES interview_intent_item (id) ON DELETE SET NULL,
    FOREIGN KEY (linked_relation_decision_id) REFERENCES purpose_relation_decision (id) ON DELETE SET NULL,
    FOREIGN KEY (linked_joint_session_id) REFERENCES joint_understanding_session (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES purpose_need_response (id) ON DELETE SET NULL
);

-- The lookup every read performs: "the current (non-superseded) response for
-- THIS need, in THIS session". System-scoped so a foreign session_id can
-- never surface another System's response.
CREATE INDEX IF NOT EXISTS idx_purpose_need_response_lookup
    ON purpose_need_response (system_id, session_id, need_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_purpose_need_response_session
    ON purpose_need_response (session_id, id DESC);

-- ---------------------------------------------------------------------------
-- Purpose Verification: Experience Hypothesis / Outcome Criterion / Reuse
-- Hypothesis (Issue #391, docs/purpose-chain.md §4).
--
-- Three OPTIONAL concepts a developer may attach to one Purpose Chain
-- element or relation, by the same STABLE STRING identity `purpose_chain.py`
-- already uses (`target_kind` + `target_id`), never a row id -- elements and
-- relations are themselves recomputed on every read and carry no row
-- identity to reference (see the `purpose_relation_decision` comment above
-- for why). None of these three is required for every System (§4.1): a row
-- exists only when a developer explicitly created it, and creation is only
-- ever OFFERED alongside a currently-available `purpose_needs` need --
-- `source_need_id` / `source_need_code` record WHICH one, so "the Purpose
-- Frame is at L1" can never be the reason a row exists (there is no code
-- path that creates one without a real need to point at).
--
-- Unlike `purpose_relation_decision` / `purpose_need_response`, these rows
-- are NOT append-only revision chains: each concept is a single row whose
-- own lifecycle columns (`state` plus the confirmed_at/confirmed_by,
-- retired_at/retired_by, human_reported_*/runtime_observed_* pairs) are
-- updated in place by later manual actions. There is no digest-based
-- staleness re-check against the CURRENT Purpose Chain here -- that
-- machinery exists in #388 because a relation's `status` is recomputed from
-- scratch every read, whereas a verification concept's own lifecycle is the
-- thing being tracked, not a projection over other rows. `target_digest` is
-- still captured at creation time for audit (what the target looked like
-- when this was proposed), deliberately left as a non-goal for automatic
-- re-evaluation in this issue.
CREATE TABLE IF NOT EXISTS purpose_experience_hypothesis (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    session_id          INTEGER NOT NULL,
    target_kind         TEXT NOT NULL CHECK (target_kind IN ('element', 'relation')),
    target_id           TEXT NOT NULL,
    target_label        TEXT NOT NULL DEFAULT '',
    target_digest       TEXT NOT NULL,
    source_need_id      TEXT NOT NULL,
    source_need_code    TEXT NOT NULL,
    statement           TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'proposed'
                             CHECK (state IN ('proposed', 'confirmed', 'retired')),
    decision_method     TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    confirmed_by        TEXT,
    confirmed_at        REAL,
    retired_by          TEXT,
    retired_at          REAL,
    retirement_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_purpose_experience_hypothesis_session
    ON purpose_experience_hypothesis (system_id, session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_purpose_experience_hypothesis_target
    ON purpose_experience_hypothesis (system_id, session_id, target_kind, target_id);

-- Identical shape to `purpose_experience_hypothesis` -- see that table's
-- comment. A separate table (not a shared one with a `concept_kind`
-- discriminator column) because #4.1 keeps the two concepts distinct even
-- though their lifecycle happens to match; a shared table would tempt a
-- future change to merge their meaning too.
CREATE TABLE IF NOT EXISTS purpose_reuse_hypothesis (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    session_id          INTEGER NOT NULL,
    target_kind         TEXT NOT NULL CHECK (target_kind IN ('element', 'relation')),
    target_id           TEXT NOT NULL,
    target_label        TEXT NOT NULL DEFAULT '',
    target_digest       TEXT NOT NULL,
    source_need_id      TEXT NOT NULL,
    source_need_code    TEXT NOT NULL,
    statement           TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'proposed'
                             CHECK (state IN ('proposed', 'confirmed', 'retired')),
    decision_method     TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    confirmed_by        TEXT,
    confirmed_at        REAL,
    retired_by          TEXT,
    retired_at          REAL,
    retirement_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_purpose_reuse_hypothesis_session
    ON purpose_reuse_hypothesis (system_id, session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_purpose_reuse_hypothesis_target
    ON purpose_reuse_hypothesis (system_id, session_id, target_kind, target_id);

-- 成果証拠. `measure` / `baseline_value` / `target_value` / `observation_window`
-- are the four fields `purpose_chain._resolution_level` checks for L3
-- (docs/purpose-chain.md §4.4) -- all plain developer-authored text.
--
-- `experiment_id` / `candidate_version_id` are §4.3's explicit lineage
-- columns: intentionally NOT enforced by a FOREIGN KEY (a deleted
-- Experiment/CandidateVersion must not silently delete this row's audit
-- trail; `purpose_verification.py` resolves "does this id still exist" at
-- READ time into `lineage_state`, so a missing target renders as `unresolved`
-- / 「関連不明」 instead of erasing the fact that a lineage claim was made).
--
-- `human_reported_evidence`/`human_reported_verdict` and
-- `runtime_observation_text`/`runtime_observation_verdict` are two SEPARATE
-- column pairs (§4.2: "human-reported evidence and runtime observation are
-- separate columns, never merged into one result") -- a result write
-- (`purpose_verification.record_outcome_result`) always targets exactly one
-- pair, named by its own `source` argument, and always carries the
-- developer's own verdict alongside the evidence text: this module never
-- infers `observed` vs `contradicted` from the evidence text itself
-- (§4.2's "runtime trace だけで利用者の成功を推測しない", applied one layer
-- up to ANY evidence, not just a raw trace).
CREATE TABLE IF NOT EXISTS purpose_outcome_criterion (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    session_id                  INTEGER NOT NULL,
    target_kind                 TEXT NOT NULL CHECK (target_kind IN ('element', 'relation')),
    target_id                   TEXT NOT NULL,
    target_label                 TEXT NOT NULL DEFAULT '',
    target_digest               TEXT NOT NULL,
    source_need_id              TEXT NOT NULL,
    source_need_code            TEXT NOT NULL,
    measure                     TEXT NOT NULL DEFAULT '',
    baseline_value               TEXT NOT NULL DEFAULT '',
    target_value                 TEXT NOT NULL DEFAULT '',
    observation_window           TEXT NOT NULL DEFAULT '',
    state                       TEXT NOT NULL DEFAULT 'proposed'
                                     CHECK (state IN
                                         ('proposed', 'confirmed', 'observed', 'contradicted',
                                          'not_observed', 'not_computed')),
    experiment_id                INTEGER,
    candidate_version_id         INTEGER,
    human_reported_evidence      TEXT,
    human_reported_verdict       TEXT CHECK (human_reported_verdict IN ('supports', 'contradicts')),
    human_reported_at            REAL,
    human_reported_by            TEXT,
    human_reported_state         TEXT CHECK (human_reported_state IN
                                        ('observed', 'contradicted', 'not_observed', 'not_computed')),
    human_reported_is_synthetic  INTEGER NOT NULL DEFAULT 0,
    runtime_observation_text     TEXT,
    runtime_observation_verdict  TEXT CHECK (runtime_observation_verdict IN ('supports', 'contradicts')),
    runtime_observed_at          REAL,
    runtime_observed_by          TEXT,
    runtime_observation_state    TEXT CHECK (runtime_observation_state IN
                                        ('observed', 'contradicted', 'not_observed', 'not_computed')),
    runtime_observation_is_synthetic INTEGER NOT NULL DEFAULT 0,
    is_synthetic                 INTEGER NOT NULL DEFAULT 0,
    decision_method               TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    created_by                    TEXT,
    created_at                    REAL NOT NULL,
    confirmed_by                  TEXT,
    confirmed_at                  REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_purpose_outcome_criterion_session
    ON purpose_outcome_criterion (system_id, session_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_purpose_outcome_criterion_target
    ON purpose_outcome_criterion (system_id, session_id, target_kind, target_id);

-- ---------------------------------------------------------------------------
-- Evolution Node Fabric (Epic #394 Phase 1, Issue #396). See app/evolution_node.py
-- for the pure finite-transition evaluator and the persistence/projection
-- layer built on these five tables, and the "Evolution Node" section of
-- docs/evolutionary-pipeline.md for the full design.
--
-- An Evolution Node is a NEW canonical entity, deliberately NOT a version-up
-- of the Probe Cell (cell_definitions/cell_bindings, Issue #297/#299): the
-- Cell owns the EXECUTION role (Role Card, orchestration, quality sampling);
-- the Node owns the UNIT OF PROCESSING THAT EVOLVES -- its business I/O
-- contract, its implementation modality, its maturity, its establishment/
-- reopen criteria, and its rollback pin. A Node LINKS to a Cell Binding (and
-- to a Component, a Probe Point, ...) through evolution_node_link below; it
-- never duplicates or supersedes cell_bindings/cell_definitions rows.
--
-- evolution_node: one row per Node. Identity is (system_id, node_key) --
-- node_key is a DEVELOPER-SUPPLIED stable slug, deliberately never derived
-- from component_id: a Node is designed before instrumentation exists
-- (Phase 2 of this Epic), may span several Components over its life, and
-- must survive a Component rename without losing its own identity. The four
-- *_implementation_id / current_version_id pointers are denormalized
-- "current state" columns kept in sync by app/evolution_node.py's
-- persistence functions inside the same transaction as the append-only row
-- they point at (create_node/add_version/add_implementation/
-- pin_stable_implementation) -- they are never written by a bare UPDATE
-- outside that module, which is what keeps them from drifting out of sync
-- with the append-only history in evolution_node_version/_implementation.
-- monitoring_contract_ref is nullable and unused until Issue #400 (this
-- Epic's Phase 5) wires a real monitoring contract; a NULL here is "no
-- contract wired yet", never "monitoring failed".
CREATE TABLE IF NOT EXISTS evolution_node (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    node_key                    TEXT NOT NULL,
    display_name                TEXT NOT NULL DEFAULT '',
    maturity                    TEXT NOT NULL DEFAULT 'exploring'
                                    CHECK (maturity IN
                                        ('exploring', 'validating', 'established',
                                         'monitoring', 'reopened', 'suspended')),
    current_version_id          INTEGER,
    current_implementation_id   INTEGER,
    stable_implementation_id    INTEGER,
    rollback_implementation_id  INTEGER,
    monitoring_contract_ref     TEXT,
    schema_version               TEXT NOT NULL DEFAULT 'evolution-node-v1',
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_version_id) REFERENCES evolution_node_version (id) ON DELETE SET NULL,
    FOREIGN KEY (current_implementation_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    FOREIGN KEY (stable_implementation_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    FOREIGN KEY (rollback_implementation_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    UNIQUE (system_id, node_key)
);

CREATE INDEX IF NOT EXISTS idx_evolution_node_system
    ON evolution_node (system_id, id DESC);

-- evolution_node_version: the Node's CONTRACT -- its business I/O shape, its
-- side-effect/trust classification, and the criteria that decide when it may
-- move to 'established'/'monitoring' or must move to 'reopened'. Append-only,
-- the same discipline as cell_bindings: a correction inserts a NEW row with
-- version_number = max+1 and sets the prior current row's superseded_by_id;
-- app/evolution_node.py never UPDATEs mission/input_contract/etc in place,
-- because a version is what an establishment/reopen decision was made
-- AGAINST and that history must survive later edits.
--
-- mission/scope/out_of_scope are free-text (not JSON) -- they are read as
-- prose, unlike input_contract/output_contract/establishment_criteria/
-- reopen_criteria/evaluation_policy_refs, which ARE TEXT JSON because each is
-- a structured list/object the pure evaluator and the projection need to
-- walk programmatically. decision_method records who AUTHORED this contract
-- version (deterministic/reasoning_llm/manual are all legitimate HERE,
-- unlike a maturity transition -- see evaluate_transition's
-- llm_state_not_allowed check in app/evolution_node.py, which is what keeps
-- an LLM-authored contract DRAFT from ever becoming a maturity decision by
-- itself).
CREATE TABLE IF NOT EXISTS evolution_node_version (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id                     INTEGER NOT NULL,
    system_id                   INTEGER NOT NULL,
    version_number              INTEGER NOT NULL,
    mission                     TEXT NOT NULL DEFAULT '',
    scope                       TEXT NOT NULL DEFAULT '',
    out_of_scope                TEXT NOT NULL DEFAULT '',
    input_contract               TEXT NOT NULL DEFAULT '{}',
    output_contract              TEXT NOT NULL DEFAULT '{}',
    side_effect_class            TEXT NOT NULL
                                    CHECK (side_effect_class IN
                                        ('pure', 'read_only', 'local_write',
                                         'external_write', 'irreversible')),
    trust_boundary                TEXT NOT NULL
                                    CHECK (trust_boundary IN
                                        ('internal', 'external_input',
                                         'external_output', 'third_party')),
    establishment_criteria       TEXT NOT NULL DEFAULT '[]',
    reopen_criteria               TEXT NOT NULL DEFAULT '[]',
    evaluation_policy_refs        TEXT NOT NULL DEFAULT '[]',
    decision_method               TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method IN
                                        ('deterministic', 'reasoning_llm', 'manual')),
    created_by                    TEXT,
    created_at                    REAL NOT NULL,
    superseded_by_id              INTEGER,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES evolution_node_version (id) ON DELETE SET NULL,
    UNIQUE (node_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_evolution_node_version_node
    ON evolution_node_version (node_id, version_number DESC);

-- evolution_node_implementation: HOW the Node currently keeps its contract's
-- promise. Append-only, same discipline as evolution_node_version --
-- swapping implementations (e.g. moving from a reasoning_llm draft to a
-- deterministic_code rewrite) inserts a new row rather than mutating the
-- old one, because the establishment/monitoring history needs to say which
-- exact implementation the evidence was gathered against.
--
-- node_version_id records which CONTRACT this implementation claims to
-- satisfy; app/evolution_node.py's stale_implementation transition check
-- compares it against the Node's current_version_id, so a contract edit
-- that leaves an old implementation behind is caught structurally rather
-- than silently let through. snapshot_id is nullable and ON DELETE SET NULL
-- (not RESTRICT): a deleted repository snapshot must not block deleting
-- unrelated snapshot history, and losing the snapshot pointer here still
-- leaves commit_sha as the durable provenance fact (Principle 5).
--
-- config_json / provenance_json are the ONLY place a real provider/model
-- name may appear for this implementation (Issue #298's Role Card
-- model-alias rule, applied here): no other column on this table may
-- participate in identity or a CHECK based on a literal provider/model
-- name, so switching providers is a config-blob edit, never a schema or
-- constraint change.
CREATE TABLE IF NOT EXISTS evolution_node_implementation (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id               INTEGER NOT NULL,
    system_id             INTEGER NOT NULL,
    implementation_number INTEGER NOT NULL,
    node_version_id       INTEGER NOT NULL,
    modality              TEXT NOT NULL
                              CHECK (modality IN
                                  ('reasoning_llm', 'lm_program', 'retrieval', 'router',
                                   'small_model', 'rule', 'deterministic_code', 'workflow',
                                   'manual', 'hybrid')),
    config_json           TEXT NOT NULL DEFAULT '{}',
    snapshot_id            INTEGER,
    commit_sha             TEXT,
    environment_ref         TEXT,
    provenance_json         TEXT NOT NULL DEFAULT '{}',
    created_by              TEXT,
    decision_method          TEXT NOT NULL DEFAULT 'manual'
                                CHECK (decision_method IN
                                    ('deterministic', 'reasoning_llm', 'manual')),
    created_at               REAL NOT NULL,
    superseded_by_id          INTEGER,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_version_id) REFERENCES evolution_node_version (id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    UNIQUE (node_id, implementation_number)
);

CREATE INDEX IF NOT EXISTS idx_evolution_node_implementation_node
    ON evolution_node_implementation (node_id, implementation_number DESC);

-- evolution_node_link: append-only links from a Node to an EXISTING asset
-- (a Component, a Probe Point, a Cell Binding, a Capability, a Flow, a
-- Purpose Chain element, a Feature). target_ref is always the STABLE STRING
-- id of the target (a component_id, a stable Capability/Purpose-element id,
-- ...) so a link keeps meaning even for a target whose row identity is
-- itself recomputed on every read (the same discipline
-- purpose_relation_decision uses above); target_row_id is the OPTIONAL row
-- id for a target that does have one (a probe_points.id, a cell_bindings.id)
-- and exists purely as a join shortcut -- app/evolution_node.py never trusts
-- target_row_id alone without also checking target_ref/link_kind, so a
-- caller cannot point a link at another System's row by id alone. Multiple
-- concurrent links of the same kind are legitimate (a Node may span more
-- than one Component), so a new link never automatically supersedes an
-- older one of the same kind -- superseded_by_id exists for a future
-- explicit "this link was corrected" operation, deliberately unused by
-- Phase 1's add_link.
CREATE TABLE IF NOT EXISTS evolution_node_link (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id           INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    link_kind         TEXT NOT NULL
                          CHECK (link_kind IN
                              ('component', 'probe_point', 'cell_binding', 'capability',
                               'flow', 'purpose_element', 'feature')),
    target_ref        TEXT NOT NULL,
    target_row_id     INTEGER,
    note              TEXT NOT NULL DEFAULT '',
    decision_method    TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN
                              ('deterministic', 'reasoning_llm', 'manual')),
    created_by         TEXT,
    created_at         REAL NOT NULL,
    superseded_by_id   INTEGER,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES evolution_node_link (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_evolution_node_link_lookup
    ON evolution_node_link (node_id, link_kind, id DESC);

-- evolution_node_event: append-only lineage of everything that happened to a
-- Node. This is what makes a drifted STORED evolution_node.maturity value
-- detectable: app/evolution_node.py's fold_events() replays every
-- event_kind='transition' row in id order and must reproduce the stored
-- maturity, exactly as ADR-4 (docs/evolutionary-pipeline.md) requires -- a
-- table that only stored the current maturity would have no way to notice
-- a bad UPDATE outside the module ever happened.
--
-- from_state/to_state are only ever non-NULL together on a 'transition'
-- event; every other event_kind uses the matching from_*_id/to_*_id pair
-- instead (version_created -> from_version_id/to_version_id,
-- implementation_created -> from_implementation_id/to_implementation_id,
-- stable_pinned/rollback_pinned -> from_implementation_id/
-- to_implementation_id), so a reader can tell what kind of pointer moved
-- without inspecting reason_code.
--
-- idempotency_key + the partial unique index below are what let
-- apply_transition() be safely retried: a client that resends the same
-- transition request after a timeout must get back the SAME event row, not
-- a second one that silently double-applies a state change the caller
-- already believes happened. An empty idempotency_key ('') means "the
-- caller did not ask for idempotency" and is deliberately excluded from the
-- uniqueness constraint (via the partial index's WHERE clause), since many
-- legitimate events share that empty value.
--
-- evidence_json holds ONLY REFS (never raw evidence content) -- the same
-- discipline cell_improvements.canary_evidence_json already uses -- so an
-- audit reader always resolves the evidence against its owning system of
-- record instead of trusting a copy that could drift from it.
CREATE TABLE IF NOT EXISTS evolution_node_event (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id                   INTEGER NOT NULL,
    system_id                 INTEGER NOT NULL,
    event_kind                TEXT NOT NULL
                                  CHECK (event_kind IN
                                      ('transition', 'version_created', 'implementation_created',
                                       'link_created', 'stable_pinned', 'rollback_pinned')),
    from_state                TEXT
                                  CHECK (from_state IS NULL OR from_state IN
                                      ('exploring', 'validating', 'established',
                                       'monitoring', 'reopened', 'suspended')),
    to_state                  TEXT
                                  CHECK (to_state IS NULL OR to_state IN
                                      ('exploring', 'validating', 'established',
                                       'monitoring', 'reopened', 'suspended')),
    from_version_id           INTEGER,
    to_version_id             INTEGER,
    from_implementation_id    INTEGER,
    to_implementation_id      INTEGER,
    actor                     TEXT,
    actor_kind                TEXT NOT NULL DEFAULT 'developer'
                                  CHECK (actor_kind IN ('developer', 'system')),
    decision_method            TEXT NOT NULL DEFAULT 'manual'
                                  CHECK (decision_method IN
                                      ('deterministic', 'reasoning_llm', 'manual')),
    reason_code                TEXT NOT NULL DEFAULT '',
    reason                     TEXT NOT NULL DEFAULT '',
    evidence_json               TEXT NOT NULL DEFAULT '[]',
    idempotency_key             TEXT NOT NULL DEFAULT '',
    created_at                  REAL NOT NULL,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (from_version_id) REFERENCES evolution_node_version (id) ON DELETE SET NULL,
    FOREIGN KEY (to_version_id) REFERENCES evolution_node_version (id) ON DELETE SET NULL,
    FOREIGN KEY (from_implementation_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    FOREIGN KEY (to_implementation_id) REFERENCES evolution_node_implementation (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_evolution_node_event_node
    ON evolution_node_event (node_id, id DESC);

-- A retried apply_transition() call must resolve to the SAME event, never a
-- second row that double-applies the transition -- see the table comment
-- above. The WHERE clause is what keeps ordinary events (idempotency_key='')
-- from colliding with each other under this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS ux_evolution_node_event_idempotency
    ON evolution_node_event (node_id, idempotency_key) WHERE idempotency_key != '';

-- ---------------------------------------------------------------------------
-- Design Studio (Epic #394 Phase 2, Issue #397, app/node_design.py)
--
-- Phase 2 turns Vision -> Outcome -> Capability -> Flow into testable Node
-- hypotheses. It creates NO second Vision/Purpose model: the Purpose Frame
-- stays a projection recomputed by app/purpose_chain.py from existing rows,
-- and the connection between it and a Node is an ordinary
-- evolution_node_link (link_kind='purpose_element'/'capability'/'flow')
-- whose decision_method already distinguishes an AI PROPOSAL
-- ('reasoning_llm') from a developer's CONFIRMATION ('manual'). No new
-- column is needed for that distinction, and adding one would create a
-- second place for the two to disagree.
--
-- What Phase 2 does have to persist is the three things that cannot be
-- re-derived: a decomposition proposal and the developer's decision on each
-- candidate, the three evaluation contracts, and the handoff bundle.
-- ---------------------------------------------------------------------------

-- node_decomposition_proposal: ONE reasoning run that proposed one or more
-- ways to cut a scope into Nodes. Persisting the run (not just its output)
-- is what makes the proposal auditable under Principle 7 -- intelligence_run_id
-- resolves to the provider/model/prompt version/schema version that produced
-- it. A failed run is recorded with status='failed' and produces NO
-- candidates: a heuristic decomposition must never be saved as if a
-- reasoning model had produced it (Principle 6).
CREATE TABLE IF NOT EXISTS node_decomposition_proposal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    session_id          INTEGER,
    scope_summary       TEXT NOT NULL DEFAULT '',
    capability_ref      TEXT NOT NULL DEFAULT '',
    flow_ref            TEXT NOT NULL DEFAULT '',
    snapshot_id         INTEGER,
    intelligence_run_id INTEGER,
    status              TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'failed')),
    error_details       TEXT NOT NULL DEFAULT '',
    is_mock             INTEGER NOT NULL DEFAULT 0,
    decision_method     TEXT NOT NULL DEFAULT 'reasoning_llm'
                            CHECK (decision_method = 'reasoning_llm'),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE SET NULL,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_node_decomposition_proposal_system
    ON node_decomposition_proposal (system_id, id DESC);

-- node_decomposition_candidate: ONE way of cutting the scope, as a set of
-- proposed nodes carried in nodes_json. #397 requires several cuts to be
-- COMPARED, so a candidate is a whole decomposition, not a single node --
-- comparing individual nodes across cuts would lose the only thing that
-- distinguishes the cuts from each other.
--
-- `decision` is the developer's judgement and is the ONLY way a candidate
-- becomes real. `adopted_node_ids_json` records which evolution_node rows
-- the adoption created, so the audit trail runs proposal -> candidate ->
-- Node without a System-wide guess about which nodes came from where.
-- decision_method is CHECKed to 'manual': an LLM proposes cuts, a human
-- adopts one (Principle 7, and #397's "Node 作成・relation 確認は人間操作").
-- open_questions_json is kept as its own column rather than folded into the
-- prose: #397 requires 未確定事項 to be visible, and a paragraph that
-- mentions uncertainty reads as a completed answer.
CREATE TABLE IF NOT EXISTS node_decomposition_candidate (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id           INTEGER NOT NULL,
    system_id             INTEGER NOT NULL,
    candidate_key         TEXT NOT NULL,
    summary               TEXT NOT NULL DEFAULT '',
    rationale             TEXT NOT NULL DEFAULT '',
    nodes_json            TEXT NOT NULL DEFAULT '[]',
    open_questions_json   TEXT NOT NULL DEFAULT '[]',
    decision              TEXT NOT NULL DEFAULT 'pending'
                              CHECK (decision IN ('pending', 'adopted', 'held', 'rejected')),
    decision_note         TEXT NOT NULL DEFAULT '',
    decision_method       TEXT NOT NULL DEFAULT 'manual'
                              CHECK (decision_method = 'manual'),
    decided_by            TEXT,
    decided_at            REAL,
    adopted_node_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at            REAL NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES node_decomposition_proposal (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (proposal_id, candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_node_decomposition_candidate_proposal
    ON node_decomposition_candidate (proposal_id, id);

-- evolution_evaluation_policy: the three evaluation contracts of ADR-7, kept
-- in one table with a finite `level` discriminator rather than three tables,
-- because they share every structural column and differ only in what they
-- judge. What must NOT be shared is their MEANING, so:
--
-- * there is no score, weight, or total column anywhere in this table. A
--   single weighted total is what ADR-7 forbids: a latency win must not be
--   able to pay for a safety regression, and the only way to guarantee that
--   is to have nowhere to write the combined number.
-- * `criteria_json` (what must be REACHED to establish) and `floors_json`
--   (what must not be BROKEN) are separate columns, not one list with a
--   flag. They are consumed at different moments -- a criterion is read by
--   the Phase 4 establishment gate, a floor is read there AND by Phase 5
--   monitoring -- and merging them makes "we met the bar" and "we did not
--   regress" indistinguishable in storage.
--
-- Append-only per (system_id, level, policy_key): a correction inserts a new
-- version_number and supersedes the prior row, because a policy is what an
-- establishment decision was made against and must survive later edits.
CREATE TABLE IF NOT EXISTS evolution_evaluation_policy (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    policy_key       TEXT NOT NULL,
    level            TEXT NOT NULL
                         CHECK (level IN ('node', 'flow_capability', 'ux_outcome')),
    version_number   INTEGER NOT NULL DEFAULT 1,
    title            TEXT NOT NULL DEFAULT '',
    subject_ref      TEXT NOT NULL DEFAULT '',
    criteria_json    TEXT NOT NULL DEFAULT '[]',
    floors_json      TEXT NOT NULL DEFAULT '[]',
    unmeasured_json  TEXT NOT NULL DEFAULT '[]',
    decision_method  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (decision_method IN ('deterministic', 'reasoning_llm', 'manual')),
    created_by       TEXT,
    created_at       REAL NOT NULL,
    superseded_by_id INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES evolution_evaluation_policy (id) ON DELETE SET NULL,
    UNIQUE (system_id, policy_key, version_number)
);

CREATE INDEX IF NOT EXISTS idx_evolution_evaluation_policy_lookup
    ON evolution_evaluation_policy (system_id, level, id DESC);

-- evolution_design_handoff: the bundle Phase 2 hands to Phase 3, assembled
-- deterministically from rows that already exist. It stores REFERENCES, never
-- copies: a copied criterion would keep reading as current after the policy
-- it came from was superseded, which is the staleness class #337/#369 both
-- had to fix elsewhere. `assembly_state` distinguishes a handoff that is
-- complete from one assembled while something it points at was missing --
-- 'incomplete' is a real, readable state, not an error to be smoothed over.
CREATE TABLE IF NOT EXISTS evolution_design_handoff (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id              INTEGER NOT NULL,
    session_id             INTEGER,
    node_ids_json          TEXT NOT NULL DEFAULT '[]',
    evaluation_policy_ids_json TEXT NOT NULL DEFAULT '[]',
    dataset_refs_json      TEXT NOT NULL DEFAULT '[]',
    probe_plan_id          INTEGER,
    establishment_criteria_draft_json TEXT NOT NULL DEFAULT '[]',
    reopen_criteria_draft_json TEXT NOT NULL DEFAULT '[]',
    exploration_brief      TEXT NOT NULL DEFAULT '',
    assembly_state         TEXT NOT NULL DEFAULT 'complete'
                               CHECK (assembly_state IN ('complete', 'incomplete')),
    missing_refs_json      TEXT NOT NULL DEFAULT '[]',
    decision_method        TEXT NOT NULL DEFAULT 'manual'
                               CHECK (decision_method = 'manual'),
    created_by             TEXT,
    created_at             REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE SET NULL,
    FOREIGN KEY (probe_plan_id) REFERENCES probe_plans (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_evolution_design_handoff_system
    ON evolution_design_handoff (system_id, id DESC);

-- ---------------------------------------------------------------------------
-- Exploration Workbench (Epic #394 Phase 3, Issue #398,
-- app/exploration_workbench.py)
--
-- Phase 3's question is NOT "which LLM candidate is best" -- it is "for the
-- SAME Node contract and the SAME evaluation refs, how do an LLM
-- implementation, a rule implementation and a deterministic-code
-- implementation compare". That is only expressible because ADR-3 versioned
-- the contract separately from the implementation.
--
-- This is a RECORD of comparisons, not a second execution engine. Replay
-- (#242-#246), Experiments (#26) and the offline shadow sandbox stay the
-- only things that run code; an exploration variant REFERENCES the run that
-- produced its numbers (`replay_run_id` / `replay_variant_id` /
-- `experiment_id`) rather than re-implementing execution. That is why there
-- is no source/patch/command column anywhere here: accepting free-form code
-- through this API would bypass the pinned-snapshot, pinned-command,
-- network-off sandbox those features already enforce (Principle 8).
-- ---------------------------------------------------------------------------

-- exploration_run: one comparison. Everything that must be held constant
-- across the variants lives HERE, not on the variants, so it is structurally
-- impossible for two variants in one run to have been measured against
-- different datasets or different evaluation contracts -- which would make
-- their numbers incomparable while still looking like a comparison.
--
-- `baseline_variant_id` is nullable only between INSERT and the baseline's
-- own insert; a completed run without one is `invalid`, because a difference
-- measured against nothing is not a difference.
CREATE TABLE IF NOT EXISTS exploration_run (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                INTEGER NOT NULL,
    node_id                  INTEGER NOT NULL,
    node_version_id          INTEGER NOT NULL,
    handoff_id               INTEGER,
    objective                TEXT NOT NULL DEFAULT '',
    dataset_kind             TEXT NOT NULL DEFAULT 'replay_set'
                                 CHECK (dataset_kind IN
                                     ('replay_set', 'golden_set', 'edge_cases', 'mixed')),
    dataset_ref              TEXT NOT NULL DEFAULT '',
    snapshot_id              INTEGER,
    commit_sha               TEXT NOT NULL DEFAULT '',
    environment_ref          TEXT NOT NULL DEFAULT '',
    evaluation_policy_ids_json TEXT NOT NULL DEFAULT '[]',
    baseline_variant_id      INTEGER,
    status                   TEXT NOT NULL DEFAULT 'open'
                                 CHECK (status IN ('open', 'completed', 'abandoned')),
    conclusion_note          TEXT NOT NULL DEFAULT '',
    created_by               TEXT,
    created_at               REAL NOT NULL,
    completed_at             REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (node_version_id) REFERENCES evolution_node_version (id) ON DELETE CASCADE,
    FOREIGN KEY (handoff_id) REFERENCES evolution_design_handoff (id) ON DELETE SET NULL,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_exploration_run_node
    ON exploration_run (system_id, node_id, id DESC);

-- exploration_variant: one implementation measured in that run.
--
-- `modality` is the whole point: an exploration is comparable across
-- modalities only because the contract (evolution_node_version) is the same
-- row for every variant in the run. Provider/model names live in
-- `config_json` / `provenance_json`, never in a column that participates in
-- identity (#298's model-alias rule).
--
-- `applicability_envelope_json` records the inputs a variant CLAIMS to
-- handle. It exists so a win can never be generalised past what was
-- measured -- #399's establishment gate reads it, and "it worked on the
-- cases it was built for" is otherwise indistinguishable from "it worked".
--
-- `execution_ref_kind` / `execution_ref_id` point at the run that actually
-- executed this variant. NULL means the variant was registered but never
-- executed, which is a real state (`not_executed`) and NOT a loss.
CREATE TABLE IF NOT EXISTS exploration_variant (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                      INTEGER NOT NULL,
    system_id                   INTEGER NOT NULL,
    variant_key                 TEXT NOT NULL,
    label                       TEXT NOT NULL DEFAULT '',
    is_baseline                 INTEGER NOT NULL DEFAULT 0,
    modality                    TEXT NOT NULL
                                    CHECK (modality IN
                                        ('reasoning_llm', 'lm_program', 'retrieval', 'router',
                                         'small_model', 'rule', 'deterministic_code',
                                         'workflow', 'manual', 'hybrid')),
    implementation_id           INTEGER,
    config_json                 TEXT NOT NULL DEFAULT '{}',
    provenance_json             TEXT NOT NULL DEFAULT '{}',
    generator                   TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (generator IN ('manual', 'reasoning_llm', 'existing_implementation')),
    applicability_envelope_json TEXT NOT NULL DEFAULT '{}',
    execution_ref_kind          TEXT
                                    CHECK (execution_ref_kind IS NULL OR execution_ref_kind IN
                                        ('replay_run', 'replay_variant', 'experiment')),
    execution_ref_id            INTEGER,
    execution_state             TEXT NOT NULL DEFAULT 'not_executed'
                                    CHECK (execution_state IN
                                        ('not_executed', 'executed', 'not_executable', 'unsupported')),
    execution_note              TEXT NOT NULL DEFAULT '',
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES exploration_run (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (implementation_id)
        REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    UNIQUE (run_id, variant_key)
);

CREATE INDEX IF NOT EXISTS idx_exploration_variant_run
    ON exploration_variant (run_id, id);

-- exploration_measurement: ONE dimension of ONE variant. One row per
-- dimension, deliberately -- not a metrics blob and not a score column.
--
-- #398 forbids composing quality / latency / cost / safety into a single
-- number, and the reliable enforcement is structural: each dimension is its
-- own row with its own coverage, and there is nowhere to write a total. A
-- consumer that wants a ranking has to state which dimension it is ranking
-- by.
--
-- `value_state` separates the four things a missing number can mean, which a
-- NULL alone cannot: `measured` (a real reading), `not_applicable` (this
-- dimension does not apply to this modality -- a `manual` variant has no
-- token cost), `not_measured` (nothing measured it yet) and `unsupported`
-- (the harness cannot measure it here). Rolling these into "0" or "-" is the
-- #366 one-word-two-facts defect applied to a metric.
--
-- `covered_case_count` / `total_case_count` are per dimension because
-- coverage genuinely differs between them -- a latency reading may cover
-- every case while a quality judgement covers only the labelled ones, and
-- comparing two variants at different coverage without saying so is the
-- failure #398's "coverage 差を敗北や成功へ丸めない" names.
CREATE TABLE IF NOT EXISTS exploration_measurement (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id         INTEGER NOT NULL,
    run_id             INTEGER NOT NULL,
    system_id          INTEGER NOT NULL,
    dimension          TEXT NOT NULL
                           CHECK (dimension IN
                               ('output_quality', 'error_rate', 'latency', 'cost',
                                'resource', 'safety', 'coverage')),
    metric_name        TEXT NOT NULL DEFAULT '',
    value_state        TEXT NOT NULL DEFAULT 'measured'
                           CHECK (value_state IN
                               ('measured', 'not_applicable', 'not_measured', 'unsupported')),
    numeric_value      REAL,
    unit               TEXT NOT NULL DEFAULT '',
    covered_case_count INTEGER,
    total_case_count   INTEGER,
    -- Deterministic facts and a reasoning model's interpretation are kept
    -- apart (CLAUDE.md: "Keep raw deterministic facts separate from LLM
    -- interpretations in storage"). A judge model's opinion is never the
    -- adoption basis on its own (#398).
    source             TEXT NOT NULL DEFAULT 'deterministic'
                           CHECK (source IN ('deterministic', 'reasoning_llm', 'manual')),
    note               TEXT NOT NULL DEFAULT '',
    created_at         REAL NOT NULL,
    FOREIGN KEY (variant_id) REFERENCES exploration_variant (id) ON DELETE CASCADE,
    FOREIGN KEY (run_id) REFERENCES exploration_run (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (variant_id, dimension, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_exploration_measurement_variant
    ON exploration_measurement (variant_id, dimension);

-- ---------------------------------------------------------------------------
-- Stabilization Evidence Package (Epic #394 Phase 4, Issue #399,
-- app/stabilization.py)
--
-- The record of WHY a Node was judged stable enough to establish. Fixation is
-- explicitly NOT "we removed the LLM": it is "the conditions under which this
-- processing works are now understood well enough to pin a reproducible,
-- rollback-able implementation" -- an LLM implementation can be established
-- exactly as legitimately as a rule one.
--
-- The package stores REFERENCES to evidence that already exists (exploration
-- runs, replay runs, experiments, evaluation policies), never copies of their
-- numbers. A copied number keeps reading as current after the run it came
-- from is superseded or its dataset changes, which is the staleness class
-- #337/#369 both had to fix elsewhere. Currency is therefore evaluated at
-- GATE time, not at build time.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stabilization_package (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                 INTEGER NOT NULL,
    node_id                   INTEGER NOT NULL,
    node_version_id           INTEGER NOT NULL,
    -- The implementation this package argues should become the stable pin.
    candidate_implementation_id INTEGER NOT NULL,
    -- The implementation it is argued against. NULL only for a Node that has
    -- never had a stable pin; the gate treats that as a first establishment
    -- and says so, rather than silently comparing against nothing.
    baseline_implementation_id INTEGER,
    exploration_run_id        INTEGER,
    -- What the package CLAIMS the candidate handles. The gate refuses an
    -- empty envelope: "it worked on the cases it was built for" and "it
    -- worked" are different claims, and only the first is ever demonstrated.
    applicability_envelope_json TEXT NOT NULL DEFAULT '{}',
    known_limitations_json    TEXT NOT NULL DEFAULT '[]',
    residual_risks_json       TEXT NOT NULL DEFAULT '[]',
    -- The package's own declaration of how much evidence it considers
    -- sufficient. Stored per package rather than as a global constant
    -- because #399 forbids a single fixed threshold across all domains --
    -- but it is declared BEFORE the gate runs, so it cannot be lowered to
    -- fit the result that came back.
    required_case_count       INTEGER NOT NULL DEFAULT 0,
    stability_window_seconds  REAL NOT NULL DEFAULT 0,
    observed_case_count       INTEGER,
    observed_window_seconds   REAL,
    -- An unmeasured Outcome is recorded WITH the reason it could not be
    -- measured. The gate accepts that; what it refuses is silence (#391's
    -- rule: never infer an Outcome, never omit the fact that it is unknown).
    outcome_unmeasured_reason TEXT NOT NULL DEFAULT '',
    rollback_implementation_id INTEGER,
    rollback_plan             TEXT NOT NULL DEFAULT '',
    status                    TEXT NOT NULL DEFAULT 'draft'
                                  CHECK (status IN
                                      ('draft', 'under_review', 'approved',
                                       'rejected', 'superseded')),
    -- The PARENT's review, which is not the approval (#304: parent approval
    -- and human approval are separate records that must not be conflated).
    -- NULL disposition means "no parent has reviewed this yet" -- never "the
    -- parent had nothing to say". Written from the authenticated principal,
    -- never from a request body, and append-only: a recorded disposition is
    -- not overwritten, a changed mind supersedes the package.
    parent_reviewed_by        TEXT,
    parent_reviewed_at        REAL,
    parent_review_disposition TEXT
                                  CHECK (parent_review_disposition IS NULL OR
                                         parent_review_disposition IN
                                             ('endorsed', 'declined')),
    parent_review_note        TEXT,
    -- Approval is a person, always. `approved_by` is written from the
    -- authenticated principal, never from a request body -- the #337
    -- provenance rule. `approve_package` additionally refuses when this is
    -- the same person as `parent_reviewed_by`: one person holding both roles
    -- is the conflation the separation exists to prevent.
    approved_by               TEXT,
    approved_at               REAL,
    decision_note             TEXT NOT NULL DEFAULT '',
    decision_method           TEXT NOT NULL DEFAULT 'manual'
                                  CHECK (decision_method = 'manual'),
    superseded_by_id          INTEGER,
    created_by                TEXT,
    created_at                REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (node_version_id) REFERENCES evolution_node_version (id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_implementation_id)
        REFERENCES evolution_node_implementation (id) ON DELETE CASCADE,
    FOREIGN KEY (baseline_implementation_id)
        REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    FOREIGN KEY (rollback_implementation_id)
        REFERENCES evolution_node_implementation (id) ON DELETE SET NULL,
    FOREIGN KEY (exploration_run_id) REFERENCES exploration_run (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES stabilization_package (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stabilization_package_node
    ON stabilization_package (system_id, node_id, id DESC);

-- stabilization_evidence: one referenced result, with its own level and
-- currency. `evidence_level` mirrors ADR-7's three contracts so Node,
-- Flow/Capability and UX/Outcome evidence can never be counted as
-- interchangeable -- a Node-level win is not evidence that the Flow it sits
-- in improved.
--
-- `verdict` is the deterministic reading of that evidence, and `unmeasured`
-- / `not_applicable` are real values rather than an absence: the gate needs
-- to distinguish "the floor held" from "nobody measured the floor", and only
-- the first may establish.
--
-- `is_mock` is carried explicitly because mock LLM output is test data
-- (Principle 7) and must never become establishment evidence. It is a column
-- rather than something inferred at gate time so the refusal is auditable
-- after the fact.
CREATE TABLE IF NOT EXISTS stabilization_evidence (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id       INTEGER NOT NULL,
    system_id        INTEGER NOT NULL,
    evidence_level   TEXT NOT NULL
                         CHECK (evidence_level IN ('node', 'flow_capability', 'ux_outcome')),
    evidence_kind    TEXT NOT NULL
                         CHECK (evidence_kind IN
                             ('criterion', 'floor', 'downstream_impact',
                              'outcome', 'stability')),
    name             TEXT NOT NULL,
    verdict          TEXT NOT NULL
                         CHECK (verdict IN
                             ('met', 'not_met', 'held', 'violated',
                              'unmeasured', 'not_applicable')),
    ref_kind         TEXT
                         CHECK (ref_kind IS NULL OR ref_kind IN
                             ('exploration_run', 'exploration_variant', 'replay_run',
                              'experiment', 'evaluation_policy')),
    ref_id           INTEGER,
    evaluation_policy_id INTEGER,
    detail           TEXT NOT NULL DEFAULT '',
    is_mock          INTEGER NOT NULL DEFAULT 0,
    source           TEXT NOT NULL DEFAULT 'deterministic'
                         CHECK (source IN ('deterministic', 'reasoning_llm', 'manual')),
    created_at       REAL NOT NULL,
    FOREIGN KEY (package_id) REFERENCES stabilization_package (id) ON DELETE CASCADE,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (evaluation_policy_id)
        REFERENCES evolution_evaluation_policy (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stabilization_evidence_package
    ON stabilization_evidence (package_id, evidence_level, id);

-- ---------------------------------------------------------------------------
-- Operations: monitoring, drift and local reopen
-- (Epic #394 Phase 5, Issue #400, app/node_operations.py)
--
-- ADR-5's separation is what this whole area exists to express: `established`
-- (the fixation decision is approved and a stable implementation is pinned)
-- and `monitoring` (that Node is ALSO actually being observed) fail
-- independently. Telemetry stopping does not make the fixation decision
-- wrong, and a Node whose telemetry died must stay distinguishable from one
-- under healthy observation -- collapsing them is the #366 one-word-two-facts
-- defect.
-- ---------------------------------------------------------------------------

-- node_monitoring_contract: what "we are watching this" means for ONE Node,
-- versioned. Append-only per node: a contract is what a monitoring judgement
-- was made against, so it must survive later edits.
--
-- Every threshold lives here rather than as a global constant, for the same
-- reason #399 refuses one establishment threshold: what counts as a drift
-- differs per Node, and a number invented centrally would be applied to
-- Nodes nobody looked at.
--
-- `observed_environment_ref` / `deployed_commit_sha` are separate from the
-- Node's pinned snapshot on purpose: what is DEPLOYED and what was ANALYSED
-- are different facts, and a drift report that conflated them would blame
-- the code for an environment change.
CREATE TABLE IF NOT EXISTS node_monitoring_contract (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                  INTEGER NOT NULL,
    node_id                    INTEGER NOT NULL,
    version_number             INTEGER NOT NULL DEFAULT 1,
    observed_environment_ref   TEXT NOT NULL DEFAULT '',
    deployed_commit_sha        TEXT NOT NULL DEFAULT '',
    sampling_note              TEXT NOT NULL DEFAULT '',
    -- The freshness budget: how long observation may be silent before the
    -- Node reads as unobserved rather than healthy. Silence is never treated
    -- as "fine" (#400: 未観測を正常扱いしない).
    freshness_budget_seconds   REAL NOT NULL DEFAULT 0,
    minimum_sample_count       INTEGER NOT NULL DEFAULT 0,
    indicators_json            TEXT NOT NULL DEFAULT '[]',
    reopen_conditions_json     TEXT NOT NULL DEFAULT '[]',
    escalation_owner           TEXT NOT NULL DEFAULT '',
    active                     INTEGER NOT NULL DEFAULT 1,
    decision_method            TEXT NOT NULL DEFAULT 'manual'
                                   CHECK (decision_method = 'manual'),
    created_by                 TEXT,
    created_at                 REAL NOT NULL,
    superseded_by_id           INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id)
        REFERENCES node_monitoring_contract (id) ON DELETE SET NULL,
    UNIQUE (node_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_node_monitoring_contract_node
    ON node_monitoring_contract (system_id, node_id, id DESC);

-- node_drift_observation: a DETERMINISTIC reading against the contract. No
-- interpretation lives here -- that is node_anomaly below, and the two are
-- separate tables precisely so a structural fact and a reasoning model's
-- reading of it can never be confused (CLAUDE.md: keep raw deterministic
-- facts separate from LLM interpretations in storage).
--
-- `observation_state` distinguishes the three things a non-drifting reading
-- can mean, which a boolean cannot: `within_budget` (measured, fine),
-- `drift_detected` (measured, moved), `insufficient_sample` (measured too
-- little to say) and `unobserved` (nothing arrived at all). The last two are
-- never rolled into "fine".
CREATE TABLE IF NOT EXISTS node_drift_observation (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    node_id               INTEGER NOT NULL,
    contract_id           INTEGER NOT NULL,
    indicator             TEXT NOT NULL,
    indicator_kind        TEXT NOT NULL
                              CHECK (indicator_kind IN
                                  ('input_distribution', 'output_quality', 'error_rate',
                                   'latency', 'cost', 'flow_success', 'outcome',
                                   'human_correction', 'compatibility')),
    observation_state     TEXT NOT NULL
                              CHECK (observation_state IN
                                  ('within_budget', 'drift_detected',
                                   'insufficient_sample', 'unobserved')),
    observed_value        REAL,
    reference_value       REAL,
    sample_count          INTEGER,
    window_seconds        REAL,
    last_observed_at      REAL,
    detail                TEXT NOT NULL DEFAULT '',
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (contract_id)
        REFERENCES node_monitoring_contract (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_node_drift_observation_node
    ON node_drift_observation (system_id, node_id, id DESC);

-- node_anomaly: the INTERPRETATION of one or more drift observations, in the
-- finite taxonomy #400 enumerates.
--
-- `classification` is never produced by a heuristic fallback: a reasoning
-- classification that fails leaves `unknown` with the failure recorded, and
-- `unknown` is a real, actionable state rather than a placeholder. The point
-- of the taxonomy is the distinction between a DEFECT and a
-- frame-breaking signal (`new_use_case_signal`,
-- `purpose_or_vision_reconsideration`): the first is fixed, the second means
-- the design was aimed at the wrong thing, and treating the second as the
-- first is how a system optimises its way further from its purpose.
--
-- `dedupe_key` is what stops one continuing condition producing a new reopen
-- every polling cycle.
CREATE TABLE IF NOT EXISTS node_anomaly (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    node_id             INTEGER NOT NULL,
    contract_id         INTEGER,
    classification      TEXT NOT NULL
                            CHECK (classification IN
                                ('implementation_defect', 'input_or_environment_drift',
                                 'upstream_downstream_mismatch', 'evaluation_gap',
                                 'new_use_case_signal',
                                 'purpose_or_vision_reconsideration', 'unknown')),
    severity            TEXT NOT NULL DEFAULT 'attention'
                            CHECK (severity IN ('blocking', 'attention', 'informational')),
    summary             TEXT NOT NULL DEFAULT '',
    observation_ids_json TEXT NOT NULL DEFAULT '[]',
    -- Which path produced the classification, and its provenance. A
    -- reasoning failure is recorded rather than replaced (Principle 6).
    decision_method     TEXT NOT NULL DEFAULT 'deterministic'
                            CHECK (decision_method IN
                                ('deterministic', 'reasoning_llm', 'manual')),
    intelligence_run_id INTEGER,
    classification_error TEXT NOT NULL DEFAULT '',
    dedupe_key          TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'acknowledged', 'resolved', 'superseded')),
    created_at          REAL NOT NULL,
    resolved_at         REAL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (contract_id) REFERENCES node_monitoring_contract (id) ON DELETE SET NULL,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_node_anomaly_node
    ON node_anomaly (system_id, node_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_node_anomaly_dedupe
    ON node_anomaly (node_id, dedupe_key)
    WHERE dedupe_key != '' AND status IN ('open', 'acknowledged');

-- node_reopen_plan: which Nodes a reopen would touch, and why.
--
-- The scope is proposed deterministically from the Node graph and then
-- APPROVED by a human -- reopening is a maturity transition, and ADR-9
-- allows no automatic ones. `stable_implementation_retained` is stored as an
-- explicit assertion rather than assumed, because ADR-5's promise that
-- production keeps running the established implementation during
-- re-exploration is the property most likely to be quietly broken later.
CREATE TABLE IF NOT EXISTS node_reopen_plan (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    origin_node_id              INTEGER NOT NULL,
    anomaly_id                  INTEGER,
    scope_node_ids_json         TEXT NOT NULL DEFAULT '[]',
    scope_rationale_json        TEXT NOT NULL DEFAULT '[]',
    excluded_node_ids_json      TEXT NOT NULL DEFAULT '[]',
    reason                      TEXT NOT NULL DEFAULT '',
    budget_note                 TEXT NOT NULL DEFAULT '',
    stable_implementation_retained INTEGER NOT NULL DEFAULT 1,
    status                      TEXT NOT NULL DEFAULT 'proposed'
                                    CHECK (status IN
                                        ('proposed', 'approved', 'rejected', 'completed')),
    decision_method             TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method = 'manual'),
    approved_by                 TEXT,
    approved_at                 REAL,
    decision_note               TEXT NOT NULL DEFAULT '',
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (origin_node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (anomaly_id) REFERENCES node_anomaly (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_node_reopen_plan_origin
    ON node_reopen_plan (system_id, origin_node_id, id DESC);

-- Notification outbox for operational signals.  One logical notification is
-- retained per Node/kind/dedupe key; repeated emissions during the cooldown
-- append a `suppressed_cooldown` event instead of producing another delivery.
-- This is deliberately an outbox, not an SMTP/webhook implementation: the
-- delivery adapter may fail or retry without losing the operational decision
-- or forging a second anomaly/reopen.
CREATE TABLE IF NOT EXISTS node_operation_notification (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    node_id             INTEGER NOT NULL,
    anomaly_id          INTEGER,
    reopen_plan_id      INTEGER,
    notification_kind   TEXT NOT NULL
                            CHECK (notification_kind IN
                                ('anomaly_detected', 'reopen_approved',
                                 'handoff_ready', 'handoff_blocked')),
    recipient           TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    dedupe_key          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'acknowledged')),
    cooldown_seconds    REAL NOT NULL DEFAULT 3600,
    last_emitted_at     REAL NOT NULL,
    cooldown_until      REAL NOT NULL,
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    suppressed_count    INTEGER NOT NULL DEFAULT 0,
    acknowledged_by     TEXT,
    acknowledged_at     REAL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (anomaly_id) REFERENCES node_anomaly (id) ON DELETE SET NULL,
    FOREIGN KEY (reopen_plan_id) REFERENCES node_reopen_plan (id) ON DELETE SET NULL,
    UNIQUE (node_id, notification_kind, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_node_operation_notification_node
    ON node_operation_notification (system_id, node_id, id DESC);

CREATE TABLE IF NOT EXISTS node_operation_notification_event (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    notification_id  INTEGER NOT NULL,
    event_type        TEXT NOT NULL
                          CHECK (event_type IN
                              ('queued', 'suppressed_cooldown', 'acknowledged')),
    actor             TEXT,
    detail            TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (notification_id)
        REFERENCES node_operation_notification (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_node_operation_notification_event
    ON node_operation_notification_event (notification_id, id ASC);

-- Staged local-reopen handoff.  This row never creates or approves a replay
-- or shadow action: it only binds already-persisted evidence in the required
-- order.  In particular, live_shadow_approval_id must reference the separate
-- human-approved gate in cell_shadow_decisions.
CREATE TABLE IF NOT EXISTS node_reopen_handoff (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                  INTEGER NOT NULL,
    reopen_plan_id             INTEGER NOT NULL,
    node_id                    INTEGER NOT NULL,
    stage                      TEXT NOT NULL DEFAULT 'awaiting_replay'
                                   CHECK (stage IN
                                       ('awaiting_replay', 'awaiting_offline_shadow',
                                        'awaiting_live_shadow_approval', 'ready')),
    replay_run_id              INTEGER,
    offline_shadow_result_id   INTEGER,
    live_shadow_approval_id    INTEGER,
    created_by                 TEXT,
    created_at                 REAL NOT NULL,
    updated_at                 REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (reopen_plan_id) REFERENCES node_reopen_plan (id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES evolution_node (id) ON DELETE CASCADE,
    FOREIGN KEY (replay_run_id) REFERENCES replay_runs (id) ON DELETE SET NULL,
    FOREIGN KEY (offline_shadow_result_id) REFERENCES shadow_results (id) ON DELETE SET NULL,
    FOREIGN KEY (live_shadow_approval_id) REFERENCES cell_shadow_decisions (id) ON DELETE SET NULL,
    UNIQUE (reopen_plan_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_node_reopen_handoff_node
    ON node_reopen_handoff (system_id, node_id, id DESC);

CREATE TABLE IF NOT EXISTS node_reopen_handoff_event (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    handoff_id     INTEGER NOT NULL,
    stage          TEXT NOT NULL
                       CHECK (stage IN
                           ('awaiting_replay', 'awaiting_offline_shadow',
                            'awaiting_live_shadow_approval', 'ready')),
    evidence_kind  TEXT NOT NULL
                       CHECK (evidence_kind IN
                           ('created', 'replay_run', 'offline_shadow_result',
                            'live_shadow_approval')),
    evidence_id    INTEGER,
    actor          TEXT,
    created_at     REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (handoff_id) REFERENCES node_reopen_handoff (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_node_reopen_handoff_event
    ON node_reopen_handoff_event (handoff_id, id ASC);

-- =============================================================================
-- UX Design Lineage (Epic #405). See docs/ux-design-lineage.md for the full
-- contract; this comment block only orients a reader of the schema itself.
--
-- Issue #407 (Journey / Requirement / Artifact, 10 tables below) and Issue
-- #408 (Solution Design, 5 tables further down) are the only new canonical
-- entities this Epic adds. Everything above/below them in this file --
-- Purpose Chain, Capability, Flow, Evolution Node, Component, Probe Cell --
-- is READ, never copied: `docs/ux-design-lineage.md` §0 invariant 1 forbids
-- a second understanding model, and §1 explains why THIS layer nonetheless
-- stores content while Purpose Chain does not -- Journey / Requirement /
-- Solution Design text cannot be re-derived from any existing row, so it has
-- to be authored and kept somewhere durable. What it does NOT store is
-- upstream (Purpose/Capability) or downstream (Flow/Node/Component/Cell)
-- CONTENT -- only a reference plus a captured digest, resolved against each
-- kind's single canonical source at read time (§1, §2.7, §3.3 -- the same
-- `_LINK_KIND_TARGET_SOURCE` discipline `node_design.py` already uses for
-- Evolution Node links).
--
-- House rules that recur on every table below and are not repeated per-table:
--   * `system_id INTEGER NOT NULL` + `FOREIGN KEY ... REFERENCES systems (id)
--     ON DELETE CASCADE` on every single table, including join/link tables,
--     so a System delete can never leave an orphaned UX Design row visible to
--     a different System (§0 has no exception for this layer).
--   * Every finite-vocabulary column carries `CHECK (col IN (...))` mirroring
--     the `Literal` alias of the same name in `app/models.py` byte-for-byte
--     (`test_interview_type_parity.py`'s `FINITE_TYPE_NAMES` then binds that
--     Python Literal to its TypeScript union, closing the loop).
--   * Append-only / revision tables never get an UPDATE path for their
--     content columns -- correction is INSERT a new row + set the prior row's
--     `superseded_by_id`. This is §0 invariant 4, applied uniformly: a
--     Journey/Requirement revision, an upstream ref, a step link, an artifact
--     reference, and every decision ledger row are all append-only for the
--     same reason `purpose_relation_decision` is -- "a human judged THIS
--     exact content at THIS time" must survive every later edit.
--   * No table in this section has a column that stores rendered/derived
--     STATUS (`design_status`, `link_state`, `recheck_state`, ...). Those are
--     computed at read time by folding the append-only decision/reference
--     rows (§2.5, §3.4) -- the same "derived, never stored" discipline #337 /
--     #338 / #349 use for Node maturity and Interview workflow state, so a
--     stored lifecycle value can never drift from the rows that describe it.
--   * No table has a column for artifact/design BODY content. `content_hash`
--     plus a `uri` reference is the only way a wireframe/ADR/spec is
--     represented (§2.8) -- the absence of a body column is a structural
--     guarantee, not a review convention, mirroring how #397 leaves out a
--     composite score column to make compositing physically impossible.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ux_journey: identity row for one UX Journey. Identity is
-- `(system_id, journey_key)`, a DEVELOPER-SUPPLIED stable slug -- never
-- derived from a Purpose element id (which hashes a claim's NAME and changes
-- when the claim is reworded, §2.2) and never derived from a row id (which
-- Understanding rebuilds reassign, #380's rule). `perspective` lives HERE,
-- on the identity row, not on `ux_journey_revision`: if a revision could
-- change `perspective`, one Journey's revision history would silently splice
-- together the record of two different subjects -- "how the system works
-- today" and "how it should work" are different journeys by construction,
-- and a `to_be` Journey names its `as_is` counterpart (if any) through
-- `baseline_journey_id` rather than by becoming it (§2.3).
--
-- `baseline_journey_id` may only point at an `as_is` Journey in the SAME
-- System (write-time check; `journey_baseline_not_as_is` / cross-System 404).
-- `baseline_mode` is the developer's own declaration of whether a baseline
-- SHOULD exist (`linked` / `greenfield` / `undecided`); `baseline_state`
-- (`app/models.py`) is derived at read time from `baseline_mode` +
-- `perspective` + whether `baseline_journey_id` currently resolves, and is
-- therefore not a column here -- the same "derived, never stored" rule as
-- `design_status` below. `current_revision_id` is a denormalized pointer,
-- written only inside the same transaction that inserts the revision it
-- points at (never by a bare UPDATE elsewhere), the same discipline
-- `evolution_node.current_version_id` uses.
CREATE TABLE IF NOT EXISTS ux_journey (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    journey_key         TEXT NOT NULL,
    perspective         TEXT NOT NULL CHECK (perspective IN ('as_is', 'to_be')),
    baseline_mode       TEXT NOT NULL DEFAULT 'undecided'
                            CHECK (baseline_mode IN ('linked', 'greenfield', 'undecided')),
    baseline_journey_id INTEGER,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'ux-journey-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (baseline_journey_id) REFERENCES ux_journey (id) ON DELETE SET NULL,
    FOREIGN KEY (current_revision_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, journey_key)
);

CREATE INDEX IF NOT EXISTS idx_ux_journey_system
    ON ux_journey (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_ux_journey_baseline
    ON ux_journey (system_id, baseline_journey_id);

-- ux_journey_revision: the Journey's CONTENT, append-only exactly like
-- `evolution_node_version` -- a correction inserts `revision_number = max+1`
-- and sets the prior current row's `superseded_by_id`; nothing here is ever
-- UPDATEd in place, because a later Solution Design or a `ux_design_decision`
-- confirmation is a judgement made AGAINST one specific revision's content
-- and that history must remain readable.
--
-- `content_digest` is `sha256` over the meaning-bearing fields ONLY --
-- `title, beneficiary, usage_context, entry_trigger, value_arrival, summary`
-- plus every Step's own `(step_key, step_order, content_digest)` (§2.6).
-- `created_by` / `created_at` / `revision_number` / `change_note` are
-- deliberately excluded from the digest for the same reason #308 excludes
-- `confirmation_id` and #337 excludes Intent's `status`: a recheck must fire
-- on a MEANING change, never on the mere existence of a new record.
-- `authored_by_kind` and `decision_method` are two of the layer's three
-- independent axes (§0 invariant 3 / §2.5's fourth axis) -- an AI-authored
-- revision (`authored_by_kind='reasoning_model'`) can still be
-- `decision_method='manual'` if a human typed the confirming edit, and an
-- AI-authored revision becoming `design_status='confirmed'` in
-- `ux_design_decision` later is "a human confirmed AI-written text", never
-- "AI confirmed its own text" -- the CHECK on `ux_design_decision.
-- decision_method` (always `manual`) is what enforces that.
CREATE TABLE IF NOT EXISTS ux_journey_revision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id        INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    revision_number   INTEGER NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    beneficiary       TEXT NOT NULL DEFAULT '',   -- 対象者
    usage_context     TEXT NOT NULL DEFAULT '',   -- 文脈
    entry_trigger     TEXT NOT NULL DEFAULT '',   -- トリガー
    value_arrival     TEXT NOT NULL DEFAULT '',   -- 価値到達
    summary           TEXT NOT NULL DEFAULT '',
    content_digest    TEXT NOT NULL,
    authored_by_kind  TEXT NOT NULL DEFAULT 'developer'
                          CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note       TEXT NOT NULL DEFAULT '',
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    schema_version    TEXT NOT NULL DEFAULT 'ux-journey-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    UNIQUE (journey_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_ux_journey_revision_journey
    ON ux_journey_revision (journey_id, revision_number DESC);

-- ux_journey_step: the Journey revision's ORDERED content, not an
-- independently-versioned entity (§2.4). Steps intentionally have no
-- `superseded_by_id` chain of their own: the sequence of Steps IS the
-- Journey's meaning, and letting Steps version independently of their
-- revision would turn "what did this Journey look like at time T" into a
-- join across two separate histories. `step_key` stays stable ACROSS
-- revisions of the same Journey (unlike `id`), which is what lets diffing
-- (`GET /ux-design/journeys/{key}/diff`) and `ux_requirement_step_link` match
-- by exact key equality rather than by position or text similarity -- the
-- same `understanding_diff` discipline, never embeddings (§0 invariant 9).
--
-- `evidence_source_kind` records only an EXPECTATION ("if this Step
-- succeeds, here is where you would look for confirmation"), never an
-- observed outcome -- §0 invariant 6 keeps this layer out of the business of
-- inferring user success from a trace; `purpose_outcome_criterion` remains
-- the one place an outcome verdict is recorded.
CREATE TABLE IF NOT EXISTS ux_journey_step (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_revision_id   INTEGER NOT NULL,
    journey_id            INTEGER NOT NULL,
    system_id             INTEGER NOT NULL,
    step_key              TEXT NOT NULL,
    step_order            INTEGER NOT NULL,
    user_intent           TEXT NOT NULL DEFAULT '',
    system_response       TEXT NOT NULL DEFAULT '',
    success_criteria      TEXT NOT NULL DEFAULT '',
    failure_mode          TEXT NOT NULL DEFAULT '',
    recovery_path         TEXT NOT NULL DEFAULT '',
    evidence_expectation  TEXT NOT NULL DEFAULT '',
    evidence_source_kind  TEXT NOT NULL DEFAULT 'none'
                              CHECK (evidence_source_kind IN
                                  ('runtime_trace', 'human_report', 'external_analytics', 'none')),
    content_digest        TEXT NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_revision_id) REFERENCES ux_journey_revision (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    UNIQUE (journey_revision_id, step_key)
);

CREATE INDEX IF NOT EXISTS idx_ux_journey_step_revision
    ON ux_journey_step (journey_revision_id, step_order);

CREATE INDEX IF NOT EXISTS idx_ux_journey_step_journey
    ON ux_journey_step (journey_id, step_key);

-- ux_journey_upstream_ref: a Journey's reference to a Purpose element,
-- Purpose relation, or Capability entity -- NEVER a copy of that thing's
-- content (§0 invariant 1's "コピーした Capability 名は元の Capability が
-- superseded された後も current として読めてしまう"). `ref_kind` fixes which
-- of the three canonical sources resolves `target_ref` at read time
-- (`app/models.py`'s `UxRefKind`; §2.7's table). `captured_digest` is the
-- source's digest AT THE TIME the reference was made, so staleness
-- (`UxRefRecheckState`) can be detected without ever mutating the reference
-- itself -- exactly `purpose_relation_decision`'s `source_digest` /
-- `target_digest` pattern, applied to a reference instead of a decision.
-- `decision_method` records who ASSERTED the reference (`manual` /
-- `reasoning_llm` / `deterministic`), which `UxRefRelationStatus` maps
-- through a fixed table (`confirmed` / `proposed` / `derived`) -- the same
-- `node_design._DECISION_METHOD_TO_RELATION_STATUS` translation, never a
-- second stored status column.
CREATE TABLE IF NOT EXISTS ux_journey_upstream_ref (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id          INTEGER NOT NULL,
    journey_id         INTEGER NOT NULL,
    ref_kind           TEXT NOT NULL CHECK (ref_kind IN
                           ('purpose_element', 'purpose_relation', 'capability_entity')),
    target_ref         TEXT NOT NULL,
    target_row_id      INTEGER,
    captured_digest    TEXT NOT NULL DEFAULT '',
    captured_session_id INTEGER,
    note               TEXT NOT NULL DEFAULT '',
    decision_method    TEXT NOT NULL DEFAULT 'manual'
                           CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by         TEXT,
    created_at         REAL NOT NULL,
    superseded_by_id   INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_journey_upstream_ref (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ux_journey_upstream_ref_journey
    ON ux_journey_upstream_ref (system_id, journey_id, id DESC);

-- ux_requirement: identity row for one Requirement, `(system_id,
-- requirement_key)` UNIQUE -- the same developer-supplied-slug identity
-- rule as `ux_journey`, for the same reason (survives rewording, never
-- derived from a row id). `requirement_kind` includes `out_of_scope`
-- alongside `functional` / `non_functional` / `constraint` DELIBERATELY:
-- "we decided not to do this" is itself a requirement worth keeping a
-- traceable record of, which is why `out_of_scope` rows live in the same
-- table rather than being silently dropped -- and why the acceptance
-- criterion table below refuses to attach criteria to one (§2.11 item 8):
-- a thing declared out of scope cannot also have a condition for verifying
-- it was done.
CREATE TABLE IF NOT EXISTS ux_requirement (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    requirement_key     TEXT NOT NULL,
    requirement_kind    TEXT NOT NULL CHECK (requirement_kind IN
                            ('functional', 'non_functional', 'constraint', 'out_of_scope')),
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'ux-requirement-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id) REFERENCES ux_requirement_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, requirement_key)
);

CREATE INDEX IF NOT EXISTS idx_ux_requirement_system
    ON ux_requirement (system_id, id DESC);

-- ux_requirement_revision: append-only content, same discipline as
-- `ux_journey_revision` above and for the same reason -- a Solution Design's
-- `solution_design_requirement_link` captures a specific revision id and
-- digest, so that link's later staleness detection depends on this row's
-- content never being mutated in place.
CREATE TABLE IF NOT EXISTS ux_requirement_revision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id    INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    revision_number   INTEGER NOT NULL,
    statement         TEXT NOT NULL DEFAULT '',
    rationale         TEXT NOT NULL DEFAULT '',
    constraint_text   TEXT NOT NULL DEFAULT '',
    out_of_scope_note TEXT NOT NULL DEFAULT '',
    content_digest    TEXT NOT NULL,
    authored_by_kind  TEXT NOT NULL DEFAULT 'developer'
                          CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note       TEXT NOT NULL DEFAULT '',
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    schema_version    TEXT NOT NULL DEFAULT 'ux-requirement-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_requirement_revision (id) ON DELETE SET NULL,
    UNIQUE (requirement_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_ux_requirement_revision_requirement
    ON ux_requirement_revision (requirement_id, revision_number DESC);

-- ux_requirement_acceptance_criterion: like `ux_journey_step`, content OF a
-- revision rather than an independently-versioned entity, for the identical
-- reason -- "what did satisfying this Requirement mean at time T" must not
-- become a two-history join. `verification_method` is deliberately a finite
-- classification of HOW a criterion COULD be checked (`manual_review` /
-- `replay` / `experiment` / `runtime_observation` / `not_verifiable`), never
-- a record that it WAS checked -- verification itself happens in each named
-- existing system (Replay, Experiments), not here.
CREATE TABLE IF NOT EXISTS ux_requirement_acceptance_criterion (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_revision_id INTEGER NOT NULL,
    requirement_id          INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    criterion_key           TEXT NOT NULL,
    criterion_order         INTEGER NOT NULL,
    statement               TEXT NOT NULL DEFAULT '',
    verification_method     TEXT NOT NULL DEFAULT 'manual_review'
                                CHECK (verification_method IN
                                    ('manual_review', 'replay', 'experiment',
                                     'runtime_observation', 'not_verifiable')),
    verification_note       TEXT NOT NULL DEFAULT '',
    content_digest          TEXT NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_revision_id) REFERENCES ux_requirement_revision (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    UNIQUE (requirement_revision_id, criterion_key)
);

CREATE INDEX IF NOT EXISTS idx_ux_requirement_acceptance_criterion_revision
    ON ux_requirement_acceptance_criterion (requirement_revision_id, criterion_order);

-- ux_requirement_step_link: the many-to-many bridge from a Requirement to
-- the Journey Step(s) it addresses. There is no FK straight to
-- `ux_journey_step.id` -- Steps live inside a specific revision, and a link
-- needs to survive the Journey moving to a NEW revision so it can report
-- `stale` / `unresolved` rather than silently vanishing (§2.9's "Journey
-- revision が動く -> その Step を指す ux_requirement_step_link が stale" /
-- "Journey Step が消える -> unresolved"). It therefore stores `step_key`
-- (stable across revisions) plus `captured_journey_revision_id` +
-- `captured_step_digest` (the revision and digest AT LINK TIME), and
-- resolves against the Journey's CURRENT revision at read time.
CREATE TABLE IF NOT EXISTS ux_requirement_step_link (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    requirement_id              INTEGER NOT NULL,
    journey_id                  INTEGER NOT NULL,
    step_key                    TEXT NOT NULL,
    captured_journey_revision_id INTEGER,
    captured_step_digest        TEXT NOT NULL DEFAULT '',
    note                        TEXT NOT NULL DEFAULT '',
    decision_method             TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    superseded_by_id            INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (captured_journey_revision_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_requirement_step_link (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ux_requirement_step_link_requirement
    ON ux_requirement_step_link (system_id, requirement_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_ux_requirement_step_link_journey
    ON ux_requirement_step_link (journey_id, step_key);

-- ux_design_artifact_reference: a pointer to a wireframe / ADR / spec /
-- diagram / research note, NEVER its content (§2.8 -- no body column exists
-- by construction, matching this file's banner comment above). `content_hash`
-- is always `sha256`; `verification_state` distinguishes a hash the SYSTEM
-- itself confirmed (`verified` -- reachable ONLY for a `repo:<path>` URI that
-- resolves via `git show <sha>:<path>` on a pinned snapshot, per Principle 5
-- -- probe-agent fetches no external URI) from a hash the DEVELOPER merely
-- typed in (`unverified`, the only reachable state for any external URL,
-- Wiki, or Figma link) from a repo path that used to resolve and no longer
-- does (`unreachable`). Collapsing any two of these into one state would
-- claim a stronger guarantee than the system actually checked.
CREATE TABLE IF NOT EXISTS ux_design_artifact_reference (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    subject_kind        TEXT NOT NULL CHECK (subject_kind IN
                            ('journey', 'journey_step', 'requirement',
                             'solution_design', 'design_option')),
    subject_key         TEXT NOT NULL,
    artifact_kind       TEXT NOT NULL CHECK (artifact_kind IN
                            ('wireframe', 'adr', 'spec', 'diagram', 'research_note', 'other')),
    title               TEXT NOT NULL DEFAULT '',
    uri                 TEXT NOT NULL,
    media_type          TEXT NOT NULL DEFAULT '',
    content_hash        TEXT NOT NULL,
    hash_algorithm      TEXT NOT NULL DEFAULT 'sha256' CHECK (hash_algorithm = 'sha256'),
    byte_size           INTEGER,
    verification_state  TEXT NOT NULL DEFAULT 'unverified'
                            CHECK (verification_state IN ('verified', 'unverified', 'unreachable')),
    verified_snapshot_id INTEGER,
    verified_commit_sha TEXT,
    verified_at         REAL,
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_design_artifact_reference (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ux_design_artifact_reference_subject
    ON ux_design_artifact_reference (system_id, subject_kind, subject_key, id DESC);

-- ux_design_decision: the ONE decision ledger for #407's own confirm / reject
-- / retire / reinstate lifecycle (`UxDesignDecisionKind`), covering Journeys,
-- Requirements, and their references/links (`UxDesignSubjectKind`).
-- DELIBERATELY has no `status` column anywhere in this table OR on
-- `ux_journey`/`ux_requirement` -- `design_status` (`app/models.py`'s
-- `UxDesignStatus`) is derived at read time from the latest non-superseded
-- row here for `(system_id, subject_kind, subject_key)`, exactly the way
-- Evolution Node maturity is derived by folding `evolution_node_event`
-- (#337 / #338 / #349's "a stored lifecycle value can drift from the rows it
-- describes, a derived one cannot"). `decision_method` is CHECKed to the
-- single literal `'manual'` -- unlike `ux_journey_upstream_ref` /
-- `ux_requirement_step_link`, which may be `reasoning_llm`/`deterministic`
-- because a REFERENCE can be proposed by the system, a CONFIRM/REJECT/RETIRE/
-- REINSTATE decision about that reference can only ever be a human's (§0
-- invariant 3). `captured_digest` / `captured_revision_id` are the content a
-- human judged AT DECISION TIME, which is what lets a later content change
-- degrade the SEPARATE `recheck_state` axis to `stale` without touching
-- `design_status` or this row (§2.5's "確定を取り消すのではなく...再確認を
-- 促す").
CREATE TABLE IF NOT EXISTS ux_design_decision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    subject_kind      TEXT NOT NULL CHECK (subject_kind IN
                          ('journey', 'requirement', 'requirement_step_link',
                           'journey_upstream_ref', 'artifact_reference')),
    subject_key       TEXT NOT NULL,
    subject_row_id    INTEGER,
    decision          TEXT NOT NULL CHECK (decision IN
                          ('confirm', 'reject', 'retire', 'reinstate')),
    rationale         TEXT NOT NULL DEFAULT '',
    captured_digest   TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method   TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    decided_by        TEXT,
    superseded_by_id  INTEGER,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_design_decision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ux_design_decision_subject
    ON ux_design_decision (system_id, subject_kind, subject_key, id DESC);

-- ---------------------------------------------------------------------------
-- Solution Design (Epic #405, Issue #408). See docs/ux-design-lineage.md §3.
--
-- Requirement -> Solution Design -> {Capability, static_flow, runtime_flow,
-- Evolution Node, Component, Cell, Probe Point} is the second half of this
-- Epic's chain (§0 diagram). Like the #407 tables above, this section stores
-- new authored content (the design options themselves) but never copies
-- upstream Requirement text or downstream target content -- only references
-- plus captured digests, resolved at read time against one canonical source
-- per `target_kind` (§3.3's table, the same discipline as §2.7).
-- ---------------------------------------------------------------------------

-- solution_design: identity row, `(system_id, design_key)` UNIQUE -- the same
-- developer-supplied-slug rule as `ux_journey` / `ux_requirement`. Carries no
-- "current option" or "status" column: which option (if any) is adopted is
-- derived by folding `solution_design_decision` for each `option_key`, the
-- identical "derived, never stored" rule `ux_design_decision` uses for
-- `design_status` just above -- and it is intentional that a design can
-- exist, and be worked on, before any option has been decided.
CREATE TABLE IF NOT EXISTS solution_design (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    design_key      TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    schema_version  TEXT NOT NULL DEFAULT 'solution-design-v1',
    created_by      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (system_id, design_key)
);

CREATE INDEX IF NOT EXISTS idx_solution_design_system
    ON solution_design (system_id, id DESC);

-- solution_design_option: one candidate approach for a Solution Design,
-- append-only (a correction supersedes rather than mutates, same as
-- `ux_journey_revision`). `authored_by_kind` may legitimately be
-- `reasoning_model` -- an AI-drafted option is exactly the kind of proposal
-- this Epic exists to support (§0 invariant 3) -- but `solution_design_
-- decision` below is CHECKed to `decision_method = 'manual'` regardless of
-- who wrote the option text, so an AI's own draft can never adopt itself.
""" + _SOLUTION_DESIGN_OPTION_DDL + """

-- solution_design_requirement_link: MANY-TO-MANY on purpose. There is no FK
-- from `solution_design` straight to one Requirement, because one design
-- legitimately satisfies several Requirements and one Requirement is
-- legitimately satisfied by several competing designs (#408 acceptance
-- condition 1) -- forcing a single FK would mean duplicating the design row
-- per Requirement, which would fracture option comparison across the
-- duplicates. Append-only like every link table in this Epic;
-- `captured_requirement_revision_id` + `captured_digest` are what the design
-- was written AGAINST, and a later Requirement revision degrades this link's
-- read-time `link_state` to `stale` (§2.9 / §3.4) without altering the link
-- row or the design.
CREATE TABLE IF NOT EXISTS solution_design_requirement_link (
    id                               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                        INTEGER NOT NULL,
    solution_design_id               INTEGER NOT NULL,
    requirement_id                   INTEGER NOT NULL,
    captured_requirement_revision_id INTEGER,
    captured_digest                  TEXT NOT NULL DEFAULT '',
    note                             TEXT NOT NULL DEFAULT '',
    decision_method                  TEXT NOT NULL DEFAULT 'manual'
                                         CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by                       TEXT,
    created_at                       REAL NOT NULL,
    superseded_by_id                 INTEGER,
    schema_version                   TEXT NOT NULL DEFAULT 'solution-design-requirement-link-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (solution_design_id) REFERENCES solution_design (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    FOREIGN KEY (captured_requirement_revision_id) REFERENCES ux_requirement_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES solution_design_requirement_link (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_solution_design_requirement_link_design
    ON solution_design_requirement_link (system_id, solution_design_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_solution_design_requirement_link_requirement
    ON solution_design_requirement_link (requirement_id, id DESC);

-- solution_design_decision: the EXCLUSIVE-CHOICE ledger, kept separate from
-- `ux_design_decision` on purpose (§3.2): confirming a Requirement is a
-- non-exclusive judgement ("this statement is correct"), while adopting an
-- Option is an exclusive judgement among N competing options ("this one, not
-- the others") -- folding the two into one decision vocabulary would leave
-- that exclusivity unrepresented anywhere in the schema. The exclusivity
-- itself is enforced at the SERVICE layer (an `adopt` while another option of
-- the same design is already `adopted` is refused with 409
-- `solution_design_option_already_adopted`, never an automatic `withdraw` of
-- the prior one -- §3.2's "システムが人間の名前で「取り下げた」という決定を
-- 捏造することになる"), which is why this table itself carries no UNIQUE
-- constraint forcing at-most-one-adopted -- the append-only history must
-- still be able to show an option that was adopted and later withdrawn.
-- `decision_method` is CHECKed to the single literal `'manual'`, same
-- reasoning as `ux_design_decision` (§3.6: adoption is never automatic).
CREATE TABLE IF NOT EXISTS solution_design_decision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    solution_design_id  INTEGER NOT NULL,
    option_id           INTEGER NOT NULL,
    option_key          TEXT NOT NULL,
    decision            TEXT NOT NULL CHECK (decision IN ('adopt', 'hold', 'reject', 'withdraw')),
    rationale           TEXT NOT NULL DEFAULT '',
    captured_digest     TEXT NOT NULL DEFAULT '',
    decision_method     TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    decided_by          TEXT,
    superseded_by_id    INTEGER,
    created_at          REAL NOT NULL,
    schema_version      TEXT NOT NULL DEFAULT 'solution-design-decision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (solution_design_id) REFERENCES solution_design (id) ON DELETE CASCADE,
    FOREIGN KEY (option_id) REFERENCES solution_design_option (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES solution_design_decision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_solution_design_decision_design
    ON solution_design_decision (system_id, solution_design_id, option_key, id DESC);

-- solution_design_target_link: the bridge from an adopted (or still-being-
-- evaluated) Option to an existing implementation target, shaped exactly
-- like `evolution_node_link` (`target_kind` CHECK enumeration, `target_ref`
-- the stable string identity of the target's OWN canonical source,
-- `target_row_id` a join shortcut that is NEVER trusted alone). `static_flow`
-- and `runtime_flow` are kept as two separate `target_kind` values rather
-- than one "flow" value because they name genuinely different facts -- a
-- statically computed entry-point path fixed to one snapshot versus a live
-- SDK-assigned execution correlation id -- and §3.3 forbids folding them into
-- one displayed word (#366's rule). A `static_flow` link cannot be created
-- without `captured_snapshot_id` (422 `flow_target_requires_snapshot`),
-- because an entry-point path with no pinned snapshot has no stable meaning
-- to capture. `probe_point` links are validated at WRITE time against
-- `evolution_node._require_approved_probe_point` (unapproved -> 409, foreign
-- System -> 404); every other `target_kind` is resolved only at READ time,
-- the same asymmetry Evolution Node Phase 1 already uses for its links.
CREATE TABLE IF NOT EXISTS solution_design_target_link (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    solution_design_id    INTEGER NOT NULL,
    option_id             INTEGER NOT NULL,
    target_kind           TEXT NOT NULL CHECK (target_kind IN
                              ('capability', 'static_flow', 'runtime_flow', 'evolution_node',
                               'component', 'cell_definition', 'cell_binding', 'probe_point')),
    target_ref            TEXT NOT NULL,
    target_row_id         INTEGER,
    captured_digest       TEXT NOT NULL DEFAULT '',
    captured_snapshot_id  INTEGER,
    note                  TEXT NOT NULL DEFAULT '',
    decision_method       TEXT NOT NULL DEFAULT 'manual'
                              CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by            TEXT,
    created_at            REAL NOT NULL,
    superseded_by_id      INTEGER,
    schema_version        TEXT NOT NULL DEFAULT 'solution-design-target-link-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (solution_design_id) REFERENCES solution_design (id) ON DELETE CASCADE,
    FOREIGN KEY (option_id) REFERENCES solution_design_option (id) ON DELETE CASCADE,
    FOREIGN KEY (captured_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES solution_design_target_link (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_solution_design_target_link_design
    ON solution_design_target_link (system_id, solution_design_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_solution_design_target_link_target
    ON solution_design_target_link (target_kind, target_ref);

-- ---------------------------------------------------------------------------
-- Execution Modes and the Flow experiment orchestrator (Epic #412).
-- Canonical contract: docs/execution-modes.md. Domain layers:
-- app/execution_mode.py (#413) and app/flow_orchestration.py (#415).
-- Issue #414's projection is derived from existing rows and adds NO table.
--
-- The execution mode is the FIFTH independent axis (#394 ADR-6): it is never
-- derived from, and never merged into, Node maturity, Cell Improvement
-- status, the SDK policy mode, or the Dashboard workflow phase. In
-- particular the SDK policy `shadow` (does the SDK run the candidate and
-- send `shadow_results`?) and the execution mode `shadow` (does the control
-- plane permit candidate comparison at all?) are two different facts, and a
-- row being one without the other is a legitimate state.
-- ---------------------------------------------------------------------------

-- execution_mode_assignment: append-only. Two record kinds, because "the
-- window a human set has elapsed" and "a human explicitly ended this
-- assignment" are two different answers (docs/execution-modes.md EM-ADR-2).
-- An `expired` assign row clamps the resolved mode to `fixed` instead of
-- letting a broader scope's `propose` take over -- otherwise the deadline the
-- human set would stop nothing. A `revoke` row lets normal inheritance
-- resume. There is deliberately no path by which the passage of time alone
-- restores a permission.
--
-- Rows are never UPDATEd except to chain `superseded_by_id` onto the
-- immediately preceding row of the same scope (the same append-only chain
-- pointer used by evolution_node_version / ux_design_decision).
--
-- `scope_ref` always carries its prefix (`runtime_flow:<flow_id>` for the
-- flow scope, the bare `node_key` for the node scope, '' for the system
-- scope). `static_flow` is deliberately NOT a mode scope: knowing which
-- Nodes lie on a static flow requires recomputing a call graph, and a
-- fail-closed gate must not depend on a derivation that can itself fail
-- (EM-ADR-1). It remains a display subject for Issue #414.
--
-- `previous_mode` is the effective mode resolved at write time, kept so the
-- audit can be read without recomputing "what changed" (#337's rule that a
-- decision record is an audit record).
CREATE TABLE IF NOT EXISTS execution_mode_assignment (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    record_kind       TEXT NOT NULL
                          CHECK (record_kind IN ('assign', 'revoke')),
    scope_kind        TEXT NOT NULL
                          CHECK (scope_kind IN ('system', 'flow', 'node')),
    scope_ref         TEXT NOT NULL DEFAULT '',
    mode              TEXT
                          CHECK (mode IS NULL OR mode IN
                              ('fixed', 'observe', 'propose', 'shadow')),
    previous_mode     TEXT,
    effective_from    REAL,
    effective_until   REAL,
    reason            TEXT NOT NULL,
    actor_kind        TEXT NOT NULL DEFAULT 'user'
                          CHECK (actor_kind IN ('user', 'system')),
    actor             TEXT,
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN
                              ('manual', 'reasoning_llm', 'deterministic')),
    supersedes_id     INTEGER,
    superseded_by_id  INTEGER,
    schema_version    TEXT NOT NULL DEFAULT 'execution-mode-assignment-v1',
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (supersedes_id) REFERENCES execution_mode_assignment (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES execution_mode_assignment (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_mode_assignment_scope
    ON execution_mode_assignment (system_id, scope_kind, scope_ref, id DESC);

-- execution_mode_observation: what mode a Node was ACTUALLY run under, so the
-- configured value and the runtime reading can be compared. `unobserved` is
-- never reported as `match`: not having looked is not a success (#380).
CREATE TABLE IF NOT EXISTS execution_mode_observation (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id      INTEGER NOT NULL,
    node_key       TEXT NOT NULL,
    observed_mode  TEXT NOT NULL
                       CHECK (observed_mode IN
                           ('fixed', 'observe', 'propose', 'shadow')),
    capability     TEXT,
    run_ref        TEXT,
    source         TEXT NOT NULL DEFAULT 'control_server'
                       CHECK (source IN ('control_server', 'sdk')),
    detail         TEXT NOT NULL DEFAULT '',
    recorded_at    REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_execution_mode_observation_node
    ON execution_mode_observation (system_id, node_key, id DESC);

-- flow_experiment_proposal: the IMMUTABLE content of one Flow-scoped
-- experiment proposal. There is deliberately NO `status` column -- the
-- lifecycle is folded from flow_experiment_event (#337/#338/#349/#405: a
-- stored lifecycle value can drift from the rows it describes, a derived one
-- cannot).
--
-- Every field the completeness gate requires is NOT NULL here so a proposal
-- that is missing its baseline, quality floor, isolation strategy, cost cap,
-- stop conditions or rollback plan cannot exist as a row at all. The finite
-- rejection codes live in app/flow_orchestration.py (docs/execution-modes.md
-- §7.1); the schema is the second line of that defence, not the first.
CREATE TABLE IF NOT EXISTS flow_experiment_proposal (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    proposal_key          TEXT NOT NULL,
    flow_subject_kind     TEXT NOT NULL
                              CHECK (flow_subject_kind IN
                                  ('runtime_flow', 'static_flow')),
    flow_subject_ref      TEXT NOT NULL,
    captured_snapshot_id  INTEGER,
    comparison_scope      TEXT NOT NULL
                              CHECK (comparison_scope IN
                                  ('single_node', 'sub_pipeline')),
    title                 TEXT NOT NULL,
    purpose               TEXT NOT NULL,
    hypothesis            TEXT NOT NULL,
    baseline_ref          TEXT NOT NULL,
    candidate_refs_json   TEXT NOT NULL DEFAULT '[]',
    evaluation_axes_json  TEXT NOT NULL DEFAULT '[]',
    quality_floor_json    TEXT NOT NULL DEFAULT '{}',
    isolation_strategy    TEXT NOT NULL
                              CHECK (isolation_strategy IN
                                  ('pure', 'mock', 'dry_run',
                                   'rollback_transaction',
                                   'isolated_workspace', 'none')),
    isolation_detail      TEXT NOT NULL DEFAULT '',
    cost_cap_json         TEXT NOT NULL DEFAULT '{}',
    stop_conditions_json  TEXT NOT NULL DEFAULT '[]',
    rollback_plan         TEXT NOT NULL,
    evidence_refs_json    TEXT NOT NULL DEFAULT '[]',
    expires_at            REAL,
    decision_method       TEXT NOT NULL DEFAULT 'manual'
                              CHECK (decision_method IN
                                  ('manual', 'reasoning_llm', 'deterministic')),
    intelligence_run_id   INTEGER,
    created_by            TEXT,
    created_at            REAL NOT NULL,
    schema_version        TEXT NOT NULL DEFAULT 'flow-experiment-proposal-v1',
    UNIQUE (system_id, proposal_key),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (captured_snapshot_id) REFERENCES repository_snapshots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_proposal_system
    ON flow_experiment_proposal (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_proposal_subject
    ON flow_experiment_proposal (system_id, flow_subject_kind, flow_subject_ref, id DESC);

-- flow_experiment_target: the Nodes the proposal is about. `target_node_key`
-- is the Evolution Node's own durable slug (#394 ADR-2), resolved at read
-- time -- never a stored row id trusted on its own (#405).
CREATE TABLE IF NOT EXISTS flow_experiment_target (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    proposal_id      INTEGER NOT NULL,
    target_node_key  TEXT NOT NULL,
    target_role      TEXT NOT NULL
                         CHECK (target_role IN ('baseline', 'candidate_target')),
    position         INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL,
    UNIQUE (proposal_id, target_node_key, target_role),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_id) REFERENCES flow_experiment_proposal (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_target_proposal
    ON flow_experiment_target (proposal_id, position, id);

-- flow_experiment_event: the append-only lifecycle ledger. This is the
-- canonical source of a proposal's status; nothing folds it into a column.
-- `decision_method` is 'manual' for every human decision reaching this table
-- through HTTP, and the actor comes from the authenticated principal, never
-- from the request body (#337).
CREATE TABLE IF NOT EXISTS flow_experiment_event (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id        INTEGER NOT NULL,
    proposal_id      INTEGER NOT NULL,
    event_kind       TEXT NOT NULL
                         CHECK (event_kind IN
                             ('proposed', 'approved', 'rejected', 'withdrawn',
                              'expired', 'execution_recorded',
                              'result_recorded',
                              'promotion_candidate_recorded',
                              'rollback_recorded')),
    actor_kind       TEXT NOT NULL DEFAULT 'user'
                         CHECK (actor_kind IN ('user', 'system')),
    actor            TEXT,
    reason           TEXT NOT NULL DEFAULT '',
    decision_method  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (decision_method IN
                             ('manual', 'reasoning_llm', 'deterministic')),
    payload_json     TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_id) REFERENCES flow_experiment_proposal (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_event_proposal
    ON flow_experiment_event (proposal_id, id);

-- flow_experiment_execution_ref: a REFERENCE to an execution that already
-- has its own canonical row elsewhere (replay_runs / experiments /
-- shadow_results). The orchestrator owns no execution of its own and writes
-- to none of those tables; it points at them and resolves the pointer at
-- read time.
CREATE TABLE IF NOT EXISTS flow_experiment_execution_ref (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id       INTEGER NOT NULL,
    proposal_id     INTEGER NOT NULL,
    execution_kind  TEXT NOT NULL
                        CHECK (execution_kind IN
                            ('replay_variant_run', 'experiment',
                             'shadow_result')),
    execution_ref   TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    recorded_at     REAL NOT NULL,
    UNIQUE (proposal_id, execution_kind, execution_ref),
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_id) REFERENCES flow_experiment_proposal (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_execution_ref_proposal
    ON flow_experiment_execution_ref (proposal_id, id);

-- One canonical execution backs exactly one proposal (§7.5.1), which is a
-- lookup by the reference rather than by the proposal.
CREATE INDEX IF NOT EXISTS idx_flow_experiment_execution_ref_target
    ON flow_experiment_execution_ref (system_id, execution_kind, execution_ref);

-- flow_experiment_draft: WHAT one reasoning-model drafting run was about.
-- `intelligence_runs` records how a run was made (provider / model / prompt
-- and schema version / status) but not its subject, so "is this the run that
-- drafted THIS Flow?" had no answer: a valid, completed draft of Flow A
-- could be attached to a hand-written proposal for Flow B and would then
-- read as reasoning-model output about B. An unverified pointer is not
-- provenance (Principle 7), and this is the row that makes the pointer
-- verifiable (docs/execution-modes.md §7.1.3).
--
-- One row per drafting run, written for a FAILED run too: what a run was
-- about is a fact about the attempt, not about its outcome. `input_digest`
-- covers the subject, the pinned snapshot, the drafted Node keys and the
-- citable evidence allowlist, so the audit can say the inputs were the ones
-- recorded without re-deriving them.
CREATE TABLE IF NOT EXISTS flow_experiment_draft (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id            INTEGER NOT NULL,
    intelligence_run_id  INTEGER NOT NULL UNIQUE,
    flow_subject_kind    TEXT NOT NULL
                             CHECK (flow_subject_kind IN
                                 ('runtime_flow', 'static_flow')),
    flow_subject_ref     TEXT NOT NULL,
    captured_snapshot_id INTEGER,
    node_keys_json       TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json    TEXT NOT NULL DEFAULT '[]',
    input_digest         TEXT NOT NULL,
    created_at           REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (intelligence_run_id) REFERENCES intelligence_runs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_flow_experiment_draft_system
    ON flow_experiment_draft (system_id, id DESC);

-- =============================================================================
-- Stakeholder Value Network (Epic #418, Issue #420). See
-- docs/stakeholder-value-network.md for the full contract; this comment
-- block only orients a reader of the schema itself.
--
-- Four new canonical entities (Stakeholder / Stakeholder Need / Environment
-- Observation / Value Exchange, §1) plus the reference/evidence/decision/
-- view-preference tables that operate on them. Everything upstream
-- (Purpose/Capability/Journey/Requirement/Outcome) and downstream
-- (Flow/Node/Component/Cell) is READ ONLY through `stakeholder_ref`, never
-- copied -- the same `_LINK_KIND_TARGET_SOURCE` discipline `node_design.py`
-- and the UX Design Lineage section above already use.
--
-- Recurring house rules (not repeated per table, same as the UX Design
-- Lineage banner above):
--   * `system_id INTEGER NOT NULL` + `FOREIGN KEY ... REFERENCES systems (id)
--     ON DELETE CASCADE` on every table, including bridge/link tables.
--   * Every finite-vocabulary column carries `CHECK (col IN (...))` mirroring
--     the `Literal` alias of the same name in `app/models.py` byte-for-byte.
--   * Append-only content: correction is INSERT a new row + set the prior
--     row's `superseded_by_id`, never an UPDATE of meaning-bearing columns.
--   * No table stores a rendered/derived STATUS (`design_status`,
--     `recheck_state`, `validity_state`, `relation_status`, ...) -- those
--     are folded at read time from the append-only decision/reference rows
--     (`app/stakeholder_network.py`), the same discipline #337/#338/#349
--     use for Node maturity and Interview workflow state.
--   * No `money` exchange has an amount/currency column anywhere (§11) --
--     the absence is a structural guarantee, mirroring how #397 leaves out
--     a composite score column to make compositing physically impossible.
--   * No coordinate or layout column anywhere (§12 / invariant 10).
-- =============================================================================

-- stakeholder: identity row for one party. Identity is `(system_id,
-- stakeholder_key)`, a DEVELOPER-SUPPLIED stable slug -- never derived from
-- a row id (a rebuild would renumber it) and never from `display_name` (a
-- rename would sever the history), the same rule Evolution Node ADR-2 and
-- #405 already apply one layer over. `current_revision_id` is written only
-- inside the same transaction that inserts the revision it points at, never
-- by a bare UPDATE elsewhere (`evolution_node.current_version_id`'s
-- discipline).
CREATE TABLE IF NOT EXISTS stakeholder (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    stakeholder_key     TEXT NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'stakeholder-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id) REFERENCES stakeholder_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, stakeholder_key)
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_system
    ON stakeholder (system_id, id DESC);

-- stakeholder_revision: append-only content (§3.1's digest table: `stakeholder_
-- key, display_name, stakeholder_kind, description, context_note`).
-- `created_by` / `created_at` / `revision_number` / `change_note` are
-- deliberately excluded from the digest -- a recheck must fire on a MEANING
-- change, never on the mere existence of a new record (#308/#337/#405's
-- rule). Role assignments are deliberately NOT part of this digest (§3.1) --
-- adding a role in one Journey must not invalidate a confirmation of WHO
-- this party is; role assignments carry their own `captured_digest` and
-- their own decision rows instead.
CREATE TABLE IF NOT EXISTS stakeholder_revision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stakeholder_id      INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    revision_number     INTEGER NOT NULL,
    display_name        TEXT NOT NULL DEFAULT '',
    stakeholder_kind    TEXT NOT NULL DEFAULT 'other'
                            CHECK (stakeholder_kind IN
                                ('end_user', 'customer_organization', 'internal_operator',
                                 'provider_team', 'partner', 'regulator', 'other')),
    description         TEXT NOT NULL DEFAULT '',
    context_note         TEXT NOT NULL DEFAULT '',
    content_digest       TEXT NOT NULL,
    authored_by_kind     TEXT NOT NULL DEFAULT 'developer'
                             CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method      TEXT NOT NULL DEFAULT 'manual'
                             CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id  INTEGER,
    change_note          TEXT NOT NULL DEFAULT '',
    created_by           TEXT,
    created_at           REAL NOT NULL,
    superseded_by_id     INTEGER,
    schema_version       TEXT NOT NULL DEFAULT 'stakeholder-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholder (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_revision (id) ON DELETE SET NULL,
    UNIQUE (stakeholder_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_revision_stakeholder
    ON stakeholder_revision (stakeholder_id, revision_number DESC);

-- stakeholder_role_assignment: what a party DOES in one scope (§1.1),
-- deliberately separate from the party's own identity/kind -- one
-- Stakeholder is routinely a `beneficiary` in one Journey Step and a
-- `payer` in another. `scope_ref` always carries its own prefix (the same
-- convention `execution_mode_assignment.scope_ref` uses one layer over).
-- `captured_digest` is the Stakeholder's OWN `content_digest` at assignment
-- time -- never the assignment's content, since the assignment has none of
-- its own beyond the four identity columns -- so a later Stakeholder
-- revision can degrade this row's read-time `recheck_state` to `stale`
-- (§4's propagation table) without mutating the row itself.
CREATE TABLE IF NOT EXISTS stakeholder_role_assignment (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    stakeholder_id    INTEGER NOT NULL,
    stakeholder_key   TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN
                          ('actor', 'beneficiary', 'payer', 'operator',
                           'approver', 'supplier', 'regulator', 'observer')),
    scope_kind        TEXT NOT NULL DEFAULT 'system'
                          CHECK (scope_kind IN ('system', 'journey', 'journey_step', 'value_exchange')),
    scope_ref         TEXT NOT NULL DEFAULT '',
    captured_digest   TEXT NOT NULL DEFAULT '',
    note              TEXT NOT NULL DEFAULT '',
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholder (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_role_assignment (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_role_assignment_stakeholder
    ON stakeholder_role_assignment (system_id, stakeholder_id, id DESC);

-- stakeholder_need: identity row for one Need/Problem/Constraint/Expectation
-- (§1.2) -- ONE table with a finite `need_kind` on the revision, not four
-- tables. `(system_id, need_key)` UNIQUE, the same developer-supplied-slug
-- identity rule as `stakeholder`.
CREATE TABLE IF NOT EXISTS stakeholder_need (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    need_key            TEXT NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'stakeholder-need-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id) REFERENCES stakeholder_need_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, need_key)
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_need_system
    ON stakeholder_need (system_id, id DESC);

-- stakeholder_need_revision: append-only content (§3.1's digest table:
-- `need_key, need_kind, statement, rationale, stakeholder_key`).
-- `stakeholder_key` lives on the REVISION (not the identity row) because
-- which party a Need is attributed to is itself part of what the Need
-- MEANS and can be corrected the same way its statement can -- a Need
-- reattributed to a different Stakeholder is a meaning change, not
-- bookkeeping. `beneficiary_problem` free text is never parsed into this
-- table automatically (§1.2/§13) -- only a developer, or a confirmed
-- `reasoning_model`-authored revision, creates one.
CREATE TABLE IF NOT EXISTS stakeholder_need_revision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    need_id             INTEGER NOT NULL,
    system_id           INTEGER NOT NULL,
    revision_number     INTEGER NOT NULL,
    need_kind           TEXT NOT NULL DEFAULT 'unmet_need'
                            CHECK (need_kind IN ('unmet_need', 'problem', 'constraint', 'expectation')),
    statement           TEXT NOT NULL DEFAULT '',
    rationale           TEXT NOT NULL DEFAULT '',
    stakeholder_key     TEXT NOT NULL DEFAULT '',
    content_digest      TEXT NOT NULL,
    authored_by_kind    TEXT NOT NULL DEFAULT 'developer'
                            CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note         TEXT NOT NULL DEFAULT '',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'stakeholder-need-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (need_id) REFERENCES stakeholder_need (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_need_revision (id) ON DELETE SET NULL,
    UNIQUE (need_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_need_revision_need
    ON stakeholder_need_revision (need_id, revision_number DESC);

-- environment_observation: a dated statement about the WORLD OUTSIDE the
-- system (§1.3) -- never a runtime observation (#412's mode observations
-- and `state_facts`' freshness readings are about THIS system's own
-- execution and must never be conflated with this table). Deliberately has
-- NO revision chain and NO `superseded_by_id` (§3): an observation is a
-- statement about a moment, and a correction is a NEW row that may declare
-- `supersedes_observation_key` -- the original is never edited or deleted,
-- the same append-only-facts reasoning #329 applies to Joint Understanding
-- findings. `observation_confidence` is a finite provenance value, never a
-- percentage (invariant 7).
CREATE TABLE IF NOT EXISTS environment_observation (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    observation_key             TEXT NOT NULL,
    statement                   TEXT NOT NULL DEFAULT '',
    source_note                 TEXT NOT NULL DEFAULT '',
    observation_confidence      TEXT NOT NULL DEFAULT 'reported'
                                    CHECK (observation_confidence IN ('observed', 'reported', 'assumed')),
    observed_at                 REAL,
    supersedes_observation_key  TEXT,
    content_digest              TEXT NOT NULL,
    authored_by_kind            TEXT NOT NULL DEFAULT 'developer'
                                    CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method             TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id         INTEGER,
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    schema_version              TEXT NOT NULL DEFAULT 'environment-observation-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (system_id, observation_key)
);

CREATE INDEX IF NOT EXISTS idx_environment_observation_system
    ON environment_observation (system_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_environment_observation_supersedes
    ON environment_observation (system_id, supersedes_observation_key);

-- environment_observation_impact: what one Observation DOES to which
-- subject (§1.3) -- `impact_kind` is a finite verb, never a signed number
-- (invariant 7). `target_ref_kind` reuses `StakeholderRefKind` (§5.2 lists
-- the identical target set: Stakeholder / Need / Purpose element / Journey
-- / Requirement / Value Exchange); resolving it against each kind's single
-- canonical source is Issue #421's (`_resolve_target`'s explicit seam) --
-- this table only PERSISTS the reference plus whatever digest that seam
-- could capture at write time. Never a copy of the target's content
-- (invariant 2).
CREATE TABLE IF NOT EXISTS environment_observation_impact (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    observation_id    INTEGER NOT NULL,
    impact_kind       TEXT NOT NULL CHECK (impact_kind IN
                          ('creates', 'worsens', 'relieves', 'invalidates', 'constrains')),
    target_ref_kind   TEXT NOT NULL CHECK (target_ref_kind IN
                          ('purpose_element', 'purpose_relation', 'capability_entity',
                           'ux_journey', 'ux_journey_step', 'ux_requirement',
                           'purpose_outcome_criterion', 'stakeholder', 'stakeholder_need',
                           'value_exchange')),
    target_ref        TEXT NOT NULL,
    target_row_id     INTEGER,
    captured_digest   TEXT NOT NULL DEFAULT '',
    note              TEXT NOT NULL DEFAULT '',
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by        TEXT,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES environment_observation (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_environment_observation_impact_observation
    ON environment_observation_impact (system_id, observation_id, id DESC);

-- value_exchange: identity row for one directional `provider -> receiver`
-- edge (§1.4). `(system_id, exchange_key)` UNIQUE, the same
-- developer-supplied-slug rule as `stakeholder` / `stakeholder_need`.
CREATE TABLE IF NOT EXISTS value_exchange (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    exchange_key         TEXT NOT NULL,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'value-exchange-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id) REFERENCES value_exchange_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, exchange_key)
);

CREATE INDEX IF NOT EXISTS idx_value_exchange_system
    ON value_exchange (system_id, id DESC);

-- value_exchange_revision: append-only content, every §1.4 field. NO
-- amount/currency column exists anywhere on this table (§11/invariant 7) --
-- a `money` Exchange records only that a flow exists, its direction, and a
-- free-text `value_statement`; the absence of an amount column is
-- structural, not a review convention, mirroring how #397 leaves out a
-- composite score column. `consideration_kind` reuses the `ValueExchangeKind`
-- vocabulary (§1.4's own rule) and is nullable -- required only when
-- `consideration_state = 'present'`, enforced at the SERVICE layer
-- (`exchange_consideration_incomplete`) rather than by a CHECK that cannot
-- express a conditional requirement between two columns.
CREATE TABLE IF NOT EXISTS value_exchange_revision (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id                 INTEGER NOT NULL,
    system_id                   INTEGER NOT NULL,
    revision_number             INTEGER NOT NULL,
    provider_stakeholder_key    TEXT NOT NULL,
    receiver_stakeholder_key    TEXT NOT NULL,
    exchange_kind               TEXT NOT NULL CHECK (exchange_kind IN
                                    ('experience', 'service', 'information', 'money',
                                     'authority', 'obligation', 'risk')),
    value_statement              TEXT NOT NULL DEFAULT '',
    consideration_state         TEXT NOT NULL DEFAULT 'unknown'
                                    CHECK (consideration_state IN ('present', 'none', 'unknown')),
    consideration_kind          TEXT
                                    CHECK (consideration_kind IS NULL OR consideration_kind IN
                                        ('experience', 'service', 'information', 'money',
                                         'authority', 'obligation', 'risk')),
    consideration_statement     TEXT NOT NULL DEFAULT '',
    channel                     TEXT NOT NULL DEFAULT '',
    trigger                     TEXT NOT NULL DEFAULT '',
    cadence                     TEXT NOT NULL DEFAULT 'unknown'
                                    CHECK (cadence IN
                                        ('one_time', 'recurring', 'continuous', 'on_demand', 'unknown')),
    valid_from                  REAL,
    valid_to                    REAL,
    content_digest              TEXT NOT NULL,
    authored_by_kind            TEXT NOT NULL DEFAULT 'developer'
                                    CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method             TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id         INTEGER,
    change_note                 TEXT NOT NULL DEFAULT '',
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    superseded_by_id            INTEGER,
    schema_version              TEXT NOT NULL DEFAULT 'value-exchange-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (exchange_id) REFERENCES value_exchange (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES value_exchange_revision (id) ON DELETE SET NULL,
    UNIQUE (exchange_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_value_exchange_revision_exchange
    ON value_exchange_revision (exchange_id, revision_number DESC);

-- stakeholder_ref: THE single reference table for this layer (§5.1) --
-- `(source_kind, source_key)` names a Stakeholder / Need / Observation /
-- Exchange / Journey-Step row this layer or #405 already owns;
-- `(ref_kind, target_ref)` names exactly ONE canonical source to resolve
-- against at READ time (§5.1's table) -- never a copy of the target's
-- content (invariant 2). A `ref_kind` outside `StakeholderRefKind` never
-- reaches this table (422 `stakeholder_ref_kind_invalid`, enforced at the
-- service layer before the INSERT). `relation_status` is the fixed
-- translation of `decision_method`, never a second stored column (§5.1).
CREATE TABLE IF NOT EXISTS stakeholder_ref (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    source_kind       TEXT NOT NULL CHECK (source_kind IN
                          ('stakeholder', 'stakeholder_need', 'environment_observation',
                           'value_exchange', 'journey_step')),
    source_key        TEXT NOT NULL,
    ref_kind          TEXT NOT NULL CHECK (ref_kind IN
                          ('purpose_element', 'purpose_relation', 'capability_entity',
                           'ux_journey', 'ux_journey_step', 'ux_requirement',
                           'purpose_outcome_criterion', 'stakeholder', 'stakeholder_need',
                           'value_exchange')),
    target_ref        TEXT NOT NULL,
    target_row_id     INTEGER,
    captured_digest   TEXT NOT NULL DEFAULT '',
    note              TEXT NOT NULL DEFAULT '',
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_ref (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_ref_source
    ON stakeholder_ref (system_id, source_kind, source_key, id DESC);

-- stakeholder_evidence_ref: attaches evidence to a Need / Observation /
-- Exchange (§6). Three provenances kept apart on purpose (`evidence_kind`)
-- -- a human interview note, a telemetry reading, and a third-party
-- analytics figure support a claim differently and must not read as one
-- number (the same reason #328 keeps investigation/translation/developer
-- findings apart). Append-only: `superseded_by_id` lets `evidence_state`
-- read "at least one NON-SUPERSEDED row resolves" (§6) without ever
-- mutating a prior evidence row. A `runtime_observation` row here never
-- changes any `design_status` and never marks an Exchange delivered
-- (invariant 8).
CREATE TABLE IF NOT EXISTS stakeholder_evidence_ref (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    subject_kind      TEXT NOT NULL CHECK (subject_kind IN
                          ('stakeholder_need', 'environment_observation', 'value_exchange')),
    subject_key       TEXT NOT NULL,
    evidence_kind     TEXT NOT NULL CHECK (evidence_kind IN
                          ('human_report', 'document', 'runtime_observation', 'external_analytics')),
    evidence_ref      TEXT NOT NULL DEFAULT '',
    statement         TEXT NOT NULL DEFAULT '',
    captured_digest   TEXT NOT NULL DEFAULT '',
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_evidence_ref (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_evidence_ref_subject
    ON stakeholder_evidence_ref (system_id, subject_kind, subject_key, id DESC);

-- stakeholder_decision: the ONE decision ledger for this layer's confirm /
-- reject / retire / reinstate lifecycle (`StakeholderDecisionKind`),
-- covering every `StakeholderSubjectKind`. DELIBERATELY has no `status`
-- column anywhere in this table or on `stakeholder` / `stakeholder_need` /
-- `value_exchange` -- `design_status` is derived at read time from the
-- latest non-superseded row here, the identical discipline
-- `ux_design_decision` uses one layer over. `decision_method` is CHECKed to
-- the single literal `'manual'` -- a REFERENCE or role assignment may be
-- machine-proposed, but a CONFIRM/REJECT/RETIRE/REINSTATE decision about it
-- can only ever be a human's (invariant 9).
CREATE TABLE IF NOT EXISTS stakeholder_decision (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    subject_kind          TEXT NOT NULL CHECK (subject_kind IN
                              ('stakeholder', 'stakeholder_need', 'environment_observation',
                               'value_exchange', 'stakeholder_ref', 'stakeholder_role_assignment')),
    subject_key           TEXT NOT NULL,
    subject_row_id        INTEGER,
    decision              TEXT NOT NULL CHECK (decision IN
                              ('confirm', 'reject', 'retire', 'reinstate')),
    rationale             TEXT NOT NULL DEFAULT '',
    captured_digest       TEXT NOT NULL DEFAULT '',
    captured_revision_id  INTEGER,
    decision_method       TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    decided_by            TEXT,
    superseded_by_id      INTEGER,
    created_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES stakeholder_decision (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_decision_subject
    ON stakeholder_decision (system_id, subject_kind, subject_key, id DESC);

-- stakeholder_view_preference: §12's DISPLAY SETTINGS ONLY -- filters,
-- collapsed refs, pinned refs, the active view. NO coordinate column, NO
-- layout column exists anywhere on this table (invariant 10); no
-- projection in this Epic reads this table as a fact. One row per
-- `(system_id, created_by)`: unlike every other table in this section this
-- is a per-viewer convenience setting, not an append-only fact, so it is
-- the one table here a plain UPDATE is appropriate for.
CREATE TABLE IF NOT EXISTS stakeholder_view_preference (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id             INTEGER NOT NULL,
    created_by            TEXT NOT NULL,
    active_view           TEXT NOT NULL DEFAULT '',
    filters_json          TEXT NOT NULL DEFAULT '{}',
    collapsed_refs_json   TEXT NOT NULL DEFAULT '[]',
    pinned_refs_json      TEXT NOT NULL DEFAULT '[]',
    updated_at            REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    UNIQUE (system_id, created_by)
);

CREATE INDEX IF NOT EXISTS idx_stakeholder_view_preference_system
    ON stakeholder_view_preference (system_id, created_by);

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


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    existing_columns: set,
    column: str,
    definition: str,
) -> None:
    """Additive ALTER TABLE for one nullable column, skipped when present.

    Only for columns that are legitimately NULL on existing rows: it never
    backfills, so it must not be used for NOT NULL/DEFAULT migrations.
    """
    if column in existing_columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    existing_columns.add(column)


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


def _migrate_intelligence_runs_snapshot_nullable(conn: sqlite3.Connection) -> None:
    """Relax intelligence_runs.snapshot_id to nullable on pre-existing DBs
    (Issue #149). Fresh DBs already get the nullable column from SCHEMA.

    Uses the safe SQLite table-rebuild with legacy_alter_table so child FK
    references to intelligence_runs are not rewritten during the rename.
    """
    info = conn.execute("PRAGMA table_info(intelligence_runs)").fetchall()
    snap = next((c for c in info if c["name"] == "snapshot_id"), None)
    if snap is None or snap["notnull"] == 0:
        return  # already nullable (or table missing)

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute("ALTER TABLE intelligence_runs RENAME TO _intelligence_runs_old")
        conn.execute(
            """
            CREATE TABLE intelligence_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id       INTEGER NOT NULL,
                snapshot_id     INTEGER,
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO intelligence_runs
                (id, system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            SELECT id, system_id, snapshot_id, run_type, provider, model,
                   prompt_version, schema_version, decision_method, status,
                   error_details, is_mock, started_at, completed_at
            FROM _intelligence_runs_old
            """
        )
        conn.execute("DROP TABLE _intelligence_runs_old")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_intelligence_runs_system "
            "ON intelligence_runs (system_id, id DESC)"
        )
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_alignment_manual_recheck_targets(conn: sqlite3.Connection) -> None:
    """Replace Issue #310's globally hash-keyed target table.

    The original shape collapsed identical content hashes across interview
    sessions and did not retain the exact reviewed policy rule or the human
    actor. Existing pending targets are expanded to every currently matching
    item; unmatched legacy targets cannot be attributed to a session safely
    and are deliberately not guessed.
    """
    columns = _columns(conn, "alignment_manual_recheck_target")
    if not columns:
        return
    if "alignment_item_id" in columns:
        # SCHEMA uses an old-shape-compatible bootstrap index so startup can
        # reach this migration even if a legacy database lost its old index.
        # Once the new columns are known to exist, install the selective form.
        conn.execute("DROP INDEX IF EXISTS idx_alignment_manual_recheck_target_pending")
        conn.execute(
            """
            CREATE INDEX idx_alignment_manual_recheck_target_pending
            ON alignment_manual_recheck_target
               (system_id, policy_version, policy_digest, policy_rule_id,
                status, session_id)
            """
        )
        return

    conn.execute("DROP TABLE IF EXISTS _alignment_manual_recheck_target_new")
    conn.execute(
        """
        CREATE TABLE _alignment_manual_recheck_target_new (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id            INTEGER NOT NULL,
            session_id           INTEGER NOT NULL,
            alignment_item_id    INTEGER UNIQUE,
            reason_code          TEXT NOT NULL,
            policy_version       TEXT NOT NULL,
            policy_digest        TEXT NOT NULL DEFAULT '',
            policy_rule_id       TEXT NOT NULL,
            content_hash         TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'pending',
            decision_method      TEXT NOT NULL DEFAULT 'manual',
            requested_by_user_id INTEGER,
            created_at           REAL NOT NULL,
            resolved_at          REAL,
            FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES interview_session (id) ON DELETE CASCADE,
            FOREIGN KEY (alignment_item_id)
                REFERENCES alignment_item (id) ON DELETE SET NULL,
            FOREIGN KEY (requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO _alignment_manual_recheck_target_new
            (system_id, session_id, alignment_item_id, reason_code,
             policy_version, policy_digest, policy_rule_id, content_hash,
             status, decision_method, requested_by_user_id, created_at, resolved_at)
        SELECT old.system_id, item.session_id, item.id, old.reason_code,
               item.policy_version, COALESCE(item.policy_digest, ''),
               COALESCE(item.policy_rule_id, 'legacy-unknown'), old.content_hash,
               old.status, 'manual', NULL, old.created_at, old.resolved_at
        FROM alignment_manual_recheck_target old
        JOIN alignment_item item
          ON item.system_id = old.system_id
         AND item.reason_code = old.reason_code
         AND item.content_hash = old.content_hash
        """
    )
    conn.execute("DROP TABLE alignment_manual_recheck_target")
    conn.execute(
        "ALTER TABLE _alignment_manual_recheck_target_new "
        "RENAME TO alignment_manual_recheck_target"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alignment_manual_recheck_target_pending
        ON alignment_manual_recheck_target
           (system_id, policy_version, policy_digest, policy_rule_id, status, session_id)
        """
    )


def _migrate_cell_improvement_event_types(conn: sqlite3.Connection) -> None:
    """Add ``approvals_invalidated`` to the SQLite CHECK constraint.

    SQLite cannot ALTER a CHECK constraint. Rebuild the append-only event
    table so databases created by an earlier Cell Fabric branch remain
    writable after upgrade.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'cell_improvement_events'"
    ).fetchone()
    if row is None or "approvals_invalidated" in (row["sql"] or ""):
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            "ALTER TABLE cell_improvement_events "
            "RENAME TO _old_cell_improvement_events"
        )
        conn.execute(
            """CREATE TABLE cell_improvement_events (
                   id                INTEGER PRIMARY KEY AUTOINCREMENT,
                   system_id         INTEGER NOT NULL,
                   improvement_id    INTEGER NOT NULL,
                   event_type        TEXT NOT NULL CHECK (event_type IN (
                       'created', 'status_transition', 'parent_approval',
                       'human_approval', 'approvals_invalidated',
                       'shadow_proposed', 'live_shadow_approval_requested',
                       'live_shadow_approved', 'suspended', 'resumed',
                       'rolled_back'
                   )),
                   from_status       TEXT,
                   to_status         TEXT,
                   actor             TEXT,
                   detail            TEXT NOT NULL DEFAULT '',
                   created_at        REAL NOT NULL,
                   FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
                   FOREIGN KEY (improvement_id)
                       REFERENCES cell_improvements (id) ON DELETE CASCADE
               )"""
        )
        conn.execute(
            """INSERT INTO cell_improvement_events
                   (id, system_id, improvement_id, event_type, from_status,
                    to_status, actor, detail, created_at)
               SELECT id, system_id, improvement_id, event_type, from_status,
                      to_status, actor, detail, created_at
               FROM _old_cell_improvement_events"""
        )
        conn.execute("DROP TABLE _old_cell_improvement_events")
        conn.execute(
            """CREATE INDEX idx_cell_improvement_events_improvement
               ON cell_improvement_events
                  (system_id, improvement_id, id ASC)"""
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_cell_ask_decision_note(conn: sqlite3.Connection) -> None:
    """Separate legacy Ask decision notes from the finite decision value.

    Older builds wrote the free-form note into ``decision``. Adding the new
    column without a backfill would leave existing accepted/held/rejected
    rows semantically malformed.
    """
    columns = _columns(conn, "cell_asks")
    if not columns or "decision_note" in columns:
        return
    conn.execute(
        "ALTER TABLE cell_asks "
        "ADD COLUMN decision_note TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        """UPDATE cell_asks
           SET decision_note = decision, decision = status
           WHERE status IN ('accepted', 'held', 'rejected')"""
    )


def _migrate_solution_design_option_unique(conn: sqlite3.Connection) -> None:
    """Scope the option_key uniqueness to the CURRENT row.

    The table shipped its first form with a table-level
    ``UNIQUE (solution_design_id, option_key)``, which contradicts the
    append-only rule it is built on: correcting an option inserts a new row
    and supersedes the old one, so the insert collided with the row it was
    replacing and no correction could ever be recorded.
    ``CREATE TABLE IF NOT EXISTS`` cannot repair that on a database created
    from the earlier form, and SQLite cannot drop a table constraint in
    place, so the table is rebuilt once, preserving every existing row.

    Detection is the implicit index SQLite creates for a table-level UNIQUE
    (``origin == 'u'``); the replacement partial index reports ``origin ==
    'c'``, so this runs exactly once and is a no-op afterwards.
    """
    if not _columns(conn, "solution_design_option"):
        return
    origins = {
        row["origin"]
        for row in conn.execute("PRAGMA index_list(solution_design_option)")
    }
    if "u" not in origins:
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        ALTER TABLE solution_design_option RENAME TO solution_design_option_legacy;
        -- A rename carries the table's indexes with it, so their NAMES are still
        -- taken and the DDL's `CREATE INDEX IF NOT EXISTS` below would silently
        -- do nothing -- leaving the rebuilt table with no constraint at all,
        -- which is worse than the constraint being wrong. Free the names first.
        DROP INDEX IF EXISTS ux_solution_design_option_current;
        DROP INDEX IF EXISTS idx_solution_design_option_design;
        """
    )
    conn.executescript(_SOLUTION_DESIGN_OPTION_DDL)
    conn.execute(
        """INSERT INTO solution_design_option
           SELECT * FROM solution_design_option_legacy"""
    )
    conn.executescript(
        """
        DROP TABLE solution_design_option_legacy;
        PRAGMA foreign_keys = ON;
        """
    )


def init_db() -> None:
    with get_conn() as conn:
        _migrate_to_system_scope(conn)
        conn.executescript(SCHEMA)
        _migrate_solution_design_option_unique(conn)
        _migrate_intelligence_runs_snapshot_nullable(conn)
        install_intelligence_run_type_guards(conn)
        _migrate_cell_improvement_event_types(conn)
        ta_cols = _columns(conn, "trace_analyzers")
        if "reviewed_at" not in ta_cols:
            conn.execute("ALTER TABLE trace_analyzers ADD COLUMN reviewed_at REAL")
        if "review_decision_method" not in ta_cols:
            conn.execute(
                "ALTER TABLE trace_analyzers ADD COLUMN review_decision_method TEXT"
            )
        if "content" not in _columns(conn, "snapshot_files"):
            conn.execute(
                "ALTER TABLE snapshot_files ADD COLUMN content BLOB NOT NULL DEFAULT X''"
            )
        sf_cols = _columns(conn, "snapshot_files")
        if "inclusion_status" not in sf_cols:
            conn.execute(
                "ALTER TABLE snapshot_files "
                "ADD COLUMN inclusion_status TEXT NOT NULL DEFAULT 'indexed'"
            )
        if "exclusion_reason" not in sf_cols:
            conn.execute(
                "ALTER TABLE snapshot_files "
                "ADD COLUMN exclusion_reason TEXT NOT NULL DEFAULT ''"
            )
        snap_cols = _columns(conn, "repository_snapshots")
        if "indexed_size" not in snap_cols:
            conn.execute(
                "ALTER TABLE repository_snapshots "
                "ADD COLUMN indexed_size INTEGER NOT NULL DEFAULT 0"
            )
        if "metadata_only_count" not in snap_cols:
            conn.execute(
                "ALTER TABLE repository_snapshots "
                "ADD COLUMN metadata_only_count INTEGER NOT NULL DEFAULT 0"
            )
        if "warnings" not in snap_cols:
            conn.execute(
                "ALTER TABLE repository_snapshots "
                "ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]'"
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
        code_symbol_cols = _columns(conn, "code_symbols")
        if "symbol_source_hash" not in code_symbol_cols:
            conn.execute("ALTER TABLE code_symbols ADD COLUMN symbol_source_hash TEXT")
        if "symbol_body_hash" not in code_symbol_cols:
            conn.execute("ALTER TABLE code_symbols ADD COLUMN symbol_body_hash TEXT")
        if "explanation_hash" not in _columns(conn, "symbol_source_metadata"):
            conn.execute(
                "ALTER TABLE symbol_source_metadata ADD COLUMN explanation_hash TEXT"
            )
        if "chunk_start_line" not in _columns(conn, "system_understanding_llm_tasks"):
            conn.execute(
                "ALTER TABLE system_understanding_llm_tasks "
                "ADD COLUMN chunk_start_line INTEGER"
            )
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
        if "apply_status" not in patch_columns:
            conn.execute(
                "ALTER TABLE probe_patches "
                "ADD COLUMN apply_status TEXT NOT NULL DEFAULT 'not_applied'"
            )
        if "apply_error" not in patch_columns:
            conn.execute("ALTER TABLE probe_patches ADD COLUMN apply_error TEXT")
        if "applied_at" not in patch_columns:
            conn.execute("ALTER TABLE probe_patches ADD COLUMN applied_at REAL")
        if "applied_by_user_id" not in patch_columns:
            conn.execute(
                "ALTER TABLE probe_patches ADD COLUMN applied_by_user_id INTEGER"
            )
        experiment_columns = _columns(conn, "experiments")
        if "human_decision_variant_key" not in experiment_columns:
            conn.execute(
                "ALTER TABLE experiments "
                "ADD COLUMN human_decision_variant_key TEXT"
            )
        _migrate_cell_ask_decision_note(conn)
        entrypoint_columns = _columns(conn, "code_entrypoints")
        if "source" not in entrypoint_columns:
            conn.execute(
                "ALTER TABLE code_entrypoints "
                "ADD COLUMN source TEXT NOT NULL DEFAULT 'deterministic'"
            )
        if "pattern_id" not in entrypoint_columns:
            conn.execute(
                "ALTER TABLE code_entrypoints ADD COLUMN pattern_id INTEGER"
            )
        session_cols = _columns(conn, "interview_session")
        if "stage" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN stage TEXT NOT NULL DEFAULT 'understanding_initialized'"
            )
        if "current_understanding" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN current_understanding TEXT"
            )
        if "gap_analysis" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN gap_analysis TEXT"
            )
        if "open_questions" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN open_questions TEXT"
            )
        if "user_intent" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN user_intent TEXT"
            )
        session_cols = _columns(conn, "interview_session")
        if "materialization_diff" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN materialization_diff TEXT"
            )
        if "materialization_ref" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN materialization_ref TEXT"
            )
        if "materialized_at" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN materialized_at REAL"
            )
        session_cols = _columns(conn, "interview_session")
        if "last_error" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN last_error TEXT"
            )
        if "understanding_confirmed_at" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN understanding_confirmed_at REAL"
            )
        if "understanding_confirmed_by" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN understanding_confirmed_by TEXT"
            )
        if "answers_revised_at" not in session_cols:
            # Issue #129: set when an interview_qa answer is corrected; cleared
            # when the developer rebuilds the understanding. Drives the
            # dashboard's "rebuild recommended" banner; never auto-cleared by
            # a revision itself (Principle 8: no automatic re-adoption).
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN answers_revised_at REAL"
            )
        if "understanding_rebuilt_at" not in session_cols:
            # Review-finding fix: last-successful-rebuild watermark for the
            # `_understanding_update_blocked` gate. Without this, the
            # interview_qa "new answer since confirmation" check always
            # compared against the original `understanding_confirmed_at`,
            # which never advances, so once a single Q&A row was
            # created/answered after confirmation the gate stayed open
            # forever even after a successful rebuild had already consumed
            # that answer. Set only on a SUCCESSFUL
            # `update_interview_understanding` rebuild; left NULL on existing
            # rows and on failed rebuilds so the gate keeps its current
            # (open) behavior until the next successful rebuild.
            conn.execute(
                "ALTER TABLE interview_session ADD COLUMN understanding_rebuilt_at REAL"
            )
        proposal_cols = _columns(conn, "interview_proposal")
        if proposal_cols and "graph_node_id" not in proposal_cols:
            conn.execute("ALTER TABLE interview_proposal ADD COLUMN graph_node_id TEXT")
            conn.execute("ALTER TABLE interview_proposal ADD COLUMN capability_name TEXT")
            conn.execute("ALTER TABLE interview_proposal ADD COLUMN evidence_summary TEXT")
            conn.execute("ALTER TABLE interview_proposal ADD COLUMN proposal_confidence REAL")
        plan_cols = _columns(conn, "probe_plans")
        if plan_cols and "origin" not in plan_cols:
            conn.execute(
                "ALTER TABLE probe_plans ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'"
            )
        graph_cols = _columns(conn, "understanding_graph_snapshots")
        if graph_cols and "snapshot_id" not in graph_cols:
            conn.execute(
                "ALTER TABLE understanding_graph_snapshots ADD COLUMN snapshot_id INTEGER"
            )
        build_cols = _columns(conn, "system_understanding_builds")
        if "cancel_requested" not in build_cols:
            conn.execute(
                "ALTER TABLE system_understanding_builds "
                "ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        if "heartbeat_at" not in build_cols:
            conn.execute(
                "ALTER TABLE system_understanding_builds ADD COLUMN heartbeat_at REAL"
            )
        # Builds left 'queued'/'running' by a previous process can never make
        # progress after a restart (their worker thread is gone). Mark them
        # failed with an explicit reason so they surface as retryable instead
        # of appearing active forever.
        conn.execute(
            """UPDATE system_understanding_builds
               SET status = 'failed',
                   error = COALESCE(error, 'Interrupted by server restart'),
                   completed_at = COALESCE(completed_at, ?)
               WHERE status IN ('queued', 'running')""",
            (time.time(),),
        )
        conn.execute(
            """UPDATE system_understanding_build_steps
               SET status = 'failed',
                   error = COALESCE(error, 'Interrupted by server restart'),
                   completed_at = COALESCE(completed_at, ?)
               WHERE status = 'running'""",
            (time.time(),),
        )
        conn.execute(
            """UPDATE system_understanding_llm_tasks
               SET status = 'failed',
                   last_error = COALESCE(last_error, 'Interrupted by server restart'),
                   completed_at = COALESCE(completed_at, ?)
               WHERE status = 'running'""",
            (time.time(),),
        )
        conn.execute(
            """UPDATE system_understanding_build_runs
               SET status = 'failed', completed_at = COALESCE(completed_at, ?)
               WHERE status = 'running'""",
            (time.time(),),
        )
        # Repair rows written with the out-of-contract 'success' status; the
        # shared schema only allows 'pending' / 'completed' / 'failed'.
        conn.execute(
            "UPDATE intelligence_runs SET status = 'completed' WHERE status = 'success'"
        )
        qa_cols = _columns(conn, "interview_qa")
        if qa_cols and "answer_unknown" not in qa_cols:
            # Existing answered/unconfirmed rows can be classified
            # deterministically because Issue #142 exclusively used
            # unconfirmed for answer_unknown. Revised rows have lost that
            # distinction, so they deliberately remain NULL/unmeasured.
            conn.execute("ALTER TABLE interview_qa ADD COLUMN answer_unknown INTEGER")
            conn.execute(
                "UPDATE interview_qa SET answer_unknown = 0 WHERE status = 'answered'"
            )
            conn.execute(
                "UPDATE interview_qa SET answer_unknown = 1 WHERE status = 'unconfirmed'"
            )
        if qa_cols and "runtime_evidence" not in qa_cols:
            # Issue #135: raw trace-aggregate + metadata-provenance JSON for
            # question_source = 'runtime' rows; existing rows stay NULL.
            conn.execute("ALTER TABLE interview_qa ADD COLUMN runtime_evidence TEXT")
        if qa_cols and "route_category" not in qa_cols:
            # Issue #286: Question Router classification, set only via
            # POST /interview/qa/{qa_id}/route (never automatic for
            # dialogue-turn questions); existing rows stay NULL (unrouted).
            conn.execute("ALTER TABLE interview_qa ADD COLUMN route_category TEXT")
        if qa_cols and "route_run_id" not in qa_cols:
            conn.execute(
                "ALTER TABLE interview_qa ADD COLUMN route_run_id INTEGER "
                "REFERENCES intelligence_runs(id) ON DELETE SET NULL"
            )
        intelligence_run_cols = _columns(conn, "intelligence_runs")
        if intelligence_run_cols and "budget_files_read" not in intelligence_run_cols:
            # Issue #286: read-only Investigation Agent budget accounting,
            # populated only for run_type='investigation' rows; every other
            # run_type (and pre-migration rows) stays NULL.
            conn.execute("ALTER TABLE intelligence_runs ADD COLUMN budget_files_read INTEGER")
        if intelligence_run_cols and "budget_chars_read" not in intelligence_run_cols:
            conn.execute("ALTER TABLE intelligence_runs ADD COLUMN budget_chars_read INTEGER")
        if intelligence_run_cols and "budget_llm_calls" not in intelligence_run_cols:
            conn.execute("ALTER TABLE intelligence_runs ADD COLUMN budget_llm_calls INTEGER")
        if intelligence_run_cols and "budget_elapsed_seconds" not in intelligence_run_cols:
            conn.execute("ALTER TABLE intelligence_runs ADD COLUMN budget_elapsed_seconds REAL")
        github_conn_cols = _columns(conn, "github_connections")
        if github_conn_cols and "last_synced_at" not in github_conn_cols:
            # Issue #216 sub-task 2: repo manager sync bookkeeping.
            conn.execute("ALTER TABLE github_connections ADD COLUMN last_synced_at TEXT")
        if github_conn_cols and "last_synced_commit_sha" not in github_conn_cols:
            conn.execute(
                "ALTER TABLE github_connections ADD COLUMN last_synced_commit_sha TEXT"
            )
        publish_job_cols = _columns(conn, "publish_jobs")
        if publish_job_cols and "retry_count" not in publish_job_cols:
            # Issue #226: publish job retry/recovery.
            conn.execute(
                "ALTER TABLE publish_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
            )
        if publish_job_cols and "last_attempt_at" not in publish_job_cols:
            conn.execute("ALTER TABLE publish_jobs ADD COLUMN last_attempt_at REAL")
        # Issue #242 Phase A / #243: replay-capture columns on traces. Existing
        # rows stay NULL (= pre-Phase-A / capture not opted in); no bulk
        # reclassification of old traces.
        trace_cols = _columns(conn, "traces")
        if "input_capture_json" not in trace_cols:
            conn.execute("ALTER TABLE traces ADD COLUMN input_capture_json TEXT")
        if "replayability" not in trace_cols:
            conn.execute("ALTER TABLE traces ADD COLUMN replayability TEXT")
        if "replay_reasons_json" not in trace_cols:
            conn.execute("ALTER TABLE traces ADD COLUMN replay_reasons_json TEXT")
        # Issue #290 Finding 5: environment/git_sha reported by the SDK
        # (PROBE_ENVIRONMENT / PROBE_GIT_SHA), so runtime provenance
        # reflects real deployment metadata instead of always-null /
        # fabricated-from-the-pinned-snapshot values. Existing rows stay
        # NULL (no SDK-side signal at the time they were ingested) -- never
        # backfilled.
        if "environment" not in trace_cols:
            conn.execute("ALTER TABLE traces ADD COLUMN environment TEXT")
        if "git_sha" not in trace_cols:
            conn.execute("ALTER TABLE traces ADD COLUMN git_sha TEXT")
        purpose_confirmation_cols = _columns(conn, "system_purpose_confirmations")
        if (
            purpose_confirmation_cols
            and "understanding_build_id" not in purpose_confirmation_cols
        ):
            # Issue #275: legacy confirmation rows remain NULL and are treated
            # as stale until a human confirms the current completed build.
            conn.execute(
                "ALTER TABLE system_purpose_confirmations "
                "ADD COLUMN understanding_build_id INTEGER "
                "REFERENCES system_understanding_builds(id) ON DELETE SET NULL"
            )
        if purpose_confirmation_cols and "decided_by_user_id" not in purpose_confirmation_cols:
            # Existing manual decisions predate actor attribution and stay
            # NULL; all new API-created confirmations require a real user.
            conn.execute(
                "ALTER TABLE system_purpose_confirmations "
                "ADD COLUMN decided_by_user_id INTEGER "
                "REFERENCES users(id) ON DELETE SET NULL"
            )
        # Issue #290: deterministic Runtime Reality Check match state
        # (match | mismatch | unobserved | stale), set only when an
        # alignment item's evidence deterministically maps to a component_id
        # with runtime trace facts (app/runtime_alignment.py). Existing rows
        # and items with no deterministic mapping stay NULL -- never guessed.
        alignment_item_cols = _columns(conn, "alignment_item")
        if alignment_item_cols and "runtime_check" not in alignment_item_cols:
            conn.execute("ALTER TABLE alignment_item ADD COLUMN runtime_check TEXT")
        # Issue #291: answerable knowledge areas + handoff. Existing sessions
        # default to '[]' (empty = no filtering, matches the pre-#291
        # behavior of showing every question); existing qa/alignment rows
        # stay NULL (unrouted / not handed off).
        session_cols = _columns(conn, "interview_session")
        if session_cols and "answerable_areas" not in session_cols:
            conn.execute(
                "ALTER TABLE interview_session "
                "ADD COLUMN answerable_areas TEXT NOT NULL DEFAULT '[]'"
            )
        qa_cols = _columns(conn, "interview_qa")
        if qa_cols and "knowledge_area" not in qa_cols:
            # Issue #291: assigned only by the question router LLM
            # (app/question_router.py question-router-v2), never inferred
            # deterministically from title/repository info.
            conn.execute("ALTER TABLE interview_qa ADD COLUMN knowledge_area TEXT")
        if qa_cols and "handoff_id" not in qa_cols:
            conn.execute(
                "ALTER TABLE interview_qa ADD COLUMN handoff_id INTEGER "
                "REFERENCES question_handoff(id) ON DELETE SET NULL"
            )
        if alignment_item_cols and "handoff_id" not in alignment_item_cols:
            conn.execute(
                "ALTER TABLE alignment_item ADD COLUMN handoff_id INTEGER "
                "REFERENCES question_handoff(id) ON DELETE SET NULL"
            )
        # Finding 4 (review of Issue #287): distinguishes a terminal
        # (answered/corrected) row that has been superseded by a fresh
        # rebuilt row for the same contrast point from the current row a
        # human should still be able to see as history. Existing rows
        # backfill to 0 (not superseded) -- a rebuild only ever marks a row
        # superseded going forward, never retroactively.
        if alignment_item_cols and "superseded" not in alignment_item_cols:
            conn.execute(
                "ALTER TABLE alignment_item "
                "ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0"
            )
        # Issue #286 review fix (Finding 1): wires Question Router /
        # Investigation Agent into the normal Q&A flow (previously only used
        # inside the Inquiry side-conversation). investigation_json holds the
        # same {status, conclusion, key_points, evidence, uncertainty,
        # confidence, decision_question} shape the Inquiry flow already
        # composes from InvestigationResult; investigation_run_id points at
        # the 'investigation' intelligence_runs row that produced it. Both
        # stay NULL until POST .../qa/route-and-investigate successfully
        # investigates a system_researchable/hybrid question -- a failed
        # investigation leaves them NULL (audit-only failed run), and
        # human_only questions never get one at all.
        # Issue #332: a database created at Issue #329 has the Joint
        # Understanding tables without the outcome-basis columns. Added
        # additively here (NULL/absent on existing rows is correct: those
        # sessions closed before an outcome basis was recorded).
        ju_cols = _columns(conn, "joint_understanding_session")
        if ju_cols and "outcome_finding_ids" not in ju_cols:
            conn.execute(
                "ALTER TABLE joint_understanding_session "
                "ADD COLUMN outcome_finding_ids TEXT NOT NULL DEFAULT '[]'"
            )
        _add_column_if_missing(
            conn, "joint_understanding_session", ju_cols,
            "outcome_premise_state", "TEXT",
        )
        # Issue #337: the shared premise bundle, the close decision audit, and
        # the provenance columns. All legitimately NULL on existing rows, and
        # the NULLs are meaningful rather than merely absent: a session with
        # no premise_tracking_version evaluates 'invalid'
        # (premise_not_captured) and therefore cannot adopt a hypothesis,
        # record a decision, or reflux. Compatibility here means "the old row
        # stays readable and keeps its recorded outcome", never "the old row
        # is promoted to a satisfied premise" -- the pre-#337 code returned
        # 'fresh' for exactly these rows, which is the bug being fixed.
        for _column_name, _definition in (
            ("outcome_premise_reason", "TEXT"),
            ("closed_by_actor_kind", "TEXT"),
            ("closed_by_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
            ("closed_by_username", "TEXT"),
            ("premise_commit_sha", "TEXT"),
            (
                "premise_revision_id",
                "INTEGER REFERENCES understanding_revision(id) ON DELETE SET NULL",
            ),
            ("premise_content_hash", "TEXT"),
            ("premise_capability_digest", "TEXT"),
            ("premise_intent_digest", "TEXT"),
            ("premise_review_subject_id", "TEXT"),
            ("premise_tracking_version", "TEXT"),
            ("premise_captured_at", "REAL"),
        ):
            _add_column_if_missing(
                conn, "joint_understanding_session", ju_cols,
                _column_name, _definition,
            )
        # Issue #339: the finite execution-failure class on a round, so a
        # research limitation (a real result) is never read as a broken run.
        ju_round_cols = _columns(conn, "joint_understanding_investigation_round")
        _add_column_if_missing(
            conn, "joint_understanding_investigation_round", ju_round_cols,
            "failure_class", "TEXT",
        )
        ju_finding_cols = _columns(conn, "joint_understanding_finding")
        for _column_name, _definition in (
            ("producer_kind", "TEXT"),
            ("actor_kind", "TEXT"),
            ("actor_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
            ("actor_username", "TEXT"),
        ):
            _add_column_if_missing(
                conn, "joint_understanding_finding", ju_finding_cols,
                _column_name, _definition,
            )
        ju_action_cols = _columns(conn, "joint_understanding_action")
        for _column_name, _definition in (
            ("actor_kind", "TEXT"),
            ("actor_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
            ("actor_username", "TEXT"),
        ):
            _add_column_if_missing(
                conn, "joint_understanding_action", ju_action_cols,
                _column_name, _definition,
            )
        reflux_cols = _columns(conn, "joint_understanding_reflux")
        if reflux_cols and "runtime_evidence_json" not in reflux_cols:
            conn.execute(
                "ALTER TABLE joint_understanding_reflux "
                "ADD COLUMN runtime_evidence_json TEXT NOT NULL DEFAULT '[]'"
            )
        qa_cols = _columns(conn, "interview_qa")
        if qa_cols and "investigation_run_id" not in qa_cols:
            conn.execute(
                "ALTER TABLE interview_qa ADD COLUMN investigation_run_id INTEGER "
                "REFERENCES intelligence_runs(id) ON DELETE SET NULL"
            )
        if qa_cols and "investigation_json" not in qa_cols:
            conn.execute("ALTER TABLE interview_qa ADD COLUMN investigation_json TEXT")
        # Issue #295: realizes the 'unchanged' review_category reserved by
        # Issue #287. content_hash is a deterministic sha256 over an item's
        # identity-bearing fields (app/alignment.py's compute_content_hash),
        # computed for every item on every build (including pre-existing
        # rows the next build leaves untouched -- those simply keep whatever
        # hash they were built with). carried_over_from records which
        # terminal (answered/corrected) row from the immediately preceding
        # build a fresh 'unchanged' row's content exactly matched, purely for
        # audit -- it is never used to resolve a FK-style join back into
        # decision-making. Existing rows backfill to NULL (their content_hash
        # was never computed and they are never retroactively matched
        # against).
        alignment_item_cols = _columns(conn, "alignment_item")
        if alignment_item_cols and "content_hash" not in alignment_item_cols:
            conn.execute("ALTER TABLE alignment_item ADD COLUMN content_hash TEXT")
        # Issue #312: preserve the pre-Capability carry key separately from
        # the manually-confirmed Capability dependency scope.  Existing
        # content_hash values are exact deterministic facts and can therefore
        # be copied without inferring any historical Capability identity.
        if alignment_item_cols and "base_content_hash" not in alignment_item_cols:
            conn.execute("ALTER TABLE alignment_item ADD COLUMN base_content_hash TEXT")
            conn.execute(
                """UPDATE alignment_item
                   SET base_content_hash = content_hash
                   WHERE content_hash IS NOT NULL"""
            )
        if alignment_item_cols and "carried_over_from" not in alignment_item_cols:
            conn.execute(
                "ALTER TABLE alignment_item ADD COLUMN carried_over_from INTEGER "
                "REFERENCES alignment_item(id) ON DELETE SET NULL"
            )
        capability_confirmation_cols = _columns(
            conn, "understanding_capability_confirmation"
        )
        if (
            capability_confirmation_cols
            and "request_digest" not in capability_confirmation_cols
        ):
            conn.execute(
                "ALTER TABLE understanding_capability_confirmation "
                "ADD COLUMN request_digest TEXT"
            )
        if (
            capability_confirmation_cols
            and "decided_by_user_id" not in capability_confirmation_cols
        ):
            conn.execute(
                "ALTER TABLE understanding_capability_confirmation "
                "ADD COLUMN decided_by_user_id INTEGER "
                "REFERENCES users(id) ON DELETE SET NULL"
            )
        if (
            capability_confirmation_cols
            and "base_confirmation_id" not in capability_confirmation_cols
        ):
            conn.execute(
                "ALTER TABLE understanding_capability_confirmation "
                "ADD COLUMN base_confirmation_id INTEGER "
                "REFERENCES understanding_capability_confirmation(id) "
                "ON DELETE SET NULL"
            )
        # Issue #313: keep policy provenance additive.  The pre-policy Python
        # rule table is recorded as legacy rather than claiming it used the
        # new YAML policy; only rows built after this migration receive a
        # validated policy version and content digest.
        alignment_item_cols = _columns(conn, "alignment_item")
        if alignment_item_cols and "policy_version" not in alignment_item_cols:
            conn.execute(
                "ALTER TABLE alignment_item "
                "ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'legacy-code-v1'"
            )
        if alignment_item_cols and "policy_digest" not in alignment_item_cols:
            conn.execute("ALTER TABLE alignment_item ADD COLUMN policy_digest TEXT")
        if alignment_item_cols and "policy_rule_id" not in alignment_item_cols:
            conn.execute("ALTER TABLE alignment_item ADD COLUMN policy_rule_id TEXT")
            # The only no_review_required rule in alignment-review-v1 is
            # unambiguous, so existing rows from that reviewed artifact can
            # be attributed without re-running or guessing classification.
            conn.execute(
                """UPDATE alignment_item
                   SET policy_rule_id = 'aligned-no-change'
                   WHERE policy_version = 'alignment-review-v1'
                     AND review_category = 'no_review_required'
                     AND reason_code = 'no_change'"""
            )
        if alignment_item_cols and "manual_recheck_required" not in alignment_item_cols:
            conn.execute(
                "ALTER TABLE alignment_item "
                "ADD COLUMN manual_recheck_required INTEGER NOT NULL DEFAULT 0"
            )
        objection_cols = _columns(conn, "alignment_rule_objection")
        if objection_cols and "policy_rule_id" not in objection_cols:
            conn.execute(
                "ALTER TABLE alignment_rule_objection "
                "ADD COLUMN policy_rule_id TEXT NOT NULL DEFAULT 'legacy-unknown'"
            )
            conn.execute(
                """UPDATE alignment_rule_objection
                   SET policy_rule_id = 'aligned-no-change'
                   WHERE policy_version = 'alignment-review-v1'
                     AND reason_code = 'no_change'"""
            )
        # Issue #308 / #321: stable review-subject identity and physical
        # lineage for alignment items. Existing rows stay NULL: their
        # subject would have to be reconstructed from a snapshot/revision
        # that may no longer exist, and inferring one from claim text is
        # exactly the similarity matching #321 forbids. They are reported
        # 'untrackable' and only rows built after this migration participate
        # in lineage.
        alignment_item_cols = _columns(conn, "alignment_item")
        if alignment_item_cols:
            _add_column_if_missing(
                conn, "alignment_item", alignment_item_cols, "review_subject_id", "TEXT",
            )
            _add_column_if_missing(
                conn, "alignment_item", alignment_item_cols, "subject_state", "TEXT",
            )
            _add_column_if_missing(
                conn, "alignment_item", alignment_item_cols, "replaces_item_id",
                "INTEGER REFERENCES alignment_item(id) ON DELETE SET NULL",
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_alignment_item_review_subject
                   ON alignment_item (session_id, review_subject_id, created_at)"""
            )
        # Issue #308 / #320 / #323: the Inquiry premise bundle and its
        # evaluation result. Purely additive; every existing Inquiry keeps
        # NULL in all of them (an already-answered conversation's premise is
        # never reconstructed by guesswork -- it is reported 'untrackable').
        inquiry_cols = _columns(conn, "interview_inquiry")
        if inquiry_cols:
            _add_column_if_missing(
                conn, "interview_inquiry", inquiry_cols, "premise_snapshot_id",
                "INTEGER REFERENCES repository_snapshots(id) ON DELETE SET NULL",
            )
            _add_column_if_missing(
                conn, "interview_inquiry", inquiry_cols, "premise_revision_id",
                "INTEGER REFERENCES understanding_revision(id) ON DELETE SET NULL",
            )
            for column in (
                "premise_review_subject_id",
                "premise_content_hash",
                "premise_capability_digest",
                "premise_intent_digest",
                "premise_tracking_version",
                "premise_evaluation",
            ):
                _add_column_if_missing(
                    conn, "interview_inquiry", inquiry_cols, column, "TEXT",
                )
            _add_column_if_missing(
                conn, "interview_inquiry", inquiry_cols, "premise_successor_item_id",
                "INTEGER REFERENCES alignment_item(id) ON DELETE SET NULL",
            )
            for column in ("premise_captured_at", "superseded_at"):
                _add_column_if_missing(
                    conn, "interview_inquiry", inquiry_cols, column, "REAL",
                )
        # Issue #369: the snapshot-freshness facts an experiment was created
        # under. Additive and never backfilled -- an experiment created before
        # this existed has no recorded decision, which is exactly what NULL
        # should say. `stale_ack_reason` is the developer's manual reason for
        # running against a snapshot that is behind HEAD.
        experiment_cols = _columns(conn, "experiments")
        if experiment_cols:
            for column in (
                "snapshot_freshness",
                "head_sha_at_creation",
                "stale_ack_reason",
            ):
                _add_column_if_missing(
                    conn, "experiments", experiment_cols, column, "TEXT"
                )
        # Issue #369 (review finding 4): the same stale-snapshot decision the
        # Experiment records, on every other record that consumes a snapshot.
        # Additive and never backfilled -- a row created before the shared
        # preflight existed has no recorded decision, which is what NULL says.
        for table in ("candidate_sessions", "replay_runs"):
            columns = _columns(conn, table)
            if columns:
                for column in (
                    "snapshot_freshness",
                    "head_sha_at_creation",
                    "stale_ack_reason",
                ):
                    _add_column_if_missing(conn, table, columns, column, "TEXT")
        # Issue #367: the server-side redaction audit summary. Additive and
        # never backfilled -- an existing row's NULL means "this row was
        # stored before ingestion-time redaction existed", which is exactly
        # the population the operational rescan in docs/secret-redaction.md
        # is for. Backfilling it here would erase that distinction.
        trace_cols = _columns(conn, "traces")
        if trace_cols:
            _add_column_if_missing(conn, "traces", trace_cols, "redaction_json", "TEXT")
        # Projections and shadow results carry the same kind of free-form
        # payload and reach storage through their own routes, so they need the
        # same audit column. NULL keeps meaning "written before ingestion-time
        # redaction existed" on every table the rescan covers.
        for table in ("trace_projections", "shadow_results"):
            columns = _columns(conn, table)
            if columns:
                _add_column_if_missing(conn, table, columns, "redaction_json", "TEXT")
        # Issue #391 review: evidence provenance is source-specific.  A single
        # synthetic/state bit cannot describe a human report and a runtime
        # observation when both exist on the same criterion.
        outcome_columns = _columns(conn, "purpose_outcome_criterion")
        if outcome_columns:
            for column, definition in (
                ("human_reported_state", "TEXT"),
                ("human_reported_is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
                ("runtime_observation_state", "TEXT"),
                ("runtime_observation_is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
            ):
                _add_column_if_missing(
                    conn, "purpose_outcome_criterion", outcome_columns, column, definition
                )
        # Issue #399 (review finding): the parent review, recorded separately
        # from the human approval (#304). Additive and never backfilled -- a
        # package created before this existed carries NULL, which says "no
        # parent has reviewed this", and the establishment gate refuses it
        # (`parent_review_missing`) rather than reading the existing
        # `approved_by` as if it had also been the parent's endorsement.
        stabilization_cols = _columns(conn, "stabilization_package")
        if stabilization_cols:
            for column, definition in (
                ("parent_reviewed_by", "TEXT"),
                ("parent_reviewed_at", "REAL"),
                (
                    "parent_review_disposition",
                    "TEXT CHECK (parent_review_disposition IS NULL OR "
                    "parent_review_disposition IN ('endorsed', 'declined'))",
                ),
                ("parent_review_note", "TEXT"),
            ):
                _add_column_if_missing(
                    conn, "stabilization_package", stabilization_cols,
                    column, definition,
                )
        _migrate_alignment_manual_recheck_targets(conn)
        _ensure_legacy_system(conn)
    _validate_startup_environment()
    _validate_publish_startup_config()
    _bootstrap_admin()
    _enforce_auth_requirement()
    # Issue #270: production must never silently fall back to host execution.
    # Run after auth checks so an auth misconfiguration remains the first,
    # most actionable startup error when both settings are invalid.
    from .execution_backend import validate_execution_backend_startup

    validate_execution_backend_startup()
    from .environment import is_production
    from .resource_limits import validate_resource_limit_config

    validate_resource_limit_config(production=is_production())


def _validate_startup_environment() -> None:
    """Fail closed on invalid/contradictory startup configuration.

    Runs before `_bootstrap_admin` (Issue #225) so a sample/weak password
    supplied via `CONTROL_ADMIN_PASSWORD` fails startup even when an admin
    row with that username already exists from an earlier boot -- the
    sample secret sitting in the environment is itself the problem,
    independent of whether bootstrap would insert a new row this time.

    `control_env()` itself enforces the finite `{development, production}`
    set (CLAUDE.md Principle 6) and always runs, regardless of environment.
    The remaining checks only apply when `CONTROL_ENV=production`; the
    `development` default keeps existing permissive behavior untouched.
    """
    from .environment import control_env
    from .security import validate_production_password

    env = control_env()  # raises RuntimeError for an unrecognized value
    if env != "production":
        return

    require_auth_raw = os.getenv("CONTROL_REQUIRE_AUTH")
    if require_auth_raw is not None and require_auth_raw.strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        raise RuntimeError(
            "CONTROL_ENV=production requires authentication, but "
            f"CONTROL_REQUIRE_AUTH={require_auth_raw!r} explicitly disables "
            "it. Remove CONTROL_REQUIRE_AUTH (or set it to a truthy value) "
            "to resolve the contradiction."
        )

    if os.getenv("CONTROL_API_KEYS", "").strip():
        raise RuntimeError(
            "CONTROL_ENV=production forbids CONTROL_API_KEYS (legacy shared "
            "service keys are never accepted in production). Remove it; "
            "SDKs must use System-scoped API tokens issued via "
            "POST /tokens/me instead."
        )

    username = os.getenv("CONTROL_ADMIN_USERNAME", "").strip()
    password = os.getenv("CONTROL_ADMIN_PASSWORD", "")
    if username and password:
        try:
            validate_production_password(username, password)
        except ValueError as exc:
            raise RuntimeError(
                f"CONTROL_ENV=production rejects CONTROL_ADMIN_PASSWORD: "
                f"{exc}. Set a unique password with at least 16 characters "
                "and restart."
            ) from exc


def _validate_publish_startup_config() -> None:
    """Fail closed at startup when the GitHub App publish workflow (Issue
    #216) is declared enabled but not actually usable (Issue #224).

    Runs right after `_validate_startup_environment()`, before admin
    bootstrap, for the same reason: a misconfigured `GITHUB_PUBLISH_ENABLED`
    is itself the problem, independent of anything else startup does. A
    no-op when `GITHUB_PUBLISH_ENABLED` is false/unset -- the existing
    fail-closed runtime gate (`github_app.github_app_configured()`) is
    unchanged.
    """
    from .github_app import validate_publish_startup_config

    validate_publish_startup_config()


def _enforce_auth_requirement() -> None:
    """Fail closed on startup when auth is required but cannot be enabled.

    `CONTROL_REQUIRE_AUTH=true` is meant for production deployments (see
    docs/deployment-https.md): if no admin user exists (bootstrap did not run
    or already ran without credentials) and `CONTROL_API_KEYS` is empty, the
    server would otherwise start in the fail-open "no auth" MVP-compat mode.
    Refuse to start instead, with an explicit error. The default
    (`CONTROL_REQUIRE_AUTH=false`) keeps existing behavior but still warns.

    `CONTROL_ENV=production` (Issue #225) forces `CONTROL_REQUIRE_AUTH` on
    (contradictions were already rejected in `_validate_startup_environment`)
    and additionally requires bootstrap to have produced at least one active
    admin user -- `auth.auth_enabled()` is unconditionally `True` in
    production, so the DB is checked directly here instead of relying on it.
    """
    from .auth import auth_enabled
    from .environment import is_production

    production = is_production()
    require_auth = production or os.getenv(
        "CONTROL_REQUIRE_AUTH", "false"
    ).strip().lower() in ("1", "true", "yes", "on")

    if production:
        with get_conn() as conn:
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()[0]
        if user_count == 0 or admin_count == 0:
            raise RuntimeError(
                "CONTROL_ENV=production requires at least one active admin "
                "user; no active admin user exists. Set "
                "CONTROL_ADMIN_USERNAME/CONTROL_ADMIN_PASSWORD to bootstrap "
                "one, or create one via POST /users with an existing admin "
                "session, then restart."
            )
        return

    if auth_enabled():
        return

    message = (
        "No admin user and no CONTROL_API_KEYS are configured; Control "
        "Server would run without authentication. Set "
        "CONTROL_ADMIN_USERNAME/CONTROL_ADMIN_PASSWORD (bootstraps an admin "
        "user) or CONTROL_API_KEYS to enable auth."
    )
    if require_auth:
        raise RuntimeError(
            "CONTROL_REQUIRE_AUTH=true but authentication cannot be enabled: "
            + message
        )
    logger.warning(message)


def _bootstrap_admin() -> None:
    """Create an initial admin from env vars if no such user exists yet."""
    import json

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
        # One-time bootstrap audit event (Issue #225). Only recorded when a
        # row is actually created; never contains the password or its hash.
        conn.execute(
            """
            INSERT INTO auth_audit_events (event_type, username, detail, created_at)
            VALUES ('admin_bootstrapped', ?, ?, ?)
            """,
            (username, json.dumps({"source": "env_bootstrap"}), time.time()),
        )
