"""Tests for Issue #130: two-pass evidence gathering before a dialogue turn.

Covers:
1. Deterministic target validation (interview_evidence.validate_evidence_targets):
   valid targets, unknown path, invalid line range, too many targets.
2. Deterministic snippet fetch (interview_evidence.read_evidence_snippets):
   reads only from the pinned commit, enforces per-file line cap and total
   character budget, and fails closed on a git read error.
3. Pass-1 reasoning call (interview_agent.select_evidence_targets): fail-closed
   for mock/non-reasoning clients, malformed output, and out-of-scope targets;
   "no evidence needed" and "targets selected" both succeed.
4. Route-level wiring (interview_dialogue_turn): pass 1 and pass 2 are each
   recorded as a separate intelligence_runs row; evidence read from the pinned
   commit is surfaced in the response and attached to the created interview_qa
   row's evidence_refs; a pass-1 failure fails the whole turn closed with no
   pass-2 call and no messages/proposals persisted.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.interview_agent import (
    EvidenceSelectionResult,
    select_evidence_targets,
)
from app.interview_evidence import (
    EvidenceReadError,
    EvidenceTarget,
    read_evidence_snippets,
    validate_evidence_targets,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.models import InterviewContextPack, InterviewEvidenceLocation, InterviewSymbolItem


# --- Shared helpers -----------------------------------------------------------


def _make_config(provider="openai", model="o3"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _evidence(path="src/summarize.py", qname="summarize.summarize_text"):
    return InterviewEvidenceLocation(
        snapshot_id=1, path=path, qualified_name=qname, start_line=1, end_line=40,
    )


def _context_pack():
    symbols = [
        InterviewSymbolItem(
            symbol_id=1, path="src/summarize.py", qualified_name="summarize.summarize_text",
            kind="function", start_line=1, end_line=40, classification="unclassified",
            has_metadata=False, evidence=_evidence(),
        ),
    ]
    return InterviewContextPack(
        system_id=1, snapshot_id=1, total_symbols=1, total_entrypoints=0,
        classified_count=0, unclassified_count=1, budget_max_chars=60000,
        budget_used_chars=1000, truncated=False, symbols=symbols,
    )


class FakeLLMClient(LLMClient):
    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error

    def generate_text(self, messages, *, temperature=None, max_tokens=None) -> str:
        if self._error:
            raise LLMError(self._error)
        import json
        return json.dumps(self._response)


def _init_repo(tmp_path, files: Dict[str, str]) -> tuple[str, str]:
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True, capture_output=True)
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


# --- validate_evidence_targets ------------------------------------------------


def test_validate_evidence_targets_accepts_known_path():
    allowed = {"src/summarize.py": [(1, 40)]}
    targets = [EvidenceTarget(path="src/summarize.py", start_line=1, end_line=10)]
    assert validate_evidence_targets(targets, allowed) is None


def test_validate_evidence_targets_rejects_unknown_path():
    allowed = {"src/summarize.py": [(1, 40)]}
    targets = [EvidenceTarget(path="src/other.py", start_line=1, end_line=10)]
    error = validate_evidence_targets(targets, allowed)
    assert error is not None
    assert "unknown path" in error


def test_validate_evidence_targets_rejects_invalid_range():
    allowed = {"src/summarize.py": [(1, 40)]}
    targets = [EvidenceTarget(path="src/summarize.py", start_line=10, end_line=5)]
    error = validate_evidence_targets(targets, allowed)
    assert error is not None
    assert "invalid line range" in error


def test_validate_evidence_targets_rejects_out_of_span_range():
    """A known path but a range outside every known span is fail-closed —
    the model must not read arbitrary rows of a context-pack file (Issue #130)."""
    allowed = {"src/summarize.py": [(10, 20)]}
    targets = [EvidenceTarget(path="src/summarize.py", start_line=1, end_line=5000)]
    error = validate_evidence_targets(targets, allowed)
    assert error is not None
    assert "not contained in any known span" in error


def test_validate_evidence_targets_accepts_range_within_span():
    allowed = {"src/summarize.py": [(10, 20)]}
    targets = [EvidenceTarget(path="src/summarize.py", start_line=12, end_line=18)]
    assert validate_evidence_targets(targets, allowed) is None


def test_validate_evidence_targets_enforces_file_limit():
    allowed = {"a.py": [(1, 10)], "b.py": [(1, 10)]}
    targets = [
        EvidenceTarget(path="a.py", start_line=1, end_line=5),
        EvidenceTarget(path="b.py", start_line=1, end_line=5),
    ]
    error = validate_evidence_targets(targets, allowed, limit=1)
    assert error is not None
    assert "INTERVIEW_EVIDENCE_MAX_FILES" in error


# --- read_evidence_snippets ---------------------------------------------------


def test_read_evidence_snippets_reads_pinned_commit(tmp_path):
    repo, sha = _init_repo(tmp_path, {
        "src/summarize.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n",
    })
    targets = [EvidenceTarget(path="src/summarize.py", start_line=1, end_line=5)]
    snippets = read_evidence_snippets(repo, sha, targets)
    assert len(snippets) == 1
    assert snippets[0].content == "line1\nline2\nline3\nline4\nline5"
    assert snippets[0].char_count == len(snippets[0].content)
    assert snippets[0].truncated is False


def test_read_evidence_snippets_enforces_per_file_line_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVIDENCE_MAX_LINES_PER_FILE", "3")
    repo, sha = _init_repo(tmp_path, {
        "src/summarize.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n",
    })
    targets = [EvidenceTarget(path="src/summarize.py", start_line=1, end_line=20)]
    snippets = read_evidence_snippets(repo, sha, targets)
    assert snippets[0].content == "line1\nline2\nline3"
    assert snippets[0].truncated is True


def test_read_evidence_snippets_enforces_total_char_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVIDENCE_MAX_CHARS", "8")
    repo, sha = _init_repo(tmp_path, {
        "a.py": "aaaaaaaaaaaaaaaaaaaa\n",
        "b.py": "bbbbbbbbbbbbbbbbbbbb\n",
    })
    targets = [
        EvidenceTarget(path="a.py", start_line=1, end_line=1),
        EvidenceTarget(path="b.py", start_line=1, end_line=1),
    ]
    snippets = read_evidence_snippets(repo, sha, targets)
    total_chars = sum(s.char_count for s in snippets)
    assert total_chars <= 8
    assert any(s.truncated for s in snippets)


def test_read_evidence_snippets_fails_closed_on_missing_path(tmp_path):
    repo, sha = _init_repo(tmp_path, {"a.py": "hello\n"})
    targets = [EvidenceTarget(path="does/not/exist.py", start_line=1, end_line=1)]
    with pytest.raises(EvidenceReadError):
        read_evidence_snippets(repo, sha, targets)


def test_read_evidence_snippets_never_reads_uncommitted_content(tmp_path):
    """Only the pinned commit is read — an uncommitted edit must not appear."""
    repo, sha = _init_repo(tmp_path, {"a.py": "committed content\n"})
    with open(os.path.join(repo, "a.py"), "w") as f:
        f.write("UNCOMMITTED CHANGE\n")
    targets = [EvidenceTarget(path="a.py", start_line=1, end_line=1)]
    snippets = read_evidence_snippets(repo, sha, targets)
    assert snippets[0].content == "committed content"


def test_read_evidence_snippets_exposes_partial_results_on_failure(tmp_path):
    """Issue #137: a target that fails partway through must not lose the
    snippets successfully read before it — they are still audit-worthy."""
    repo, sha = _init_repo(tmp_path, {"a.py": "hello\n"})
    targets = [
        EvidenceTarget(path="a.py", start_line=1, end_line=1),
        EvidenceTarget(path="does/not/exist.py", start_line=1, end_line=1),
    ]
    with pytest.raises(EvidenceReadError) as exc_info:
        read_evidence_snippets(repo, sha, targets)
    assert len(exc_info.value.partial_snippets) == 1
    assert exc_info.value.partial_snippets[0].path == "a.py"


# --- select_evidence_targets (pass 1 reasoning call) -------------------------


def test_select_evidence_targets_mock_client_fails_closed():
    result = select_evidence_targets(
        MockLLMClient(),
        LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30),
        context_pack=_context_pack(), history=[], user_message="hi",
    )
    assert result.error is not None
    assert "reasoning model" in result.error.lower()


