"""Tests for Issue #286: read-only Investigation Agent (app/investigation_agent.py).

Covers:
1. Pinned-snapshot-only reads: investigation never sees uncommitted/newer
   content, and evidence refs resolve only inside the excerpts actually read.
2. Budget enforcement: files_read <= max_files, exhaustion -> unresolved
   without spending an LLM call, timeout -> unresolved.
3. Evidence validation: unverifiable citations pruned (recorded) when at
   least one valid citation remains; all-invalid -> failed closed.
4. Fail-closed: mock/non-reasoning client, LLM error, malformed response,
   invalid status/category all -> status="failed".
5. Read-only boundary: the repo fixture is untouched (`git status
   --porcelain` empty) after an investigation run.
6. Review fix: `_keywords()` extracts CJK tokens from a Japanese-only
   question; a Japanese question plus a router `search_keywords` hint
   selects/reads a candidate file instead of returning unresolved-with-
   zero-files; a `status="completed"` response with no valid evidence at
   all (code or runtime) is demoted to `"unresolved"`; a `"completed"`
   backed only by valid `runtime_evidence` stays `"completed"`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pytest

from app.investigation_agent import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    InvestigationBudget,
    _keywords,
    investigate,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


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


class FakeLLMClient(LLMClient):
    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error
        self.calls = 0

    def generate_text(self, messages: List[Dict[str, str]], *, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        if self._error:
            raise LLMError(self._error)
        return json.dumps(self._response)


def _valid_investigation_response(**overrides):
    base = {
        "status": "completed",
        "conclusion": "この関数はテキストを要約します。",
        "key_points": ["入力は自由文"],
        "evidence": [{"path": "src/summarize.py", "start_line": 1, "end_line": 2, "summary": "定義"}],
        "uncertainty": "",
        "confidence": "confirmed",
        "decision_question": None,
    }
    base.update(overrides)
    return base


# --- Fail-closed ----------------------------------------------------------------


def test_investigate_fails_closed_on_mock_client(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    result = investigate(
        MockLLMClient(), _mock_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?",
    )
    assert result.status == "failed"
    assert result.error is not None


def test_investigate_fails_closed_on_non_reasoning_model(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(model="claude-3-5-haiku-latest"), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?",
    )
    assert result.status == "failed"


def test_investigate_fails_closed_on_llm_error(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(error="upstream timeout")
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha, question="summarize とは何ですか?",
    )
    assert result.status == "failed"
    assert result.error == "upstream timeout"
    assert result.files_read >= 1  # candidate content was gathered before the call failed


def test_investigate_fails_closed_on_malformed_json(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response={"status": "completed"})  # missing required fields
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha, question="summarize とは何ですか?",
    )
    assert result.status == "failed"
    assert result.error is not None


def test_investigate_fails_closed_on_invalid_status(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response(status="not_a_real_status"))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha, question="summarize とは何ですか?",
    )
    assert result.status == "failed"


# --- Pinned-snapshot-only reads --------------------------------------------------


def test_investigate_never_reads_uncommitted_content(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    return 'pinned content'\n"})
    # Add uncommitted/newer content after the pin.
    with open(os.path.join(repo, "src/summarize.py"), "a") as f:
        f.write("\n# UNCOMMITTED_MARKER_SHOULD_NEVER_APPEAR\n")
    with open(os.path.join(repo, "src/new_file.py"), "w") as f:
        f.write("UNCOMMITTED_NEW_FILE_SHOULD_NEVER_APPEAR\n")

    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "completed"
    for snippet in result.read_snippets:
        assert "UNCOMMITTED_MARKER_SHOULD_NEVER_APPEAR" not in snippet.content
        assert snippet.path != "src/new_file.py"


def test_investigate_evidence_resolves_against_pinned_sha(tmp_path):
    repo, sha = _init_repo(tmp_path, {
        "src/summarize.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n",
    })
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[
        {"path": "src/summarize.py", "start_line": 1, "end_line": 5, "summary": "top"},
    ]))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "completed"
    assert len(result.evidence) == 1
    assert result.evidence[0].path == "src/summarize.py"


# --- Budget enforcement -----------------------------------------------------------


def test_investigate_caps_files_read_at_max_files(tmp_path):
    files = {f"src/summarize_{i}.py": f"def summarize_{i}():\n    pass\n" for i in range(10)}
    repo, sha = _init_repo(tmp_path, files)
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[]))
    budget = InvestigationBudget(max_files=3)
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize functions", research_focus="summarize",
        budget=budget,
    )
    assert result.files_read <= 3


def test_investigate_no_matching_candidates_is_unresolved_without_llm_call(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/other_topic.py": "def unrelated():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="totally_unmatched_keyword_zzzzz", research_focus="totally_unmatched_keyword_zzzzz",
    )
    assert result.status == "unresolved"
    assert result.files_read == 0
    assert result.llm_calls == 0
    assert client.calls == 0
    assert result.uncertainty


def test_investigate_char_budget_exhaustion_limits_files_read(tmp_path):
    files = {f"src/summarize_{i}.py": ("def summarize_{}():\n    pass\n".format(i)) * 500 for i in range(5)}
    repo, sha = _init_repo(tmp_path, files)
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[]))
    budget = InvestigationBudget(max_snippet_chars=1_000, max_files=20)
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize functions", research_focus="summarize",
        budget=budget,
    )
    assert result.chars_read <= 1_000
    assert result.files_read < 5


def test_investigate_timeout_is_unresolved_without_llm_call(tmp_path, monkeypatch):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())

    # First call (start_time) returns a small value; every subsequent
    # monotonic() read (the per-candidate timeout check) returns a value far
    # past the 1-second budget, deterministically forcing a timeout before
    # any candidate is read.
    calls = {"n": 0}
    real_monotonic = time.monotonic

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 1000.0

    monkeypatch.setattr("app.investigation_agent.time.monotonic", fake_monotonic)
    budget = InvestigationBudget(timeout_seconds=1)
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize", budget=budget,
    )
    assert result.status == "unresolved"
    assert result.files_read == 0
    assert client.calls == 0


def test_investigation_budget_clamps_out_of_range_values():
    budget = InvestigationBudget(max_files=10_000, max_snippet_chars=1, max_llm_calls=0, timeout_seconds=-5)
    assert budget.max_files <= 50
    assert budget.max_snippet_chars >= 1_000
    assert budget.max_llm_calls >= 1
    assert budget.timeout_seconds >= 1


# --- Evidence pruning / fail-closed on all-invalid -------------------------------


def test_investigate_prunes_unverifiable_evidence_keeping_valid_ones(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "\n".join(f"l{i}" for i in range(1, 11)) + "\n"})
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[
        {"path": "src/summarize.py", "start_line": 1, "end_line": 3, "summary": "valid"},
        {"path": "src/does_not_exist.py", "start_line": 1, "end_line": 3, "summary": "invented"},
    ]))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "completed"
    assert len(result.evidence) == 1
    assert result.evidence[0].path == "src/summarize.py"
    assert len(result.pruned_evidence) == 1


def test_investigate_fails_closed_when_all_evidence_invalid(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "\n".join(f"l{i}" for i in range(1, 11)) + "\n"})
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[
        {"path": "src/does_not_exist.py", "start_line": 1, "end_line": 3, "summary": "invented"},
    ]))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "failed"
    assert result.error is not None


# --- hybrid decision_question ------------------------------------------------------


def test_investigate_decision_question_null_when_not_hybrid(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response(decision_question="should be discarded"))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        hybrid_decision_question=False,
    )
    assert result.decision_question is None


def test_investigate_decision_question_kept_when_hybrid(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response(decision_question="非同期化すべきですか?"))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        hybrid_decision_question=True,
    )
    assert result.decision_question == "非同期化すべきですか?"


# --- Read-only boundary -----------------------------------------------------------


def test_investigate_never_writes_to_the_repo(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    status = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"], check=True, capture_output=True, text=True,
    ).stdout
    assert status == ""


# --- Audit provenance --------------------------------------------------------------


def test_investigate_success_records_prompt_and_schema_version(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.prompt_version == PROMPT_VERSION
    assert result.schema_version == SCHEMA_VERSION
    assert result.llm_calls == 1
    assert result.elapsed_seconds >= 0


# --- Issue #290: runtime_fact evidence ------------------------------------------


@pytest.fixture
def system_and_snapshot(tmp_path, monkeypatch):
    """A real System + repository_snapshots row (FK-satisfying) for code_symbols."""
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-investigation-runtime.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
        assert r.status_code == 200, r.text
        token = r.cookies.get("probe_session")
        r = client.post(
            "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        system_id = r.json()["id"]

        from app.db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO repository_snapshots
                    (system_id, repo_path, commit_sha, status, created_at, completed_at)
                VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
                (system_id, time.time(), time.time()),
            )
            snapshot_id = cur.lastrowid
            yield conn, system_id, snapshot_id


def _insert_symbol(conn, *, snapshot_id, system_id, path, component_id, start_line=1, end_line=5):
    conn.execute(
        """INSERT INTO code_symbols
            (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, component_id)
        VALUES (?, ?, ?, 'fn', 'function', ?, ?, ?)""",
        (snapshot_id, system_id, path, start_line, end_line, component_id),
    )


def _insert_trace(conn, system_id, component_id, *, timestamp):
    import uuid

    conn.execute(
        """INSERT INTO traces
            (system_id, trace_id, component_id, mode, input_json, output_text, error,
             duration_ms, timestamp)
        VALUES (?, ?, ?, 'trace', '{}', 'ok', NULL, 5.0, ?)""",
        (system_id, str(uuid.uuid4()), component_id, timestamp),
    )


def test_investigate_without_conn_never_offers_runtime_evidence(tmp_path):
    """Omitting conn/system_id/snapshot_id keeps investigate() exactly as
    read-only/git-only as before Issue #290 -- no runtime facts gathered."""
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "completed"
    assert result.runtime_evidence == []
    assert result.runtime_candidates_offered == 0


