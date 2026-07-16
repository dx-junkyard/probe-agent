"""Issue #238 built these as contract tests pinning that two "what should the
user do next" systems agreed, for a set of representative scenarios, while
both were still returned from the API:

- ``system_understanding_service._derive_primary_action`` /
  ``_build_next_actions`` -> ``GET /repository/system-understanding``'s
  ``primary_action`` / ``next_actions``.
- ``system_state.select_primary_item`` -> ``GET /system-state``'s
  ``primary_item``.

Issue #239 removed the old fields/functions (Sub 4 of Issue #235's
consolidation plan) once the Dashboard's notification surfaces switched to
consuming ``primary_item`` / ``page_items`` exclusively. The old-vs-new
comparisons this file used to assert are no longer possible (the old side is
gone), so these tests were narrowed to pin that ``select_primary_item``
alone picks the expected item for each representative scenario the parity
tests used to cover -- the scenarios and fixtures are unchanged, only the
old-system half of each assertion was dropped. Two outcomes that used to be
pinned as *intentional divergences* from the old system are now simply the
expected new-system behavior:

1. A "fully satisfied, nothing left to review" system: ``select_primary_item``
   only ever returns problem/follow-up items (``severity != "ok"``), so it
   returns ``None`` -- silence, not a decorative "explore further" nudge.
2. An active build does not unconditionally suppress ``primary_item``: only
   the specific pipeline step(s) the active build is working on become
   "running"/wait (excluded from primary-item candidacy); an unrelated
   outstanding item (e.g. a proposed probe plan awaiting review) is NOT
   suppressed just because a System Understanding build happens to be
   running concurrently. This file only exercises the case with no such
   unrelated confounder (see
   ``test_build_running_suppresses_pipeline_cta``).

This also pins that the ``interview.materialized.rebuild_required``
StateItem -- now the sole, canonical source for what used to be the
``understanding_refresh_recommended`` flag -- appears/disappears exactly
when the underlying materialize-vs-build-completion fact says it should
(``_check_understanding_refresh_recommended``, unchanged, still lives in
``system_understanding_service.py`` and is called directly by
``system_state.py``).
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-parity-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _configure_reasoning_llm(monkeypatch):
    """A non-mock, reasoning-capable LLM config (control-server SKILL.md's
    documented pattern for tests that need user_phase past "setup" --
    LLM_PROVIDER=mock otherwise pins intelligence_llm_config to "blocked"
    and user_phase to "setup", which would phase-suppress every item this
    file's ``primary_item`` assertions depend on).
    """
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-20250514")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup(client, name="parity-sys"):
    token = _login(client)
    sys = _create_system(client, token, name)
    return sys, _headers(token, sys["id"])


def _init_git_repo(tmp_path, name="repo"):
    import subprocess

    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    (repo / "main.py").write_text("def run():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _configure_repo_and_snapshot(client, hdrs, tmp_path):
    repo, sha = _init_git_repo(tmp_path)
    r = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
        headers=hdrs,
    )
    assert r.status_code == 200, r.text
    snap = client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
    assert snap.status_code == 201, snap.text
    return snap.json()["id"]


def _insert_intelligence_run(system_id, snapshot_id, run_type, status):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method,
                    status, is_mock, started_at, completed_at)
               VALUES (?, ?, ?, 'deterministic', 'none', 'v1', 'v1',
                       'deterministic', ?, 0, 0, 0)""",
            (system_id, snapshot_id, run_type, status),
        )
        return cur.lastrowid


def _insert_build_step(system_id, snapshot_id, step, status, *, build_status="completed"):
    from app.db import get_conn

    with get_conn() as conn:
        build = conn.execute(
            """INSERT INTO system_understanding_builds
                   (system_id, snapshot_id, status, current_step, heartbeat_at, started_at, created_at)
               VALUES (?, ?, ?, NULL, 0, 0, 0)""",
            (system_id, snapshot_id, build_status),
        )
        conn.execute(
            """INSERT INTO system_understanding_build_steps
                   (build_id, system_id, snapshot_id, step, status, artifact_provenance,
                    heartbeat_at, started_at, created_at)
               VALUES (?, ?, ?, ?, ?, '{"chunk_count": 1}', 0, 0, 0)""",
            (build.lastrowid, system_id, snapshot_id, step, status),
        )
        return build.lastrowid


