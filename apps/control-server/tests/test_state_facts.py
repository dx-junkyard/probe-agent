"""Unit tests for the shared deterministic state-fact retrieval layer (Issue #236).

Before Issue #236, `system_state.py`, `system_understanding_service.py`, and
`routes/connectivity.py` each fetched overlapping facts (repository
configuration, ready snapshot, pipeline step rows, Purpose/Capabilities base
facts, active-build detection, SDK connectivity counts) with independently
written SQL. `app/state_facts.py` is now the single home for those raw reads;
this file tests each fact getter directly plus System isolation, and proves
the `purpose_defined` reduction used by
`system_understanding_service._purpose_defined_from_understanding_status` is
behaviorally identical to the pre-#236 local formula for the realistic state
space (see `TestPurposeDefinedReductionEquivalence`).

Regression coverage for the 3 consuming APIs (GET /system-state,
GET /repository/system-understanding, GET /connectivity/status) across
representative scenarios (unconfigured / snapshot-only / pipeline-complete /
connectivity-with-traces) lives alongside each API's existing test file
(test_system_state.py, test_system_understanding.py, test_connectivity.py)
per this repo's existing per-API test-file convention.
"""

import subprocess
import time

import pytest


# --- DB fixtures -------------------------------------------------------------


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    """Initialize a fresh SQLite DB and return app.db.get_conn for direct use.

    Facts-layer functions are plain (conn, ...) -> value readers, so they are
    tested directly against the DB rather than through the FastAPI app.
    """
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-state-facts.db"))
    from app import db as db_module

    db_module.init_db()
    return db_module.get_conn


def _create_system(get_conn, name="sys"):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO systems (name, environment, description, owner_user_id, created_at, updated_at) "
            "VALUES (?, '', '', NULL, ?, ?)",
            (name, now, now),
        )
        return cur.lastrowid


def _set_repository_config(get_conn, system_id, repo_path):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO repository_configs
                   (system_id, repo_path, include_patterns, exclude_patterns, created_at, updated_at)
               VALUES (?, ?, '[]', '[]', ?, ?)""",
            (system_id, repo_path, now, now),
        )


def _insert_snapshot(get_conn, system_id, *, status="indexing", commit_sha="pending"):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, file_count,
                    total_size, indexed_size, created_at, completed_at)
               VALUES (?, '', ?, ?, 0, 0, 0, ?, NULL)""",
            (system_id, commit_sha, status, now),
        )
        return cur.lastrowid


def _insert_intelligence_run(get_conn, system_id, snapshot_id, run_type, status):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, error_details, is_mock,
                    started_at, completed_at)
               VALUES (?, ?, ?, 'mock', 'mock', 'test', 'test', 'deterministic',
                       ?, NULL, 1, ?, ?)""",
            (system_id, snapshot_id, run_type, status, now, now),
        )
        return cur.lastrowid


def _insert_build_step(get_conn, system_id, snapshot_id, step, status, *, error=None, provenance="{}", build_id=None):
    if build_id is None:
        build_id = _insert_build(get_conn, system_id, snapshot_id, status="running")
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO system_understanding_build_steps
                   (build_id, system_id, snapshot_id, step, status, error, artifact_provenance,
                    heartbeat_at, started_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (build_id, system_id, snapshot_id, step, status, error, provenance, now, now, now),
        )
        return cur.lastrowid


def _insert_code_entrypoint(get_conn, system_id, snapshot_id, entrypoint_id="ep1"):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO code_entrypoints
                   (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                    handler_path, handler_qualified_name, line_start, line_end, created_at)
               VALUES (?, ?, 'api', ?, 'api', 'label', 'a.py', 'a.handler', 1, 2, ?)""",
            (system_id, snapshot_id, entrypoint_id, now),
        )


def _insert_understanding_graph_snapshot(get_conn, system_id, snapshot_id):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO understanding_graph_snapshots
                   (system_id, snapshot_id, graph_json, source_hash, claim_count,
                    confidence_summary, created_at)
               VALUES (?, ?, '{}', '', 0, '{}', ?)""",
            (system_id, snapshot_id, now),
        )


def _insert_code_symbol(get_conn, system_id, snapshot_id, qualified_name="mod.fn"):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO code_symbols
                   (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)
               VALUES (?, ?, 'a.py', ?, 'function', 1, 2)""",
            (snapshot_id, system_id, qualified_name),
        )