def test_investigate_offers_runtime_facts_for_candidate_components(tmp_path, system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                    path="src/summarize.py", component_id="summarize_fn")
    _insert_trace(conn, system_id, "summarize_fn", timestamp=time.time())

    client = FakeLLMClient(response=_valid_investigation_response(
        runtime_evidence=[{"component_id": "summarize_fn", "runtime_check": "match", "summary": "観測あり"}],
    ))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id,
    )
    assert result.status == "completed"
    assert result.runtime_candidates_offered == 1
    assert len(result.runtime_evidence) == 1
    entry = result.runtime_evidence[0]
    assert entry.component_id == "summarize_fn"
    assert entry.runtime_check == "match"
    assert entry.provenance.freshness == "fresh"


def test_investigate_prunes_runtime_evidence_citing_unoffered_component(tmp_path, system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                    path="src/summarize.py", component_id="summarize_fn")
    _insert_trace(conn, system_id, "summarize_fn", timestamp=time.time())

    client = FakeLLMClient(response=_valid_investigation_response(
        runtime_evidence=[{"component_id": "invented_component", "runtime_check": "match", "summary": "x"}],
    ))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id,
    )
    # Unlike code evidence, an invalid runtime_evidence citation is dropped
    # but never fails the whole run closed (it is always supplementary).
    assert result.status == "completed"
    assert result.runtime_evidence == []
    assert len(result.pruned_runtime_evidence) == 1


