"""Tests for Issue #69: reasoning dialogue and combined proposal generation.

Covers: proposal generation from mock reasoning (marked mock); reasoning
failure → fail closed with persisted detail; denylist enforcement; structured-
output validation rejection; dialogue turn persistence through #67.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-agent.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name="System A"):
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


def _insert_symbols(system_id, snapshot_id, names, prefix="src/app.py"):
    from app.db import get_conn

    ids = []
    with get_conn() as conn:
        for i, name in enumerate(names):
            cur = conn.execute(
                """INSERT INTO code_symbols
                       (snapshot_id, system_id, path, qualified_name, kind,
                        start_line, end_line)
                   VALUES (?, ?, ?, ?, 'function', ?, ?)""",
                (snapshot_id, system_id, prefix, name, 10 * i + 1, 10 * i + 9),
            )
            ids.append(cur.lastrowid)
    return ids


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- AC: mock proposal generation from snapshot with no metadata -------------


def test_mock_proposals_from_unclassified_snapshot(admin_client):
    """Proposal generation from a snapshot with no existing metadata yields
    validated combined proposals (mock reasoning client, marked mock)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _insert_symbols(system_id, snapshot_id, ["func_a", "func_b", "func_c"])
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/dialogue-turns",
        json={"message": "Analyze the unclassified symbols"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    turn = r.json()

    assert turn["error"] is None
    assert turn["user_message"]["role"] == "user"
    assert turn["assistant_message"]["role"] == "assistant"
    assert turn["assistant_message"]["intelligence_run_id"] is not None

    assert len(turn["proposals"]) == 3
    for p in turn["proposals"]:
        assert p["is_mock"] is True
        assert p["decision_method"] == "reasoning_llm"
        assert p["approval_state"] == "proposed"
        assert p["metadata"]["element_type"] in (
            "system", "core", "capability", "element", "supporting", "boundary",
        )
        assert p["metadata"]["operation_kind"] in (
            "analysis", "read", "write", "mutation", "io", "orchestration",
            "validation", "other",
        )
        assert all(
            s in ("none", "database-read", "database-write", "network",
                  "filesystem", "cache", "external-api", "queue")
            for s in p["metadata"]["state_effects"]
        )
        assert p["probe_plan"]["recommended_mode"] in ("trace", "shadow")
        assert p["probe_plan"]["side_effect_risk"] in (
            "none", "low", "medium", "high",
        )
        assert p["probe_plan"]["replayability"] in ("safe", "caution", "unsafe")

    # Intelligence run is persisted.
    run = turn["proposals"][0]["intelligence_run"]
    assert run["run_type"] == "interview_proposal"
    assert run["is_mock"] is True
    assert run["status"] == "completed"

    # Messages are persisted to the session detail.
    detail = admin_client.get(
        f"/interview/sessions/{session['id']}",
        headers=headers,
    ).json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"
    assert len(detail["proposals"]) == 3


# --- AC: reasoning failure → fail closed, persisted detail -------------------


def test_reasoning_failure_fails_closed(admin_client, monkeypatch):
    """Reasoning-model failure → run fails closed, failure persisted, no
    proposal stored, no heuristic fallback."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LLM_API_KEY", "fake-key")

    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _insert_symbols(system_id, snapshot_id, ["func_a"])
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/dialogue-turns",
        json={"message": "Analyze this"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    turn = r.json()

    assert turn["error"] is not None
    assert "reasoning model" in turn["error"].lower()
    assert turn["proposals"] == []
    assert turn["assistant_message"] is None

    # User message is still persisted.
    assert turn["user_message"]["role"] == "user"
    assert turn["user_message"]["content"] == "Analyze this"

    # Failed run is persisted in the DB.
    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'interview_proposal'
               ORDER BY id DESC LIMIT 1""",
            (system_id,),
        ).fetchone()
        assert run is not None
        assert run["status"] == "failed"
        assert run["error_details"] is not None
        assert "reasoning model" in run["error_details"].lower()

        # No proposals stored.
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_proposal WHERE session_id = ?",
            (session["id"],),
        ).fetchone()["n"]
        assert count == 0


# --- AC: denylist enforcement ------------------------------------------------