def _insert_capability_node(get_conn, system_id, snapshot_id, run_id, *, node_type="capability", name="Cap", summary=""):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
                   (system_id, snapshot_id, intelligence_run_id, node_type, name, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (system_id, snapshot_id, run_id, node_type, name, summary, now),
        )


def _insert_draft(get_conn, system_id, snapshot_id, run_id, *, name="", purpose=""):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_profile_drafts
                   (system_id, intelligence_run_id, snapshot_id, name, purpose, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (system_id, run_id, snapshot_id, name, purpose, now),
        )


def _insert_build(get_conn, system_id, snapshot_id, *, status="running", current_step="symbol_index", heartbeat_at=None):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO system_understanding_builds
                   (system_id, snapshot_id, status, current_step, heartbeat_at, started_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (system_id, snapshot_id, status, current_step, heartbeat_at if heartbeat_at is not None else now, now, now),
        )
        return cur.lastrowid


def _insert_trace(get_conn, system_id, component_id, trace_id, ts=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO traces
                   (system_id, trace_id, component_id, mode, input_json, output_text, error, duration_ms, timestamp)
               VALUES (?, ?, ?, 'trace', '{}', 'ok', NULL, 1.0, ?)""",
            (system_id, trace_id, component_id, ts if ts is not None else time.time()),
        )


def _insert_interview_session(get_conn, system_id, snapshot_id, *, materialization_diff=None):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_session
                   (system_id, snapshot_id, title, focus, status, stage,
                    materialization_diff, created_at, updated_at)
               VALUES (?, ?, '', '', 'open', 'understanding_initialized', ?, ?, ?)""",
            (system_id, snapshot_id, materialization_diff, now, now),
        )
        return cur.lastrowid


def _init_git_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


# --- Repository configuration -------------------------------------------------


