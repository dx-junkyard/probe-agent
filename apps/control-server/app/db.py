import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

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
-- zero gaps of a given type simply has no row for that gap_type; "no row"
-- and "count 0" are equivalent when reading a build's history back.
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
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (connection_id) REFERENCES github_connections (id) ON DELETE CASCADE,
    FOREIGN KEY (patch_id) REFERENCES probe_patches (id) ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id) REFERENCES repository_snapshots (id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_system
    ON publish_jobs (system_id, id DESC);
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


def init_db() -> None:
    with get_conn() as conn:
        _migrate_to_system_scope(conn)
        conn.executescript(SCHEMA)
        _migrate_intelligence_runs_snapshot_nullable(conn)
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
        if qa_cols and "runtime_evidence" not in qa_cols:
            # Issue #135: raw trace-aggregate + metadata-provenance JSON for
            # question_source = 'runtime' rows; existing rows stay NULL.
            conn.execute("ALTER TABLE interview_qa ADD COLUMN runtime_evidence TEXT")
        github_conn_cols = _columns(conn, "github_connections")
        if github_conn_cols and "last_synced_at" not in github_conn_cols:
            # Issue #216 sub-task 2: repo manager sync bookkeeping.
            conn.execute("ALTER TABLE github_connections ADD COLUMN last_synced_at TEXT")
        if github_conn_cols and "last_synced_commit_sha" not in github_conn_cols:
            conn.execute(
                "ALTER TABLE github_connections ADD COLUMN last_synced_commit_sha TEXT"
            )
        _ensure_legacy_system(conn)
    _bootstrap_admin()
    _enforce_auth_requirement()


def _enforce_auth_requirement() -> None:
    """Fail closed on startup when auth is required but cannot be enabled.

    `CONTROL_REQUIRE_AUTH=true` is meant for production deployments (see
    docs/deployment-https.md): if no admin user exists (bootstrap did not run
    or already ran without credentials) and `CONTROL_API_KEYS` is empty, the
    server would otherwise start in the fail-open "no auth" MVP-compat mode.
    Refuse to start instead, with an explicit error. The default
    (`CONTROL_REQUIRE_AUTH=false`) keeps existing behavior but still warns.
    """
    from .auth import auth_enabled

    require_auth = os.getenv("CONTROL_REQUIRE_AUTH", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
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
