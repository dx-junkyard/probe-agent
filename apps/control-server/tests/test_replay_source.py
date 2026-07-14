"""Tests for the two deterministic Replay source/diff helpers added for the
Simulation Workbench (Issue #242 Phase D / #246):

  * ``GET /replay-sets/{id}/source`` -- pinned-snapshot file content + the
    resolved symbol's span.
  * ``POST /replay-source-diff`` -- a deterministic unified diff between the
    pinned snapshot and a developer-edited copy of the file (reuses
    ``replay_draft``'s worktree + ``git diff`` mechanism; rejects non-Python
    input with a 422).

Neither endpoint executes code, so ``PROBE_UNSAFE_ALLOW_HOST_EXECUTION`` is
not required here -- only the diff endpoint's git-diff worktree needs a real
git fixture repo. Both endpoints are deliberately reasoning-model-free and
require no replay approval (Principle 6): they only read the pinned snapshot
and perform structural text diffing.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def unsafe_test_execution(monkeypatch):
    # Only test_source_diff_runs_as_a_variant actually executes the harness
    # (the source/diff endpoints themselves never run code); harmless to set
    # for every test in this file.
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "replay-source-test.db"


@pytest.fixture
def admin_client(tmp_path, db_file, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(db_file))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv(
        "PROBE_REPLAY_WORKSPACE_BASE", str(tmp_path / "replay-workspaces")
    )
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client
    get_llm_client.cache_clear()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


SVC_SOURCE = '''\
def probe(component_id=None):
    def deco(fn):
        return fn
    return deco


@probe(component_id="norm")
def normalize(payload):
    return {"kind": "a", "size": len(payload)}
'''


@pytest.fixture
def replay_repo(tmp_path):
    repo = tmp_path / "source-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "svc.py").write_text(SVC_SOURCE)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    response = client.post(
        "/systems", json={"name": name, "environment": "test"}, headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _prepare(client, repo, name="source-system"):
    token = _login(client)
    system = _create_system(client, token, name)
    headers = _headers(token, system["id"])
    assert client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": []},
        headers=headers,
    ).status_code == 200
    snapshot = client.post("/repository/snapshots", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    assert client.post("/repository/symbols/index", headers=headers).status_code == 201
    return token, system, headers, snapshot.json()


def test_replay_management_routes_reject_sdk_api_tokens(admin_client, replay_repo):
    token, system, headers, _ = _prepare(admin_client, replay_repo)
    issued = admin_client.post(
        "/tokens/me",
        json={"name": "sdk-only", "system_id": system["id"]},
        headers=headers,
    )
    assert issued.status_code == 201, issued.text
    sdk_headers = {
        "Authorization": f"Bearer {issued.json()['token']}",
        "X-Probe-System-Id": str(system["id"]),
    }

    response = admin_client.get("/replay-sets", headers=sdk_headers)
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "SDK API tokens cannot access management APIs; use a login session"
    )


def _post_trace(client, headers, trace_id, component_id, *, args=(), output=None):
    import time

    from probe_agent.replay_capture import capture_input, compile_spec

    capture, replayability, reasons = capture_input(
        tuple(args), {}, compile_spec(True)
    )
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [repr(a) for a in args], "kwargs": {}},
        "output": output,
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
        "input_capture": capture,
        "replayability": replayability,
        "replay_reasons": reasons,
    }
    response = client.post("/traces", json=event, headers=headers)
    assert response.status_code == 201, response.text


def _create_set(client, headers, trace_ids, component_id, name="set"):
    response = client.post(
        "/replay-sets",
        json={"component_id": component_id, "name": name, "trace_ids": trace_ids},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assert_repo_clean(repo):
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    )
    assert status.stdout.strip() == ""
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    )
    assert worktrees.stdout.count("worktree ") == 1


# ---------------------------------------------------------------------------
# GET /replay-sets/{id}/source
# ---------------------------------------------------------------------------


def test_get_source_returns_pinned_file_content_and_span(admin_client, replay_repo):
    _, _, headers, snapshot = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")

    response = admin_client.get(
        f"/replay-sets/{replay_set['id']}/source", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["replay_set_id"] == replay_set["id"]
    assert body["component_id"] == "norm"
    assert body["path"] == "svc.py"
    assert body["qualified_name"] == "normalize"
    assert body["commit_sha"] == snapshot["commit_sha"]
    assert body["snapshot_id"] == snapshot["id"]
    assert body["source"] == SVC_SOURCE
    lines = SVC_SOURCE.splitlines()
    # code_symbols.start_line begins at the `def` line, excluding decorators.
    assert lines[body["start_line"] - 1].startswith("def normalize")
    assert lines[body["end_line"] - 1].strip().startswith("return")


def test_get_source_unknown_replay_set_404s(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    response = admin_client.get("/replay-sets/999999/source", headers=headers)
    assert response.status_code == 404


def test_get_source_rejects_foreign_system(admin_client, replay_repo):
    token, _, headers_a, _ = _prepare(admin_client, replay_repo, "sys-a")
    _post_trace(admin_client, headers_a, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers_a, ["t1"], "norm")

    system_b = _create_system(admin_client, token, "sys-b")
    headers_b = _headers(token, system_b["id"])
    response = admin_client.get(
        f"/replay-sets/{replay_set['id']}/source", headers=headers_b
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /replay-source-diff
# ---------------------------------------------------------------------------


def test_source_diff_returns_an_applicable_patch(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")

    edited = SVC_SOURCE.replace('"kind": "a"', '"kind": "b"')
    response = admin_client.post(
        "/replay-source-diff",
        json={"replay_set_id": replay_set["id"], "edited_source": edited},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "kind" in body["patch_text"]
    assert body["patch_text"].startswith("diff --git")
    assert len(body["patch_hash"]) == 64  # sha256 hex digest

    # The returned patch is a real, applicable unified diff against the
    # pinned commit (mirrors how POST /replay-variant-runs would apply it).
    check = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=str(replay_repo),
        input=body["patch_text"],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr

    # The endpoint never mutates the target repository or leaves worktrees
    # behind (Principle 5 / Principle 8).
    _assert_repo_clean(replay_repo)


def test_source_diff_runs_as_a_variant(admin_client, replay_repo):
    """End-to-end: the diff endpoint's patch_text is directly usable as a
    replay variant patch (the Workbench's Direct-edit -> Run flow)."""
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    assert admin_client.post(
        "/components/norm/replay-approval", json={"reason": "test"}, headers=headers
    ).status_code == 201

    edited = SVC_SOURCE.replace('"kind": "a"', '"kind": "b"')
    diff = admin_client.post(
        "/replay-source-diff",
        json={"replay_set_id": replay_set["id"], "edited_source": edited},
        headers=headers,
    ).json()

    run = admin_client.post(
        "/replay-variant-runs",
        json={
            "replay_set_id": replay_set["id"],
            "variants": [
                {"label": "direct-edit", "patch_text": diff["patch_text"], "source": "manual"}
            ],
        },
        headers=headers,
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "completed"
    variant = next(v for v in body["variants"] if not v["is_baseline"])
    assert variant["apply_status"] == "applied"
    assert variant["aggregate"]["diff"] == 1
    assert variant["cases"][0]["field_diffs"] == ["kind"]


def test_source_diff_rejects_non_python_edited_source(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")

    response = admin_client.post(
        "/replay-source-diff",
        json={"replay_set_id": replay_set["id"], "edited_source": "def broken(:\n"},
        headers=headers,
    )
    assert response.status_code == 422
    _assert_repo_clean(replay_repo)


def test_source_diff_rejects_no_op_edit(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")

    response = admin_client.post(
        "/replay-source-diff",
        json={"replay_set_id": replay_set["id"], "edited_source": SVC_SOURCE},
        headers=headers,
    )
    assert response.status_code == 422
    assert "no changes" in response.json()["detail"]


def test_source_diff_rejects_foreign_system(admin_client, replay_repo):
    token, _, headers_a, _ = _prepare(admin_client, replay_repo, "sys-a")
    _post_trace(admin_client, headers_a, "t1", "norm", args=("hi",),
                output="{'kind': 'a', 'size': 2}")
    replay_set = _create_set(admin_client, headers_a, ["t1"], "norm")

    system_b = _create_system(admin_client, token, "sys-b")
    headers_b = _headers(token, system_b["id"])
    edited = SVC_SOURCE.replace('"kind": "a"', '"kind": "b"')
    response = admin_client.post(
        "/replay-source-diff",
        json={"replay_set_id": replay_set["id"], "edited_source": edited},
        headers=headers_b,
    )
    assert response.status_code == 404
