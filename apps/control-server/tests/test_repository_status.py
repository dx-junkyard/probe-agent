"""Tests for the Repository refresh-hub status endpoint and draft stale flag
(Issue #158).

These verify that reading repository state is strictly read-only (never mutates
the target repository) and that stale detection reflects latest-snapshot-vs-HEAD
divergence.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-status.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token):
    r = client.post(
        "/systems",
        json={"name": "sys", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("# Test\n")
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _configure(client, token, sid, repo):
    r = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": [], "exclude_patterns": []},
        headers=_headers(token, sid),
    )
    assert r.status_code in (200, 201), r.text


def _snapshot(client, token, sid):
    r = client.post("/repository/snapshots", headers=_headers(token, sid))
    assert r.status_code == 201, r.text
    return r.json()


def test_status_unconfigured(admin_client):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)
    r = admin_client.get("/repository/status", headers=_headers(token, sid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is False
    assert body["latest_snapshot"] is None


def test_status_head_and_snapshot_fresh(admin_client, git_repo):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)
    _configure(admin_client, token, sid, git_repo)
    snap = _snapshot(admin_client, token, sid)

    r = admin_client.get("/repository/status", headers=_headers(token, sid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["current_head"] == _head(git_repo)
    assert body["current_head"] == snap["commit_sha"]
    assert body["working_tree_dirty"] is False
    assert body["snapshot_stale"] is False
    assert body["latest_snapshot"]["commit_sha"] == snap["commit_sha"]
    # No index yet -> symbols_stale until Index Symbols runs.
    assert body["symbols_stale"] is True


def test_status_stale_after_new_commit(admin_client, git_repo):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)
    _configure(admin_client, token, sid, git_repo)
    _snapshot(admin_client, token, sid)

    # Advance HEAD after the snapshot was taken.
    (git_repo / "main.py").write_text("def hello():\n    return 2\n")
    _git(git_repo, "commit", "-am", "change")
    new_head = _head(git_repo)

    r = admin_client.get("/repository/status", headers=_headers(token, sid))
    body = r.json()
    assert body["current_head"] == new_head
    assert body["snapshot_stale"] is True
    assert any("new snapshot" in a for a in body["next_actions"])


def test_status_reports_dirty_tree_without_mutation(admin_client, git_repo):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)
    _configure(admin_client, token, sid, git_repo)

    head_before = _head(git_repo)
    (git_repo / "untracked.txt").write_text("scratch\n")

    r = admin_client.get("/repository/status", headers=_headers(token, sid))
    body = r.json()
    assert body["working_tree_dirty"] is True
    assert body["dirty_file_count"] >= 1
    # Reading status must not have committed, added, or otherwise mutated the repo.
    assert _head(git_repo) == head_before
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    assert "untracked.txt" in status  # still untracked, not staged/committed


def test_issue_draft_stale_after_new_snapshot(admin_client, git_repo):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)
    _configure(admin_client, token, sid, git_repo)
    _snapshot(admin_client, token, sid)

    gap = {
        "gap_type": "missing_evidence",
        "severity": "warning",
        "node_name": "hello",
        "title": "gap",
        "doc_refs": [], "symbol_refs": [], "entrypoint_refs": [], "next_actions": [],
    }
    r = admin_client.post(
        "/issue-drafts", json={"gap": gap}, headers=_headers(token, sid)
    )
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["stale"] is False

    # New commit + new snapshot moves the latest commit forward.
    (git_repo / "main.py").write_text("def hello():\n    return 3\n")
    _git(git_repo, "commit", "-am", "change")
    _snapshot(admin_client, token, sid)

    listed = admin_client.get(
        "/issue-drafts", headers=_headers(token, sid)
    ).json()
    assert listed[0]["stale"] is True
    single = admin_client.get(
        f"/issue-drafts/{draft['id']}", headers=_headers(token, sid)
    ).json()
    assert single["stale"] is True