def test_investigate_stale_guard_overrides_model_claimed_match(tmp_path, system_and_snapshot):
    """A stale/unobserved component_id must never be persisted as 'match'
    even when the model's own output claims it."""
    conn, system_id, snapshot_id = system_and_snapshot
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                    path="src/summarize.py", component_id="summarize_fn")
    # No trace inserted -> deterministic baseline is 'unobserved'.

    client = FakeLLMClient(response=_valid_investigation_response(
        runtime_evidence=[{"component_id": "summarize_fn", "runtime_check": "match", "summary": "x"}],
    ))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id,
    )
    assert result.status == "completed"
    assert len(result.runtime_evidence) == 1
    assert result.runtime_evidence[0].runtime_check == "unobserved"


def test_investigate_respects_max_runtime_facts_budget(tmp_path, system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    files = {f"src/mod_{i}.py": f"def fn_{i}():\n    pass\n" for i in range(5)}
    repo, sha = _init_repo(tmp_path, files)
    for i in range(5):
        _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                        path=f"src/mod_{i}.py", component_id=f"mod_{i}_fn")
        _insert_trace(conn, system_id, f"mod_{i}_fn", timestamp=time.time())

    client = FakeLLMClient(response=_valid_investigation_response(evidence=[], runtime_evidence=[]))
    budget = InvestigationBudget(max_runtime_facts=2, max_files=20)
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="mod functions", research_focus="mod",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id, budget=budget,
    )
    assert result.runtime_candidates_offered <= 2


def test_investigate_zero_runtime_budget_disables_runtime_facts(tmp_path, system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                    path="src/summarize.py", component_id="summarize_fn")
    _insert_trace(conn, system_id, "summarize_fn", timestamp=time.time())

    client = FakeLLMClient(response=_valid_investigation_response(evidence=[], runtime_evidence=[]))
    budget = InvestigationBudget(max_runtime_facts=0)
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id, budget=budget,
    )
    assert result.runtime_candidates_offered == 0