def test_select_evidence_targets_no_evidence_needed():
    client = FakeLLMClient(response={"need_evidence": False, "targets": []})
    result = select_evidence_targets(
        client, _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask about the summarizer.",
    )
    assert result.error is None
    assert result.need_evidence is False
    assert result.targets == []


def test_select_evidence_targets_valid_targets():
    client = FakeLLMClient(response={
        "need_evidence": True,
        "targets": [{"path": "src/summarize.py", "start_line": 1, "end_line": 20, "reason": "check body"}],
    })
    result = select_evidence_targets(
        client, _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask about the summarizer.",
    )
    assert result.error is None
    assert result.need_evidence is True
    assert len(result.targets) == 1
    assert result.targets[0].path == "src/summarize.py"


def test_select_evidence_targets_rejects_unknown_path():
    client = FakeLLMClient(response={
        "need_evidence": True,
        "targets": [{"path": "src/invented.py", "start_line": 1, "end_line": 20, "reason": "?"}],
    })
    result = select_evidence_targets(
        client, _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask about the summarizer.",
    )
    assert result.error is not None
    assert "validation failed" in result.error.lower()


def test_select_evidence_targets_malformed_json_fails_closed():
    class BrokenClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return "not json {{"

    result = select_evidence_targets(
        BrokenClient(), _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask.",
    )
    assert result.error is not None
    assert "parse" in result.error.lower()


def test_select_evidence_targets_llm_error_fails_closed():
    client = FakeLLMClient(error="timeout")
    result = select_evidence_targets(
        client, _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask.",
    )
    assert result.error is not None
    assert "timeout" in result.error


