"""Tests for Issue #67: system-understanding interview persistence and CRUD.

Covers the pure persistence + contract layer for the #66 conversational
metadata/probe authoring flow: session creation bound to a system + pinned
snapshot, ordered message append, combined per-symbol proposal storage
(docstring metadata block + probe plan), reasoning-run audit linkage,
decision_method defaulting, schema validation, System isolation, and the
additive migration/backfill behavior. No LLM call or worktree write is
exercised because this issue introduces none.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-test.db"))
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


def _insert_snapshot(system_id, commit_sha="abc123"):
    """Insert a pinned repository snapshot directly (no indexing needed here)."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)
            """,
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_symbol(system_id, snapshot_id, path, qualified_name, source_hash):
    from app.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO code_symbols
                (snapshot_id, system_id, path, qualified_name, kind,
                 start_line, end_line, symbol_source_hash, symbol_body_hash)
            VALUES (?, ?, ?, ?, 'function', 1, 5, ?, ?)
            """,
            (snapshot_id, system_id, path, qualified_name, source_hash, source_hash),
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
                evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
                confidence=0.9,
            )
        ],
    )
    graph = build_understanding_graph([result])
    with get_conn() as conn:
        return save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _advance_to_proposal_generation(client, session_id, headers):
    """Advance a session through all stages to proposal_generation.

    Stage advancement only — this does NOT satisfy the Issue #83/#123
    understanding gate, so proposal creation stays locked.
    """
    stages = [
        "purpose_confirmation",
        "capability_confirmation",
        "element_classification",
        "api_boundary_mapping",
        "probe_flow_selection",
        "proposal_generation",
    ]
    for stage in stages:
        client.post(
            f"/interview/sessions/{session_id}/advance-stage",
            json={"stage": stage},
            headers=headers,
        )


def _confirm_and_reach_proposal_generation(client, session_id, headers):
    """Satisfy the proposal gate the way the zero-base UI flow does:
    record an interview answer, then the developer's manual confirmation
    (which also advances the session to proposal_generation)."""
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


def _valid_proposal_item():
    return {
        "path": "src/summarize.py",
        "qualified_name": "summarize.summarize_text",
        "metadata": {
            "role": "Summarize free text into a short abstract",
            "capability": "summarization",
            "system_purpose": "Help users digest long documents",
            "probe_value": "Validate summary quality and latency",
            "element_type": "core",
            "operation_kind": "analysis",
            "consumers": ["api.handlers.summarize_endpoint"],
            "state_effects": ["network", "external-api"],
        },
        "probe_plan": {
            "feature_id": "summarization",
            "objective": "Trace summarizer inputs/outputs",
            "reason": "Pure-ish transformation, safe to trace",
            "recommended_mode": "trace",
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


# --- Session CRUD ----------------------------------------------------------


def test_create_list_and_get_session(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)

    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "Understand summarizer", "focus": "summarize"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    session = r.json()
    assert session["status"] == "open"
    assert session["system_id"] == system_id
    assert session["snapshot_id"] == snapshot_id

    r = admin_client.get("/interview/sessions", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["messages"] == []
    assert detail["proposals"] == []


def test_session_requires_snapshot_from_same_system(admin_client):
    token, system_a, _ = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snapshot_b = _insert_snapshot(system_b["id"], commit_sha="deadbeef")

    # Try to bind a System A session to System B's snapshot.
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_b, "title": "x"},
        headers=_headers(token, system_a),
    )
    assert r.status_code == 404, r.text


# --- Messages --------------------------------------------------------------


def test_messages_are_stored_in_order(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id},
        headers=headers,
    ).json()
    sid = session["id"]

    r = admin_client.post(
        f"/interview/sessions/{sid}/messages",
        json={"role": "user", "content": "summarizerの役割を知りたい"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = admin_client.post(
        f"/interview/sessions/{sid}/messages",
        json={"role": "assistant", "content": "提案を作りました"},
        headers=headers,
    )
    assert r.status_code == 201

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_message_with_unknown_run_reference_is_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
    ).json()
    r = admin_client.post(
        f"/interview/sessions/{session['id']}/messages",
        json={"role": "assistant", "content": "x", "intelligence_run_id": 9999},
        headers=headers,
    )
    assert r.status_code == 404, r.text


# --- Proposals -------------------------------------------------------------


def test_proposal_roundtrips_with_audit_and_default_decision_method(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
    ).json()
    sid = session["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert len(created) == 1
    proposal = created[0]

    # decision_method defaults to reasoning_llm; approval_state to proposed.
    assert proposal["decision_method"] == "reasoning_llm"
    assert proposal["approval_state"] == "proposed"

    # Combined payload round-trips: metadata block + probe plan.
    assert proposal["metadata"]["element_type"] == "core"
    assert proposal["metadata"]["operation_kind"] == "analysis"
    assert proposal["metadata"]["state_effects"] == ["network", "external-api"]
    assert proposal["metadata"]["consumers"] == ["api.handlers.summarize_endpoint"]
    assert proposal["probe_plan"]["recommended_mode"] == "trace"
    assert proposal["probe_plan"]["replayability"] == "safe"

    # Reasoning-run audit metadata is recorded and linked.
    assert proposal["intelligence_run_id"] is not None
    run = proposal["intelligence_run"]
    assert run["run_type"] == "interview_proposal"
    assert run["provider"] == "mock"
    assert run["model"] == "mock-reasoner"
    assert run["prompt_version"] == "interview-v1"
    assert run["decision_method"] == "reasoning_llm"
    assert run["is_mock"] is True

    # Listing and session detail surface the same proposal.
    listed = admin_client.get(
        f"/interview/sessions/{sid}/proposals", headers=headers
    ).json()
    assert len(listed) == 1
    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert len(detail["proposals"]) == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p["metadata"].__setitem__("element_type", "not-a-kind"),
        lambda p: p["metadata"].__setitem__("operation_kind", "destroy"),
        lambda p: p["metadata"].__setitem__("state_effects", ["telepathy"]),
        lambda p: p["probe_plan"].__setitem__("recommended_mode", "replace"),
        lambda p: p["probe_plan"].__setitem__("side_effect_risk", "catastrophic"),
        lambda p: p["metadata"].__setitem__("unknown_field", "x"),
        lambda p: p.pop("qualified_name"),
    ],
)
def test_malformed_proposal_is_rejected(admin_client, mutator):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
    ).json()

    item = _valid_proposal_item()
    mutator(item)
    r = admin_client.post(
        f"/interview/sessions/{session['id']}/proposals",
        json={"audit": _valid_audit(), "proposals": [item]},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# --- System isolation ------------------------------------------------------


def test_system_isolation_for_sessions_and_proposals(admin_client):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snapshot_b = _insert_snapshot(system_b["id"], commit_sha="bbb")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_a}, headers=headers_a
    ).json()
    _confirm_and_reach_proposal_generation(admin_client, session_a["id"], headers_a)
    admin_client.post(
        f"/interview/sessions/{session_a['id']}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers_a,
    )

    # System B sees no sessions.
    assert admin_client.get("/interview/sessions", headers=headers_b).json() == []
    # System B cannot read System A's session or its proposals.
    assert (
        admin_client.get(
            f"/interview/sessions/{session_a['id']}", headers=headers_b
        ).status_code
        == 404
    )
    assert (
        admin_client.get(
            f"/interview/sessions/{session_a['id']}/proposals", headers=headers_b
        ).status_code
        == 404
    )
    # System B cannot append to System A's session.
    assert (
        admin_client.post(
            f"/interview/sessions/{session_a['id']}/messages",
            json={"role": "user", "content": "x"},
            headers=headers_b,
        ).status_code
        == 404
    )
    # A System B session created against System B's snapshot is independent.
    session_b = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_b}, headers=headers_b
    )
    assert session_b.status_code == 201