def test_denylisted_symbol_excluded_from_probe_suggestions(admin_client):
    """A denylisted symbol (payment/auth/email) is excluded from probe
    suggestions even if the model proposes it."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _insert_symbols(
        system_id,
        snapshot_id,
        ["safe_func", "process_payment", "send_email_notification", "auth_login"],
    )
    session = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/dialogue-turns",
        json={"message": "Analyze all symbols"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    turn = r.json()

    assert turn["error"] is None
    proposal_names = {p["qualified_name"] for p in turn["proposals"]}
    assert "safe_func" in proposal_names
    assert "process_payment" not in proposal_names
    assert "send_email_notification" not in proposal_names
    assert "auth_login" not in proposal_names

    assert len(turn["denied_symbols"]) == 3
    denied_text = " ".join(turn["denied_symbols"])
    assert "payment" in denied_text.lower()
    assert "email" in denied_text.lower()
    assert "auth_login" in denied_text.lower()


# --- AC: structured-output validation rejects invalid vocab ------------------


def test_invalid_element_type_rejected(admin_client, monkeypatch):
    """Structured-output validation rejects an invalid element_type value."""
    from app.interview_agent import InterviewTurnResult, generate_interview_turn
    from app.llm import LLMClient, LLMConfig

    class _BadClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return json.dumps({
                "assistant_message": "Here are my proposals.",
                "findings": [],
                "proposals": [{
                    "path": "src/app.py",
                    "qualified_name": "func_a",
                    "metadata": {
                        "role": "Does stuff",
                        "element_type": "not_a_valid_type",
                        "operation_kind": "analysis",
                    },
                    "probe_plan": {
                        "objective": "Trace it",
                        "reason": "Need data",
                        "recommended_mode": "trace",
                    },
                }],
                "next_questions": [],
            })

    from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=1, total_entrypoints=0,
        classified_count=0, unclassified_count=1,
        budget_max_chars=60000, budget_used_chars=100,
        symbols=[InterviewSymbolItem(
            symbol_id=1, path="src/app.py", qualified_name="func_a",
            kind="function", start_line=1, end_line=10,
            classification="unclassified",
            evidence=InterviewEvidenceLocation(
                snapshot_id=1, path="src/app.py", qualified_name="func_a",
                start_line=1, end_line=10,
            ),
        )],
    )

    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _BadClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is not None
    assert "invalid metadata" in result.error.lower() or "element_type" in result.error.lower()
    assert result.proposals == []


def test_invalid_operation_kind_rejected():
    """Structured-output validation rejects an invalid operation_kind."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig
    from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation

    class _BadOpClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return json.dumps({
                "assistant_message": "Proposals.",
                "findings": [],
                "proposals": [{
                    "path": "src/app.py",
                    "qualified_name": "func_a",
                    "metadata": {
                        "element_type": "core",
                        "operation_kind": "destroy",
                    },
                    "probe_plan": {
                        "objective": "Trace",
                        "reason": "Test",
                    },
                }],
                "next_questions": [],
            })

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=1, total_entrypoints=0,
        classified_count=0, unclassified_count=1,
        budget_max_chars=60000, budget_used_chars=100,
        symbols=[InterviewSymbolItem(
            symbol_id=1, path="src/app.py", qualified_name="func_a",
            kind="function", start_line=1, end_line=10,
            classification="unclassified",
            evidence=InterviewEvidenceLocation(
                snapshot_id=1, path="src/app.py", qualified_name="func_a",
                start_line=1, end_line=10,
            ),
        )],
    )

    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _BadOpClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is not None
    assert "operation_kind" in result.error.lower() or "invalid" in result.error.lower()
    assert result.proposals == []


def test_invalid_state_effects_rejected():
    """Structured-output validation rejects an invalid state_effects value."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig
    from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation

    class _BadStateClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return json.dumps({
                "assistant_message": "Proposals.",
                "findings": [],
                "proposals": [{
                    "path": "src/app.py",
                    "qualified_name": "func_a",
                    "metadata": {
                        "element_type": "core",
                        "operation_kind": "analysis",
                        "state_effects": ["telepathy"],
                    },
                    "probe_plan": {
                        "objective": "Trace",
                        "reason": "Test",
                    },
                }],
                "next_questions": [],
            })

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=1, total_entrypoints=0,
        classified_count=0, unclassified_count=1,
        budget_max_chars=60000, budget_used_chars=100,
        symbols=[InterviewSymbolItem(
            symbol_id=1, path="src/app.py", qualified_name="func_a",
            kind="function", start_line=1, end_line=10,
            classification="unclassified",
            evidence=InterviewEvidenceLocation(
                snapshot_id=1, path="src/app.py", qualified_name="func_a",
                start_line=1, end_line=10,
            ),
        )],
    )

    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _BadStateClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is not None
    assert "state_effects" in result.error.lower() or "invalid" in result.error.lower()
    assert result.proposals == []


# --- LLM API error → fail closed --------------------------------------------


def test_llm_api_error_fails_closed():
    """An LLMError during generate_text fails closed with no proposals."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig, LLMError
    from app.models import InterviewContextPack

    class _ErrorClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            raise LLMError("Connection timeout after 120s")

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=0, total_entrypoints=0,
        classified_count=0, unclassified_count=0,
        budget_max_chars=60000, budget_used_chars=100,
    )
    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _ErrorClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is not None
    assert "timeout" in result.error.lower()
    assert result.proposals == []
    assert result.is_mock is False


# --- Malformed JSON → fail closed --------------------------------------------