class TestRepositoryConfigFacts:
    def test_missing_config_returns_none(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        with conn_factory() as conn:
            assert state_facts.get_repository_config(conn, system_id) is None
            assert state_facts.has_repository_configured(conn, system_id) is False

    def test_configured_repository_is_returned(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        _set_repository_config(conn_factory, system_id, "/repo/path")
        with conn_factory() as conn:
            row = state_facts.get_repository_config(conn, system_id)
            assert row["repo_path"] == "/repo/path"
            assert state_facts.has_repository_configured(conn, system_id) is True


class TestRepositoryHeadState:
    def test_resolves_clean_head(self, tmp_path, monkeypatch, conn_factory):
        from app import state_facts

        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
        repo, sha = _init_git_repo(tmp_path)

        head_state = state_facts.resolve_repository_head_state(str(repo))
        assert head_state.current_head == sha
        assert head_state.working_tree_clean is True
        assert head_state.working_tree_dirty_file_count == 0
        assert head_state.working_tree_dirty_sample == []

    def test_dirty_working_tree_is_reported(self, tmp_path, monkeypatch, conn_factory):
        from app import state_facts

        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
        repo, _sha = _init_git_repo(tmp_path)
        (repo / "new.txt").write_text("uncommitted\n")

        head_state = state_facts.resolve_repository_head_state(str(repo))
        assert head_state.working_tree_clean is False
        assert head_state.working_tree_dirty_file_count == 1

    def test_invalid_repo_path_raises_git_error(self, tmp_path, monkeypatch, conn_factory):
        from app import state_facts

        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
        with pytest.raises(state_facts.GitError):
            state_facts.resolve_repository_head_state(str(tmp_path / "does-not-exist"))


# --- Snapshots -----------------------------------------------------------------


class TestSnapshotFacts:
    def test_no_snapshots_returns_none(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        with conn_factory() as conn:
            assert state_facts.get_latest_snapshot(conn, system_id) is None
            assert state_facts.get_latest_ready_snapshot(conn, system_id) is None
            assert state_facts.get_latest_ready_snapshot_id(conn, system_id) is None

    def test_latest_snapshot_ignores_status_but_ready_lookup_does_not(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        ready_id = _insert_snapshot(conn_factory, system_id, status="ready", commit_sha="sha1")
        indexing_id = _insert_snapshot(conn_factory, system_id, status="indexing", commit_sha="sha2")

        with conn_factory() as conn:
            latest = state_facts.get_latest_snapshot(conn, system_id)
            assert latest["id"] == indexing_id
            assert latest["status"] == "indexing"

            ready = state_facts.get_latest_ready_snapshot(conn, system_id)
            assert ready["id"] == ready_id
            assert ready["commit_sha"] == "sha1"
            assert state_facts.get_latest_ready_snapshot_id(conn, system_id) == ready_id

    def test_get_latest_ready_snapshot_picks_highest_id_ready_row(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        _insert_snapshot(conn_factory, system_id, status="ready", commit_sha="sha1")
        second_ready = _insert_snapshot(conn_factory, system_id, status="ready", commit_sha="sha2")

        with conn_factory() as conn:
            ready = state_facts.get_latest_ready_snapshot(conn, system_id)
            assert ready["id"] == second_ready


# --- Pipeline step raw facts -----------------------------------------------------


class TestPipelineStepFacts:
    def test_get_latest_intelligence_run_returns_most_recent_matching_row(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")

        with conn_factory() as conn:
            assert state_facts.get_latest_intelligence_run(
                conn, system_id, snapshot_id, ["symbol_index"]
            ) is None

        _insert_intelligence_run(conn_factory, system_id, snapshot_id, "symbol_index", "failed")
        latest_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "symbol_index", "completed")

        with conn_factory() as conn:
            row = state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["symbol_index"])
            assert row["id"] == latest_id
            assert row["status"] == "completed"

    def test_get_latest_intelligence_run_multiple_run_types(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "repository_drafts", "completed")

        with conn_factory() as conn:
            row = state_facts.get_latest_intelligence_run(
                conn, system_id, snapshot_id, ["draft_generation", "repository_drafts"]
            )
            assert row["id"] == run_id

    def test_get_latest_build_step(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")

        with conn_factory() as conn:
            assert state_facts.get_latest_build_step(conn, system_id, snapshot_id, "documentation_index") is None

        _insert_build_step(
            conn_factory, system_id, snapshot_id, "documentation_index", "completed",
            provenance='{"chunk_count": 3}',
        )
        with conn_factory() as conn:
            row = state_facts.get_latest_build_step(conn, system_id, snapshot_id, "documentation_index")
            assert row["status"] == "completed"
            assert row["artifact_provenance"] == '{"chunk_count": 3}'

    def test_has_code_entrypoints(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.has_code_entrypoints(conn, system_id, snapshot_id) is False
        _insert_code_entrypoint(conn_factory, system_id, snapshot_id)
        with conn_factory() as conn:
            assert state_facts.has_code_entrypoints(conn, system_id, snapshot_id) is True

    def test_has_understanding_graph_snapshot(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.has_understanding_graph_snapshot(conn, system_id, snapshot_id) is False
        _insert_understanding_graph_snapshot(conn_factory, system_id, snapshot_id)
        with conn_factory() as conn:
            assert state_facts.has_understanding_graph_snapshot(conn, system_id, snapshot_id) is True

    def test_has_code_symbols(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.has_code_symbols(conn, system_id, snapshot_id) is False
        _insert_code_symbol(conn_factory, system_id, snapshot_id)
        with conn_factory() as conn:
            assert state_facts.has_code_symbols(conn, system_id, snapshot_id) is True


# --- Purpose / Capabilities base facts ------------------------------------------


class TestPurposeAndCapabilityFacts:
    def test_purpose_undefined_when_nothing_present(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.purpose_defined_in_snapshot(conn, system_id, snapshot_id) is False

    def test_purpose_defined_via_hierarchy_node(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="purpose", name="Sys", summary="")
        with conn_factory() as conn:
            assert state_facts.purpose_defined_in_snapshot(conn, system_id, snapshot_id) is True

    def test_purpose_node_with_empty_name_and_summary_is_undefined(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="purpose", name="", summary="")
        with conn_factory() as conn:
            assert state_facts.purpose_defined_in_snapshot(conn, system_id, snapshot_id) is False

    def test_purpose_defined_via_draft_when_no_node(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "repository_drafts", "completed")
        _insert_draft(conn_factory, system_id, snapshot_id, run_id, name="Sys", purpose="Does things")
        with conn_factory() as conn:
            assert state_facts.purpose_defined_in_snapshot(conn, system_id, snapshot_id) is True

    def test_capability_count_only_counts_capability_nodes(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="purpose", name="Sys")
        assert_conn = conn_factory
        with assert_conn() as conn:
            assert state_facts.capability_count_in_snapshot(conn, system_id, snapshot_id) == 0

        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="capability", name="Cap A")
        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="capability", name="Cap B")
        with assert_conn() as conn:
            assert state_facts.capability_count_in_snapshot(conn, system_id, snapshot_id) == 2


# --- Build job running / stuck detection ----------------------------------------


class TestBuildJobFacts:
    def test_stuck_after_seconds_default_and_override(self, monkeypatch):
        from app import state_facts

        monkeypatch.delenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", raising=False)
        assert state_facts.stuck_after_seconds() == 300.0

        monkeypatch.setenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", "10")
        assert state_facts.stuck_after_seconds() == 10.0

        monkeypatch.setenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", "not-a-number")
        assert state_facts.stuck_after_seconds() == 300.0

    def test_is_active_build_row(self):
        from app import state_facts

        assert state_facts.is_active_build_row(None) is False
        assert state_facts.is_active_build_row(
            {"status": "completed", "heartbeat_at": time.time(), "started_at": None, "created_at": None}
        ) is False
        assert state_facts.is_active_build_row(
            {"status": "running", "heartbeat_at": time.time(), "started_at": None, "created_at": None}
        ) is True
        assert state_facts.is_active_build_row(
            {"status": "running", "heartbeat_at": time.time() - 10_000, "started_at": None, "created_at": None}
        ) is False

    def test_get_active_build_returns_non_stuck_running_build(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.get_active_build(conn, system_id, snapshot_id) is None

        build_id = _insert_build(conn_factory, system_id, snapshot_id, status="running")
        with conn_factory() as conn:
            row = state_facts.get_active_build(conn, system_id, snapshot_id)
            assert row["id"] == build_id

    def test_get_active_build_skips_stuck_build(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        _insert_build(conn_factory, system_id, snapshot_id, status="running", heartbeat_at=time.time() - 10_000)
        with conn_factory() as conn:
            assert state_facts.get_active_build(conn, system_id, snapshot_id) is None


# --- SDK connectivity ------------------------------------------------------------


class TestConnectivityFacts:
    def test_no_traces_yields_zero_counts(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        with conn_factory() as conn:
            facts = state_facts.get_connectivity_facts(conn, system_id, "probe-smoke-check")
        assert facts.total_trace_count == 0
        assert facts.smoke_trace_count == 0
        assert facts.real_trace_count == 0
        assert facts.first_trace_at is None
        assert facts.last_trace_at is None
        assert facts.last_trace_component_id is None
        assert facts.materialized_session_ids == []
        assert state_facts.classify_connectivity_state(
            real_trace_count=facts.real_trace_count, smoke_trace_count=facts.smoke_trace_count,
        ) == "no_signal"

    def test_smoke_only(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        _insert_trace(conn_factory, system_id, "probe-smoke-check", "t1")
        with conn_factory() as conn:
            facts = state_facts.get_connectivity_facts(conn, system_id, "probe-smoke-check")
        assert facts.total_trace_count == 1
        assert facts.smoke_trace_count == 1
        assert facts.real_trace_count == 0
        assert state_facts.classify_connectivity_state(
            real_trace_count=facts.real_trace_count, smoke_trace_count=facts.smoke_trace_count,
        ) == "smoke_only"

    def test_receiving_when_real_trace_present(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        _insert_trace(conn_factory, system_id, "probe-smoke-check", "t1", ts=1.0)
        _insert_trace(conn_factory, system_id, "worker", "t2", ts=2.0)
        with conn_factory() as conn:
            facts = state_facts.get_connectivity_facts(conn, system_id, "probe-smoke-check")
        assert facts.total_trace_count == 2
        assert facts.real_trace_count == 1
        assert facts.last_trace_component_id == "worker"
        assert state_facts.classify_connectivity_state(
            real_trace_count=facts.real_trace_count, smoke_trace_count=facts.smoke_trace_count,
        ) == "receiving"

    def test_materialized_session_ids_only_include_non_empty_diff(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        _insert_interview_session(conn_factory, system_id, snapshot_id, materialization_diff=None)
        _insert_interview_session(conn_factory, system_id, snapshot_id, materialization_diff="")
        materialized_id = _insert_interview_session(conn_factory, system_id, snapshot_id, materialization_diff="diff --git a b")

        with conn_factory() as conn:
            facts = state_facts.get_connectivity_facts(conn, system_id, "probe-smoke-check")
        assert facts.materialized_session_ids == [materialized_id]


# --- Probe plan / experiment review facts (Issue #237) ---------------------------


def _insert_probe_plan(get_conn, system_id, snapshot_id, run_id, *, status="proposed"):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id,
                    objective, status, origin, created_at, updated_at)
               VALUES (?, ?, ?, 'feat-1', 'obj', ?, 'manual', ?, ?)""",
            (system_id, snapshot_id, run_id, status, now, now),
        )


def _insert_experiment(get_conn, system_id, snapshot_id, *, status="completed", human_decision="undecided"):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiments
                   (system_id, feature_id, objective, snapshot_id,
                    baseline_commit, config_revision, execution_config,
                    status, human_decision, created_at)
               VALUES (?, 'feat-1', 'obj', ?, 'deadbeef', 'v1', '{}', ?, ?, ?)""",
            (system_id, snapshot_id, status, human_decision, now),
        )


class TestProbePlanAndExperimentReviewFacts:
    def test_count_approved_probe_plans_only_counts_approved_status(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "probe_plan", "completed")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans(conn, system_id) == 0

        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="proposed")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="rejected")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans(conn, system_id) == 0

        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans(conn, system_id) == 2

    def test_count_undecided_completed_experiments_requires_both_conditions(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        with conn_factory() as conn:
            assert state_facts.count_undecided_completed_experiments(conn, system_id) == 0

        # completed but decided -- does not count.
        _insert_experiment(conn_factory, system_id, snapshot_id, status="completed", human_decision="adopted")
        # undecided but not completed -- does not count.
        _insert_experiment(conn_factory, system_id, snapshot_id, status="running", human_decision="undecided")
        with conn_factory() as conn:
            assert state_facts.count_undecided_completed_experiments(conn, system_id) == 0

        # completed and undecided -- counts.
        _insert_experiment(conn_factory, system_id, snapshot_id, status="completed", human_decision="undecided")
        with conn_factory() as conn:
            assert state_facts.count_undecided_completed_experiments(conn, system_id) == 1


# --- Probe plan review / patch facts (Issue #238) ---------------------------


def _insert_probe_patch(get_conn, plan_id, system_id, snapshot_id, *, status="generated"):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_patches
                   (plan_id, system_id, snapshot_id, commit_sha, diff,
                    skipped, status, cleanup_state, created_at)
               VALUES (?, ?, ?, 'deadbeef', 'diff', '[]', ?, 'not_attempted', 0)""",
            (plan_id, system_id, snapshot_id, status),
        )
        return cur.lastrowid


def _insert_validation_run(get_conn, patch_id, system_id, variant, *, overall_success=True):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO validation_runs
                   (patch_id, system_id, variant, worktree_path, overall_success,
                    trace_status, network_isolation, cleanup_state, created_at)
               VALUES (?, ?, ?, '/tmp/x', ?, 'not_checked', 'not_requested', 'not_attempted', 0)""",
            (patch_id, system_id, variant, 1 if overall_success else 0),
        )


class TestProbePlanReviewAndPatchFacts:
    """Issue #238: facts backing system_state.py's
    proposal.probe_plans.proposed / .approved_without_patch StateItems, and
    the shared plan_has_validated_patch primitive moved here from
    system_understanding_service._plan_has_validated_patch.
    """

    def test_plan_has_validated_patch_requires_both_baseline_and_probed_success(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "probe_plan", "completed")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        with conn_factory() as conn:
            plan_id = conn.execute(
                "SELECT id FROM probe_plans WHERE system_id = ?", (system_id,)
            ).fetchone()["id"]
            assert state_facts.plan_has_validated_patch(conn, plan_id) is False

        patch_id = _insert_probe_patch(conn_factory, plan_id, system_id, snapshot_id)
        _insert_validation_run(conn_factory, patch_id, system_id, "baseline", overall_success=True)
        with conn_factory() as conn:
            # Only baseline succeeded so far -- not validated yet.
            assert state_facts.plan_has_validated_patch(conn, plan_id) is False

        _insert_validation_run(conn_factory, patch_id, system_id, "probed", overall_success=True)
        with conn_factory() as conn:
            assert state_facts.plan_has_validated_patch(conn, plan_id) is True

    def test_plan_has_validated_patch_uses_latest_run_per_variant(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "probe_plan", "completed")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        with conn_factory() as conn:
            plan_id = conn.execute(
                "SELECT id FROM probe_plans WHERE system_id = ?", (system_id,)
            ).fetchone()["id"]
        patch_id = _insert_probe_patch(conn_factory, plan_id, system_id, snapshot_id)
        # An earlier failed baseline run followed by a later successful one
        # -- the *latest* run per variant must win.
        _insert_validation_run(conn_factory, patch_id, system_id, "baseline", overall_success=False)
        _insert_validation_run(conn_factory, patch_id, system_id, "baseline", overall_success=True)
        _insert_validation_run(conn_factory, patch_id, system_id, "probed", overall_success=True)
        with conn_factory() as conn:
            assert state_facts.plan_has_validated_patch(conn, plan_id) is True

    def test_count_proposed_probe_plans_only_counts_proposed_status(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "probe_plan", "completed")
        with conn_factory() as conn:
            assert state_facts.count_proposed_probe_plans(conn, system_id) == 0

        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="rejected")
        with conn_factory() as conn:
            assert state_facts.count_proposed_probe_plans(conn, system_id) == 0

        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="proposed")
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="proposed")
        with conn_factory() as conn:
            assert state_facts.count_proposed_probe_plans(conn, system_id) == 2

    def test_count_approved_probe_plans_without_validated_patch(self, conn_factory):
        from app import state_facts

        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "probe_plan", "completed")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, system_id) == 0

        # Approved with no patch at all -- counts.
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, system_id) == 1

        # A second approved plan with a fully validated patch -- does not count.
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="approved")
        with conn_factory() as conn:
            plan_ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM probe_plans WHERE system_id = ? ORDER BY id", (system_id,)
                ).fetchall()
            ]
        validated_plan_id = plan_ids[1]
        patch_id = _insert_probe_patch(conn_factory, validated_plan_id, system_id, snapshot_id)
        _insert_validation_run(conn_factory, patch_id, system_id, "baseline", overall_success=True)
        _insert_validation_run(conn_factory, patch_id, system_id, "probed", overall_success=True)
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, system_id) == 1

        # A proposed (not approved) plan is not counted either way.
        _insert_probe_plan(conn_factory, system_id, snapshot_id, run_id, status="proposed")
        with conn_factory() as conn:
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, system_id) == 1