# --- Migration / backfill --------------------------------------------------


def test_migration_creates_tables_and_preserves_existing_data(admin_client):
    """init_db is additive and idempotent: re-running it creates the new
    interview tables (if missing) and leaves existing rows intact."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
    ).json()

    from app.db import get_conn, init_db

    # Re-running init_db must not raise and must not drop data.
    init_db()

    with get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"interview_session", "interview_message", "interview_proposal"} <= tables
        # Existing data survived the re-run.
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM interview_session WHERE id = ?",
                (session["id"],),
            ).fetchone()["n"]
            == 1
        )
        # A pre-existing table is still populated.
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM systems WHERE id = ?", (system_id,)
            ).fetchone()["n"]
            == 1
        )


# --- Stage Workflow (Issue #82) -----------------------------------------------


def test_session_has_initial_stage(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "stage test"},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["stage"] == "understanding_initialized"


def test_advance_stage(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "advance test"},
        headers=headers,
    )
    session_id = r.json()["id"]

    r = admin_client.post(
        f"/interview/sessions/{session_id}/advance-stage",
        json={"stage": "purpose_confirmation"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "purpose_confirmation"


def test_advance_stage_does_not_go_backward(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "no backward"},
        headers=headers,
    )
    session_id = r.json()["id"]

    admin_client.post(
        f"/interview/sessions/{session_id}/advance-stage",
        json={"stage": "capability_confirmation"},
        headers=headers,
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/advance-stage",
        json={"stage": "purpose_confirmation"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "capability_confirmation"


def test_advance_stage_saves_user_intent(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "intent test"},
        headers=headers,
    )
    session_id = r.json()["id"]

    r = admin_client.post(
        f"/interview/sessions/{session_id}/advance-stage",
        json={"stage": "purpose_confirmation", "user_intent": "Understand the summarizer pipeline"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["user_intent"] == "Understand the summarizer pipeline"


def test_session_understanding_fields_initially_null(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "null test"},
        headers=headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["current_understanding"] is None
    assert data["gap_analysis"] is None
    assert data["open_questions"] is None
    assert data["user_intent"] is None
    # Issue #229: a fresh (not yet confirmed) session is never blocked from
    # rebuilding its understanding.
    assert data["understanding_update_available"] is True


def test_invalid_stage_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "invalid"},
        headers=headers,
    )
    session_id = r.json()["id"]

    r = admin_client.post(
        f"/interview/sessions/{session_id}/advance-stage",
        json={"stage": "nonexistent_stage"},
        headers=headers,
    )
    assert r.status_code == 422


def test_session_has_last_error_field(admin_client):
    """P1: last_error field is present and initially null."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "error test"},
        headers=headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert "last_error" in data
    assert data["last_error"] is None


