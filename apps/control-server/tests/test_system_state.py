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
