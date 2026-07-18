"""Route-level tests for Issue #135: Runtime Reality Check API.

Covers:
1. GET runtime-facts is deterministic-only (numeric aggregation, no
   intelligence_runs row) and reflects approved elements' component_id.
2. POST runtime-reality-check success: intelligence_runs audit row +
   question_source='runtime' interview_qa rows with runtime_evidence.
3. Fail-closed: LLM/config failure still records a failed intelligence_runs
   row and creates no interview_qa rows.
4. Suppression: a second run is skipped while an unanswered runtime
   question exists, without creating another intelligence_runs row.
5. System isolation: traces from another System never affect facts here.
6. Metadata/probe plan/policy are never auto-changed by this flow.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-runtime-reality-route-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _valid_proposal_item(
    path="src/summarize.py",
    qualified_name="summarize.summarize_text",
    state_effects=None,
    recommended_mode="trace",
):
    return {
        "path": path,
        "qualified_name": qualified_name,
        "metadata": {
            "role": "Summarize free text with no side effects",
            "capability": "summarization",
            "system_purpose": "Document processing",
            "probe_value": "Validate latency and correctness",
            "element_type": "core",
            "operation_kind": "analysis",
            "consumers": ["api"],
            "state_effects": state_effects or ["none"],
        },
        "probe_plan": {
            "feature_id": "summarization",
            "objective": "Trace summarizer",
            "reason": "Safe to trace",
            "recommended_mode": recommended_mode,
            "side_effect_risk": "low",
            "replayability": "safe",
        },
    }


def _valid_audit():
    return {
        "provider": "mock",
        "model": "mock-reasoner",
        "prompt_version": "interview-v1",
        "schema_version": "1",
        "is_mock": True,
    }


def _advance_to_proposal_generation(client, session_id, headers):
    r = client.post(
        f"/interview/sessions/{session_id}/messages",
        json={"role": "user", "content": "対象と目的を確認しました"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/interview/sessions/{session_id}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def _create_approved_session(client, headers, snapshot_id, proposals=None):
    """Create a session, generate proposals, and approve all of them."""
    r = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "Runtime reality check session"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]

    _advance_to_proposal_generation(client, session_id, headers)

    if proposals is None:
        proposals = [_valid_proposal_item()]

    r = client.post(
        f"/interview/sessions/{session_id}/proposals",
        json={"audit": _valid_audit(), "proposals": proposals},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created = r.json()

    for p in created:
        r = client.post(
            f"/interview/sessions/{session_id}/proposals/{p['id']}/approve",
            json={"actor": "reviewer"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    return session_id


def _insert_trace(system_id, component_id, *, error=None, duration_ms=10.0):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO traces
                (system_id, trace_id, component_id, mode, input_json, output_text,
                 error, duration_ms, timestamp)
            VALUES (?, ?, ?, 'trace', '{}', 'out', ?, ?, ?)""",
            (system_id, f"trace-{time.time_ns()}", component_id, error, duration_ms, time.time()),
        )


# --- Facts endpoint: deterministic-only ---------------------------------------