def _configure_provider_key_mismatch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_dialogue_turn_records_llm_config_failure(admin_client, monkeypatch):
    """Provider/key mismatch must fail closed as a recorded turn, not 500."""
    _configure_provider_key_mismatch(monkeypatch)
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "config mismatch"},
        headers=headers,
    ).json()

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/dialogue-turn",
        json={"user_message": "このシステムを説明して"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assistant_message"] == ""
    assert body["error"]
    assert "ANTHROPIC_API_KEY" in body["error"]
    run = body["intelligence_run"]
    assert run["run_type"] == "interview_dialogue"
    assert run["provider"] == "anthropic"
    assert run["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in run["error_details"]

    detail = admin_client.get(
        f"/interview/sessions/{session['id']}", headers=headers
    ).json()
    assert [m["role"] for m in detail["messages"]] == ["user"]
    assert detail["last_error"]
    assert "ANTHROPIC_API_KEY" in detail["last_error"]


def test_dialogue_turn_failure_persists_last_error_and_success_clears_it(
    admin_client, monkeypatch
):
    """A structured-response validation failure (e.g. the model returning too
    many answer_options) must not just flash a toast: it has to be readable
    from the session afterwards, and a later successful turn must clear it."""
    from app.routes import interview as interview_routes
    from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult

    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "parse failure"},
        headers=headers,
    ).json()
    sid = session["id"]

    monkeypatch.setattr(
        interview_routes, "create_llm_client", lambda config: object()
    )
    monkeypatch.setattr(
        interview_routes,
        "select_evidence_targets",
        lambda client, config, **kwargs: EvidenceSelectionResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            need_evidence=False,
        ),
    )
    monkeypatch.setattr(
        interview_routes,
        "generate_interview_turn",
        lambda client, config, **kwargs: InterviewTurnResult(
            provider="anthropic",
            model="claude-sonnet-4-5",
            is_mock=False,
            error=(
                "Failed to parse structured response: next_questions.0.answer_options "
                "List should have at most 4 items after validation, not 5"
            ),
        ),
    )
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "このシステムを説明して"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"]

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["last_error"]
    assert "answer_options" in detail["last_error"]

    _stub_reasoning_turn(monkeypatch)
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "続けます"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["last_error"] is None


def test_update_understanding_records_llm_config_failure(admin_client, monkeypatch):
    """Start/refresh understanding should surface config failure on the session."""
    _configure_provider_key_mismatch(monkeypatch)
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "update mismatch"},
        headers=headers,
    ).json()

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_error"]
    assert "ANTHROPIC_API_KEY" in body["last_error"]

    detail = admin_client.get(
        f"/interview/sessions/{session['id']}", headers=headers
    ).json()
    assert detail["messages"][-1]["role"] == "assistant"
    assert "ANTHROPIC_API_KEY" in detail["messages"][-1]["content"]

    # Principle 7: the failed understanding review is a recorded reasoning
    # run, linked from the assistant failure message.
    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'understanding_review'
               ORDER BY id DESC""",
            (system_id,),
        ).fetchone()
    assert run is not None
    assert run["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in run["error_details"]
    assert run["prompt_version"] == "understanding-review-v4"
    assert detail["messages"][-1]["intelligence_run_id"] == run["id"]


def test_update_understanding_uses_existing_graph_without_claim_scan(admin_client, monkeypatch):
    """Interview refresh must not synchronously rescan all documentation chunks."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    _insert_understanding_graph(system_id, snapshot_id)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "reuse graph"},
        headers=headers,
    ).json()

    import app.documentation_claim_scanner as scanner

    def fail_scan(*args, **kwargs):
        raise AssertionError("update-understanding must not run claim scan")

    monkeypatch.setattr(scanner, "scan_all_chunks", fail_scan)

    class FakeReviewClient:
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            import json

            return json.dumps({
                "system_purpose": [{
                    "name": "Probe repository inspection",
                    "summary": "Inspects probe agent repositories",
                    "confidence": {"level": "likely", "reason": "README evidence"},
                    "evidence": [{
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 5,
                        "summary": "README states purpose",
                    }],
                    "why_core": "",
                    "related_docs": ["README.md"],
                    "related_apis": [],
                    "children": [],
                }],
                "core_capabilities": [],
                "capability_elements": [],
                "supporting_elements": [],
                "api_boundaries": [],
                "probe_flow_candidates": [],
                "gap_analysis": [],
                "open_questions": [],
                "suggested_next_action": "confirm_purpose",
            })

    import app.routes.interview as interview_route

    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: FakeReviewClient())

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_error"] is None
    assert body["current_understanding"]


def test_update_understanding_records_run_and_reviewer_qa_rows(admin_client, monkeypatch):
    """A successful understanding review is recorded in intelligence_runs
    (Principle 7 / Issue #127) and its open questions become ID-addressable
    interview_qa rows with qa_id carried in the open_questions JSON
    (Issue #129). Rebuilding does not duplicate identical questions."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    _insert_understanding_graph(system_id, snapshot_id)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "reviewer qa"},
        headers=headers,
    ).json()

    class FakeReviewClient:
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            import json

            return json.dumps({
                "system_purpose": [{
                    "name": "Probe repository inspection",
                    "summary": "Inspects probe agent repositories",
                    "confidence": {"level": "likely", "reason": "README evidence"},
                    "evidence": [{
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 5,
                        "summary": "README states purpose",
                    }],
                    "why_core": "",
                    "related_docs": ["README.md"],
                    "related_apis": [],
                    "children": [],
                }],
                "core_capabilities": [],
                "capability_elements": [],
                "supporting_elements": [],
                "api_boundaries": [],
                "probe_flow_candidates": [],
                "gap_analysis": [],
                "open_questions": [{
                    "question": "トレースの保持期間はどれくらいですか?",
                    "category": "capability",
                    "priority": "high",
                }],
                "suggested_next_action": "confirm_purpose",
            })

    import app.routes.interview as interview_route

    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: FakeReviewClient())

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_error"] is None

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'understanding_review'
               ORDER BY id DESC""",
            (system_id,),
        ).fetchone()
    assert run is not None
    assert run["status"] == "completed"
    assert run["prompt_version"] == "understanding-review-v4"
    assert run["decision_method"] == "reasoning_llm"

    qa_listing = admin_client.get(
        f"/interview/sessions/{session['id']}/qa", headers=headers
    ).json()
    reviewer_items = [
        i for i in qa_listing["items"] if i["question_source"] == "reviewer"
    ]
    assert len(reviewer_items) == 1
    assert reviewer_items[0]["question_text"] == "トレースの保持期間はどれくらいですか?"
    assert reviewer_items[0]["question_category"] == "capability"

    # The open_questions JSON carries the qa_id so the dashboard can answer
    # by ID instead of exact text.
    assert body["open_questions"][0]["qa_id"] == reviewer_items[0]["id"]

    # A rebuild with the same questions must not duplicate the Q&A rows.
    r2 = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    qa_after = admin_client.get(
        f"/interview/sessions/{session['id']}/qa", headers=headers
    ).json()
    assert len([
        i for i in qa_after["items"] if i["question_source"] == "reviewer"
    ]) == 1