def test_malformed_json_fails_closed():
    """Non-JSON model output fails closed."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig
    from app.models import InterviewContextPack

    class _GarbageClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return "This is not JSON at all."

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=0, total_entrypoints=0,
        classified_count=0, unclassified_count=0,
        budget_max_chars=60000, budget_used_chars=100,
    )
    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _GarbageClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is not None
    assert "parse" in result.error.lower()
    assert result.proposals == []


# --- Valid reasoning response is accepted ------------------------------------


def test_valid_reasoning_response_produces_proposals():
    """A well-formed reasoning response yields validated proposals."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig
    from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation

    class _GoodClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return json.dumps({
                "assistant_message": "I analyzed the codebase.",
                "findings": ["func_a processes data"],
                "proposals": [{
                    "path": "src/app.py",
                    "qualified_name": "func_a",
                    "symbol_id": 1,
                    "metadata": {
                        "role": "Processes incoming data",
                        "capability": "data-processing",
                        "element_type": "core",
                        "operation_kind": "analysis",
                        "state_effects": ["none"],
                        "consumers": ["api.handler"],
                    },
                    "probe_plan": {
                        "feature_id": "data-processing",
                        "objective": "Trace data transformations",
                        "reason": "Pure function, safe to trace",
                        "recommended_mode": "trace",
                        "side_effect_risk": "none",
                        "replayability": "safe",
                    },
                }],
                "next_questions": ["What is the expected throughput?"],
            })

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=1, total_entrypoints=0,
        classified_count=0, unclassified_count=1,
        budget_max_chars=60000, budget_used_chars=100,
        symbols=[InterviewSymbolItem(
            symbol_id=1, path="src/app.py", qualified_name="func_a",
            kind="function", start_line=1, end_line=10,
            classification="unclassified",
            evidence=InterviewEvidenceLocation(
                snapshot_id=1, path="src/app.py", qualified_name="func_a",
                start_line=1, end_line=10,
            ),
        )],
    )

    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _GoodClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is None
    assert result.is_mock is False
    assert len(result.proposals) == 1
    p = result.proposals[0]
    assert p.path == "src/app.py"
    assert p.qualified_name == "func_a"
    assert p.metadata.element_type == "core"
    assert p.metadata.operation_kind == "analysis"
    assert p.metadata.state_effects == ["none"]
    assert p.probe_plan.recommended_mode == "trace"
    assert p.probe_plan.replayability == "safe"
    assert result.assistant_message == "I analyzed the codebase."
    assert result.next_questions == ["What is the expected throughput?"]
    assert result.denied_symbols == []


# --- Denylist enforcement at the agent level ---------------------------------


def test_denylist_excludes_from_valid_reasoning_response():
    """The denylist removes proposals from a real reasoning response."""
    from app.interview_agent import generate_interview_turn
    from app.llm import LLMClient, LLMConfig
    from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation

    class _MixedClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return json.dumps({
                "assistant_message": "Found symbols.",
                "findings": [],
                "proposals": [
                    {
                        "path": "src/app.py",
                        "qualified_name": "safe_func",
                        "metadata": {"element_type": "element", "operation_kind": "read"},
                        "probe_plan": {"objective": "Trace", "reason": "Safe"},
                    },
                    {
                        "path": "src/app.py",
                        "qualified_name": "process_payment",
                        "metadata": {"element_type": "boundary", "operation_kind": "mutation"},
                        "probe_plan": {"objective": "Trace", "reason": "Watch it"},
                    },
                ],
                "next_questions": [],
            })

    pack = InterviewContextPack(
        system_id=1, snapshot_id=1,
        total_symbols=2, total_entrypoints=0,
        classified_count=0, unclassified_count=2,
        budget_max_chars=60000, budget_used_chars=100,
        symbols=[
            InterviewSymbolItem(
                symbol_id=1, path="src/app.py", qualified_name="safe_func",
                kind="function", start_line=1, end_line=10,
                classification="unclassified",
                evidence=InterviewEvidenceLocation(
                    snapshot_id=1, path="src/app.py", qualified_name="safe_func",
                    start_line=1, end_line=10,
                ),
            ),
            InterviewSymbolItem(
                symbol_id=2, path="src/app.py", qualified_name="process_payment",
                kind="function", start_line=11, end_line=20,
                classification="unclassified",
                evidence=InterviewEvidenceLocation(
                    snapshot_id=1, path="src/app.py", qualified_name="process_payment",
                    start_line=11, end_line=20,
                ),
            ),
        ],
    )

    config = LLMConfig(
        provider="openai", api_key="fake", model="o3",
        base_url=None, timeout=30,
    )
    result = generate_interview_turn(
        _MixedClient(), config,
        context_pack=pack, history=[], user_message="Analyze",
    )

    assert result.error is None
    assert len(result.proposals) == 1
    assert result.proposals[0].qualified_name == "safe_func"
    assert len(result.denied_symbols) == 1
    assert "payment" in result.denied_symbols[0].lower()
