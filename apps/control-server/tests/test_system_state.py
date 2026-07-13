"""Tests for Issue #193: the System State Assessment layer.

Covers GET /system-state: the normalized, deterministic, LLM-free state
model for System Understanding / snapshot / pipeline state, and that the
Diagnostics dialog and Assistant screen context share the same evidence
(system_diagnostics.evaluate_understanding is backed by
system_state.evaluate_understanding).
"""

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-state-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT",
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
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _setup(client, name="state-sys"):
    token = _login(client)
    sys = _create_system(client, token, name)
    return token, sys, _headers(token, sys["id"])


def _init_git_repo(tmp_path, name="repo"):
    import subprocess

    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test\n")
    (repo / "main.py").write_text("def run():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _commit_file(repo, path, content, message="change"):
    import subprocess

    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _insert_confirmed_understanding(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    understanding = {
        "system_purpose": [{
            "name": "Probe repository inspection", "summary": "Inspect repositories.",
            "confidence": {"level": "confirmed", "reason": "manual"},
            "evidence": [], "why_core": "", "related_docs": [], "related_apis": [], "children": [],
        }],
        "core_capabilities": [{
            "name": "Repository scanning", "summary": "Create snapshots and index code.",
            "confidence": {"level": "confirmed", "reason": "manual"},
            "evidence": [], "why_core": "", "related_docs": [], "related_apis": [], "children": [],
        }],
        "capability_elements": [], "supporting_elements": [],
        "api_boundaries": [], "probe_flow_candidates": [],
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, stage,
                 current_understanding, understanding_confirmed_at,
                 understanding_confirmed_by, created_at, updated_at)
            VALUES (?, ?, 'baseline', '', 'open', 'proposal_generation',
                    ?, ?, 'tester', ?, ?)
            """,
            (system_id, snapshot_id, json.dumps(understanding), now, now, now),
        )


def _insert_unconfirmed_understanding(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    understanding = {
        "system_purpose": [{
            "name": "Probe repository inspection", "summary": "Inspect repositories.",
            "confidence": {"level": "likely", "reason": "reasoning"},
            "evidence": [], "why_core": "", "related_docs": [], "related_apis": [], "children": [],
        }],
        "core_capabilities": [{
            "name": "Repository scanning", "summary": "Create snapshots and index code.",
            "confidence": {"level": "likely", "reason": "reasoning"},
            "evidence": [], "why_core": "", "related_docs": [], "related_apis": [], "children": [],
        }],
        "capability_elements": [], "supporting_elements": [],
        "api_boundaries": [], "probe_flow_candidates": [],
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, stage,
                 current_understanding, understanding_confirmed_at,
                 understanding_confirmed_by, created_at, updated_at)
            VALUES (?, ?, 'candidate', '', 'open', 'purpose_confirmation',
                    ?, NULL, NULL, ?, ?)
            """,
            (system_id, snapshot_id, json.dumps(understanding), now, now),
        )


def _insert_snapshot(system_id, *, status="indexing", commit_sha="pending"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, file_count,
                 total_size, indexed_size, created_at, completed_at)
            VALUES (?, '', ?, ?, 0, 0, 0, ?, NULL)
            """,
            (system_id, commit_sha, status, now),
        )
        return cur.lastrowid


def _insert_intelligence_run(system_id, snapshot_id, run_type, status):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, error_details, is_mock,
                 started_at, completed_at)
            VALUES (?, ?, ?, 'mock', 'mock', 'test', 'test', 'deterministic',
                    ?, 'boom', 1, ?, ?)
            """,
            (system_id, snapshot_id, run_type, status, now, now),
        )
        return cur.lastrowid