def _insert_understanding_graph_snapshot(system_id, snapshot_id):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO understanding_graph_snapshots
                   (system_id, snapshot_id, graph_json, source_hash, claim_count,
                    confidence_summary, created_at)
               VALUES (?, ?, '{}', '', 0, '{}', 0)""",
            (system_id, snapshot_id),
        )


def _insert_code_symbol_with_metadata(system_id, snapshot_id):
    """A single non-function-kind code symbol (module docstring metadata
    carrier) with full source metadata: satisfies has_code_symbols() /
    docs_code_reconciled AND keeps metadata coverage at 100% (avoiding the
    unrelated "Add source metadata" NextAction), without tripping
    docs_code_reconciler's "code_only" gap detection (which only flags
    function/class/method-kind symbols).
    """
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO code_symbols
                   (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)
               VALUES (?, ?, 'main.py', 'main', 'module', 1, 1)""",
            (snapshot_id, system_id),
        )
        symbol_id = cur.lastrowid
        conn.execute(
            """INSERT INTO symbol_source_metadata
                   (snapshot_id, system_id, symbol_id, path, qualified_name,
                    start_line, end_line, role, capability, raw_block)
               VALUES (?, ?, ?, 'main.py', 'main', 1, 1, 'entrypoint', 'core', 'probe-agent: ...')""",
            (snapshot_id, system_id, symbol_id),
        )
        return symbol_id


def _insert_code_entrypoint(system_id, snapshot_id, entrypoint_id="GET /items", *, handler_symbol_id=None):
    """``handler_symbol_id``, when given, must also have a
    ``symbol_source_metadata`` row (see ``_insert_code_symbol_with_metadata``)
    for ``docs_code_reconciler.reconcile``'s OWN unclassified_entrypoint
    check (keyed on ``handler_symbol_id`` + source metadata / classified
    capability nodes) to agree with ``_detect_extra_gaps``'s SQL-based
    unclassified_entrypoint check (keyed on
    ``capability_hierarchy_nodes.entrypoint_id``) -- the two pre-existing
    gap-detection paths use different classification signals, so a
    test fixture must satisfy both to avoid an unrelated gap confounding
    these scenarios.
    """
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO code_entrypoints
                   (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                    handler_symbol_id, handler_path, handler_qualified_name, line_start, line_end,
                    created_at)
               VALUES (?, ?, 'api', ?, 'api', 'label', ?, 'main.py', 'main', 1, 1, 0)""",
            (system_id, snapshot_id, entrypoint_id, handler_symbol_id),
        )
        return cur.lastrowid


def _insert_purpose_and_capability(system_id, snapshot_id, run_id, *, entrypoint_db_id=None):
    """Insert a purpose node and a capability node. When ``entrypoint_db_id``
    is given, the capability node references it (classifying that
    entrypoint) so docs-code reconciliation does not report an
    unclassified_entrypoint gap for it.
    """
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
                   (system_id, snapshot_id, intelligence_run_id, node_type, name, summary, created_at)
               VALUES (?, ?, ?, 'purpose', 'Test system', 'Serves test items.', 0)""",
            (system_id, snapshot_id, run_id),
        )
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
                   (system_id, snapshot_id, intelligence_run_id, node_type, name, summary,
                    entrypoint_id, created_at)
               VALUES (?, ?, ?, 'capability', 'Item management', 'Manages items.', ?, 0)""",
            (system_id, snapshot_id, run_id, entrypoint_db_id),
        )


def _insert_probe_plan(system_id, snapshot_id, run_id, *, status="proposed"):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id,
                    objective, status, origin, created_at, updated_at)
               VALUES (?, ?, ?, 'feat-1', 'obj', ?, 'manual', 0, 0)""",
            (system_id, snapshot_id, run_id, status),
        )
        return cur.lastrowid