def test_update_understanding_without_graph_fails_fast(admin_client, monkeypatch):
    """Missing graph should surface a fast actionable error, not trigger a heavy scan."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "missing graph"},
        headers=headers,
    ).json()

    import app.documentation_claim_scanner as scanner

    def fail_scan(*args, **kwargs):
        raise AssertionError("missing graph path must not run claim scan")

    monkeypatch.setattr(scanner, "scan_all_chunks", fail_scan)

    class FakeClient:
        pass

    import app.routes.interview as interview_route

    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: FakeClient())

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_error"]
    assert "理解グラフが未構築" in body["last_error"]


def test_update_understanding_is_rejected_after_confirmation_without_revision(admin_client, monkeypatch):
    """A direct refresh must not re-run the reviewer in proposal stage."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "confirmed session"},
        headers=headers,
    ).json()
    _confirm_and_reach_proposal_generation(admin_client, session["id"], headers)

    repeated_confirm = admin_client.post(
        f"/interview/sessions/{session['id']}/confirm-understanding",
        json={"actor": "another-user"},
        headers=headers,
    )
    assert repeated_confirm.status_code == 200, repeated_confirm.text
    detail = admin_client.get(
        f"/interview/sessions/{session['id']}", headers=headers,
    ).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "system"]
    # Issue #229: the session serializer must mirror the API gate exactly so
    # the Dashboard can disable the button without a second source of truth.
    assert detail["understanding_update_available"] is False

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "understanding_update_not_available",
        "message": "Understanding is already confirmed. Update it after correcting an interview answer.",
        "next_action": "generate_or_review_proposals",
    }

    from app.db import get_conn

    with get_conn() as conn:
        runs = conn.execute(
            """SELECT COUNT(*) AS n FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'understanding_review'""",
            (system_id,),
        ).fetchone()["n"]
    assert runs == 0

    # A real answer correction re-enables the route. The mock client keeps
    # this check local; the missing graph then returns the normal session
    # error rather than the stage guard.
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET answers_revised_at = ? WHERE id = ?",
            (time.time(), session["id"]),
        )
    # The flag flips to available purely from the DB state, before the
    # rebuild endpoint is ever called again.
    reopened = admin_client.get(
        f"/interview/sessions/{session['id']}", headers=headers,
    ).json()
    assert reopened["understanding_update_available"] is True

    allowed = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding",
        headers=headers,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["last_error"] != ""


# --- Issue #263: Q&A panel answers reach the review + widened rebuild gate --


def test_update_understanding_opens_gate_on_first_time_answer_after_confirmation(
    admin_client, monkeypatch,
):
    """A first-time answer (open -> answered) given after confirmation must
    open the rebuild gate, even though `answers_revised_at` is only ever set
    by the *correction* path (Issue #263)."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "first answer after confirm"},
        headers=headers,
    ).json()
    sid = session["id"]

    # The question already existed (e.g. from an earlier review pass) before
    # confirmation -- only its *answer* is new.
    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "対象コンポーネントは何ですか?",
            "question_category": "purpose",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()

    time.sleep(0.01)
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    still_blocked = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert still_blocked.status_code == 409, still_blocked.text
    blocked_detail = admin_client.get(
        f"/interview/sessions/{sid}", headers=headers,
    ).json()
    assert blocked_detail["understanding_update_available"] is False

    time.sleep(0.01)
    answer = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "APIサーバーです", "actor": "root"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["qa"]["status"] == "answered"
    # Confirms this really is the first-answer branch, not a correction.
    assert answer.json()["previous"] is None

    # The session flag opens as soon as the Q&A answer lands — a first-time
    # answer never touches `answers_revised_at`, so this proves the flag
    # tracks the same widened Issue #263 condition as the API gate rather
    # than re-deriving the pre-#263 `answers_revised_at`-only check.
    reopened_detail = admin_client.get(
        f"/interview/sessions/{sid}", headers=headers,
    ).json()
    assert reopened_detail["understanding_update_available"] is True

    allowed = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert allowed.status_code == 200, allowed.text

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT answers_revised_at FROM interview_session WHERE id = ?", (sid,),
        ).fetchone()
    assert row["answers_revised_at"] is None  # this branch never sets it


def test_update_understanding_opens_gate_on_answer_revision(admin_client, monkeypatch):
    """Correcting an already-answered question after confirmation opens the
    gate via the existing `answers_revised_at` path, driven end-to-end
    through the real answer/correction routes rather than a direct DB poke."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "revision after confirm"},
        headers=headers,
    ).json()
    sid = session["id"]

    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "対象コンポーネントは何ですか?",
            "question_category": "purpose",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "APIサーバーです", "actor": "root"},
        headers=headers,
    )

    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    still_blocked = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert still_blocked.status_code == 409, still_blocked.text

    correction = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "バッチジョブサーバーです", "actor": "root"},
        headers=headers,
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["previous"] is not None

    allowed = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert allowed.status_code == 200, allowed.text


