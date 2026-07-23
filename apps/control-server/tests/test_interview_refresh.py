"""Tests for Issue #288: automatic refresh after an answer batch.

Covers app/interview_refresh.py (request_refresh/run_refresh_job/
retry_refresh_job) and routes/interview_refresh.py (refresh-status, retry):

1. answering a Q&A question, in eager mode, runs a refresh job end-to-end:
   a new understanding_revision is produced, Alignment is rebuilt, and
   GET .../refresh-status reflects the finished job.
2. the answer is persisted even when the understanding rebuild's reasoning
   call fails; the job is 'failed' and retry re-runs successfully.
3. dedupe: with an 'updating' job in flight, repeated request_refresh calls
   enqueue at most one extra 'pending' job (bounded batch).
4. stale: an older job that runs after a newer job already completed is
   marked 'stale' and writes nothing (the newer result is not overwritten).
5. idempotency: running the same completed job again is a no-op.
6. a must_review alignment item produced by the refresh appears in the
   Review Queue.
7. audit lineage: job -> intelligence_run -> understanding_revision is
   queryable, with the reviewer's prompt/schema versions recorded.
8. two threads racing run_refresh_job() on the same job only rebuild once
   (the per-session lock + status check serialize them).
9. when nothing new is waiting (the manual-update blocked gate), the job
   is marked 'updated' with a note instead of rebuilding.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Dict

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-refresh-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    # Deterministic tests: run refresh jobs synchronously on the caller's
    # thread instead of a background thread (see interview_refresh.py).
    monkeypatch.setenv("PROBE_REFRESH_EAGER", "1")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _init_repo(tmp_path, files: Dict[str, str]) -> "tuple[str, str]":
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True, capture_output=True)
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_understanding_graph(system_id, snapshot_id):
    from app.documentation_claim_scanner import ChunkScanResult, ClaimEvidence, DocumentationClaim
    from app.understanding_graph import build_understanding_graph, save_graph_snapshot
    from app.db import get_conn

    result = ChunkScanResult(
        chunk_id="chunk-1",
        chunk_content_hash="hash-1",
        prompt_version="claim-scanner-v1",
        schema_version="claim-scanner-v1",
        claims=[
            DocumentationClaim(
                claim_type="system_purpose",
                summary="System helps inspect probe agent repositories",
                evidence=ClaimEvidence(path="src/a.py", start_line=1, end_line=5),
                confidence=0.9,
            )
        ],
    )
    graph = build_understanding_graph([result])
    with get_conn() as conn:
        return save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)


def _setup(client, tmp_path, name="System A", files=None):
    token = _login(client)
    system = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, files or {"src/a.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"})
    snapshot_id = _insert_snapshot(system["id"], repo, sha)
    _insert_understanding_graph(system["id"], snapshot_id)
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_qa(client, headers, session_id, **overrides):
    body = {
        "question_text": "システムの主な目的は何ですか?",
        "question_category": "purpose",
        "question_source": "reviewer",
    }
    body.update(overrides)
    r = client.post(f"/interview/sessions/{session_id}/qa", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _answer_qa(client, headers, session_id, qa_id, answer_text="回答です"):
    r = client.post(
        f"/interview/sessions/{session_id}/qa/{qa_id}/answer",
        json={"answer_text": answer_text, "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


class _FakeReviewClient:
    """Stub for system_understanding_reviewer's LLM call -- one purpose, no
    open questions unless overridden. ``fail=True`` returns unparsable
    output so the *real* generate_understanding_review fails closed on its
    own structured-output validation, without needing to monkeypatch
    generate_understanding_review itself (which would leak across a test
    that re-stubs the client afterwards, e.g. a retry-after-failure test)."""

    def __init__(self, open_questions=None, fail=False):
        self._open_questions = open_questions or []
        self._fail = fail
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(messages)
        if self._fail:
            return "not valid json"
        return json.dumps({
            "system_purpose": [{
                "name": "Probe repository inspection",
                "summary": "Inspects probe agent repositories",
                "confidence": {"level": "likely", "reason": "README evidence"},
                "evidence": [{
                    "path": "src/a.py", "start_line": 1, "end_line": 5,
                    "summary": "states purpose",
                }],
                "why_core": "",
                "related_docs": [],
                "related_apis": [],
                "children": [],
            }],
            "core_capabilities": [],
            "capability_elements": [],
            "supporting_elements": [],
            "api_boundaries": [],
            "probe_flow_candidates": [],
            "gap_analysis": [],
            "open_questions": self._open_questions,
            "suggested_next_action": "confirm_purpose",
        })


def _stub_understanding_review(monkeypatch, *, open_questions=None):
    import app.routes.interview as interview_route

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = _FakeReviewClient(open_questions=open_questions)
    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: client)
    return client


def _stub_understanding_review_failure(monkeypatch):
    import app.routes.interview as interview_route

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(
        interview_route, "create_llm_client", lambda config: _FakeReviewClient(fail=True),
    )


def _stub_alignment_build(monkeypatch, *, items=None, error=None):
    from app.alignment import AlignmentProposalItem, AlignmentProposalResult
    from app.routes import interview_alignment as alignment_routes

    def fake_create_llm_client(config):
        return object()

    def fake_generate_alignment_proposal(client, config, **kwargs):
        return AlignmentProposalResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            items=[AlignmentProposalItem(**i) for i in (items or [])],
            error=error,
        )

    monkeypatch.setattr(alignment_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(alignment_routes, "generate_alignment_proposal", fake_generate_alignment_proposal)


def _alignment_item(**overrides):
    from app.alignment import AlignmentEvidenceItem

    base = dict(
        intent_field=None,
        intent_ref_hint=None,
        current_claim="現在は手動でトレースを確認している",
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=3, summary="")],
        alignment_state="gap",
        risk_flags=[],
        confidence="likely",
        gap_summary="自動化されていない",
        proposed_interpretation=None,
    )
    base.update(overrides)
    return base


def _advance_to_confirmed_proposal_stage(client, headers, session_id):
    for stage in [
        "purpose_confirmation", "capability_confirmation", "element_classification",
        "api_boundary_mapping", "probe_flow_selection", "proposal_generation",
    ]:
        response = client.post(
            f"/interview/sessions/{session_id}/advance-stage",
            json={"stage": stage},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    response = client.post(
        f"/interview/sessions/{session_id}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


# --- 1. end-to-end: answer -> refresh job -> revision + alignment + status --


def test_answer_triggers_refresh_end_to_end(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[_alignment_item()])

    _answer_qa(admin_client, headers, session_id, qa["id"])

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    assert status["latest_job"] is not None
    job = status["latest_job"]
    assert job["trigger_kind"] == "qa_answer"
    assert job["status"] == "updated"
    assert job["error"] is None
    assert job["result_revision_id"] is not None
    assert job["intelligence_run_id"] is not None
    assert status["pending_count"] == 0

    revisions = admin_client.get(
        f"/interview/sessions/{session_id}/understanding-revisions", headers=headers
    ).json()
    assert len(revisions["items"]) >= 1

    alignment = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    assert sum(alignment["counts"].values()) == 1


# --- 2. answers survive refresh failure; retry recovers -----------------


def test_answer_persisted_when_refresh_fails_and_retry_recovers(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review_failure(monkeypatch)

    answer = _answer_qa(admin_client, headers, session_id, qa["id"], answer_text="失敗時でも残る回答")
    assert answer["qa"]["answer_text"] == "失敗時でも残る回答"
    assert answer["qa"]["status"] == "answered"

    # The answer row itself is untouched by the refresh failure.
    listing = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    assert listing["items"][0]["answer_text"] == "失敗時でも残る回答"

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    job = status["latest_job"]
    assert job["status"] == "failed"
    assert job["error"]

    # Fix the stub, then retry the failed job.
    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[])

    r = admin_client.post(
        f"/interview/sessions/{session_id}/refresh-jobs/{job['id']}/retry", headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "updated"


def test_retry_rejects_non_failed_job(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[])
    _answer_qa(admin_client, headers, session_id, qa["id"])

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    job_id = status["latest_job"]["id"]
    assert status["latest_job"]["status"] == "updated"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/refresh-jobs/{job_id}/retry", headers=headers,
    )
    assert r.status_code == 409, r.text


def test_retry_allowed_for_partial_failure(admin_client, tmp_path, monkeypatch):
    """Understanding の再構築は成功したが Alignment build だけ失敗した job
    (status='updated' + error 残存)は、成功扱いで固定せず再試行できる。"""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, error="reasoning call failed")
    _answer_qa(admin_client, headers, session_id, qa["id"])

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    job = status["latest_job"]
    assert job["status"] == "updated"
    assert job["error"]  # partial failure recorded

    _stub_alignment_build(monkeypatch, items=[])
    r = admin_client.post(
        f"/interview/sessions/{session_id}/refresh-jobs/{job['id']}/retry", headers=headers,
    )
    assert r.status_code == 200, r.text
    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    assert status["latest_job"]["id"] != job["id"]


# --- 3. dedupe: an in-flight 'updating' job bounds new pending jobs ------


def test_dedupe_bounds_pending_jobs_during_updating(admin_client, tmp_path):
    from app.db import get_conn
    from app.interview_refresh import request_refresh

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_revision_id,
                 base_answer_marker, status, created_at, started_at)
            VALUES (?, ?, 'qa_answer', NULL, ?, 'updating', ?, ?)""",
            (session_id, system_id, now, now, now),
        )

    # Three rapid triggers while a job is 'updating' must collapse into
    # exactly one follow-up 'pending' job (bounded batch), not three.
    ids = [request_refresh(session_id, system_id, "qa_answer") for _ in range(3)]
    assert len(set(ids)) == 1

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status FROM interview_refresh_job WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    statuses = [r["status"] for r in rows]
    assert statuses == ["updating", "pending"]


