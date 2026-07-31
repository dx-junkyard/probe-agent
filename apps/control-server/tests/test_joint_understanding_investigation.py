"""Tests for Epic #328 Phase B / Issue #330: the iterative investigation loop.

Covers:

1. Multiple rounds with carry-over: round 2 sees round 1's search leads,
   open hypotheses and missing evidence, and reads files round 1 did not.
2. The finite stop reasons -- ``answered`` / ``no_new_evidence`` /
   ``budget_exhausted`` / ``unresolved`` / ``failed``.
3. Fail-closed: mock / non-reasoning client, LLM error, malformed output.
   A round-1 failure yields no findings; a later-round failure keeps the
   findings the earlier rounds already validated.
4. The Phase A contract applied at the source: a hypothesis without
   competing explanations / refutation conditions is dropped, unverifiable
   citations are pruned, and an ungrounded non-``unknown`` statement is
   dropped.
5. Read-only boundary: only the pinned commit is read (a dirty working tree
   changes nothing) and the target repository is untouched afterwards.
6. The API: rounds persist their own audit rows, findings land as
   ``origin_role='investigation'``, a retry RESUMES from persisted leads,
   and the origin confirmation item is never written.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.investigation_loop import (
    STOP_REASONS,
    InvestigationLoopBudget,
    LoopCarryOver,
    run_investigation_loop,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


def _init_repo(tmp_path, files: Dict[str, str]) -> "tuple[str, str]":
    repo = str(tmp_path / "repo")
    os.makedirs(repo, exist_ok=True)
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
            f.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


class ScriptedLLMClient(LLMClient):
    """Returns one scripted response per call; records the prompts it saw."""

    def __init__(self, responses: List[Any], error_after: Optional[int] = None):
        self._responses = list(responses)
        self._error_after = error_after
        self.prompts: List[str] = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None) -> str:
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        self.prompts.append(user)
        index = len(self.prompts) - 1
        if self._error_after is not None and index >= self._error_after:
            raise LLMError("boom")
        if index >= len(self._responses):
            raise AssertionError("more LLM calls than scripted responses")
        response = self._responses[index]
        return response if isinstance(response, str) else json.dumps(response)


def _round_response(**overrides):
    base = {
        "status": "unresolved",
        "conclusion": "途中経過です。",
        "findings": [],
        "search_leads": [],
        "open_hypotheses": [],
        "missing_evidence": [],
    }
    base.update(overrides)
    return base


def _fact(path="src/alpha.py", statement="alpha はリトライを3回行う"):
    return {
        "claim_kind": "fact",
        "statement": statement,
        "evidence": [{"path": path, "start_line": 1, "end_line": 2, "summary": "定義"}],
    }


REPO_FILES = {
    "src/alpha.py": "def alpha():\n    return 'alpha'\n",
    "src/beta.py": "def beta():\n    # alpha を呼ぶ\n    return 'beta'\n",
    "src/gamma.py": "def gamma():\n    return 'gamma'\n",
}


# --- Unit: rounds and carry-over ------------------------------------------------


def test_loop_runs_multiple_rounds_and_carries_leads_forward(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([
        _round_response(
            findings=[_fact()],
            search_leads=["beta"],
            open_hypotheses=["beta が alpha の呼び出し元かもしれない"],
            missing_evidence=["beta の呼び出し経路"],
        ),
        _round_response(
            status="completed",
            conclusion="beta が alpha を呼びます。",
            findings=[_fact(path="src/beta.py", statement="beta が alpha を呼ぶ")],
        ),
    ])

    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha の呼び出し元は?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=3, max_files_per_round=1),
    )

    assert result.stop_reason == "answered"
    assert [r.round_index for r in result.rounds] == [1, 2]
    assert len(result.findings) == 2
    # Round 2's prompt carries round 1's leads/hypotheses/missing evidence and
    # the already-read path, so it neither restarts nor re-reads.
    second_prompt = client.prompts[1]
    assert "beta が alpha の呼び出し元かもしれない" in second_prompt
    assert "beta の呼び出し経路" in second_prompt
    assert "Already read in earlier rounds" in second_prompt
    assert result.rounds[1].read_snippets[0].path != result.rounds[0].read_snippets[0].path


def test_loop_resumes_from_supplied_carry_over(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([_round_response(status="completed", findings=[_fact("src/beta.py")])])

    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="この経路は?",
        # No usable keyword in the question: only the carried-over lead can
        # select a candidate. A retry that lost its leads would find nothing.
        carry_over=LoopCarryOver(
            search_leads=["beta"], open_hypotheses=["前回の仮説"],
            missing_evidence=["前回不足していた証拠"], read_paths=["src/alpha.py"],
        ),
        budget=InvestigationLoopBudget(max_rounds=1),
    )

    assert result.rounds, "the carried-over lead should have selected a candidate"
    assert "src/beta.py" in [s.path for s in result.rounds[0].read_snippets]
    assert "src/alpha.py" not in [s.path for s in result.rounds[0].read_snippets]
    assert "前回の仮説" in client.prompts[0]


def test_loop_stops_with_no_new_evidence_when_a_round_reads_nothing_new(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/alpha.py": "def alpha():\n    return 1\n"})
    client = ScriptedLLMClient([
        _round_response(findings=[_fact()], missing_evidence=["まだ足りない"]),
    ])

    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=3),
    )

    # Only one file exists; round 2 has nothing unread to offer.
    assert len(result.rounds) == 1
    assert result.stop_reason == "no_new_evidence"
    assert len(client.prompts) == 1


def test_loop_stops_on_budget_and_reports_unread_candidates(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([
        _round_response(findings=[_fact()], missing_evidence=["続きが必要"]),
    ])

    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha beta gamma について", search_keywords=["alpha", "beta", "gamma"],
        budget=InvestigationLoopBudget(max_rounds=1, max_files_per_round=2),
    )

    assert result.stop_reason == "budget_exhausted"
    assert result.rounds[0].files_read == 2
    # "not looked at" stays distinguishable from "not there".
    assert result.rounds[0].candidate_paths
    assert result.rounds[0].unread_candidates


def test_loop_stops_when_a_round_adds_no_validated_evidence(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([_round_response(
        findings=[{"claim_kind": "unknown", "statement": "まだ特定できない"}],
        search_leads=["beta"], missing_evidence=["呼び出し関係"],
    )])

    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha beta の関係は?", search_keywords=["alpha", "beta"],
        budget=InvestigationLoopBudget(max_rounds=3, max_files_per_round=1),
    )

    assert result.stop_reason == "no_new_evidence"
    assert len(result.rounds) == 1
    assert len(client.prompts) == 1


def test_loop_stop_reasons_are_from_the_finite_set(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([_round_response(status="completed", findings=[_fact()])])
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    assert result.stop_reason in STOP_REASONS


# --- Unit: fail-closed ----------------------------------------------------------


def test_loop_fails_closed_on_mock_client(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    result = run_investigation_loop(
        MockLLMClient(), _mock_config(), repo_path=repo, commit_sha=sha, question="alpha?",
    )
    assert result.stop_reason == "failed"
    assert result.rounds == []
    assert result.findings == []
    assert result.error is not None


def test_loop_fails_closed_on_non_reasoning_model(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    result = run_investigation_loop(
        ScriptedLLMClient([]), _make_config(model="gpt-4o-mini"),
        repo_path=repo, commit_sha=sha, question="alpha?",
    )
    assert result.stop_reason == "failed"
    assert result.findings == []


def test_first_round_failure_produces_no_findings(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([], error_after=0)
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
    )
    assert result.stop_reason == "failed"
    assert result.findings == []
    assert result.rounds[0].status == "failed"


def test_later_round_failure_keeps_earlier_findings(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient(
        [_round_response(findings=[_fact()], search_leads=["beta"], missing_evidence=["続き"])],
        error_after=1,
    )
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=3, max_files_per_round=1),
    )
    assert result.stop_reason == "failed"
    assert len(result.findings) == 1
    assert result.rounds[-1].status == "failed"


def test_malformed_response_fails_closed(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient(["not json at all"])
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
    )
    assert result.stop_reason == "failed"
    assert "parse" in (result.error or "")


# --- Unit: the Phase A contract enforced at the source --------------------------


def test_hypothesis_without_competing_explanations_is_dropped(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([
        _round_response(status="completed", findings=[
            {
                "claim_kind": "hypothesis",
                "statement": "たぶんキャッシュ由来",
                "evidence": [{"path": "src/alpha.py", "start_line": 1, "end_line": 2}],
            },
            {
                "claim_kind": "hypothesis",
                "statement": "設定由来かもしれない",
                "evidence": [{"path": "src/alpha.py", "start_line": 1, "end_line": 2}],
                "competing_explanations": ["キャッシュ由来"],
                "refutation_conditions": ["設定を変えても再現する"],
                "next_investigation": "設定を変えて再現を確認する",
            },
        ]),
    ])
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    assert [f.statement for f in result.findings] == ["設定由来かもしれない"]
    assert result.rounds[0].pruned_findings == 1


def test_unverifiable_citations_are_pruned_and_ungrounded_claims_dropped(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([
        _round_response(status="completed", findings=[
            # Citation outside the excerpts actually read.
            {
                "claim_kind": "fact", "statement": "でっち上げ",
                "evidence": [{"path": "src/nope.py", "start_line": 1, "end_line": 2}],
            },
            # No citation at all, but claims something about the code.
            {"claim_kind": "inference", "statement": "根拠のない推論"},
            # No citation, but explicitly records what could NOT be determined.
            {"claim_kind": "unknown", "statement": "呼び出し元は特定できなかった"},
        ]),
    ])
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    assert [f.claim_kind for f in result.findings] == ["unknown"]
    assert result.rounds[0].pruned_findings == 2
    assert result.rounds[0].pruned_evidence


def test_completed_round_without_findings_is_demoted(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    client = ScriptedLLMClient([_round_response(status="completed", findings=[])])
    result = run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    assert result.rounds[0].status == "unresolved"
    assert result.stop_reason != "answered"


# --- Unit: read-only boundary ---------------------------------------------------


def test_loop_reads_only_the_pinned_commit_and_leaves_the_repo_untouched(tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    # Dirty the working tree AFTER pinning: none of it may be read.
    with open(os.path.join(repo, "src/alpha.py"), "w") as f:
        f.write("SECRET = 'leaked'\n")
    with open(os.path.join(repo, "src/untracked.py"), "w") as f:
        f.write("SECRET2 = 'leaked'\n")

    client = ScriptedLLMClient([_round_response(status="completed", findings=[_fact()])])
    run_investigation_loop(
        client, _make_config(), repo_path=repo, commit_sha=sha,
        question="alpha とは?", search_keywords=["alpha"],
        budget=InvestigationLoopBudget(max_rounds=1),
    )
    prompt = client.prompts[0]
    assert "leaked" not in prompt
    assert "def alpha" in prompt

    status = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True, check=True,
    ).stdout
    # Only the edits the test itself made are present -- the loop added none.
    assert "src/alpha.py" in status
    assert subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip() == sha


# --- Route-level tests ----------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-investigation-test.db"))
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


def _setup(client, repo_path, commit_sha):
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
            (system["id"], repo_path, commit_sha, now, now),
        )
        snapshot_id = cur.lastrowid

    session_id = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "alpha の呼び出し元は?"}, headers=headers,
    ).json()
    ju = client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "unknown_answer",
            "question_text": "alpha の呼び出し元が分かりません",
        },
        headers=headers,
    ).json()
    return headers, system["id"], session_id, qa, ju["session"]["id"]


def _stub_client(monkeypatch, client_obj):
    from app.routes import joint_understanding as ju_routes

    monkeypatch.setattr(ju_routes, "create_llm_client", lambda config: client_obj)
    monkeypatch.setattr(
        ju_routes, "LLMConfig",
        type("_C", (), {"intelligence_from_env": staticmethod(lambda: _make_config())}),
    )


def _qa_row(qa_id):
    from app.db import get_conn

    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone())


def test_investigate_persists_rounds_findings_and_audit(admin_client, tmp_path, monkeypatch):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    headers, system_id, session_id, qa, ju_id = _setup(admin_client, repo, sha)
    before = _qa_row(qa["id"])

    _stub_client(monkeypatch, ScriptedLLMClient([
        _round_response(
            findings=[_fact()], search_leads=["beta"], missing_evidence=["呼び出し経路"],
        ),
        _round_response(
            status="completed", conclusion="beta が alpha を呼びます。",
            findings=[_fact(path="src/beta.py", statement="beta が alpha を呼ぶ")],
        ),
    ]))

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/investigate",
        json={"search_keywords": ["alpha"], "max_rounds": 3}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stop_reason"] == "answered"
    assert len(body["rounds"]) == 2
    assert body["rounds"][0]["stop_reason"] is None
    assert body["rounds"][1]["stop_reason"] == "answered"
    assert all(f["origin_role"] == "investigation" for f in body["findings"])
    assert all(f["decision_method"] == "reasoning_llm" for f in body["findings"])
    assert all(f["intelligence_run_id"] is not None for f in body["findings"])

    from app.db import get_conn

    with get_conn() as conn:
        runs = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'joint_investigation' "
            "AND system_id = ? ORDER BY id",
            (system_id,),
        ).fetchall()
        assert len(runs) == 2
        assert all(run["decision_method"] == "reasoning_llm" for run in runs)
        assert [run["budget_llm_calls"] for run in runs] == [1, 1]
        evidence_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM intelligence_run_evidence WHERE system_id = ?",
            (system_id,),
        ).fetchone()
        assert evidence_rows["n"] > 0

    # The origin Q&A row is untouched by investigation.
    assert _qa_row(qa["id"]) == before

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert len(detail["investigation_rounds"]) == 2
    assert [r["round_index"] for r in detail["investigation_rounds"]] == [1, 2]
    assert len(detail["findings"]) == 2


def test_investigate_again_resumes_from_persisted_leads(admin_client, tmp_path, monkeypatch):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    headers, system_id, session_id, qa, ju_id = _setup(admin_client, repo, sha)

    first = ScriptedLLMClient([
        _round_response(
            findings=[_fact()], search_leads=["beta"],
            open_hypotheses=["beta が呼び出し元かもしれない"],
            missing_evidence=["beta の経路"],
        ),
    ])
    _stub_client(monkeypatch, first)
    r1 = admin_client.post(
        f"/joint-understanding/{ju_id}/investigate",
        json={"search_keywords": ["alpha"], "max_rounds": 1}, headers=headers,
    )
    assert r1.status_code == 200, r1.text

    second = ScriptedLLMClient([_round_response(status="completed", findings=[_fact("src/beta.py")])])
    _stub_client(monkeypatch, second)
    r2 = admin_client.post(
        f"/joint-understanding/{ju_id}/investigate",
        # No keywords this time: only the persisted leads can drive the retry.
        json={"max_rounds": 1}, headers=headers,
    )
    assert r2.status_code == 200, r2.text
    prompt = second.prompts[0]
    assert "beta が呼び出し元かもしれない" in prompt
    assert "beta の経路" in prompt
    assert "src/alpha.py" in prompt  # listed as already read
    assert "src/beta.py" in [
        s for round_ in r2.json()["rounds"] for s in round_["read_paths"]
    ]

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert len(detail["investigation_rounds"]) == 2


def test_investigate_fails_closed_without_a_reasoning_model(admin_client, tmp_path, monkeypatch):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    headers, system_id, session_id, qa, ju_id = _setup(admin_client, repo, sha)

    from app.routes import joint_understanding as ju_routes

    monkeypatch.setattr(ju_routes, "create_llm_client", lambda config: MockLLMClient())
    monkeypatch.setattr(
        ju_routes, "LLMConfig",
        type("_C", (), {"intelligence_from_env": staticmethod(lambda: _mock_config())}),
    )

    r = admin_client.post(f"/joint-understanding/{ju_id}/investigate", json={}, headers=headers)
    assert r.status_code == 502, r.text

    from app.db import get_conn

    with get_conn() as conn:
        findings = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_finding "
            "WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()
        assert findings["n"] == 0
        failed = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'joint_investigation' "
            "AND system_id = ?",
            (system_id,),
        ).fetchall()
        assert len(failed) == 1
        assert failed[0]["status"] == "failed"


def test_investigate_requires_an_open_session(admin_client, tmp_path, monkeypatch):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    headers, system_id, session_id, qa, ju_id = _setup(admin_client, repo, sha)
    admin_client.post(f"/joint-understanding/{ju_id}/hold", json={}, headers=headers)
    r = admin_client.post(f"/joint-understanding/{ju_id}/investigate", json={}, headers=headers)
    assert r.status_code == 409


def test_investigate_is_system_isolated(admin_client, tmp_path):
    repo, sha = _init_repo(tmp_path, REPO_FILES)
    headers, system_id, session_id, qa, ju_id = _setup(admin_client, repo, sha)
    token = _login(admin_client)
    other = admin_client.post(
        "/systems", json={"name": "Other", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    r = admin_client.post(
        f"/joint-understanding/{ju_id}/investigate", json={},
        headers=_headers(token, other["id"]),
    )
    assert r.status_code == 404
