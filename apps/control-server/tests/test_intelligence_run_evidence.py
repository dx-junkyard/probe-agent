"""Route-level tests for Issue #137: persisting all evidence read in pass 1.

Covers:
1. Every snippet read for a successful two-pass turn is persisted, linked to
   the pass-1 (interview_evidence_selection) run, and retrievable via
   GET /interview/evidence-runs/{run_id}/evidence.
2. Evidence not cited by the resulting question is still recorded (the read
   audit is independent of citation).
3. A partial read failure persists whatever was successfully read before the
   failing target, alongside the failed run.
4. Snippet content itself is never persisted or returned.
5. System isolation.
6. Existing evidence_used / interview_qa.evidence_refs behavior is unchanged.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult
from app.interview_evidence import EvidenceTarget


def _init_repo(tmp_path, files: Dict[str, str]) -> tuple[str, str]:
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Test"], check=True, capture_output=True,
    )
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(content))
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-run-evidence-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup_session_with_repo(client, tmp_path, name="Sys", files=None):
    repo, sha = _init_repo(tmp_path, files or {
        "src/summarize.py": "\n".join(f"def summarize_text(): pass  # {i}" for i in range(1, 30)) + "\n",
        "src/classifier.py": "\n".join(f"def classify(): pass  # {i}" for i in range(1, 10)) + "\n",
    })
    token = _login(client)
    system = client.post(
        "/systems", json={"name": name, "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    headers = _headers(token, system["id"])

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system["id"], repo, sha, now, now),
        )
        snapshot_id = cur.lastrowid

    session = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()
    return headers, session["id"], snapshot_id, system["id"]


def _stub_two_pass(monkeypatch, *, evidence_result, turn_result):
    from app.routes import interview as interview_routes

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        return turn_result

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(interview_routes, "select_evidence_targets", fake_select_evidence_targets)
    monkeypatch.setattr(interview_routes, "generate_interview_turn", fake_generate_interview_turn)


def test_all_read_snippets_persisted_and_linked_to_pass1_run(admin_client, monkeypatch, tmp_path):
    headers, session_id, snapshot_id, system_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=True,
        targets=[
            EvidenceTarget(path="src/summarize.py", start_line=1, end_line=5),
            EvidenceTarget(path="src/classifier.py", start_line=1, end_line=3),
        ],
    )
    # The question does not cite either snippet — citation and read-audit
    # are independent (this is the core Issue #137 behavior).
    turn_result = InterviewTurnResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        assistant_message="読みました。",
    )
    _stub_two_pass(monkeypatch, evidence_result=evidence_result, turn_result=turn_result)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "詳細を教えてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert len(body["evidence_reads"]) == 2
    paths = {e["path"] for e in body["evidence_reads"]}
    assert paths == {"src/summarize.py", "src/classifier.py"}
    for e in body["evidence_reads"]:
        assert "content" not in e  # snippet content is never returned

    run_id = body["evidence_run"]["id"]
    listing = admin_client.get(
        f"/interview/evidence-runs/{run_id}/evidence", headers=headers,
    ).json()
    assert len(listing["items"]) == 2
    assert listing["intelligence_run_id"] == run_id

    # Not cited: interview_qa rows carry no evidence_refs for these reads.
    qa = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    assert qa["items"] == []  # no next_questions were generated in this turn

    from app.db import get_conn

    with get_conn() as conn:
        cols = [
            row[1] for row in conn.execute("PRAGMA table_info(intelligence_run_evidence)")
        ]
    assert "content" not in cols


def test_partial_read_failure_persists_what_was_read(admin_client, monkeypatch, tmp_path):
    headers, session_id, snapshot_id, system_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=True,
        targets=[
            EvidenceTarget(path="src/summarize.py", start_line=1, end_line=5),
            EvidenceTarget(path="src/does_not_exist.py", start_line=1, end_line=5),
        ],
    )

    from app.routes import interview as interview_routes

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        raise AssertionError("pass 2 must not run when the evidence read fails")

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(interview_routes, "select_evidence_targets", fake_select_evidence_targets)
    monkeypatch.setattr(interview_routes, "generate_interview_turn", fake_generate_interview_turn)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "詳細を教えてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is not None
    assert body["evidence_run"]["status"] == "completed"  # pass-1 selection itself succeeded
    assert len(body["evidence_reads"]) == 1
    assert body["evidence_reads"][0]["path"] == "src/summarize.py"

    run_id = body["evidence_run"]["id"]
    listing = admin_client.get(
        f"/interview/evidence-runs/{run_id}/evidence", headers=headers,
    ).json()
    assert len(listing["items"]) == 1


def test_evidence_reads_isolated_across_systems(admin_client, monkeypatch, tmp_path):
    headers_a, session_a, snapshot_a, system_a = _setup_session_with_repo(
        admin_client, tmp_path / "a", "System A",
    )
    headers_b, session_b, snapshot_b, system_b = _setup_session_with_repo(
        admin_client, tmp_path / "b", "System B",
    )

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=True,
        targets=[EvidenceTarget(path="src/summarize.py", start_line=1, end_line=5)],
    )
    turn_result = InterviewTurnResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False, assistant_message="OK",
    )
    _stub_two_pass(monkeypatch, evidence_result=evidence_result, turn_result=turn_result)

    r = admin_client.post(
        f"/interview/sessions/{session_a}/dialogue-turn",
        json={"user_message": "hi"}, headers=headers_a,
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["evidence_run"]["id"]

    # System B cannot see System A's evidence-selection run.
    r2 = admin_client.get(
        f"/interview/evidence-runs/{run_id}/evidence", headers=headers_b,
    )
    assert r2.status_code == 404

    from app.db import get_conn

    with get_conn() as conn:
        count_b = conn.execute(
            "SELECT COUNT(*) AS c FROM intelligence_run_evidence WHERE system_id = ?",
            (system_b,),
        ).fetchone()["c"]
    assert count_b == 0