def test_pending_job_promotes_trigger_when_alignment_feedback_joins_batch(
    admin_client, tmp_path, monkeypatch,
):
    """A deduped pending intent job must not hide later Alignment feedback."""
    from app.db import get_conn
    import app.interview_refresh as refresh

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    monkeypatch.setattr(refresh, "_dispatch", lambda job_id: None)

    first_id = refresh.request_refresh(session_id, system_id, "intent_update")
    second_id = refresh.request_refresh(session_id, system_id, "alignment_answer")

    assert second_id == first_id
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trigger_kind FROM interview_refresh_job WHERE id = ?",
            (first_id,),
        ).fetchone()
    assert row["trigger_kind"] == "alignment_answer"


# --- 4. stale: an older job finishing after a newer one is discarded ----


def test_stale_job_does_not_overwrite_newer_result(admin_client, tmp_path, monkeypatch):
    from app.db import get_conn
    from app.interview_refresh import run_refresh_job

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[])

    now = time.time()
    with get_conn() as conn:
        job1 = conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_revision_id,
                 base_answer_marker, status, created_at)
            VALUES (?, ?, 'qa_answer', NULL, ?, 'pending', ?)""",
            (session_id, system_id, now, now),
        ).lastrowid
        job2 = conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_revision_id,
                 base_answer_marker, status, created_at)
            VALUES (?, ?, 'qa_answer', NULL, ?, 'pending', ?)""",
            (session_id, system_id, now + 1, now + 1),
        ).lastrowid

    # job2 (created later / higher id) finishes first.
    run_refresh_job(job2)
    with get_conn() as conn:
        job2_row = conn.execute("SELECT * FROM interview_refresh_job WHERE id = ?", (job2,)).fetchone()
    assert job2_row["status"] == "updated"
    result_revision_id = job2_row["result_revision_id"]
    assert result_revision_id is not None

    # job1 (older) runs after job2 already completed -> stale, no writes.
    run_refresh_job(job1)
    with get_conn() as conn:
        job1_row = conn.execute("SELECT * FROM interview_refresh_job WHERE id = ?", (job1,)).fetchone()
        revision_count = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert job1_row["status"] == "stale"
    assert job1_row["result_revision_id"] is None
    assert job1_row["error"]
    # job1 never ran a rebuild, so the only revision is the one job2 wrote.
    assert revision_count == 1