def test_update_understanding_opens_gate_on_runtime_check_answer_after_confirmation(
    admin_client, monkeypatch,
):
    """A Runtime Reality Check question (question_source='runtime') answered
    after confirmation must open the gate the same way a reviewer question
    does (Issue #263)."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "runtime check after confirm"},
        headers=headers,
    ).json()
    sid = session["id"]

    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "実行時にこの関数は本当に呼ばれていますか?",
            "question_category": "general",
            "question_source": "runtime",
        },
        headers=headers,
    ).json()

    time.sleep(0.01)
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    still_blocked = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert still_blocked.status_code == 409, still_blocked.text

    time.sleep(0.01)
    answer = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "はい、1日あたり数百回呼ばれています", "actor": "root"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text

    allowed = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert allowed.status_code == 200, allowed.text


def test_update_understanding_stays_blocked_with_no_new_qa_activity(
    admin_client, monkeypatch,
):
    """Confirmed with no answer/correction/new question at all: the gate
    must stay closed exactly as before (Issue #263 must not loosen this
    case)."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "no new info"},
        headers=headers,
    ).json()
    sid = session["id"]

    # A question exists but is answered BEFORE confirmation -- ordinary,
    # already-incorporated input, not "new info" relative to confirmation.
    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "対象コンポーネントは何ですか?",
            "question_category": "purpose",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "APIサーバーです", "actor": "root"},
        headers=headers,
    )

    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    r = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert r.status_code == 409, r.text

    from app.db import get_conn

    with get_conn() as conn:
        runs = conn.execute(
            """SELECT COUNT(*) AS n FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'understanding_review'""",
            (system_id,),
        ).fetchone()["n"]
    assert runs == 0


# --- Review-finding fix: last-successful-rebuild watermark ------------------


def test_update_understanding_gate_closes_after_successful_rebuild(admin_client, monkeypatch):
    """A new Q&A answer given after confirmation opens the rebuild gate
    (Issue #263). Once a rebuild actually SUCCEEDS and consumes that answer,
    the gate must close again -- without this fix, the interview_qa
    condition kept comparing against the original confirmation timestamp
    forever, so the same already-consumed row kept the gate open even after
    a successful rebuild."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    _insert_understanding_graph(system_id, snapshot_id)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "gate closes after rebuild"},
        headers=headers,
    ).json()
    sid = session["id"]

    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "対象コンポーネントは何ですか?",
            "question_category": "purpose",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()

    time.sleep(0.01)
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    time.sleep(0.01)
    answer = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "APIサーバーです", "actor": "root"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text

    opened = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert opened["understanding_update_available"] is True

    class FakeReviewClient:
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            import json as _json

            return _json.dumps({
                "system_purpose": [{
                    "name": "Probe repository inspection",
                    "summary": "Inspects probe agent repositories",
                    "confidence": {"level": "likely", "reason": "README evidence"},
                    "evidence": [{
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 5,
                        "summary": "README states purpose",
                    }],
                    "why_core": "",
                    "related_docs": ["README.md"],
                    "related_apis": [],
                    "children": [],
                }],
                "core_capabilities": [],
                "capability_elements": [],
                "supporting_elements": [],
                "api_boundaries": [],
                "probe_flow_candidates": [],
                "gap_analysis": [],
                "open_questions": [],
                "suggested_next_action": "confirm_purpose",
            })

    import app.routes.interview as interview_route

    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: FakeReviewClient())

    rebuild = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert rebuild.status_code == 200, rebuild.text
    assert rebuild.json()["last_error"] is None

    # The successful rebuild consumed the new answer -- the gate must close
    # again with no further developer input.
    closed = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert closed["understanding_update_available"] is False

    blocked_again = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert blocked_again.status_code == 409, blocked_again.text
    assert blocked_again.json()["detail"]["code"] == "understanding_update_not_available"

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT understanding_rebuilt_at FROM interview_session WHERE id = ?",
            (sid,),
        ).fetchone()
    assert row["understanding_rebuilt_at"] is not None


def test_update_understanding_failed_rebuild_leaves_gate_open(admin_client, monkeypatch):
    """A FAILED rebuild (missing graph) must not advance the watermark: the
    same new Q&A answer that opened the gate must keep it open until a
    rebuild actually succeeds."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    # No _insert_understanding_graph call -- update-understanding hits the
    # graph_not_built failure path below.
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "failed rebuild keeps gate open"},
        headers=headers,
    ).json()
    sid = session["id"]

    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "対象コンポーネントは何ですか?",
            "question_category": "purpose",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()

    time.sleep(0.01)
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    time.sleep(0.01)
    answer = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "APIサーバーです", "actor": "root"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text

    opened = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert opened["understanding_update_available"] is True

    failed = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["last_error"]
    assert "理解グラフが未構築" in failed.json()["last_error"]

    # The failure must not have closed the gate.
    still_open = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert still_open["understanding_update_available"] is True

    retried = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert retried.status_code == 200, retried.text

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT understanding_rebuilt_at FROM interview_session WHERE id = ?",
            (sid,),
        ).fetchone()
    assert row["understanding_rebuilt_at"] is None