def _insert_validated_patch(plan_id, system_id, snapshot_id):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_patches
                   (plan_id, system_id, snapshot_id, commit_sha, diff,
                    skipped, status, cleanup_state, created_at)
               VALUES (?, ?, ?, 'deadbeef', 'diff', '[]', 'generated', 'not_attempted', 0)""",
            (plan_id, system_id, snapshot_id),
        )
        patch_id = cur.lastrowid
        for variant in ("baseline", "probed"):
            conn.execute(
                """INSERT INTO validation_runs
                       (patch_id, system_id, variant, worktree_path, overall_success,
                        trace_status, network_isolation, cleanup_state, created_at)
                   VALUES (?, ?, ?, '/tmp/x', 1, 'not_checked', 'not_requested', 'not_attempted', 0)""",
                (patch_id, system_id, variant),
            )


def _insert_trace(system_id, component_id="worker"):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO traces
                   (system_id, trace_id, component_id, mode, input_json,
                    output_text, error, duration_ms, timestamp)
               VALUES (?, ?, ?, 'trace', '{}', 'ok', NULL, 1.0, ?)""",
            (system_id, f"trace-{component_id}", component_id, time.time()),
        )


def _insert_active_build(system_id, snapshot_id, *, current_step="symbol_index"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO system_understanding_builds
                   (system_id, snapshot_id, status, current_step, heartbeat_at, started_at, created_at)
               VALUES (?, ?, 'running', ?, ?, ?, ?)""",
            (system_id, snapshot_id, current_step, now, now, now),
        )
        return cur.lastrowid


def _get_primary_item(client, hdrs):
    r = client.get("/system-state", headers=hdrs)
    assert r.status_code == 200, r.text
    return r.json()


def _full_pipeline(client, hdrs, tmp_path, system_id):
    """A system with every deterministic pipeline step complete, Purpose +
    Core Capabilities defined for the current snapshot, and metadata
    coverage/gaps clean (no confounding NextAction/StateItem). Individual
    tests layer probe-plan/connectivity facts on top of this baseline.
    """
    snapshot_id = _configure_repo_and_snapshot(client, hdrs, tmp_path)
    _insert_intelligence_run(system_id, snapshot_id, "symbol_index", "completed")
    _insert_intelligence_run(system_id, snapshot_id, "entrypoint_index", "completed")
    _insert_build_step(system_id, snapshot_id, "documentation_index", "completed")
    _insert_understanding_graph_snapshot(system_id, snapshot_id)
    symbol_id = _insert_code_symbol_with_metadata(system_id, snapshot_id)
    entrypoint_db_id = _insert_code_entrypoint(system_id, snapshot_id, handler_symbol_id=symbol_id)
    run_id = _insert_intelligence_run(system_id, snapshot_id, "capability_hierarchy", "completed")
    _insert_purpose_and_capability(system_id, snapshot_id, run_id, entrypoint_db_id=entrypoint_db_id)
    return snapshot_id


class TestPrimaryRecommendationParity:
    """Representative cases pinning that ``select_primary_item`` picks the
    expected item (Issue #239 narrowed this from an old-vs-new comparison --
    see module docstring).
    """

    def test_repository_not_configured(self, admin_client, monkeypatch):
        # A configured reasoning provider keeps the setup-phase LLM
        # diagnostic "ok" (see _configure_reasoning_llm's docstring) so it
        # does not outrank repository.configuration.missing by severity
        # (LLM_PROVIDER=mock's intelligence_llm_config is "blocked", which
        # would otherwise win regardless of what this test is isolating).
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"]["state_id"] == "repository.configuration.missing"
        assert new["primary_item"]["target_ui"]["route"] == "/repository"

    def test_snapshot_not_ready(self, admin_client, tmp_path, monkeypatch):
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        repo, _sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )

        new = _get_primary_item(admin_client, hdrs)
        # Every diagnostic.pipeline_* check reports severity="blocked" (not
        # "warning") when no ready snapshot exists
        # (system_diagnostics._no_ready_snapshot_check), which outranks the
        # native snapshot.ready.missing item (severity="warning") in
        # select_primary_item's priority order. Both still target the same
        # remediation route (kept as a documented, not asserted-equal, detail
        # since only one item is selected).
        assert new["primary_item"]["state_id"].startswith("diagnostic.pipeline_")
        assert new["primary_item"]["target_ui"]["route"] == "/repository"
        assert new["primary_item"]["target_ui"]["anchor"] == "snapshot-create"

    def test_symbols_not_indexed_only(self, admin_client, tmp_path, monkeypatch):
        """A single incomplete pipeline step selects the specific
        pipeline.symbol_index item, targeting the System Understanding build
        action.
        """
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _configure_repo_and_snapshot(admin_client, hdrs, tmp_path)
        _insert_intelligence_run(sys["id"], snapshot_id, "entrypoint_index", "completed")
        _insert_build_step(sys["id"], snapshot_id, "documentation_index", "completed")
        _insert_understanding_graph_snapshot(sys["id"], snapshot_id)
        symbol_id = _insert_code_symbol_with_metadata(sys["id"], snapshot_id)
        entrypoint_db_id = _insert_code_entrypoint(sys["id"], snapshot_id, handler_symbol_id=symbol_id)
        run_id = _insert_intelligence_run(sys["id"], snapshot_id, "capability_hierarchy", "completed")
        _insert_purpose_and_capability(sys["id"], snapshot_id, run_id, entrypoint_db_id=entrypoint_db_id)
        # symbol_index itself never ran -- everything else (entrypoints,
        # documentation, capability hierarchy, docs-code reconciliation) is
        # satisfied so this is the only outstanding pipeline step.

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"]["state_id"].startswith("pipeline.symbol_index.")
        assert new["primary_item"]["target_ui"]["anchor"] == "build"

    def test_pipeline_complete_without_purpose(self, admin_client, tmp_path, monkeypatch):
        """Every pipeline step complete (capability_hierarchy_ready only
        requires capability_count > 0, not a purpose node -- see
        _check_capability_hierarchy_ready / _capability_count_in_snapshot),
        but no purpose node/draft exists for this snapshot: the
        understanding.purpose.missing_baseline item is selected, targeting
        Interview.
        """
        from app.db import get_conn

        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _configure_repo_and_snapshot(admin_client, hdrs, tmp_path)
        _insert_intelligence_run(sys["id"], snapshot_id, "symbol_index", "completed")
        _insert_intelligence_run(sys["id"], snapshot_id, "entrypoint_index", "completed")
        _insert_build_step(sys["id"], snapshot_id, "documentation_index", "completed")
        _insert_understanding_graph_snapshot(sys["id"], snapshot_id)
        symbol_id = _insert_code_symbol_with_metadata(sys["id"], snapshot_id)
        entrypoint_db_id = _insert_code_entrypoint(sys["id"], snapshot_id, handler_symbol_id=symbol_id)
        run_id = _insert_intelligence_run(sys["id"], snapshot_id, "capability_hierarchy", "completed")
        with get_conn() as conn:
            # A capability node only -- no purpose node/draft anywhere for
            # this system, so System Purpose has no baseline at all.
            conn.execute(
                """INSERT INTO capability_hierarchy_nodes
                       (system_id, snapshot_id, intelligence_run_id, node_type, name, summary,
                        entrypoint_id, created_at)
                   VALUES (?, ?, ?, 'capability', 'Item management', 'Manages items.', ?, 0)""",
                (sys["id"], snapshot_id, run_id, entrypoint_db_id),
            )

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"]["state_id"] == "understanding.purpose.missing_baseline"
        assert new["primary_item"]["target_ui"]["route"] == "/interview"

    def test_proposed_probe_plan_awaiting_review(self, admin_client, tmp_path, monkeypatch):
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _full_pipeline(admin_client, hdrs, tmp_path, sys["id"])
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        _insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="proposed")

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"]["state_id"] == "proposal.probe_plans.proposed"
        assert new["primary_item"]["target_ui"]["route"] == "/probe-planner"

    def test_approved_probe_plan_without_validated_patch(self, admin_client, tmp_path, monkeypatch):
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _full_pipeline(admin_client, hdrs, tmp_path, sys["id"])
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        _insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="approved")

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"]["state_id"] == "proposal.probe_plans.approved_without_patch"
        assert new["primary_item"]["target_ui"]["route"] == "/probe-planner"

    def test_fully_satisfied_is_silent(self, admin_client, tmp_path, monkeypatch):
        """Once every problem/follow-up condition is satisfied,
        select_primary_item returns no primary_item at all -- silence, not a
        decorative "explore further" nudge (see module docstring #1)."""
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _full_pipeline(admin_client, hdrs, tmp_path, sys["id"])
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        plan_id = _insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="approved")
        _insert_validated_patch(plan_id, sys["id"], snapshot_id)
        _insert_trace(sys["id"])

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"] is None


class TestBuildRunningSuppression:
    def test_build_running_suppresses_pipeline_cta(
        self, admin_client, tmp_path, monkeypatch,
    ):
        """A queued/running build only marks the pipeline step(s) it is
        actively working on as "running"/wait (excluded from primary-item
        candidacy); it has no blanket suppression rule. This test reaches
        primary_item == None because nothing else is actually outstanding:
        every pipeline step, Purpose/Capabilities, and the
        instrumentation-path signal are all already satisfied, so a newly
        queued/running Refresh build (which has not yet produced any new
        run/step rows of its own) does not by itself introduce any
        actionable StateItem (see module docstring #2).
        """
        _configure_reasoning_llm(monkeypatch)
        sys, hdrs = _setup(admin_client)
        snapshot_id = _full_pipeline(admin_client, hdrs, tmp_path, sys["id"])
        # Satisfy the preparation instrumentation-path signal so nothing
        # else (e.g. runtime.connectivity.no_signal) competes for
        # primary_item.
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        plan_id = _insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="approved")
        _insert_validated_patch(plan_id, sys["id"], snapshot_id)
        _insert_trace(sys["id"])

        # A user clicks Refresh: a new build row is queued/running, but it
        # has not produced any new intelligence_runs/build_steps rows yet --
        # the previously completed rows are still each step's latest.
        _insert_active_build(sys["id"], snapshot_id, current_step="symbol_index")

        new = _get_primary_item(admin_client, hdrs)
        assert new["primary_item"] is None