def test_runtime_facts_derives_component_id_from_qualified_name(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    _insert_trace(system_id, "summarize_summarize_text")
    _insert_trace(system_id, "summarize_summarize_text", error="boom")

    r = admin_client.get(
        f"/interview/sessions/{session_id}/runtime-facts", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["component_id"] == "summarize_summarize_text"
    assert item["facts"]["call_count"] == 2
    assert item["facts"]["error_count"] == 1
    assert item["facts"]["has_traces"] is True

    # No reasoning run should be recorded for the deterministic-only facts read.
    from app.db import get_conn

    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM intelligence_runs "
            "WHERE run_type = 'runtime_reality_check' AND system_id = ?",
            (system_id,),
        ).fetchone()["c"]
    assert count == 0


def test_runtime_facts_zero_traces_for_approved_probe_plan(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    r = admin_client.get(
        f"/interview/sessions/{session_id}/runtime-facts", headers=headers
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["facts"]["has_traces"] is False
    assert item["facts"]["call_count"] == 0


def test_runtime_facts_isolated_across_systems(admin_client):
    token_a, system_a, snapshot_a = _setup(admin_client, "System A")
    headers_a = _headers(token_a, system_a)
    session_a = _create_approved_session(admin_client, headers_a, snapshot_a)

    token_b, system_b, snapshot_b = _setup(admin_client, "System B")
    headers_b = _headers(token_b, system_b)
    _create_approved_session(admin_client, headers_b, snapshot_b)

    # Traces recorded under System B's component_id must never leak into A's facts.
    _insert_trace(system_b, "summarize_summarize_text")
    _insert_trace(system_b, "summarize_summarize_text")

    r = admin_client.get(
        f"/interview/sessions/{session_a}/runtime-facts", headers=headers_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["facts"]["call_count"] == 0


# --- Reasoning run: success, fail-closed, suppression -------------------------


def _patch_llm(monkeypatch, response=None, error=None):
    import json as _json

    import app.routes.interview as interview_route
    from app.llm import LLMClient, LLMError

    class _FakeClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            if error:
                raise LLMError(error)
            return _json.dumps(response)

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: _FakeClient())


def test_runtime_reality_check_success_creates_qa_rows(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    for i in range(10):
        _insert_trace(
            system_id, "summarize_summarize_text",
            error="timeout" if i < 2 else None,
        )

    _patch_llm(monkeypatch, response={
        "questions": [
            {
                "component_id": "summarize_summarize_text",
                "qualified_name": "summarize.summarize_text",
                "question_text": "20%のリクエストがタイムアウトしていますが、副作用なしという理解は正しいですか?",
                "hypothesis": "state_effects: [none] だが error_rate が高い",
                "answer_options": ["はい、正しいです", "いいえ(修正を入力)"],
            }
        ]
    })

    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] is False
    assert body["error"] is None
    assert body["items_considered"] == 1
    assert len(body["created_qa_ids"]) == 1
    run = body["intelligence_run"]
    assert run["run_type"] == "runtime_reality_check"
    assert run["decision_method"] == "reasoning_llm"
    assert run["status"] == "completed"

    qa_list = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers,
    ).json()
    runtime_rows = [q for q in qa_list["items"] if q["question_source"] == "runtime"]
    assert len(runtime_rows) == 1
    qa = runtime_rows[0]
    assert qa["status"] == "open"
    assert qa["hypothesis"]
    assert qa["runtime_evidence"]["component_id"] == "summarize_summarize_text"
    assert qa["runtime_evidence"]["facts"]["call_count"] == 10
    assert qa["runtime_evidence"]["facts"]["error_count"] == 2
    assert qa["runtime_evidence"]["declared"]["state_effects"] == ["none"]


def test_runtime_reality_check_fails_closed_on_llm_error(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    _patch_llm(monkeypatch, error="provider unavailable")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"]
    assert body["created_qa_ids"] == []
    run = body["intelligence_run"]
    assert run["status"] == "failed"
    assert run["run_type"] == "runtime_reality_check"

    qa_list = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers,
    ).json()
    assert all(q["question_source"] != "runtime" for q in qa_list["items"])


def test_runtime_reality_check_suppressed_while_open_question_exists(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    _patch_llm(monkeypatch, response={
        "questions": [
            {
                "component_id": "summarize_summarize_text",
                "qualified_name": "summarize.summarize_text",
                "question_text": "確認質問",
                "hypothesis": "仮説",
                "answer_options": [],
            }
        ]
    })

    r1 = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r1.status_code == 200, r1.text
    first_run_id = r1.json()["intelligence_run"]["id"]

    r2 = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["skipped"] is True
    assert body2["intelligence_run"] is None

    # No second intelligence_runs row was created for the skipped run.
    from app.db import get_conn

    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM intelligence_runs "
            "WHERE run_type = 'runtime_reality_check' AND system_id = ?",
            (system_id,),
        ).fetchone()["c"]
    assert count == 1


def test_runtime_reality_check_never_auto_changes_metadata_or_policy(admin_client, monkeypatch):
    """Acceptance criterion: metadata/probe plan/policy are never auto-changed."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    before = admin_client.get(
        f"/interview/sessions/{session_id}/approved-set", headers=headers,
    ).json()

    _patch_llm(monkeypatch, response={
        "questions": [
            {
                "component_id": "summarize_summarize_text",
                "qualified_name": "summarize.summarize_text",
                "question_text": "確認質問",
                "hypothesis": "仮説",
                "answer_options": [],
            }
        ]
    })
    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 200, r.text

    after = admin_client.get(
        f"/interview/sessions/{session_id}/approved-set", headers=headers,
    ).json()
    assert after == before

    policy = admin_client.get(
        "/components/summarize_summarize_text/policy", headers=headers,
    )
    assert policy.status_code == 200
    assert policy.json()["mode"] in ("trace", "off")


def test_runtime_evidence_survives_answer_correction(admin_client, monkeypatch):
    """Correcting a runtime question's answer inserts a new revision row;
    the runtime_evidence provenance must carry over to that new current row
    (review fix: it was previously dropped, unlike evidence_refs)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    _patch_llm(monkeypatch, response={
        "questions": [
            {
                "component_id": "summarize_summarize_text",
                "qualified_name": "summarize.summarize_text",
                "question_text": "確認質問",
                "hypothesis": "仮説",
                "answer_options": [],
            }
        ]
    })
    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 200, r.text
    qa_id = r.json()["created_qa_ids"][0]

    # First answer, then a correction (creates a new revision row).
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa_id}/answer",
        json={"answer_text": "はい、正しいです", "actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa_id}/answer",
        json={"answer_text": "いいえ、実はDB書き込みがあります", "actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    corrected = r.json()["qa"]
    assert corrected["id"] != qa_id
    assert corrected["question_source"] == "runtime"
    assert corrected["runtime_evidence"] is not None
    assert corrected["runtime_evidence"]["component_id"] == "summarize_summarize_text"


def test_runtime_evidence_excludes_llm_output(admin_client, monkeypatch):
    """runtime_evidence stores raw facts + declared-metadata provenance only;
    LLM output (answer_options and the like) must not be mixed into it."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    _patch_llm(monkeypatch, response={
        "questions": [
            {
                "component_id": "summarize_summarize_text",
                "qualified_name": "summarize.summarize_text",
                "question_text": "確認質問",
                "hypothesis": "仮説",
                "answer_options": ["はい", "いいえ"],
            }
        ]
    })
    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 200, r.text

    qa_list = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers,
    ).json()
    qa = next(q for q in qa_list["items"] if q["question_source"] == "runtime")
    assert set(qa["runtime_evidence"].keys()) == {
        "component_id", "qualified_name", "path", "metadata_source",
        "declared", "facts",
    }


def test_runtime_facts_misconfigured_window_returns_422(admin_client, monkeypatch):
    """A misconfigured RUNTIME_REALITY_CHECK_WINDOW_DAYS is a structured 422,
    not an unhandled 500 (fail-closed configuration reporting)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_approved_session(admin_client, headers, snapshot_id)

    monkeypatch.setenv("RUNTIME_REALITY_CHECK_WINDOW_DAYS", "0")
    r = admin_client.get(
        f"/interview/sessions/{session_id}/runtime-facts", headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "RUNTIME_REALITY_CHECK_WINDOW_DAYS" in r.json()["detail"]

    r = admin_client.post(
        f"/interview/sessions/{session_id}/runtime-reality-check", headers=headers,
    )
    assert r.status_code == 422, r.text