def test_update_understanding_injects_qa_panel_only_answer(admin_client, monkeypatch):
    """An answer given ONLY through the Q&A panel (no interview_message row
    is ever created for it) must still reach the review prompt (Issue
    #263)."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    _insert_understanding_graph(system_id, snapshot_id)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "qa panel only"},
        headers=headers,
    ).json()
    sid = session["id"]

    qa = admin_client.post(
        f"/interview/sessions/{sid}/qa",
        json={
            "question_text": "このコンポーネントのオーナーは誰ですか?",
            "question_category": "general",
            "question_source": "reviewer",
        },
        headers=headers,
    ).json()
    answer = admin_client.post(
        f"/interview/sessions/{sid}/qa/{qa['id']}/answer",
        json={"answer_text": "プラットフォームチームです", "actor": "root"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text

    # No interview_message was ever posted for this session.
    from app.db import get_conn

    with get_conn() as conn:
        message_count = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_message WHERE session_id = ?",
            (sid,),
        ).fetchone()["n"]
    assert message_count == 0

    captured = {}

    class CapturingReviewClient:
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            import json

            captured["messages"] = messages
            return json.dumps({
                "system_purpose": [],
                "core_capabilities": [],
                "capability_elements": [],
                "supporting_elements": [],
                "api_boundaries": [],
                "probe_flow_candidates": [],
                "gap_analysis": [],
                "open_questions": [],
                "suggested_next_action": "confirm_purpose",
            })

    import app.routes.interview as interview_route

    monkeypatch.setattr(interview_route, "create_llm_client", lambda config: CapturingReviewClient())

    r = admin_client.post(
        f"/interview/sessions/{sid}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["last_error"] is None

    user_prompt = captured["messages"][1]["content"]
    assert "このコンポーネントのオーナーは誰ですか?" in user_prompt
    assert "プラットフォームチームです" in user_prompt


def test_review_history_excludes_confirmation_control_events():
    """Control/audit messages must not be interpreted as developer evidence."""
    from app.routes.interview import _review_history

    history = _review_history([
        {"role": "user", "content": "対象は API サーバーです。"},
        {"role": "system", "content": "Interview snapshot updated from #1 to #2."},
        # Legacy sessions persisted this workflow event with role=user.
        {"role": "user", "content": "これまでの回答内容を確定し、提案生成に進みます。"},
        {"role": "assistant", "content": "目的を確認してください。"},
    ])

    assert history == [
        {"role": "user", "content": "対象は API サーバーです。"},
        {"role": "assistant", "content": "目的を確認してください。"},
    ]


def test_proposals_rejected_before_proposal_stage(admin_client):
    """P1: /proposals API must enforce stage gate."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "gate test"},
        headers=headers,
    ).json()
    sid = session["id"]

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers,
    )
    assert r.status_code == 422
    assert "proposal_generation" in r.json()["detail"]


def test_proposals_accepted_in_proposal_stage(admin_client):
    """P1: /proposals API works when in proposal_generation stage."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "gate accept"},
        headers=headers,
    ).json()
    sid = session["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers,
    )
    assert r.status_code == 201


def test_proposal_provenance_fields_persisted(admin_client):
    """P2: proposal provenance fields are stored and returned."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "provenance test"},
        headers=headers,
    ).json()
    sid = session["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    item = _valid_proposal_item()
    item["graph_node_id"] = "abc123def456"
    item["capability_name"] = "Trace Recording"
    item["evidence_summary"] = "README.md:1-5 describes trace recording"
    item["proposal_confidence"] = 0.85

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [item]},
        headers=headers,
    )
    assert r.status_code == 201
    proposal = r.json()[0]
    assert proposal["graph_node_id"] == "abc123def456"
    assert proposal["capability_name"] == "Trace Recording"
    assert proposal["evidence_summary"] == "README.md:1-5 describes trace recording"
    assert proposal["proposal_confidence"] == 0.85


# --- Issue #123: zero-base confirmation gate + open-question consumption -----


def _stub_reasoning_turn(
    monkeypatch, *, proposals=None, next_questions=None, captured=None,
):
    """Stub the route-level reasoning turn with a successful result.

    Only the LLM call is stubbed; persistence, gating, and question
    consumption run through the real route code. When ``captured`` (a dict)
    is supplied, the keyword arguments the route passes to the reasoning
    turn are recorded into it for assertions.
    """
    from app.routes import interview as interview_routes
    from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(
        client, config, *, context_pack, history, user_message, **kwargs
    ):
        return EvidenceSelectionResult(
            provider="anthropic",
            model="claude-sonnet-4-5",
            is_mock=False,
            need_evidence=False,
        )

    def fake_generate_interview_turn(
        client, config, *, context_pack, history, user_message, **kwargs
    ):
        if captured is not None:
            captured.update(kwargs)
            captured["user_message"] = user_message
        return InterviewTurnResult(
            provider="anthropic",
            model="claude-sonnet-4-5",
            is_mock=False,
            assistant_message="了解しました。",
            proposals=list(proposals or []),
            next_questions=list(next_questions or []),
        )

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )


def _stub_proposal_result():
    from app.interview_agent import InterviewProposalResult
    from app.models import InterviewProposalMetadataBlock, InterviewProposalProbePlan

    item = _valid_proposal_item()
    return InterviewProposalResult(
        path=item["path"],
        qualified_name=item["qualified_name"],
        symbol_id=None,
        metadata=InterviewProposalMetadataBlock(**item["metadata"]),
        probe_plan=InterviewProposalProbePlan(**item["probe_plan"]),
    )


def _set_open_questions(session_id, questions):
    import json

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET open_questions = ? WHERE id = ?",
            (json.dumps(questions, ensure_ascii=False), session_id),
        )


def test_confirm_understanding_requires_context(admin_client):
    """Confirming an empty session (no understanding, no answers) is rejected."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "empty confirm"},
        headers=headers,
    ).json()

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 422


def test_confirm_understanding_unlocks_zero_base_proposals(admin_client, monkeypatch):
    """Zero-base flow: manual confirmation satisfies the proposal gate."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "zero base"},
        headers=headers,
    ).json()
    sid = session["id"]

    # Zero-base answers exist as user messages; no current_understanding.
    r = admin_client.post(
        f"/interview/sessions/{sid}/messages",
        json={"role": "user", "content": "目標はトレースの安定運用です"},
        headers=headers,
    )
    assert r.status_code == 201

    r = admin_client.post(
        f"/interview/sessions/{sid}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["understanding_confirmed_at"] is not None
    assert body["understanding_confirmed_by"] == "root"
    assert body["stage"] == "proposal_generation"
    assert body["current_understanding"] is None

    # With confirmation recorded, a successful reasoning turn now persists
    # proposals even though current_understanding is still null.
    _stub_reasoning_turn(monkeypatch, proposals=[_stub_proposal_result()])
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "提案を生成してください", "generate_proposals": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert len(body["proposals"]) == 1

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert len(detail["proposals"]) == 1
    assert detail["proposals"][0]["decision_method"] == "reasoning_llm"


def test_proposals_stay_gated_without_understanding_or_confirmation(
    admin_client, monkeypatch
):
    """Reaching proposal_generation alone must not unlock proposals."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "still gated"},
        headers=headers,
    ).json()
    sid = session["id"]
    _advance_to_proposal_generation(admin_client, sid, headers)

    _stub_reasoning_turn(monkeypatch, proposals=[_stub_proposal_result()])
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "提案を生成してください", "generate_proposals": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["proposals"] == []

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["proposals"] == []


# --- Empty-proposal narrowing (proposals_requested contract) -----------------


def test_requested_turn_with_zero_proposals_reports_narrowing(
    admin_client, monkeypatch
):
    """A generate_proposals turn that yields no proposals is a narrowing
    turn: proposals_requested is reported, the model was told about the
    request, and the narrowing question is persisted for the dashboard."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "narrowing"},
        headers=headers,
    ).json()
    sid = session["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    captured: dict = {}
    _stub_reasoning_turn(
        monkeypatch,
        next_questions=["対象は要約フローで正しいですか?"],
        captured=captured,
    )
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "提案を生成してください", "generate_proposals": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert body["proposals"] == []
    assert body["proposals_requested"] is True
    assert [q["question_text"] for q in body["next_questions"]] == [
        "対象は要約フローで正しいですか?"
    ]
    # The route told the reasoning model that proposals were requested
    # (prompt interview-v6 contract: propose or keep narrowing).
    assert captured["proposals_requested"] is True
    # The narrowing question is persisted so the dashboard can re-surface it
    # in ready_for_proposals and consume it on the next turn.
    texts = [q["question"] for q in body["open_questions_structured"]]
    assert "対象は要約フローで正しいですか?" in texts
    # Narrowing is a valid turn, not a failure.
    assert body["intelligence_run"]["status"] == "completed"


def test_gated_turn_does_not_request_proposals_from_model(
    admin_client, monkeypatch
):
    """With the understanding gate closed, generate_proposals never reaches
    the reasoning model as a request, and the response reports it."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "gated request"},
        headers=headers,
    ).json()
    sid = session["id"]
    _advance_to_proposal_generation(admin_client, sid, headers)

    captured: dict = {}
    _stub_reasoning_turn(
        monkeypatch, proposals=[_stub_proposal_result()], captured=captured
    )
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "提案を生成してください", "generate_proposals": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert captured["proposals_requested"] is False
    assert body["proposals_requested"] is False
    assert body["proposals"] == []


def test_plain_turn_reports_proposals_not_requested(admin_client, monkeypatch):
    """Even with the gate open, a turn without generate_proposals is an
    ordinary conversation turn."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "plain turn"},
        headers=headers,
    ).json()
    sid = session["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)

    captured: dict = {}
    _stub_reasoning_turn(monkeypatch, captured=captured)
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={"user_message": "補足です: 精度を重視したい"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert captured["proposals_requested"] is False
    assert r.json()["proposals_requested"] is False


def test_dialogue_turn_consumes_answered_question(admin_client, monkeypatch):
    """The answered open question is removed; model follow-ups are appended."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "consume question"},
        headers=headers,
    ).json()
    sid = session["id"]
    _set_open_questions(sid, [
        {"question": "認証はどの層で行いますか?", "category": "boundary", "priority": "high"},
        {"question": "トレースの保持期間は?", "category": "capability", "priority": "low"},
    ])

    _stub_reasoning_turn(monkeypatch, next_questions=["リトライ方針はありますか?"])
    r = admin_client.post(
        f"/interview/sessions/{sid}/dialogue-turn",
        json={
            "user_message": "APIゲートウェイ層で認証します",
            "answered_question": "認証はどの層で行いますか?",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    questions = body["open_questions_structured"]
    texts = [q["question"] for q in questions]
    assert "認証はどの層で行いますか?" not in texts
    assert "トレースの保持期間は?" in texts
    assert "リトライ方針はありますか?" in texts
    followup = next(q for q in questions if q["question"] == "リトライ方針はありますか?")
    assert followup["category"] == "followup"
    assert followup["priority"] == "medium"

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert [q["question"] for q in detail["open_questions"]] == texts


def test_understanding_confirmation_columns_migrated(admin_client):
    """Additive migration: confirmation columns exist and default to null."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    data = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "migration"},
        headers=headers,
    ).json()
    assert data["understanding_confirmed_at"] is None
    assert data["understanding_confirmed_by"] is None


def test_proposals_endpoint_rejected_without_understanding_confirmation(admin_client):
    """Stage alone must not unlock /proposals persistence (Issue #123)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "persist gate"},
        headers=headers,
    ).json()
    sid = session["id"]
    _advance_to_proposal_generation(admin_client, sid, headers)

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers,
    )
    assert r.status_code == 422
    assert "locked until understanding is confirmed" in r.json()["detail"]

    # After an interview answer and the manual confirmation, the same
    # request succeeds.
    r = admin_client.post(
        f"/interview/sessions/{sid}/messages",
        json={"role": "user", "content": "対象と目的を確認しました"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = admin_client.post(
        f"/interview/sessions/{sid}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [_valid_proposal_item()]},
        headers=headers,
    )
    assert r.status_code == 201, r.text


def test_rebase_snapshot_preserves_unchanged_approved_proposal(admin_client):
    token, system_id, snapshot_a = _setup(admin_client)
    headers = _headers(token, system_id)
    snapshot_b = _insert_snapshot(system_id, "def456")
    sym_a = _insert_symbol(
        system_id, snapshot_a, "src/summarize.py",
        "summarize.summarize_text", "same-source",
    )
    sym_b = _insert_symbol(
        system_id, snapshot_b, "src/summarize.py",
        "summarize.summarize_text", "same-source",
    )

    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_a, "title": "rebase preserve"},
        headers=headers,
    )
    sid = r.json()["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)
    item = _valid_proposal_item()
    item["symbol_id"] = sym_a
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [item]},
        headers=headers,
    )
    proposal_id = r.json()[0]["id"]
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{proposal_id}/approve",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    r = admin_client.post(
        f"/interview/sessions/{sid}/rebase-snapshot",
        json={"target_snapshot_id": snapshot_b, "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_snapshot_id"] == snapshot_a
    assert body["to_snapshot_id"] == snapshot_b
    assert body["proposals_preserved"] == 1
    assert body["proposals_marked_needs_review"] == 0

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["snapshot_id"] == snapshot_b
    assert detail["proposals"][0]["snapshot_id"] == snapshot_b
    assert detail["proposals"][0]["symbol_id"] == sym_b
    assert detail["proposals"][0]["approval_state"] == "approved"
    approved = admin_client.get(
        f"/interview/sessions/{sid}/approved-set", headers=headers,
    ).json()
    assert approved["snapshot_id"] == snapshot_b
    assert approved["approved_count"] == 1


def test_rebase_snapshot_marks_changed_approved_proposal_needs_review(admin_client):
    token, system_id, snapshot_a = _setup(admin_client)
    headers = _headers(token, system_id)
    snapshot_b = _insert_snapshot(system_id, "def456")
    sym_a = _insert_symbol(
        system_id, snapshot_a, "src/summarize.py",
        "summarize.summarize_text", "old-source",
    )
    sym_b = _insert_symbol(
        system_id, snapshot_b, "src/summarize.py",
        "summarize.summarize_text", "new-source",
    )

    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_a, "title": "rebase changed"},
        headers=headers,
    )
    sid = r.json()["id"]
    _confirm_and_reach_proposal_generation(admin_client, sid, headers)
    item = _valid_proposal_item()
    item["symbol_id"] = sym_a
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals",
        json={"audit": _valid_audit(), "proposals": [item]},
        headers=headers,
    )
    proposal_id = r.json()[0]["id"]
    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{proposal_id}/approve",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    r = admin_client.post(
        f"/interview/sessions/{sid}/rebase-snapshot",
        json={"target_snapshot_id": snapshot_b, "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposals_preserved"] == 0
    assert body["proposals_marked_needs_review"] == 1
    assert body["proposals_changed_source"] == 1

    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["snapshot_id"] == snapshot_b
    assert detail["proposals"][0]["snapshot_id"] == snapshot_b
    assert detail["proposals"][0]["symbol_id"] == sym_b
    assert detail["proposals"][0]["approval_state"] == "needs_review"
    approved = admin_client.get(
        f"/interview/sessions/{sid}/approved-set", headers=headers,
    ).json()
    assert approved["approved_count"] == 0
    assert approved["pending_count"] == 1

    r = admin_client.post(
        f"/interview/sessions/{sid}/proposals/{proposal_id}/approve",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    detail = admin_client.get(f"/interview/sessions/{sid}", headers=headers).json()
    assert detail["proposals"][0]["approval_state"] == "approved"
    approved = admin_client.get(
        f"/interview/sessions/{sid}/approved-set", headers=headers,
    ).json()
    assert approved["approved_count"] == 1