# --- 5. idempotency: running the same job twice is a no-op --------------


def test_running_same_job_twice_is_idempotent(admin_client, tmp_path, monkeypatch):
    from app.db import get_conn
    from app.interview_refresh import run_refresh_job

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[_alignment_item()])

    now = time.time()
    with get_conn() as conn:
        job_id = conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_revision_id,
                 base_answer_marker, status, created_at)
            VALUES (?, ?, 'qa_answer', NULL, ?, 'pending', ?)""",
            (session_id, system_id, now, now),
        ).lastrowid

    run_refresh_job(job_id)
    run_refresh_job(job_id)

    with get_conn() as conn:
        revision_count = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        alignment_count = conn.execute(
            "SELECT COUNT(*) FROM alignment_item WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert revision_count == 1
    assert alignment_count == 1


# --- 6. must_review items produced by refresh appear in the queue -------


def test_refresh_produced_must_review_item_appears_in_queue(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[_alignment_item(risk_flags=["security"])])

    _answer_qa(admin_client, headers, session_id, qa["id"])

    queue = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    assert len(queue["items"]) == 1
    assert queue["items"][0]["review_category"] == "must_review"
    assert queue["items"][0]["reason_code"] == "security_related"


# --- 7. audit lineage: job -> intelligence_run -> revision ---------------


def test_audit_lineage_job_run_and_revision_are_linked(admin_client, tmp_path, monkeypatch):
    from app.db import get_conn
    from app.system_understanding_reviewer import PROMPT_VERSION, SCHEMA_VERSION

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[])
    _answer_qa(admin_client, headers, session_id, qa["id"])

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    job = status["latest_job"]

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?", (job["intelligence_run_id"],)
        ).fetchone()
        revision = conn.execute(
            "SELECT * FROM understanding_revision WHERE id = ?", (job["result_revision_id"],)
        ).fetchone()

    assert run is not None
    assert run["run_type"] == "understanding_review"
    assert run["decision_method"] == "reasoning_llm"
    assert run["prompt_version"] == PROMPT_VERSION
    assert run["schema_version"] == SCHEMA_VERSION
    assert revision is not None
    assert revision["intelligence_run_id"] == job["intelligence_run_id"]
    assert revision["session_id"] == session_id


# --- 8. concurrency guard -------------------------------------------------


def test_concurrent_run_refresh_job_only_rebuilds_once(admin_client, tmp_path, monkeypatch):
    from app.db import get_conn
    from app.interview_refresh import run_refresh_job

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)
    _stub_alignment_build(monkeypatch, items=[])

    now = time.time()
    with get_conn() as conn:
        job_id = conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_revision_id,
                 base_answer_marker, status, created_at)
            VALUES (?, ?, 'qa_answer', NULL, ?, 'pending', ?)""",
            (session_id, system_id, now, now),
        ).lastrowid

    threads = [threading.Thread(target=run_refresh_job, args=(job_id,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    with get_conn() as conn:
        revision_count = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        job_row = conn.execute("SELECT * FROM interview_refresh_job WHERE id = ?", (job_id,)).fetchone()
    assert revision_count == 1
    assert job_row["status"] == "updated"


# --- 9. trigger-specific work after confirmation --------------------------


def test_intent_update_rebuilds_alignment_without_regenerating_understanding(
    admin_client, tmp_path, monkeypatch,
):
    """Intent is desired state: reuse the latest facts revision and rebuild
    Alignment even when the Q&A-only Understanding gate is closed."""
    import app.routes.interview as interview_route
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)
    initial = admin_client.post(
        f"/interview/sessions/{session_id}/update-understanding",
        headers=headers,
    )
    assert initial.status_code == 200, initial.text
    _advance_to_confirmed_proposal_stage(admin_client, headers, session_id)

    created = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "最初の目標"},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    with get_conn() as conn:
        revision_before = conn.execute(
            """SELECT id FROM understanding_revision
               WHERE session_id = ? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()["id"]

    def fail_if_understanding_regenerates(_config):
        raise AssertionError("intent_update must not regenerate Understanding")

    monkeypatch.setattr(
        interview_route, "create_llm_client", fail_if_understanding_regenerates,
    )
    _stub_alignment_build(monkeypatch, items=[_alignment_item(current_claim="目標更新後")])

    corrected = admin_client.post(
        f"/interview/intent/{created.json()['id']}/correct",
        json={"value_text": "更新した目標"},
        headers=headers,
    )
    assert corrected.status_code == 200, corrected.text

    status = admin_client.get(
        f"/interview/sessions/{session_id}/refresh-status", headers=headers,
    ).json()["latest_job"]
    assert status["trigger_kind"] == "intent_update"
    assert status["status"] == "updated"
    assert status["error"] is None
    assert status["intelligence_run_id"] is None
    assert status["result_revision_id"] == revision_before

    alignment = admin_client.get(
        f"/interview/sessions/{session_id}/alignment", headers=headers,
    ).json()
    claims = [
        item["current_claim"]
        for items in alignment["items_by_category"].values()
        for item in items
    ]
    assert "目標更新後" in claims

    with get_conn() as conn:
        revision_after = conn.execute(
            """SELECT id FROM understanding_revision
               WHERE session_id = ? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()["id"]
    assert revision_after == revision_before


def test_alignment_correction_bypasses_qa_gate_and_reaches_reviewer_prompt(
    admin_client, tmp_path, monkeypatch,
):
    """An explicit correction is human evidence even with no post-confirm
    Q&A, so it must produce a new Understanding revision."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)
    initial = admin_client.post(
        f"/interview/sessions/{session_id}/update-understanding",
        headers=headers,
    )
    assert initial.status_code == 200, initial.text
    _advance_to_confirmed_proposal_stage(admin_client, headers, session_id)

    _stub_alignment_build(monkeypatch, items=[
        _alignment_item(
            current_claim="認可は自動で行われる",
            proposed_interpretation="追加の承認は不要",
            risk_flags=["security"],
        ),
    ])
    built = admin_client.post(
        f"/interview/sessions/{session_id}/alignment/build", headers=headers,
    )
    assert built.status_code == 200, built.text
    item_id = built.json()["items"][0]["id"]

    with get_conn() as conn:
        revision_before = conn.execute(
            """SELECT id FROM understanding_revision
               WHERE session_id = ? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()["id"]

    review_client = _stub_understanding_review(monkeypatch)
    corrected = admin_client.post(
        f"/interview/alignment/{item_id}/correct",
        json={"corrected_interpretation": "認可には運用者の明示承認が必要"},
        headers=headers,
    )
    assert corrected.status_code == 200, corrected.text

    assert review_client.calls
    prompt = review_client.calls[0][1]["content"]
    assert "Human Alignment Review Feedback" in prompt
    assert "corrected" in prompt
    assert "認可には運用者の明示承認が必要" in prompt
    assert "追加の承認は不要" in prompt

    status = admin_client.get(
        f"/interview/sessions/{session_id}/refresh-status", headers=headers,
    ).json()["latest_job"]
    assert status["trigger_kind"] == "alignment_answer"
    assert status["status"] == "updated"
    assert status["intelligence_run_id"] is not None
    assert status["result_revision_id"] > revision_before


# --- 10. nothing new -> skip with a note, no rebuild ---------------------


def test_refresh_skips_rebuild_when_nothing_new(admin_client, tmp_path, monkeypatch):
    """Mirrors update_interview_understanding's manual 409 gate
    (_understanding_update_blocked): once confirmed with no new answers,
    the job marks itself 'updated' with a note instead of rebuilding."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_understanding_review(monkeypatch)

    admin_client.post(
        f"/interview/sessions/{session_id}/messages",
        json={"role": "user", "content": "対象と目的を確認しました"},
        headers=headers,
    )
    for stage in [
        "purpose_confirmation", "capability_confirmation", "element_classification",
        "api_boundary_mapping", "probe_flow_selection", "proposal_generation",
    ]:
        admin_client.post(
            f"/interview/sessions/{session_id}/advance-stage",
            json={"stage": stage}, headers=headers,
        )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/confirm-understanding",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text

    from app.interview_refresh import request_refresh

    with get_conn() as conn:
        revision_count_before = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    request_refresh(session_id, system_id, "qa_answer")

    status = admin_client.get(f"/interview/sessions/{session_id}/refresh-status", headers=headers).json()
    job = status["latest_job"]
    assert job["status"] == "updated"
    assert job["error"]  # informational skip note

    with get_conn() as conn:
        revision_count_after = conn.execute(
            "SELECT COUNT(*) FROM understanding_revision WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert revision_count_after == revision_count_before