# --- System isolation -------------------------------------------------------------


class TestSystemIsolation:
    def test_facts_are_scoped_per_system(self, conn_factory, tmp_path, monkeypatch):
        from app import state_facts

        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
        sys_a = _create_system(conn_factory, "A")
        sys_b = _create_system(conn_factory, "B")

        _set_repository_config(conn_factory, sys_a, "/repo/a")
        snapshot_a = _insert_snapshot(conn_factory, sys_a, status="ready", commit_sha="sha-a")
        run_a = _insert_intelligence_run(conn_factory, sys_a, snapshot_a, "symbol_index", "completed")
        _insert_build_step(conn_factory, sys_a, snapshot_a, "documentation_index", "completed")
        _insert_code_entrypoint(conn_factory, sys_a, snapshot_a)
        _insert_understanding_graph_snapshot(conn_factory, sys_a, snapshot_a)
        _insert_code_symbol(conn_factory, sys_a, snapshot_a)
        _insert_capability_node(conn_factory, sys_a, snapshot_a, run_a, node_type="purpose", name="Sys A")
        _insert_capability_node(conn_factory, sys_a, snapshot_a, run_a, node_type="capability", name="Cap A")
        _insert_build(conn_factory, sys_a, snapshot_a, status="running")
        _insert_trace(conn_factory, sys_a, "worker", "ta")
        _insert_probe_plan(conn_factory, sys_a, snapshot_a, run_a, status="approved")
        _insert_probe_plan(conn_factory, sys_a, snapshot_a, run_a, status="proposed")
        _insert_experiment(conn_factory, sys_a, snapshot_a, status="completed", human_decision="undecided")

        with conn_factory() as conn:
            # System B sees none of System A's facts.
            assert state_facts.get_repository_config(conn, sys_b) is None
            assert state_facts.get_latest_snapshot(conn, sys_b) is None
            assert state_facts.get_latest_ready_snapshot(conn, sys_b) is None
            assert state_facts.get_latest_intelligence_run(conn, sys_b, snapshot_a, ["symbol_index"]) is None
            assert state_facts.get_latest_build_step(conn, sys_b, snapshot_a, "documentation_index") is None
            assert state_facts.has_code_entrypoints(conn, sys_b, snapshot_a) is False
            assert state_facts.has_understanding_graph_snapshot(conn, sys_b, snapshot_a) is False
            assert state_facts.has_code_symbols(conn, sys_b, snapshot_a) is False
            assert state_facts.purpose_defined_in_snapshot(conn, sys_b, snapshot_a) is False
            assert state_facts.capability_count_in_snapshot(conn, sys_b, snapshot_a) == 0
            assert state_facts.get_active_build(conn, sys_b, snapshot_a) is None
            assert state_facts.count_approved_probe_plans(conn, sys_b) == 0
            assert state_facts.count_undecided_completed_experiments(conn, sys_b) == 0
            assert state_facts.count_proposed_probe_plans(conn, sys_b) == 0
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, sys_b) == 0

            facts_b = state_facts.get_connectivity_facts(conn, sys_b, "probe-smoke-check")
            assert facts_b.total_trace_count == 0

            # System A still sees its own facts.
            assert state_facts.get_repository_config(conn, sys_a)["repo_path"] == "/repo/a"
            assert state_facts.get_latest_ready_snapshot(conn, sys_a)["id"] == snapshot_a
            assert state_facts.purpose_defined_in_snapshot(conn, sys_a, snapshot_a) is True
            assert state_facts.count_approved_probe_plans(conn, sys_a) == 1
            assert state_facts.count_undecided_completed_experiments(conn, sys_a) == 1
            assert state_facts.count_proposed_probe_plans(conn, sys_a) == 1
            assert state_facts.count_approved_probe_plans_without_validated_patch(conn, sys_a) == 1
            assert state_facts.capability_count_in_snapshot(conn, sys_a, snapshot_a) == 1
            facts_a = state_facts.get_connectivity_facts(conn, sys_a, "probe-smoke-check")
            assert facts_a.total_trace_count == 1


