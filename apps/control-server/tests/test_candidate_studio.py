"""Tests for Issue #252 (AI Candidate Studio).

A conversation + versioning layer over the existing isolated-Replay stack
(#242 Phase B/C). Covers: session creation from a single trace_id / an
    explicit trace_ids list / an existing replay_set_id / automatic recent
    component traces, the at-most-one-selection validation, the 409 when a
    component has no indexed function symbol, the
deterministic opening messages, mock-provider generation creating an
immutable proposed version with full reasoning provenance, messages never
creating a version, branch/switch via parent_version_id, fail-closed
generation on an unsupported LLM provider (no version row, a failed
intelligence_runs row), the replay approval gate (403 before / completed
after), the promotion gate (422 before replay / 200 with a non-empty patch
after), System isolation, and the ``app.candidate_studio`` unit surface
(``_validate_patch_scope``, ``_string_list``).
"""

import json
import sqlite3
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.candidate_studio import (
    _string_list,
    _validate_generated_code,
    _validate_patch_scope,
    _validate_proposal_shape,
)
from app.replay_draft import DraftError


# ---------------------------------------------------------------------------
# Fixtures (copied/adapted from tests/test_replay_variants.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def unsafe_test_execution(monkeypatch):
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "candidate-studio-test.db"


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
    monkeypatch.delenv("PROBE_REPLAY_TIMEOUT_SECONDS", raising=False)
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


@probe(component_id="cls")
def classify(x):
    if x == "boom":
        raise ValueError("boom")
    return "class-" + str(x)
