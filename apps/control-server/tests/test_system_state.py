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
        self, admin_client, tmp_path
    ):
        """Issue #210: a completed run with zero capability nodes must not
        silently disappear (return None, same as a genuinely "done" run).
        It gets a distinct state_id whose remediation points at
        Interview/metadata, not the generic "Build / Refresh を実行してください"
        used for not-yet-run/failed/blocked pipeline steps."""
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