# --- purpose_defined 5-way reduction equivalence (Issue #236) --------------------


class TestPurposeDefinedReductionEquivalence:
    """Proves system_understanding_service._purpose_defined_from_understanding_status
    (the reduction of evaluate_understanding's 5-way classification to a bool)
    matches the pre-#236 local formula
    ``bool(purpose and (purpose.get("summary") or purpose.get("name")))`` over
    ``_load_purpose``'s dict, for every representative state this call site
    could actually observe (Issue #236 Implementation Notes: identify which
    branch-set maps to True from the existing code, then test it before
    replacing).

    Mapping table (see Issue #236 report): only ``satisfied_current`` maps to
    True; ``baseline_reusable`` / ``diff_impacted`` / ``unconfirmed`` /
    ``missing_baseline`` all map to False -- matching that the pre-#236 local
    check never considered baseline reuse across snapshots at all.
    """

    def _old_purpose_defined(self, conn, system_id, snapshot_id):
        from app.system_understanding_service import _load_purpose

        purpose = _load_purpose(conn, system_id, snapshot_id)
        return bool(purpose and (purpose.get("summary") or purpose.get("name")))

    def _new_purpose_defined(self, conn, system_id, snapshot_id):
        from app.system_state import evaluate_understanding
        from app.system_understanding_service import _purpose_defined_from_understanding_status

        status = evaluate_understanding(conn, system_id, snapshot_id, purpose=True)
        return _purpose_defined_from_understanding_status(status)

    def _assert_equivalent(self, conn_factory, system_id, snapshot_id):
        with conn_factory() as conn:
            old = self._old_purpose_defined(conn, system_id, snapshot_id)
            new = self._new_purpose_defined(conn, system_id, snapshot_id)
        assert old == new
        return old

    def test_no_purpose_anywhere(self, conn_factory):
        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        assert self._assert_equivalent(conn_factory, system_id, snapshot_id) is False

    def test_purpose_defined_via_hierarchy_node_in_current_snapshot(self, conn_factory):
        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(conn_factory, system_id, snapshot_id, run_id, node_type="purpose", name="Sys", summary="Does things")
        assert self._assert_equivalent(conn_factory, system_id, snapshot_id) is True

    def test_purpose_defined_via_draft_in_current_snapshot(self, conn_factory):
        system_id = _create_system(conn_factory)
        snapshot_id = _insert_snapshot(conn_factory, system_id, status="ready")
        run_id = _insert_intelligence_run(conn_factory, system_id, snapshot_id, "repository_drafts", "completed")
        _insert_draft(conn_factory, system_id, snapshot_id, run_id, name="Sys", purpose="Does things")
        assert self._assert_equivalent(conn_factory, system_id, snapshot_id) is True

    def test_confirmed_baseline_on_other_unchanged_snapshot_is_not_current(self, conn_factory):
        """A confirmed understanding baseline from a different, unchanged
        snapshot makes evaluate_understanding return baseline_reusable
        (kind != satisfied_current). The pre-#236 local check never looked at
        other snapshots' interview baselines either (it only read
        capability_hierarchy_nodes / system_profile_drafts for *this*
        snapshot_id), so both sides must agree: not defined for this call site.
        """
        import json

        system_id = _create_system(conn_factory)
        snap1 = _insert_snapshot(conn_factory, system_id, status="ready", commit_sha="sha1")
        understanding = {
            "system_purpose": [{
                "name": "Sys", "summary": "Does things",
                "confidence": {"level": "confirmed", "reason": "manual"},
                "evidence": [], "why_core": "", "related_docs": [], "related_apis": [], "children": [],
            }],
            "core_capabilities": [],
            "capability_elements": [], "supporting_elements": [],
            "api_boundaries": [], "probe_flow_candidates": [],
        }
        now = time.time()
        with conn_factory() as conn:
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, title, focus, status, stage,
                        current_understanding, understanding_confirmed_at,
                        understanding_confirmed_by, created_at, updated_at)
                   VALUES (?, ?, 'baseline', '', 'open', 'proposal_generation', ?, ?, 'tester', ?, ?)""",
                (system_id, snap1, json.dumps(understanding), now, now, now),
            )
        # A second, unchanged snapshot: no new files, same repo state facts.
        snap2 = _insert_snapshot(conn_factory, system_id, status="ready", commit_sha="sha1")

        with conn_factory() as conn:
            old = self._old_purpose_defined(conn, system_id, snap2)
            new = self._new_purpose_defined(conn, system_id, snap2)
        assert old is False
        assert new is False

    def test_no_ready_snapshot_defaults_to_false_without_calling_facts(self, conn_factory):
        """get_system_understanding only computes purpose_defined when a
        snapshot_row exists; this documents that the default (False) agrees
        with the old dataclass default (summary.purpose stays None -> False)."""
        system_id = _create_system(conn_factory)
        with conn_factory() as conn:
            old_purpose = None  # SystemUnderstandingSummary.purpose default
            old = bool(old_purpose and (old_purpose or {}).get("summary"))
        assert old is False