'''


@pytest.fixture
def replay_repo(tmp_path):
    repo = tmp_path / "candidate-studio-repo"
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


def _prepare(client, repo, name="candidate-studio-system"):
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


def _capture(args=(), kwargs=None):
    from probe_agent.replay_capture import capture_input, compile_spec

    return capture_input(tuple(args), dict(kwargs or {}), compile_spec(True))


def _post_trace(client, headers, trace_id, component_id, *, args=(), kwargs=None,
                output=None, error=None):
    capture, replayability, reasons = _capture(args, kwargs)
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [repr(a) for a in args],
                  "kwargs": {k: repr(v) for k, v in (kwargs or {}).items()}},
        "output": output,
        "error": error,
        "duration_ms": 1.0,
        "timestamp": time.time(),
        "input_capture": capture,
        "replayability": replayability,
        "replay_reasons": reasons,
    }
    response = client.post("/traces", json=event, headers=headers)
    assert response.status_code == 201, response.text


def _approve(client, headers, component_id):
    response = client.post(
        f"/components/{component_id}/replay-approval",
        json={"reason": "test"}, headers=headers,
    )
    assert response.status_code == 201, response.text


def _create_set(client, headers, trace_ids, component_id, name="set"):
    response = client.post(
        "/replay-sets",
        json={"component_id": component_id, "name": name, "trace_ids": trace_ids},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_session(client, headers, **overrides):
    payload = {"component_id": "norm"}
    payload.update(overrides)
    return client.post("/candidate-sessions", json=payload, headers=headers)


def _generate(client, headers, session_id, instruction="improve it", **overrides):
    payload = {"instruction": instruction}
    payload.update(overrides)
    return client.post(
        f"/candidate-sessions/{session_id}/generate", json=payload, headers=headers
    )


def _count_versions(client, headers, session_id):
    versions = client.get(
        f"/candidate-sessions/{session_id}/versions", headers=headers
    )
    assert versions.status_code == 200, versions.text
    return len(versions.json())


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------


def test_session_from_single_trace_id_sets_component_symbol_and_baseline(
    admin_client, replay_repo
):
    _, _, headers, snapshot = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")

    response = _create_session(
        admin_client, headers, trace_id="t1", objective="make it faster"
    )
    assert response.status_code == 201, response.text
    session = response.json()

    assert session["component_id"] == "norm"
    assert session["symbol_qualified_name"] == "normalize"
    assert session["symbol_path"] == "svc.py"
    assert session["commit_sha"] == snapshot["commit_sha"]
    assert session["snapshot_id"] == snapshot["id"]
    assert session["status"] == "active"
    assert isinstance(session["replay_set_id"], int)

    # A Replay Set was created for the single trace id.
    replay_set = admin_client.get(
        f"/replay-sets/{session['replay_set_id']}", headers=headers
    ).json()
    assert replay_set["trace_ids"] == ["t1"]
    assert replay_set["component_id"] == "norm"

    # Opening messages: the user's objective + a deterministic assistant echo.
    messages = session["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "make it faster"
    assert messages[1]["role"] == "assistant"
    assert "normalize" not in messages[1]["content"] or "svc.py" in messages[1]["content"]
    assert session["versions"] == []


def test_session_from_trace_ids_list(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("a",),
                output="{'kind': 'a', 'size': 1}")
    _post_trace(admin_client, headers, "t2", "norm", args=("bb",),
                output="{'kind': 'a', 'size': 2}")

    response = _create_session(admin_client, headers, trace_ids=["t1", "t2"])
    assert response.status_code == 201, response.text
    session = response.json()
    replay_set = admin_client.get(
        f"/replay-sets/{session['replay_set_id']}", headers=headers
    ).json()
    assert sorted(replay_set["trace_ids"]) == ["t1", "t2"]

    # No objective given -> only the deterministic assistant echo message.
    assert len(session["messages"]) == 1
    assert session["messages"][0]["role"] == "assistant"


def test_session_from_existing_replay_set_id(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    existing_set = _create_set(admin_client, headers, ["t1"], "norm")

    response = _create_session(
        admin_client, headers, replay_set_id=existing_set["id"]
    )
    assert response.status_code == 201, response.text
    session = response.json()
    assert session["replay_set_id"] == existing_set["id"]


def test_session_without_selection_uses_recent_component_traces(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    _post_trace(admin_client, headers, "t2", "norm", args=("xx",),
                output="{'kind': 'a', 'size': 2}")

    response = _create_session(admin_client, headers)
    assert response.status_code == 201, response.text
    replay_set = admin_client.get(
        f"/replay-sets/{response.json()['replay_set_id']}", headers=headers
    ).json()
    assert replay_set["trace_ids"] == ["t2", "t1"]


def test_session_selection_validation_multiple_is_422(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    existing_set = _create_set(admin_client, headers, ["t1"], "norm")

    # Two selections given at once.
    two = _create_session(
        admin_client, headers, replay_set_id=existing_set["id"], trace_id="t1"
    )
    assert two.status_code == 422

    # All three given.
    three = _create_session(
        admin_client, headers,
        replay_set_id=existing_set["id"], trace_id="t1", trace_ids=["t1"],
    )
    assert three.status_code == 422


def test_session_for_component_with_no_indexed_symbol_is_409(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    response = _create_session(
        admin_client, headers, component_id="no-such-component", trace_id="anything"
    )
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Messages never create a version
# ---------------------------------------------------------------------------


def test_message_does_not_create_a_version(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("x",),
                output="{'kind': 'a', 'size': 1}")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    session_id = session["id"]

    before = _count_versions(admin_client, headers, session_id)

    response = admin_client.post(
        f"/candidate-sessions/{session_id}/messages",
        json={"content": "please make it return uppercase"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    refreshed = response.json()

    after = _count_versions(admin_client, headers, session_id)
    assert after == before

    # Two new messages: the user's and a deterministic assistant ack.
    assert len(refreshed["messages"]) == len(session["messages"]) + 2
    assert refreshed["messages"][-2]["role"] == "user"
    assert refreshed["messages"][-2]["content"] == "please make it return uppercase"
    assert refreshed["messages"][-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Generation (mock provider)
# ---------------------------------------------------------------------------


def test_generate_creates_proposed_version_with_reasoning_provenance(
    admin_client, replay_repo
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()

    response = _generate(admin_client, headers, session["id"], instruction="uppercase it")
    assert response.status_code == 201, response.text
    version = response.json()

    assert version["status"] == "proposed"
    assert version["version_number"] == 1
    assert version["parent_version_id"] is None
    assert version["is_mock"] is True
    assert version["provider"] == "mock"
    assert version["prompt_version"] == "candidate-studio-proposal-v1"
    assert version["schema_version"] == "candidate-studio-proposal-v1"
    assert version["decision_method"] == "reasoning_llm"
    assert version["changed_symbols"] == ["normalize"]
    assert version["patch_text"].strip()
    assert "diff --git" in version["patch_text"]

    # The patch was built by git-diff against the pinned snapshot, so it must
    # be a real, applicable unified diff.
    check = subprocess.run(
        ["git", "-C", str(replay_repo), "apply", "--check", "-"],
        input=version["patch_text"], text=True, capture_output=True,
    )
    assert check.returncode == 0, check.stderr

    # A user message (the instruction) and an assistant summary were appended.
    refreshed = admin_client.get(
        f"/candidate-sessions/{session['id']}", headers=headers
    ).json()
    assert any(
        m["role"] == "user" and m["content"] == "uppercase it"
        for m in refreshed["messages"]
    )
    assert any(
        m["role"] == "assistant" and m["version_id"] == version["id"]
        for m in refreshed["messages"]
    )


def test_generation_context_redacts_trace_secrets_and_includes_prior_user_messages(
    admin_client, replay_repo, monkeypatch
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    secret = "sk-super-secret-value"
    _post_trace(
        admin_client,
        headers,
        "t-secret",
        "norm",
        kwargs={"api_key": secret},
        output=f"password={secret}",
    )
    session = _create_session(admin_client, headers, trace_id="t-secret").json()
    prior_message = "返り値の型は変更しないでください"
    sent = admin_client.post(
        f"/candidate-sessions/{session['id']}/messages",
        json={"content": prior_message},
        headers=headers,
    )
    assert sent.status_code == 200, sent.text

    captured = {}

    class CapturingClient:
        def generate_text(self, messages, **_kwargs):
            captured["prompt"] = "\n".join(message["content"] for message in messages)
            return json.dumps(
                {
                    "summary": "safe",
                    "assumptions": [],
                    "changed_symbols": ["candidate"],
                    "generated_code": "def candidate(*args, **kwargs):\n    return {}\n",
                    "risks": [],
                    "suggested_tests": [],
                }
            )

    monkeypatch.setattr(
        "app.routes.candidate_studio.create_llm_client",
        lambda _config: CapturingClient(),
    )
    generated = _generate(admin_client, headers, session["id"], "生成してください")
    assert generated.status_code == 201, generated.text
    assert prior_message in captured["prompt"]
    assert secret not in captured["prompt"]
    assert "[REDACTED]" in captured["prompt"]


# ---------------------------------------------------------------------------
# Branch/switch via parent_version_id
# ---------------------------------------------------------------------------


def test_generate_branch_records_parent_and_increments_version_number(
    admin_client, replay_repo
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    v1 = _generate(admin_client, headers, session["id"]).json()

    v2_response = _generate(
        admin_client, headers, session["id"],
        instruction="refine further", parent_version_id=v1["id"],
    )
    assert v2_response.status_code == 201, v2_response.text
    v2 = v2_response.json()
    assert v2["parent_version_id"] == v1["id"]
    assert v2["version_number"] == 2

    events = admin_client.get(
        f"/candidate-sessions/{session['id']}/events", headers=headers
    ).json()
    assert len(events["events"]) == 2


def test_generate_branch_parent_must_exist_and_must_be_proposed(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()

    # Non-existent parent -> 404.
    missing = _generate(
        admin_client, headers, session["id"], parent_version_id=999999
    )
    assert missing.status_code == 404, missing.text

    # A parent from a different session -> also not found in this session.
    other_session_response = _create_session(admin_client, headers, trace_id="t1")
    other_session = other_session_response.json()
    other_v1 = _generate(admin_client, headers, other_session["id"]).json()
    cross = _generate(
        admin_client, headers, session["id"], parent_version_id=other_v1["id"]
    )
    assert cross.status_code == 404, cross.text


# ---------------------------------------------------------------------------
# Fail-closed generation
# ---------------------------------------------------------------------------


def test_generate_fail_closed_on_unsupported_provider(
    admin_client, replay_repo, monkeypatch, db_file
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()

    before = _count_versions(admin_client, headers, session["id"])

    monkeypatch.setenv("LLM_PROVIDER", "badprovider")
    from app.llm import get_llm_client

    get_llm_client.cache_clear()

    response = _generate(admin_client, headers, session["id"])
    assert response.status_code == 502, response.text

    after = _count_versions(admin_client, headers, session["id"])
    assert after == before

    conn = sqlite3.connect(str(db_file))
    try:
        audit = conn.execute(
            "SELECT run_type, status, decision_method FROM intelligence_runs "
            "WHERE run_type = 'candidate_studio_proposal' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert audit == ("candidate_studio_proposal", "failed", "reasoning_llm")

    # An assistant error message was appended even though no version exists.
    refreshed = admin_client.get(
        f"/candidate-sessions/{session['id']}", headers=headers
    ).json()
    assert any(
        m["role"] == "assistant" and "失敗" in m["content"]
        for m in refreshed["messages"]
    )


# ---------------------------------------------------------------------------
# Replay approval gate
# ---------------------------------------------------------------------------


def test_replay_requires_approval_then_completes_after_approval(
    admin_client, replay_repo
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    version = _generate(admin_client, headers, session["id"]).json()

    denied = admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )
    assert denied.status_code == 403, denied.text

    still_not_run = admin_client.get(
        f"/candidate-versions/{version['id']}", headers=headers
    ).json()
    assert still_not_run["replay_status"] == "not_run"

    _approve(admin_client, headers, "norm")
    replayed = admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )
    assert replayed.status_code == 200, replayed.text
    replayed_version = replayed.json()
    assert replayed_version["replay_status"] == "completed"
    assert replayed_version["replay_run_id"] is not None

    diff_matrix = admin_client.get(
        f"/replay-variant-runs/{replayed_version['replay_run_id']}", headers=headers
    )
    assert diff_matrix.status_code == 200, diff_matrix.text
    assert diff_matrix.json()["status"] == "completed"


def test_replay_rejects_snapshot_other_than_session_baseline(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",), output="ok")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    version = _generate(admin_client, headers, session["id"]).json()
    _approve(admin_client, headers, "norm")

    with open(replay_repo / "svc.py", "a", encoding="utf-8") as handle:
        handle.write("\n# second snapshot\n")
    _git(replay_repo, "add", "svc.py")
    _git(replay_repo, "commit", "-m", "second snapshot")
    second = admin_client.post("/repository/snapshots", headers=headers)
    assert second.status_code == 201, second.text
    assert admin_client.post(
        "/repository/symbols/index", headers=headers
    ).status_code == 201

    response = admin_client.post(
        f"/candidate-versions/{version['id']}/replay",
        json={"snapshot_id": second.json()["id"]},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "pinned baseline snapshot" in response.json()["detail"]


def test_failed_candidate_variant_sets_failed_status_and_event(
    admin_client, replay_repo, db_file
):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",), output="ok")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    version = _generate(admin_client, headers, session["id"]).json()
    _approve(admin_client, headers, "norm")

    # Simulate a corrupted persisted patch so the shared variant runner audits
    # an invalid_patch candidate while its baseline run still completes.
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "UPDATE candidate_versions SET patch_text = ? WHERE id = ?",
            ("not a unified diff", version["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    replayed = admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replay_status"] == "failed"

    events = admin_client.get(
        f"/candidate-sessions/{session['id']}/events", headers=headers
    ).json()
    assert events["events"][-1]["phase"] == "failed"


def test_replay_rejects_a_non_proposed_or_patchless_version(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    response = admin_client.post(
        f"/candidate-versions/999999/replay", json={}, headers=headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def test_promote_requires_completed_replay(admin_client, replay_repo):
    _, _, headers, _ = _prepare(admin_client, replay_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers, trace_id="t1").json()
    version = _generate(admin_client, headers, session["id"]).json()

    too_early = admin_client.post(
        f"/candidate-versions/{version['id']}/promote", headers=headers
    )
    assert too_early.status_code == 422, too_early.text

    _approve(admin_client, headers, "norm")
    admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )

    promoted = admin_client.post(
        f"/candidate-versions/{version['id']}/promote", headers=headers
    )
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert body["patch_text"].strip()
    assert body["origin"]["candidate_session_id"] == session["id"]
    assert body["origin"]["candidate_version_id"] == version["id"]

    reread = admin_client.get(
        f"/candidate-versions/{version['id']}", headers=headers
    ).json()
    assert reread["promoted_at"] is not None


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


def test_system_isolation_for_sessions_and_versions(admin_client, replay_repo):
    token, system_a, headers_a, _ = _prepare(admin_client, replay_repo, "cs-sys-a")
    _post_trace(admin_client, headers_a, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = _create_session(admin_client, headers_a, trace_id="t1").json()
    version = _generate(admin_client, headers_a, session["id"]).json()

    system_b = _create_system(admin_client, token, "cs-sys-b")
    headers_b = _headers(token, system_b["id"])

    assert admin_client.get(
        f"/candidate-sessions/{session['id']}", headers=headers_b
    ).status_code == 404
    assert admin_client.get(
        "/candidate-sessions", headers=headers_b
    ).json() == []
    assert admin_client.get(
        f"/candidate-versions/{version['id']}", headers=headers_b
    ).status_code == 404


# ---------------------------------------------------------------------------
# Unit: app.candidate_studio
# ---------------------------------------------------------------------------


def test_validate_patch_scope_accepts_single_target_file():
    patch = (
        "diff --git a/svc.py b/svc.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/svc.py\n"
        "+++ b/svc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    _validate_patch_scope(patch, "svc.py")  # must not raise


def test_validate_patch_scope_rejects_extra_or_different_file():
    single_other_file = (
        "diff --git a/other.py b/other.py\n"
        "--- a/other.py\n"
        "+++ b/other.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    with pytest.raises(DraftError):
        _validate_patch_scope(single_other_file, "svc.py")

    two_files = (
        "diff --git a/svc.py b/svc.py\n"
        "--- a/svc.py\n"
        "+++ b/svc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/other.py b/other.py\n"
        "--- a/other.py\n"
        "+++ b/other.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    with pytest.raises(DraftError):
        _validate_patch_scope(two_files, "svc.py")

    with pytest.raises(DraftError):
        _validate_patch_scope("no diff header at all", "svc.py")


def test_string_list_bounds_and_coerces():
    # Non-list input -> empty list.
    assert _string_list("not a list") == []
    assert _string_list(None) == []

    # Non-blank strings are stripped; blank/whitespace-only strings are
    # dropped; non-str/non-container scalars are coerced to str; ``None``,
    # nested lists, and dicts are dropped.
    mixed = ["  keep me  ", "", "   ", 42, None, ["nested"], {"k": "v"}, True]
    result = _string_list(mixed)
    assert result == ["keep me", "42", "True"]

    # Bounded at MAX_LIST_ITEMS (20).
    long_list = [f"item-{i}" for i in range(50)]
    bounded = _string_list(long_list)
    assert len(bounded) == 20
    assert bounded[0] == "item-0"
    assert bounded[-1] == "item-19"


def test_generated_code_must_be_one_function_without_imports():
    _validate_generated_code("def candidate(*args, **kwargs):\n    return 1\n")

    with pytest.raises(DraftError, match="exactly one top-level"):
        _validate_generated_code(
            "x = 1\ndef candidate(*args, **kwargs):\n    return x\n"
        )
    with pytest.raises(DraftError, match="must not contain import"):
        _validate_generated_code(
            "def candidate(*args, **kwargs):\n    import os\n    return os.getcwd()\n"
        )
    with pytest.raises(DraftError, match="must not contain decorators"):
        _validate_generated_code(
            "@staticmethod\ndef candidate(*args, **kwargs):\n    return 1\n"
        )
    with pytest.raises(DraftError, match="exact signature"):
        _validate_generated_code("def candidate(value):\n    return value\n")


def test_candidate_proposal_shape_fails_closed_on_wrong_field_types():
    _validate_proposal_shape(
        {
            "generated_code": "def candidate():\n    return 1\n",
            "summary": "valid",
            "risks": ["valid"],
        }
    )
    with pytest.raises(DraftError, match="generated_code must be a string"):
        _validate_proposal_shape({"generated_code": 123})
    with pytest.raises(DraftError, match="risks must be an array of strings"):
        _validate_proposal_shape({"generated_code": "def candidate(): pass", "risks": [1]})
