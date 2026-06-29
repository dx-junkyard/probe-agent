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
    return r.json()["access_token"]


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


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


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