def test_select_evidence_targets_invalid_config_fails_closed(monkeypatch):
    """An invalid INTERVIEW_EVIDENCE_* value must become a recorded turn
    failure (fail-closed), never an unhandled exception."""
    monkeypatch.setenv("INTERVIEW_EVIDENCE_MAX_FILES", "not-a-number")
    client = FakeLLMClient(response={
        "need_evidence": True,
        "targets": [{"path": "src/summarize.py", "start_line": 1, "end_line": 20, "reason": "check"}],
    })
    result = select_evidence_targets(
        client, _make_config(), context_pack=_context_pack(), history=[],
        user_message="Ask about the summarizer.",
    )
    assert result.error is not None
    assert "INTERVIEW_EVIDENCE_MAX_FILES" in result.error


# --- Route-level two-pass wiring ----------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-evidence-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup_session_with_repo(client, tmp_path):
    repo, sha = _init_repo(tmp_path, {
        "src/summarize.py": "\n".join(f"def summarize_text(): pass  # {i}" for i in range(1, 30)) + "\n",
    })
    token = _login(client)
    system = client.post(
        "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
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
    return headers, session["id"], snapshot_id


def _stub_two_pass(monkeypatch, *, evidence_result, turn_result):
    from app.routes import interview as interview_routes

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        # Record whether evidence_snippets were threaded through, for assertions.
        fake_generate_interview_turn.received_snippets = kwargs.get("evidence_snippets")
        return turn_result

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )
    return fake_generate_interview_turn


def test_two_pass_records_both_intelligence_runs(admin_client, monkeypatch, tmp_path):
    from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult
    from app.interview_evidence import EvidenceTarget

    headers, session_id, snapshot_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=True,
        targets=[EvidenceTarget(path="src/summarize.py", start_line=1, end_line=10)],
    )
    turn_result = InterviewTurnResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        assistant_message="読みました。",
    )
    fake_turn = _stub_two_pass(monkeypatch, evidence_result=evidence_result, turn_result=turn_result)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "詳細を教えてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert body["evidence_run"] is not None
    assert body["evidence_run"]["run_type"] == "interview_evidence_selection"
    assert body["intelligence_run"]["run_type"] == "interview_dialogue"
    assert len(body["evidence_used"]) == 1
    assert body["evidence_used"][0]["path"] == "src/summarize.py"

    # generate_interview_turn actually received the fetched snippet content.
    assert fake_turn.received_snippets is not None
    assert len(fake_turn.received_snippets) == 1
    assert "def summarize_text" in fake_turn.received_snippets[0].content

    from app.db import get_conn
    with get_conn() as conn:
        run_types = {
            row["run_type"] for row in conn.execute(
                "SELECT run_type FROM intelligence_runs WHERE snapshot_id = ?",
                (snapshot_id,),
            )
        }
    assert {"interview_evidence_selection", "interview_dialogue"} <= run_types


def test_two_pass_skips_fetch_when_no_evidence_needed(admin_client, monkeypatch, tmp_path):
    from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult

    headers, session_id, snapshot_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False, need_evidence=False,
    )
    turn_result = InterviewTurnResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        assistant_message="OK",
    )
    fake_turn = _stub_two_pass(monkeypatch, evidence_result=evidence_result, turn_result=turn_result)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "hello"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evidence_used"] == []
    assert fake_turn.received_snippets == []


def test_pass1_failure_fails_whole_turn_closed_without_calling_pass2(admin_client, monkeypatch, tmp_path):
    from app.interview_agent import EvidenceSelectionResult

    headers, session_id, snapshot_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        error="Evidence target validation failed: bad target",
    )
    pass2_calls = []

    from app.routes import interview as interview_routes

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        pass2_calls.append(1)
        raise AssertionError("pass 2 must not be called when pass 1 fails")

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "詳細を教えてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is not None
    assert pass2_calls == []
    assert body["evidence_run"]["status"] == "failed"

    # No assistant message or Q&A rows were created for a failed turn.
    detail = admin_client.get(f"/interview/sessions/{session_id}", headers=headers).json()
    assert all(m["role"] != "assistant" for m in detail["messages"])
    qa = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    assert qa["items"] == []


def test_evidence_read_failure_fails_turn_closed(admin_client, monkeypatch, tmp_path):
    """A validated target that the deterministic fetch step cannot actually
    read (e.g. it vanished from disk between validation and fetch) fails the
    whole turn; there is no continue-without-evidence fallback."""
    from app.interview_agent import EvidenceSelectionResult
    from app.interview_evidence import EvidenceTarget

    headers, session_id, snapshot_id = _setup_session_with_repo(admin_client, tmp_path)

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=True,
        targets=[EvidenceTarget(path="src/does_not_exist.py", start_line=1, end_line=5)],
    )

    from app.routes import interview as interview_routes

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        raise AssertionError("pass 2 must not be called when the evidence read fails")

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "詳細を教えてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is not None