def test_investigation_budget_clamps_max_runtime_facts():
    budget = InvestigationBudget(max_runtime_facts=1_000)
    assert budget.max_runtime_facts <= 20
    budget = InvestigationBudget(max_runtime_facts=-5)
    assert budget.max_runtime_facts == 0


# --- Review fix: Unicode `_keywords`, `search_keywords` hint, evidence-gated
# --- "completed" ------------------------------------------------------------


def test_keywords_extracts_cjk_tokens_from_japanese_question():
    """Before this fix, a Japanese-only question tokenized to [] entirely
    (the old regex was `[A-Za-z0-9_]+` only). CJK sequences (min length 2)
    are now also extracted -- they can match Japanese-named paths/docs, and
    are harmless (just unused) against ASCII-only repo paths."""
    keywords = _keywords(None, "認証はどのように実装されていますか")
    assert keywords != []
    assert any("認証" in kw for kw in keywords)


def test_keywords_still_extracts_ascii_tokens_unchanged():
    keywords = _keywords("the summarize function", None)
    assert "summarize" in keywords
    assert "function" in keywords
    assert "the" not in keywords  # stopword, unchanged behavior


def test_investigate_japanese_question_without_search_keywords_is_unresolved(tmp_path):
    """Finding 2 regression guard: WITHOUT a search_keywords hint, a
    Japanese-only question still cannot select any candidate against
    ASCII-only file paths (CJK tokens don't substring-match ASCII paths) --
    proves the next test's success comes from the hint, not from CJK
    tokenization alone."""
    repo, sha = _init_repo(tmp_path, {"src/auth.py": "def authenticate(user, password):\n    return True\n"})
    client = FakeLLMClient(response=_valid_investigation_response())
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="認証はどのように実装されていますか",
    )
    assert result.status == "unresolved"
    assert result.files_read == 0
    assert client.calls == 0


def test_investigate_japanese_question_with_search_keywords_selects_candidate(tmp_path):
    """Finding 2 fix: the Question Router's ASCII search_keywords hint lets
    a Japanese-only question select and read a candidate file, instead of
    ending unresolved-with-zero-files before any LLM call."""
    repo, sha = _init_repo(tmp_path, {
        "src/auth.py": "def authenticate(user, password):\n    return True\n",
        "src/unrelated.py": "def unrelated():\n    pass\n",
    })
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[
        {"path": "src/auth.py", "start_line": 1, "end_line": 2, "summary": "認証の定義"},
    ]))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="認証はどのように実装されていますか",
        search_keywords=["auth", "login", "token", "session"],
    )
    assert result.status == "completed"
    assert result.files_read >= 1
    assert any(s.path == "src/auth.py" for s in result.read_snippets)
    assert client.calls == 1


def test_investigate_completed_with_empty_evidence_demotes_to_unresolved(tmp_path):
    """Finding 3 fix: status="completed" with zero valid evidence of either
    kind is an ungrounded conclusion -- deterministically demoted, never
    reported as completed."""
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    client = FakeLLMClient(response=_valid_investigation_response(evidence=[]))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
    )
    assert result.status == "unresolved"
    assert result.status != "completed"
    assert result.evidence == []
    assert "demoted to unresolved" in result.uncertainty


def test_investigate_completed_with_only_runtime_evidence_stays_completed(tmp_path, system_and_snapshot):
    """A "completed" conclusion backed only by valid runtime_evidence (no
    code evidence) is grounded and must NOT be demoted."""
    conn, system_id, snapshot_id = system_and_snapshot
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "def summarize():\n    pass\n"})
    _insert_symbol(conn, snapshot_id=snapshot_id, system_id=system_id,
                    path="src/summarize.py", component_id="summarize_fn")
    _insert_trace(conn, system_id, "summarize_fn", timestamp=time.time())

    client = FakeLLMClient(response=_valid_investigation_response(
        evidence=[],
        runtime_evidence=[{"component_id": "summarize_fn", "runtime_check": "match", "summary": "観測あり"}],
    ))
    result = investigate(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="summarize とは何ですか?", research_focus="summarize",
        conn=conn, system_id=system_id, snapshot_id=snapshot_id,
    )
    assert result.status == "completed"
    assert result.evidence == []
    assert len(result.runtime_evidence) == 1
    assert result.runtime_evidence[0].runtime_check == "match"