def _insert_capability_node(system_id, snapshot_id, run_id, *, node_type="capability", name="Cap"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO capability_hierarchy_nodes
                (system_id, snapshot_id, intelligence_run_id, node_type, name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (system_id, snapshot_id, run_id, node_type, name, now),
        )


def _insert_active_build(system_id, snapshot_id, *, current_step="symbol_index"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO system_understanding_builds
                (system_id, snapshot_id, status, current_step, heartbeat_at,
                 started_at, created_at)
            VALUES (?, ?, 'running', ?, ?, ?, ?)
            """,
            (system_id, snapshot_id, current_step, now, now, now),
        )
        return cur.lastrowid


def _insert_build_step(system_id, snapshot_id, build_id, step, status, *, error=None):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO system_understanding_build_steps
                (build_id, system_id, snapshot_id, step, status, error,
                 heartbeat_at, started_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (build_id, system_id, snapshot_id, step, status, error, now, now, now),
        )
        return cur.lastrowid


def _get_state(client, hdrs):
    r = client.get("/system-state", headers=hdrs)
    assert r.status_code == 200, r.text
    data = r.json()
    return data, {i["state_id"]: i for i in data["items"]}


class TestSystemStateBasics:
    def test_requires_auth(self, admin_client):
        r = admin_client.get("/system-state")
        assert r.status_code == 401

    def test_no_snapshot_yields_missing_state(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        data, items = _get_state(admin_client, hdrs)

        assert data["overall_severity"] in ("ok", "info", "warning", "blocked", "error")
        assert "snapshot.ready.missing" in items
        item = items["snapshot.ready.missing"]
        assert item["state_group"] == "snapshot"
        assert item["status"] == "missing"
        assert item["user_action_kind"] == "create_snapshot"
        assert item["intervention_timing"] == "now"
        assert item["target_ui"]["route"] == "/repository"
        assert item["decision_method"] == "deterministic"
        # No ready snapshot means understanding/pipeline items are not evaluated.
        assert not any(i.startswith("understanding.") for i in items)

    def test_response_projects_primary_notifications_and_page_items(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        data, items = _get_state(admin_client, hdrs)

        assert data["primary_item"] is not None
        assert data["primary_item"]["state_id"] in items
        assert data["notification_items"]
        assert "/repository" in data["page_items"]
        assert any(item["state_id"] == data["primary_item"]["state_id"] for item in data["items"])

    def test_primary_selection_is_deterministic_and_prioritizes_severity_then_timing(self):
        from app.system_state import StateItem, select_primary_item

        warning_now = StateItem("warning", "repository", "warning", "missing", "configure", "now", "x", "x", "x")
        error_later = StateItem("error", "pipeline", "error", "failed", "rerun", "before_next_step", "x", "x", "x")
        assert select_primary_item([warning_now, error_later]) is error_later
        early = StateItem("early", "repository", "warning", "missing", "configure", "now", "x", "x", "x")
        late = StateItem("late", "repository", "warning", "missing", "configure", "before_next_step", "x", "x", "x")
        assert select_primary_item([late, early]) is early

    def test_notification_items_are_priority_ordered_not_alphabetical(self, admin_client):
        # Regression for Issue #207/#208: notification_items used to inherit
        # _dedupe_items' alphabetical-by-state_id order, so the dashboard's
        # floating notice (notification_items[0]) was not the most important
        # item. It must now match select_primary_item's own choice.
        _, _, hdrs = _setup(admin_client)
        data, _ = _get_state(admin_client, hdrs)

        assert data["primary_item"] is not None
        assert data["notification_items"]
        assert data["notification_items"][0]["state_id"] == data["primary_item"]["state_id"]

    def test_priority_key_sorts_severity_before_alphabetical_state_id(self):
        from app.system_state import StateItem, _priority_key

        # state_id "aaa" would sort first alphabetically, but its severity
        # (warning) must lose to the blocked item's higher-priority severity.
        warning_first_alpha = StateItem(
            "aaa", "repository", "warning", "missing", "configure", "now", "x", "x", "x",
        )
        blocked_last_alpha = StateItem(
            "zzz", "pipeline", "blocked", "blocked", "build", "before_next_step", "x", "x", "x",
        )
        ordered = sorted([warning_first_alpha, blocked_last_alpha], key=_priority_key)
        assert [item.state_id for item in ordered] == ["zzz", "aaa"]

    def test_page_items_excludes_ok_severity_even_with_target_ui(self):
        # Hardening regression: page_items[route][0] renders as a
        # warning-styled action banner in the dashboard, so an "ok" item must
        # never appear there even if it carries a target_ui.
        from app.system_state import StateItem, TargetUi, _build_page_items

        ok_item = StateItem(
            "ok.with_ui", "repository", "ok", "satisfied", "none", "none", "x", "x", "x",
            target_ui=TargetUi(route="/repository", anchor=None, action_label="x"),
        )
        warning_item = StateItem(
            "warning.with_ui", "repository", "warning", "missing", "configure", "now", "x", "x", "x",
            target_ui=TargetUi(route="/repository", anchor=None, action_label="x"),
        )
        page_items = _build_page_items([ok_item, warning_item])
        state_ids = {item.state_id for item in page_items["/repository"]}
        assert state_ids == {"warning.with_ui"}

    def test_page_items_projects_explicit_display_route_and_keeps_target_route(self):
        from app.system_state import StateItem, TargetUi, _build_page_items

        item = StateItem(
            "observed.elsewhere", "pipeline", "warning", "missing", "confirm", "before_next_step", "x", "x", "x",
            target_ui=TargetUi(route="/interview", anchor=None, action_label="Fix"),
            display_routes=["/system-understanding"],
        )
        page_items = _build_page_items([item])
        assert page_items["/system-understanding"] == [item]
        assert page_items["/interview"] == [item]

    def test_page_items_without_display_routes_remain_target_only(self):
        from app.system_state import StateItem, TargetUi, _build_page_items

        item = StateItem(
            "target.only", "repository", "warning", "missing", "configure", "now", "x", "x", "x",
            target_ui=TargetUi(route="/repository", anchor=None, action_label="Fix"),
        )
        assert _build_page_items([item]) == {"/repository": [item]}

    def test_all_items_carry_finite_vocabulary(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)

        _, items = _get_state(admin_client, hdrs)
        assert items
        for item in items.values():
            assert item["severity"] in ("ok", "info", "warning", "blocked", "error")
            assert item["user_action_kind"] in (
                "none", "configure", "create_snapshot", "build", "confirm",
                "review", "rerun", "inspect", "wait",
            )
            assert item["intervention_timing"] in (
                "now", "before_next_step", "optional", "after_build", "none",
            )
            assert item["decision_method"] == "deterministic"


class TestUnderstandingState:
    def test_missing_baseline_is_understanding_missing(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)

        _, items = _get_state(admin_client, hdrs)
        assert items["understanding.purpose.missing_baseline"]["status"] == "missing"
        assert items["understanding.purpose.missing_baseline"]["severity"] == "warning"
        assert items["understanding.purpose.missing_baseline"]["target_ui"]["anchor"] == "interview-purpose"
        assert items["understanding.capabilities.missing_baseline"]["status"] == "missing"

    def test_unconfirmed_understanding_is_unconfirmed_state(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_unconfirmed_understanding(sys["id"], snap.json()["id"])

        _, items = _get_state(admin_client, hdrs)
        purpose = items["understanding.purpose.unconfirmed"]
        caps = items["understanding.capabilities.unconfirmed"]
        assert purpose["status"] == "unconfirmed"
        assert purpose["severity"] == "warning"
        assert purpose["user_action_kind"] == "confirm"
        assert purpose["intervention_timing"] == "before_next_step"
        assert purpose["evidence"]["candidate_count"] == 1
        assert caps["status"] == "unconfirmed"

    def test_confirmed_baseline_unchanged_is_reusable(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap1 = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap1.status_code == 201, snap1.text
        _insert_confirmed_understanding(sys["id"], snap1.json()["id"])

        sha2 = _commit_file(repo, "main.py", "def run():\n    return 2\n", "implementation")
        snap2 = admin_client.post("/repository/snapshots", json={"commit_sha": sha2}, headers=hdrs)
        assert snap2.status_code == 201, snap2.text

        _, items = _get_state(admin_client, hdrs)
        purpose = items["understanding.purpose.baseline_reusable"]
        caps = items["understanding.capabilities.baseline_reusable"]
        assert purpose["status"] == "satisfied"
        assert purpose["severity"] == "ok"
        assert purpose["user_action_kind"] == "none"
        assert caps["status"] == "satisfied"

    def test_confirmed_baseline_with_doc_diff_is_impacted(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap1 = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap1.status_code == 201, snap1.text
        _insert_confirmed_understanding(sys["id"], snap1.json()["id"])

        sha2 = _commit_file(repo, "README.md", "# Test\n\nNew purpose wording.\n", "docs")
        snap2 = admin_client.post("/repository/snapshots", json={"commit_sha": sha2}, headers=hdrs)
        assert snap2.status_code == 201, snap2.text

        _, items = _get_state(admin_client, hdrs)
        purpose = items["understanding.purpose.diff_impacted"]
        caps = items["understanding.capabilities.diff_impacted"]
        assert purpose["status"] == "impacted"
        assert purpose["severity"] == "warning"
        assert purpose["user_action_kind"] == "confirm"
        assert "README.md" in purpose["detail"]
        assert purpose["evidence"]["impact_status"] == "directly_impacted"
        assert caps["status"] == "impacted"


class TestPipelineState:
    def test_head_ahead_of_snapshot_is_canonical_repository_state(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put("/repository", json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []}, headers=hdrs)
        created = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert created.status_code == 201, created.text
        _commit_file(repo, "main.py", "def run():\n    return 2\n")

        data, items = _get_state(admin_client, hdrs)
        stale = items["repository.snapshot.stale"]
        assert stale["user_action_kind"] == "create_snapshot"
        assert stale["target_ui"] == {"route": "/repository", "anchor": "snapshot-create", "action_label": "Snapshot を作成"}
        # A blocking configuration diagnostic may correctly outrank this item;
        # regardless, stale HEAD is part of the global notification projection.
        assert "repository.snapshot.stale" in {item["state_id"] for item in data["notification_items"]}

    def test_indexing_snapshot_is_running_wait_state(self, admin_client):
        _, sys, hdrs = _setup(admin_client)
        snap_id = _insert_snapshot(sys["id"], status="indexing")

        _, items = _get_state(admin_client, hdrs)
        item = items["snapshot.ready.running"]
        assert item["status"] == "running"
        assert item["severity"] == "info"
        assert item["user_action_kind"] == "wait"
        assert item["intervention_timing"] == "none"
        assert item["evidence"]["latest_snapshot_id"] == snap_id
        assert "snapshot.ready.missing" not in items

    def test_repository_configured_without_snapshot_has_no_stale_item(self, admin_client, tmp_path):
        # Regression for Issue #207: repository.snapshot.stale used to fire
        # whenever latest_ready was None, duplicating snapshot.ready.missing
        # with a misleading "HEAD が最新 snapshot より進んでいます" summary.
        # It must only fire once a ready snapshot exists and HEAD has moved.
        _, _, hdrs = _setup(admin_client)
        repo, _sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )

        _, items = _get_state(admin_client, hdrs)
        assert "repository.snapshot.stale" not in items
        assert "snapshot.ready.missing" in items

    def test_failed_run_is_failed_error_state(self, admin_client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.system_understanding_service._is_reasoning_model_available",
            lambda: True,
        )
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_intelligence_run(sys["id"], snap.json()["id"], "symbol_index", "failed")

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.symbol_index.failed"]
        assert item["status"] == "failed"
        assert item["severity"] == "error"
        assert item["user_action_kind"] == "rerun"
        assert "pipeline.symbol_index.not_run" not in items

    def test_active_build_without_run_is_running_wait_state(self, admin_client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.system_understanding_service._is_reasoning_model_available",
            lambda: True,
        )
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        build_id = _insert_active_build(sys["id"], snap.json()["id"], current_step="symbol_index")

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.symbol_index.running"]
        assert item["status"] == "running"
        assert item["severity"] == "info"
        assert item["user_action_kind"] == "wait"
        assert item["intervention_timing"] == "none"
        assert item["evidence"]["active_build_id"] == build_id

    def test_running_build_step_is_running_wait_state(self, admin_client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.system_understanding_service._is_reasoning_model_available",
            lambda: True,
        )
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        build_id = _insert_active_build(sys["id"], snap.json()["id"], current_step="documentation_index")
        _insert_build_step(sys["id"], snap.json()["id"], build_id, "documentation_index", "running")

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.documentation_index.running"]
        assert item["status"] == "running"
        assert item["severity"] == "info"
        assert item["user_action_kind"] == "wait"
        assert "pipeline.documentation_index.failed" not in items

    def test_capability_hierarchy_missing_reasoning_is_blocked(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.capability_hierarchy.blocked_by_reasoning"]
        assert item["status"] == "blocked"
        assert item["severity"] == "blocked"
        assert item["user_action_kind"] == "configure"

    def test_capability_hierarchy_failed_plus_missing_reasoning_stays_blocked(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_intelligence_run(sys["id"], snap.json()["id"], "capability_hierarchy", "failed")

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.capability_hierarchy.blocked_by_reasoning"]
        assert item["status"] == "blocked"
        assert item["severity"] == "blocked"
        assert item["user_action_kind"] == "configure"
        assert "pipeline.capability_hierarchy.failed" not in items

    def test_capability_hierarchy_not_run_with_reasoning_available_is_missing(
        self, admin_client, tmp_path, monkeypatch
    ):
        """Regression: no run + reasoning available -> plain "not_run"
        (missing), not blocked_by_reasoning and not the new empty-result item."""
        monkeypatch.setattr(
            "app.system_understanding_service._is_reasoning_model_available",
            lambda: True,
        )
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text

        _, items = _get_state(admin_client, hdrs)
        item = items["pipeline.capability_hierarchy.not_run"]
        assert item["status"] == "missing"
        assert item["severity"] == "warning"
        assert "pipeline.capability_hierarchy.blocked_by_reasoning" not in items
        assert "pipeline.capability_hierarchy.empty" not in items

    def test_capability_hierarchy_completed_zero_capabilities_is_warning_item(
        self, admin_client, tmp_path, monkeypatch
    ):
        """Issue #210: a completed run with zero capability nodes must not
        silently disappear (return None, same as a genuinely "done" run).
        It gets a distinct state_id whose remediation points at
        Interview/metadata, not the generic "Build / Refresh を実行してください"
        used for not-yet-run/failed/blocked pipeline steps."""
        # Issue #237: page_items is phase-suppressed. A real reasoning LLM
        # config completes the setup phase (LLM_PROVIDER=mock pins
        # intelligence_llm_config to "blocked" and thus user_phase to
        # "setup", which would hide this preparation-phase item from
        # page_items -- the item itself is still asserted via `items`).
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-20250514")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_intelligence_run(sys["id"], snap.json()["id"], "capability_hierarchy", "completed")

        _, items = _get_state(admin_client, hdrs)
        assert "pipeline.capability_hierarchy.blocked_by_reasoning" not in items
        assert "pipeline.capability_hierarchy.not_run" not in items
        item = items["pipeline.capability_hierarchy.empty"]
        assert item["status"] == "missing"
        assert item["severity"] == "warning"
        assert item["user_action_kind"] == "confirm"
        assert "Interview" in item["remediation"]
        assert "新しい snapshot" in item["remediation"]
        assert item["display_routes"] == ["/system-understanding"]

        assessment = admin_client.get("/system-state", headers=hdrs).json()
        page_items = assessment["page_items"]
        assert "pipeline.capability_hierarchy.empty" in {
            projected["state_id"] for projected in page_items["/system-understanding"]
        }
        assert "pipeline.capability_hierarchy.empty" in {
            projected["state_id"] for projected in page_items["/interview"]
        }

    def test_capability_hierarchy_completed_with_capabilities_returns_no_item(
        self, admin_client, tmp_path
    ):
        """Regression: a completed run that actually produced capabilities
        stays "done" (no state item), same as before this issue."""
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        snapshot_id = snap.json()["id"]
        run_id = _insert_intelligence_run(
            sys["id"], snapshot_id, "capability_hierarchy", "completed"
        )
        _insert_capability_node(sys["id"], snapshot_id, run_id)

        _, items = _get_state(admin_client, hdrs)
        assert "pipeline.capability_hierarchy.empty" not in items
        assert "pipeline.capability_hierarchy.blocked_by_reasoning" not in items
        assert "pipeline.capability_hierarchy.not_run" not in items


class TestDiagnosticsProjectionCompatibility:
    """Diagnostics stays backward compatible while sharing evidence with state assessment."""

    def test_diagnostics_understanding_checks_match_state_evidence(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_unconfirmed_understanding(sys["id"], snap.json()["id"])

        diag = admin_client.get("/system-diagnostics", headers=hdrs)
        assert diag.status_code == 200, diag.text
        diag_checks = {c["check_id"]: c for c in diag.json()["checks"]}

        _, state_items = _get_state(admin_client, hdrs)

        assert diag_checks["system_purpose"]["severity"] == "warning"
        assert state_items["understanding.purpose.unconfirmed"]["severity"] == "warning"
        assert diag_checks["system_capabilities"]["severity"] == "warning"
        assert state_items["understanding.capabilities.unconfirmed"]["severity"] == "warning"

    def test_diagnostics_covered_by_native_related_checks_are_not_duplicated(self, admin_client):
        # Regression for Issue #207: the same root cause (no repository
        # configured, no ready snapshot) used to appear twice in `items`:
        # once as the native repository.configuration.missing /
        # snapshot.ready.missing item, and again as diagnostic.repository_config
        # / diagnostic.snapshot_status, because those native items declare
        # the check via related_checks but build_system_state projected every
        # non-ok diagnostic unconditionally.
        _, _, hdrs = _setup(admin_client)

        _, items = _get_state(admin_client, hdrs)

        assert "repository.configuration.missing" in items
        assert "snapshot.ready.missing" in items
        assert "diagnostic.repository_config" not in items
        assert "diagnostic.snapshot_status" not in items
        # A diagnostic check with no covering native item must still be
        # projected. In this default (no LLM configured) test setup the
        # llm_base_config check reliably fires and has no native counterpart.
        assert "diagnostic.llm_base_config" in items

    def test_reasoning_not_run_stays_informational_while_pipeline_warning_is_primary(self):
        # Issue #232: no reasoning run is an observation, while the missing
        # symbol index is a real pipeline warning. The former must not become
        # a warning/banner/CTA merely because it is projected into System State.
        from app.system_state import StateItem, TargetUi, _diagnostic_state_item, select_primary_item
        from app.system_diagnostics import DiagnosticCheck
        check = DiagnosticCheck(
            check_id="llm_last_run", category="llm",
            title="直近の reasoning モデル実行", severity="unknown",
            detail="reasoning 実行は未記録です。", impact="",
            remediation="任意で reasoning 機能の疎通を確認できます。",
            fix_kind="navigate", fix_page="/system-understanding", fix_anchor="build",
        )
        reasoning = _diagnostic_state_item(check)
        pipeline = StateItem(
            "pipeline.symbol_index.not_run", "pipeline", "warning", "missing", "build",
            "before_next_step", "シンボル索引", "シンボル索引が未実行です。", "未実行です。",
            target_ui=TargetUi("/system-understanding", "build", "Build / Refresh を実行"),
        )

        assert reasoning.severity == "info"
        assert reasoning.status == "unconfirmed"
        assert reasoning.user_action_kind == "none"
        assert reasoning.target_ui is None
        assert select_primary_item([reasoning, pipeline]) is pipeline


class TestAssistantScreenContextSharesState:
    def test_screen_context_reflects_unconfirmed_understanding_severity(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_unconfirmed_understanding(sys["id"], snap.json()["id"])

        r = admin_client.get("/assistant/screen-context/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        ctx = r.json()
        assert ctx["state_severity"] != "ok"
        checks_by_id = {c["check_id"]: c for c in ctx["screen_checks"]}
        assert checks_by_id["system_purpose"]["severity"] == "warning"
        assert checks_by_id["system_capabilities"]["severity"] == "warning"


class TestIssue236FactsExtractionRegression:
    """Issue #236: repository config, ready-snapshot, pipeline-step, and
    Purpose/Capabilities facts were consolidated into app/state_facts.py.
    This pins GET /system-state's response shape across representative
    scenarios (unconfigured / snapshot only / pipeline complete) so a
    regression in the shared facts layer is caught here.
    """

    def test_unconfigured_system_response_shape(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, items = _get_state(admin_client, hdrs)

        assert items["repository.configuration.missing"]["status"] == "missing"
        assert items["snapshot.ready.missing"]["status"] == "missing"
        assert not any(i.startswith("pipeline.") for i in items)
        assert not any(i.startswith("understanding.") for i in items)

    def test_snapshot_only_response_shape(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text

        _, items = _get_state(admin_client, hdrs)
        assert "repository.configuration.missing" not in items
        assert "snapshot.ready.missing" not in items
        assert "repository.snapshot.stale" not in items
        assert items["understanding.purpose.missing_baseline"]["status"] == "missing"
        assert items["understanding.capabilities.missing_baseline"]["status"] == "missing"
        assert items["pipeline.symbol_index.not_run"]["status"] == "missing"

    def test_pipeline_complete_response_shape(self, admin_client, tmp_path):
        """Every pipeline step system_state.py tracks is complete and
        Purpose/Core Capabilities are satisfied for the current snapshot --
        the highest-risk scenario for state_facts.py since every pipeline
        raw-fact getter and both purpose/capability base facts are exercised
        for a non-empty result at once."""
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        snapshot_id = snap.json()["id"]

        _insert_intelligence_run(sys["id"], snapshot_id, "symbol_index", "completed")
        _insert_intelligence_run(sys["id"], snapshot_id, "entrypoint_index", "completed")
        build_id = _insert_active_build(sys["id"], snapshot_id)
        _insert_build_step(sys["id"], snapshot_id, build_id, "documentation_index", "completed")
        run_id = _insert_intelligence_run(sys["id"], snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="purpose", name="Test system")
        _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="capability", name="Item management")

        data, items = _get_state(admin_client, hdrs)
        assert not any(i.startswith("pipeline.") for i in items)
        assert "repository.configuration.missing" not in items
        assert "snapshot.ready.missing" not in items
        assert "repository.snapshot.stale" not in items
        assert items["understanding.purpose.satisfied"]["status"] == "satisfied"
        assert items["understanding.purpose.satisfied"]["severity"] == "ok"
        assert items["understanding.capabilities.satisfied"]["status"] == "satisfied"
        assert items["understanding.capabilities.satisfied"]["severity"] == "ok"

        # /repository/system-understanding agrees: no purpose/capabilities gap.
        su_r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert su_r.status_code == 200, su_r.text
        su_data = su_r.json()
        assert su_data["purpose"]["name"] == "Test system"
        assert len(su_data["capabilities"]) == 1
        labels = [a["action"] for a in su_data["next_actions"]]
        assert "Define System Purpose" not in labels
        assert "Identify main system capabilities" not in labels


class TestDeriveUserPhase:
    """Issue #237: derive_user_phase is a pure, DB-free function of
    UserPhaseFacts. Boundary cases mirror the issue's required test list
    (未設定 / 環境診断 error / snapshot のみ / pipeline 途中 / Purpose 未確定
    / capability 0 件 / probe plan 承認済みでトレース無し / 受信中)."""

    def _facts(self, **overrides):
        from app.system_state import UserPhaseFacts

        base = dict(
            repository_configured=True,
            setup_diagnostics_blocking=False,
            ready_snapshot_exists=True,
            pipeline_all_complete=True,
            purpose_satisfied=True,
            capabilities_satisfied=True,
            approved_probe_plan_count=0,
            connectivity_state="no_signal",
        )
        base.update(overrides)
        return UserPhaseFacts(**base)

    def test_repository_not_configured_is_setup(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(repository_configured=False))
        assert result.user_phase == "setup"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase == {"setup": False, "preparation": False, "diagnosis": False}

    def test_blocking_environment_diagnostic_is_setup(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(
            self._facts(repository_configured=True, setup_diagnostics_blocking=True)
        )
        assert result.user_phase == "setup"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase["setup"] is False

    def test_snapshot_only_no_pipeline_is_preparation(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(
            ready_snapshot_exists=True, pipeline_all_complete=False,
            purpose_satisfied=False, capabilities_satisfied=False,
        ))
        assert result.user_phase == "preparation"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase["setup"] is True
        assert by_phase["preparation"] is False

    def test_pipeline_partial_is_preparation(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(pipeline_all_complete=False))
        assert result.user_phase == "preparation"

    def test_purpose_unconfirmed_is_preparation(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(purpose_satisfied=False))
        assert result.user_phase == "preparation"

    def test_zero_capabilities_is_preparation(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(capabilities_satisfied=False))
        assert result.user_phase == "preparation"

    def test_no_instrumentation_signal_stays_preparation(self):
        from app.system_state import derive_user_phase

        # Every other condition satisfied, but neither an approved probe
        # plan nor non-"no_signal" connectivity exists yet.
        result = derive_user_phase(self._facts())
        assert result.user_phase == "preparation"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase == {"setup": True, "preparation": False, "diagnosis": False}

    def test_approved_plan_without_traces_reaches_diagnosis(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(
            self._facts(approved_probe_plan_count=1, connectivity_state="no_signal")
        )
        assert result.user_phase == "diagnosis"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase == {"setup": True, "preparation": True, "diagnosis": False}

    def test_receiving_traces_without_approved_plan_reaches_diagnosis(self):
        from app.system_state import derive_user_phase

        result = derive_user_phase(
            self._facts(approved_probe_plan_count=0, connectivity_state="receiving")
        )
        assert result.user_phase == "diagnosis"

    def test_smoke_only_connectivity_also_satisfies_instrumentation_signal(self):
        # Issue #237's completion rule is "connectivity != no_signal", not
        # "connectivity == receiving": a smoke-check trace still proves the
        # instrumentation path is wired up structurally.
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(connectivity_state="smoke_only"))
        assert result.user_phase == "diagnosis"

    def test_setup_incomplete_forces_preparation_incomplete_regardless_of_other_facts(self):
        # Preparation cannot be "complete" while setup itself is not --
        # otherwise the phases list would report a later phase done while an
        # earlier one is not, which is incoherent for a strict progression.
        from app.system_state import derive_user_phase

        result = derive_user_phase(self._facts(
            repository_configured=False,
            approved_probe_plan_count=1, connectivity_state="receiving",
        ))
        assert result.user_phase == "setup"
        by_phase = {p.phase: p.complete for p in result.phases}
        assert by_phase["preparation"] is False


class TestPhaseTagging:
    """Issue #237: _phase_for_item applies STATE_GROUP_PHASE by default, with
    STATE_ID_PHASE_OVERRIDES taking precedence for a small, explicit
    exception list."""

    def test_default_group_mapping(self):
        from app.system_state import StateItem, _phase_for_item

        cases = {
            "repository": "setup",
            "configuration": "setup",
            "snapshot": "preparation",
            "pipeline": "preparation",
            "understanding": "preparation",
            "interview": "preparation",
            "runtime": "diagnosis",
            "proposal": "diagnosis",
        }
        for group, expected in cases.items():
            item = StateItem(f"x.{group}", group, "warning", "missing", "none", "none", "s", "s", "s")
            assert _phase_for_item(item) == expected, group

    def test_connectivity_no_signal_overrides_to_preparation(self):
        from app.system_state import StateItem, _phase_for_item

        item = StateItem(
            "runtime.connectivity.no_signal", "runtime", "warning", "missing",
            "review", "before_next_step", "s", "s", "s",
        )
        assert _phase_for_item(item) == "preparation"

    def test_repository_diagnostic_overrides_to_setup(self):
        from app.system_state import StateItem, _phase_for_item

        for check_id in ("repository_roots", "repository_config", "snapshot_status"):
            item = StateItem(
                f"diagnostic.{check_id}", "runtime", "error", "failed",
                "configure", "now", "s", "s", "s",
            )
            assert _phase_for_item(item) == "setup", check_id

    def test_pipeline_and_understanding_diagnostics_override_to_preparation(self):
        from app.system_state import StateItem, _phase_for_item

        for check_id in (
            "pipeline_symbol_index", "pipeline_entrypoint_index",
            "pipeline_documentation_index", "pipeline_understanding_graph",
            "pipeline_capability_hierarchy", "system_purpose", "system_capabilities",
        ):
            item = StateItem(
                f"diagnostic.{check_id}", "runtime", "warning", "missing",
                "inspect", "before_next_step", "s", "s", "s",
            )
            assert _phase_for_item(item) == "preparation", check_id

    def test_llm_and_auth_and_database_diagnostics_use_configuration_group_default(self):
        # These already land in state_group="configuration" (not "runtime")
        # via _diagnostic_state_item, so the group default alone (no
        # override needed) already yields "setup".
        from app.system_state import StateItem, _phase_for_item

        for check_id in ("llm_base_config", "intelligence_llm_config", "llm_last_run", "auth_scope", "database_storage"):
            item = StateItem(
                f"diagnostic.{check_id}", "configuration", "warning", "missing",
                "configure", "before_next_step", "s", "s", "s",
            )
            assert _phase_for_item(item) == "setup", check_id

    def test_unrelated_runtime_diagnostic_stays_diagnosis(self):
        from app.system_state import StateItem, _phase_for_item

        item = StateItem(
            "diagnostic.some_future_check", "runtime", "warning", "missing",
            "inspect", "before_next_step", "s", "s", "s",
        )
        assert _phase_for_item(item) == "diagnosis"


class TestUserPhaseIntegration:
    """Issue #237: GET /system-state's user_phase / phases / notification
    suppression, exercised end-to-end through the API."""

    def _configure_reasoning_llm(self, monkeypatch):
        # A non-mock, reasoning-capable LLM config so the setup-phase llm
        # diagnostics (llm_base_config / intelligence_llm_config) come back
        # "ok" instead of the "blocked" severity LLM_PROVIDER=mock always
        # produces for intelligence_llm_config.
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-20250514")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _insert_probe_plan(self, system_id, snapshot_id, run_id, *, status="proposed"):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, 'feat-1', 'obj', ?, 'manual', 0, 0)""",
                (system_id, snapshot_id, run_id, status),
            )

    def _insert_experiment(self, system_id, snapshot_id, *, status="completed", human_decision="undecided"):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO experiments
                       (system_id, feature_id, objective, snapshot_id,
                        baseline_commit, config_revision, execution_config,
                        status, human_decision, created_at)
                   VALUES (?, 'feat-1', 'obj', ?, 'deadbeef', 'v1', '{}', ?, ?, 0)""",
                (system_id, snapshot_id, status, human_decision),
            )

    def _insert_trace(self, system_id, component_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO traces
                       (system_id, trace_id, component_id, mode, input_json,
                        output_text, error, duration_ms, timestamp)
                   VALUES (?, ?, ?, 'trace', '{}', 'ok', NULL, 1.0, ?)""",
                (system_id, f"trace-{component_id}", component_id, time.time()),
            )

    def _complete_pipeline(self, admin_client, sys, hdrs, tmp_path):
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        snapshot_id = snap.json()["id"]

        _insert_intelligence_run(sys["id"], snapshot_id, "symbol_index", "completed")
        _insert_intelligence_run(sys["id"], snapshot_id, "entrypoint_index", "completed")
        build_id = _insert_active_build(sys["id"], snapshot_id)
        _insert_build_step(sys["id"], snapshot_id, build_id, "documentation_index", "completed")
        run_id = _insert_intelligence_run(sys["id"], snapshot_id, "capability_hierarchy", "completed")
        _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="purpose", name="Test system")
        _insert_capability_node(sys["id"], snapshot_id, run_id, node_type="capability", name="Item management")
        return snapshot_id

    def test_unconfigured_repository_is_setup_and_suppresses_later_phase_items(self, admin_client):
        _, _, hdrs = _setup(admin_client)

        data, items = _get_state(admin_client, hdrs)
        assert data["user_phase"] == "setup"
        by_phase = {p["phase"]: p["complete"] for p in data["phases"]}
        assert by_phase == {"setup": False, "preparation": False, "diagnosis": False}

        # runtime.connectivity.no_signal is preparation-tagged: present in
        # `items` (audit trail) but suppressed from every notification
        # projection (notification_items / page_items / primary_item) while
        # user_phase is still "setup".
        assert "runtime.connectivity.no_signal" in items
        notif_ids = {i["state_id"] for i in data["notification_items"]}
        assert "runtime.connectivity.no_signal" not in notif_ids
        for notif in data["notification_items"]:
            assert notif["phase"] == "setup"
        page_ids = {
            projected["state_id"]
            for route_items in data["page_items"].values()
            for projected in route_items
        }
        assert "runtime.connectivity.no_signal" not in page_ids
        assert data["primary_item"]["phase"] == "setup"

    def test_pipeline_complete_without_instrumentation_is_preparation(self, admin_client, tmp_path, monkeypatch):
        self._configure_reasoning_llm(monkeypatch)
        _, sys, hdrs = _setup(admin_client)
        snapshot_id = self._complete_pipeline(admin_client, sys, hdrs, tmp_path)
        # A completed, undecided experiment gives a concrete diagnosis-tagged
        # item to prove it stays suppressed while still in "preparation".
        self._insert_experiment(sys["id"], snapshot_id)

        data, items = _get_state(admin_client, hdrs)
        assert data["user_phase"] == "preparation"
        by_phase = {p["phase"]: p["complete"] for p in data["phases"]}
        assert by_phase["setup"] is True
        assert by_phase["preparation"] is False

        assert "proposal.experiments.undecided" in items
        notif_ids = {i["state_id"] for i in data["notification_items"]}
        assert "proposal.experiments.undecided" not in notif_ids
        # Suppression covers page_items and primary_item too (Issue #237:
        # phase scope is the outermost criterion of every projection).
        page_ids = {
            projected["state_id"]
            for route_items in data["page_items"].values()
            for projected in route_items
        }
        assert "proposal.experiments.undecided" not in page_ids
        if data["primary_item"] is not None:
            assert data["primary_item"]["phase"] in ("setup", "preparation")
        # preparation-phase items stay visible while user_phase is
        # "preparation" itself.
        assert "runtime.connectivity.no_signal" in notif_ids

    def test_approved_probe_plan_reaches_diagnosis(self, admin_client, tmp_path, monkeypatch):
        self._configure_reasoning_llm(monkeypatch)
        _, sys, hdrs = _setup(admin_client)
        snapshot_id = self._complete_pipeline(admin_client, sys, hdrs, tmp_path)
        self._insert_experiment(sys["id"], snapshot_id)
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        self._insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="approved")

        data, items = _get_state(admin_client, hdrs)
        assert data["user_phase"] == "diagnosis"
        by_phase = {p["phase"]: p["complete"] for p in data["phases"]}
        assert by_phase == {"setup": True, "preparation": True, "diagnosis": False}

        # Diagnosis-phase items are now visible in notification_items too.
        notif_ids = {i["state_id"] for i in data["notification_items"]}
        assert "proposal.experiments.undecided" in notif_ids
        # An approved plan already satisfies the instrumentation-path
        # condition, so the still-no_signal connectivity item softens from
        # "warning"/blocking to "info".
        assert items["runtime.connectivity.no_signal"]["severity"] == "info"

    def test_receiving_traces_without_probe_plan_reaches_diagnosis(self, admin_client, tmp_path, monkeypatch):
        self._configure_reasoning_llm(monkeypatch)
        _, sys, hdrs = _setup(admin_client)
        self._complete_pipeline(admin_client, sys, hdrs, tmp_path)
        self._insert_trace(sys["id"], "worker-component")

        data, items = _get_state(admin_client, hdrs)
        assert data["user_phase"] == "diagnosis"
        # Real traces mean connectivity_state != "no_signal", so the
        # connectivity item no longer fires at all.
        assert "runtime.connectivity.no_signal" not in items

    def test_proposed_but_not_approved_probe_plan_does_not_satisfy_instrumentation(
        self, admin_client, tmp_path, monkeypatch
    ):
        self._configure_reasoning_llm(monkeypatch)
        _, sys, hdrs = _setup(admin_client)
        snapshot_id = self._complete_pipeline(admin_client, sys, hdrs, tmp_path)
        plan_run_id = _insert_intelligence_run(sys["id"], snapshot_id, "probe_plan", "completed")
        self._insert_probe_plan(sys["id"], snapshot_id, plan_run_id, status="proposed")

        data, _items = _get_state(admin_client, hdrs)
        assert data["user_phase"] == "preparation"