class TestRebuildRequiredStateItem:
    """Issue #239: the interview.materialized.rebuild_required StateItem is
    the sole, canonical source for what the deleted
    ``understanding_refresh_recommended`` API flag used to expose. Its
    presence/absence must still track the same underlying fact
    (``_check_understanding_refresh_recommended``, unchanged) that used to
    back the flag.
    """

    def _insert_materialized_session(self, system_id, snapshot_id, materialized_at):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, materialized_at, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0)""",
                (system_id, snapshot_id, materialized_at),
            )

    def _insert_build(self, system_id, snapshot_id, status, completed_at):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO system_understanding_builds
                       (system_id, snapshot_id, status, completed_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (system_id, snapshot_id, status, completed_at, completed_at or 0),
            )

    def test_item_present_when_materialize_postdates_build(self, admin_client, tmp_path):
        from app.db import get_conn
        from app.system_understanding_service import _check_understanding_refresh_recommended

        sys, hdrs = _setup(admin_client)
        snapshot_id = _configure_repo_and_snapshot(admin_client, hdrs, tmp_path)
        self._insert_build(sys["id"], snapshot_id, "completed", completed_at=10)
        self._insert_materialized_session(sys["id"], snapshot_id, materialized_at=20)

        with get_conn() as conn:
            assert _check_understanding_refresh_recommended(conn, sys["id"]) is True

        new = _get_primary_item(admin_client, hdrs)
        assert any(
            i["state_id"] == "interview.materialized.rebuild_required" for i in new["items"]
        )

    def test_item_absent_when_build_postdates_materialize(self, admin_client, tmp_path):
        from app.db import get_conn
        from app.system_understanding_service import _check_understanding_refresh_recommended

        sys, hdrs = _setup(admin_client)
        snapshot_id = _configure_repo_and_snapshot(admin_client, hdrs, tmp_path)
        self._insert_build(sys["id"], snapshot_id, "completed", completed_at=20)
        self._insert_materialized_session(sys["id"], snapshot_id, materialized_at=10)

        with get_conn() as conn:
            assert _check_understanding_refresh_recommended(conn, sys["id"]) is False

        new = _get_primary_item(admin_client, hdrs)
        assert not any(i["state_id"] == "interview.materialized.rebuild_required" for i in new["items"])

    def test_item_absent_without_materialized_session(self, admin_client, tmp_path):
        from app.db import get_conn
        from app.system_understanding_service import _check_understanding_refresh_recommended

        sys, hdrs = _setup(admin_client)
        snapshot_id = _configure_repo_and_snapshot(admin_client, hdrs, tmp_path)
        self._insert_build(sys["id"], snapshot_id, "completed", completed_at=10)

        with get_conn() as conn:
            assert _check_understanding_refresh_recommended(conn, sys["id"]) is False

        new = _get_primary_item(admin_client, hdrs)
        assert not any(i["state_id"] == "interview.materialized.rebuild_required" for i in new["items"])
