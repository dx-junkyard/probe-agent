"""Tests for Issue #287: Alignment Review / Review Queue.

Covers:
1. app/alignment.py's deterministic rule table (classify_alignment_item):
   exhaustive table-driven coverage of every (alignment_state, risk_flags,
   confidence, intent_field) branch, determinism, and the must_review
   regression set.
2. app/alignment.py's review_sort_key: a fixed-fixture ordering contract
   test.
3. app/alignment.py's generate_alignment_proposal: fail-closed on mock/
   non-reasoning clients, LLM errors, malformed JSON, and any field outside
   its finite set (alignment_state/confidence/risk_flags/intent_field).
4. app/alignment.py's validate_evidence_against_snapshot: a real git
   fixture, valid/invalid line ranges, missing paths.
5. Route-level (routes/interview_alignment.py): build (mocked
   generate_alignment_proposal, like Issue #284's _stub_propose pattern),
   fail-closed on no understanding_revision / LLM error / all-evidence-
   invalid, rebuild-merge (preserves user progress, refreshes untouched
   open rows), review-queue filtering + ordering, answer/correct/hold,
   user_decision never auto-set, System isolation, and the review_item
   Inquiry round trip end-to-end through the real build endpoint.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.alignment import (
    ALIGNMENT_STATES,
    CONFIDENCE_LEVELS,
    REASON_CODES,
    REVIEW_CATEGORIES,
    RISK_FLAGS,
    USER_REASON_TEMPLATES,
    AlignmentEvidenceItem,
    AlignmentProposalItem,
    AlignmentProposalResult,
    classify_alignment_item,
    compute_content_hash,
    generate_alignment_proposal,
    review_sort_key,
    user_reason_for,
    validate_evidence_against_snapshot,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient


# --- Unit tests: classify_alignment_item (deterministic rule table) ---------


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


# Table-driven: (alignment_state, risk_flags, confidence, intent_field) ->
# (expected_category, expected_reason_code). Enumerates every distinguishing
# branch of the brief's rule table, in priority order.
RULE_TABLE_CASES = [
    # Risk flags win regardless of state/confidence.
    (("aligned", ["security"], "confirmed", None), ("must_review", "security_related")),
    (("not_applicable", ["security"], "confirmed", None), ("must_review", "security_related")),
    (("gap", ["high_risk"], "likely", "pain"), ("must_review", "high_risk")),
    # security beats high_risk when both present (first rule wins).
    (("gap", ["high_risk", "security"], "likely", None), ("must_review", "security_related")),
    (("gap", ["core_intent"], "likely", "pain"), ("must_review", "core_intent")),
    # goal + gap/conflict is core_intent even without an explicit risk flag.
    (("gap", [], "likely", "goal"), ("must_review", "core_intent")),
    (("conflict", [], "likely", "goal"), ("must_review", "core_intent")),
    # goal + aligned/unknown/not_applicable does NOT trigger core_intent.
    (("aligned", [], "confirmed", "goal"), ("no_review_required", "no_change")),
    # conflict (no risk flags, no goal) -> conflict_detected.
    (("conflict", [], "likely", "pain"), ("must_review", "conflict_detected")),
    (("conflict", [], "likely", None), ("must_review", "conflict_detected")),
    # low confidence overrides gap/aligned but not conflict (conflict is
    # checked first).
    (("gap", [], "uncertain", "pain"), ("must_review", "low_confidence")),
    (("gap", [], "conflicting", "pain"), ("must_review", "low_confidence")),
    (("aligned", [], "uncertain", None), ("must_review", "low_confidence")),
    # unknown state -> low_confidence reason (even with confirmed confidence).
    (("unknown", [], "confirmed", None), ("must_review", "low_confidence")),
    (("unknown", [], "likely", "pain"), ("must_review", "low_confidence")),
    # gap, confirmed/likely confidence, no risk flags, non-goal field.
    (("gap", [], "likely", "pain"), ("batch_reviewable", "routine_update")),
    (("gap", [], "confirmed", None), ("batch_reviewable", "routine_update")),
    # aligned, confirmed/likely confidence -> no_review_required.
    (("aligned", [], "confirmed", "pain"), ("no_review_required", "no_change")),
    (("aligned", [], "likely", None), ("no_review_required", "no_change")),
    # not_applicable, confirmed/likely confidence -> informational.
    (("not_applicable", [], "confirmed", None), ("informational", "informational_only")),
    (("not_applicable", [], "likely", "constraints"), ("informational", "informational_only")),
]


@pytest.mark.parametrize("inputs,expected", RULE_TABLE_CASES)
def test_classify_alignment_item_rule_table(inputs, expected):
    state, risk, conf, ifield = inputs
    result = classify_alignment_item(
        alignment_state=state, risk_flags=risk, confidence=conf, intent_field=ifield,
    )
    assert result == expected


def test_classify_alignment_item_is_deterministic():
    for inputs, expected in RULE_TABLE_CASES:
        state, risk, conf, ifield = inputs
        first = classify_alignment_item(
            alignment_state=state, risk_flags=risk, confidence=conf, intent_field=ifield,
        )
        second = classify_alignment_item(
            alignment_state=state, risk_flags=risk, confidence=conf, intent_field=ifield,
        )
        assert first == second == expected


@pytest.mark.parametrize(
    "inputs",
    [
        ("aligned", ["security"], "confirmed", None),
        ("gap", ["high_risk"], "likely", None),
        ("gap", ["core_intent"], "likely", None),
        ("gap", [], "likely", "goal"),
        ("conflict", [], "confirmed", None),
        ("unknown", [], "confirmed", None),
        ("aligned", [], "uncertain", None),
        ("gap", [], "conflicting", None),
    ],
)
def test_must_review_regression_set(inputs):
    """Every one of these classes must land in must_review (Issue #287 brief's
    explicit regression list: security/high_risk/core_intent/conflict/
    unknown/uncertain)."""
    state, risk, conf, ifield = inputs
    category, _reason = classify_alignment_item(
        alignment_state=state, risk_flags=risk, confidence=conf, intent_field=ifield,
    )
    assert category == "must_review"


def test_classify_alignment_item_covers_every_alignment_state():
    """Every value in the finite ALIGNMENT_STATES set resolves to some rule
    with only default (no-risk, confirmed-confidence, no intent_field)
    inputs -- the rule table must never raise for a validated state."""
    for state in ALIGNMENT_STATES:
        category, reason = classify_alignment_item(
            alignment_state=state, risk_flags=[], confidence="confirmed", intent_field=None,
        )
        assert category in REVIEW_CATEGORIES
        assert reason in REASON_CODES


def test_user_reason_templates_cover_every_reason_code():
    for reason_code in REASON_CODES:
        assert isinstance(user_reason_for(reason_code), str)
        assert user_reason_for(reason_code)
    assert set(USER_REASON_TEMPLATES) == set(REASON_CODES)


# --- Unit tests: compute_content_hash (Issue #295 unchanged carry-over) -----
#
# Review fix (PR #296, Finding 1): compute_content_hash now requires
# repo_path/commit_sha (it reads each evidence citation's exact source text
# from the pinned commit to compute a per-citation source_digest) and also
# hashes intent_summary/gap_summary/proposed_interpretation. _hash_repo below
# gives every test below a real two-file git fixture to read from.


def _hash_repo(tmp_path):
    return _init_repo(tmp_path, {
        "src/a.py": "\n".join(f"line{i}" for i in range(1, 11)) + "\n",
        "src/b.py": "\n".join(f"line{i}" for i in range(1, 11)) + "\n",
    })


def _hash_kwargs(repo_path, commit_sha, **overrides):
    base = dict(
        current_claim="クレーム",
        current_evidence=[{"path": "src/a.py", "start_line": 1, "end_line": 3, "summary": "s"}],
        alignment_state="gap",
        risk_flags=["security"],
        confidence="likely",
        intent_field="pain",
        runtime_check=None,
        intent_summary="意図の要約",
        gap_summary="ギャップの要約",
        proposed_interpretation="提案された解釈",
        repo_path=repo_path,
        commit_sha=commit_sha,
    )
    base.update(overrides)
    return base


def test_compute_content_hash_is_deterministic(tmp_path):
    repo, sha = _hash_repo(tmp_path)
    kwargs = _hash_kwargs(repo, sha)
    assert compute_content_hash(**kwargs) == compute_content_hash(**kwargs)


def test_compute_content_hash_ignores_evidence_order(tmp_path):
    repo, sha = _hash_repo(tmp_path)
    e1 = {"path": "src/a.py", "start_line": 1, "end_line": 3, "summary": "s"}
    e2 = {"path": "src/b.py", "start_line": 4, "end_line": 5, "summary": "t"}
    h1 = compute_content_hash(**_hash_kwargs(repo, sha, current_evidence=[e1, e2]))
    h2 = compute_content_hash(**_hash_kwargs(repo, sha, current_evidence=[e2, e1]))
    assert h1 == h2


def test_compute_content_hash_ignores_risk_flag_order(tmp_path):
    repo, sha = _hash_repo(tmp_path)
    h1 = compute_content_hash(**_hash_kwargs(repo, sha, risk_flags=["security", "high_risk"]))
    h2 = compute_content_hash(**_hash_kwargs(repo, sha, risk_flags=["high_risk", "security"]))
    assert h1 == h2


@pytest.mark.parametrize("field,value", [
    ("current_claim", "別のクレーム"),
    ("alignment_state", "aligned"),
    ("confidence", "confirmed"),
    ("intent_field", "goal"),
    ("runtime_check", "mismatch"),
    ("risk_flags", ["high_risk"]),
    ("intent_summary", "別の要約"),
    ("gap_summary", "別のギャップ要約"),
    ("proposed_interpretation", "別の提案解釈"),
])
def test_compute_content_hash_changes_when_any_classification_input_changes(tmp_path, field, value):
    """Every field that feeds classify_alignment_item's rule table, the claim
    text itself, and (review fix, Finding 1) every meaning-bearing summary
    field must be part of the hash -- a change in any of them must never be
    silently masked as 'unchanged' (Issue #295 brief)."""
    repo, sha = _hash_repo(tmp_path)
    base = compute_content_hash(**_hash_kwargs(repo, sha))
    changed = compute_content_hash(**_hash_kwargs(repo, sha, **{field: value}))
    assert base != changed


def test_compute_content_hash_changes_on_evidence_diff(tmp_path):
    repo, sha = _hash_repo(tmp_path)
    base = compute_content_hash(**_hash_kwargs(repo, sha))
    changed_evidence = [{"path": "src/a.py", "start_line": 1, "end_line": 4, "summary": "s"}]
    changed = compute_content_hash(**_hash_kwargs(repo, sha, current_evidence=changed_evidence))
    assert base != changed


def test_compute_content_hash_changes_when_source_text_changes_at_same_citation(tmp_path):
    """Review fix (PR #296, Finding 1): the same claim, the same evidence
    citation (identical path/start_line/end_line/summary), but the actual
    source text at that pinned commit differs between two builds -- the
    source_digest must catch this even though every other hashed field is
    byte-identical, closing the pre-fix gap where a reference-only evidence
    hash could not detect an edited source line."""
    repo1, sha1 = _init_repo(
        tmp_path / "r1", {"src/a.py": "\n".join(f"line{i}" for i in range(1, 11)) + "\n"},
    )
    repo2, sha2 = _init_repo(
        tmp_path / "r2", {"src/a.py": "\n".join(f"CHANGED{i}" for i in range(1, 11)) + "\n"},
    )
    evidence = [{"path": "src/a.py", "start_line": 1, "end_line": 3, "summary": "s"}]
    h1 = compute_content_hash(**_hash_kwargs(repo1, sha1, current_evidence=evidence))
    h2 = compute_content_hash(**_hash_kwargs(repo2, sha2, current_evidence=evidence))
    assert h1 is not None
    assert h2 is not None
    assert h1 != h2


def test_compute_content_hash_returns_none_when_evidence_source_unreadable(tmp_path):
    """Fail-closed (PR #296 review fix, Finding 1): when an evidence
    citation's source text cannot be read/validated at the pinned commit,
    the WHOLE hash is None so the item can never be treated as an
    unchanged-carry-over candidate now, nor become a valid future carry
    candidate (callers filter on content_hash IS NOT NULL)."""
    repo, sha = _hash_repo(tmp_path)
    bad_evidence = [{"path": "src/does_not_exist.py", "start_line": 1, "end_line": 1, "summary": "s"}]
    result = compute_content_hash(**_hash_kwargs(repo, sha, current_evidence=bad_evidence))
    assert result is None


def test_compute_content_hash_source_digest_cache_is_reused(tmp_path):
    repo, sha = _hash_repo(tmp_path)
    cache: Dict[str, Optional[List[str]]] = {}
    first = compute_content_hash(**_hash_kwargs(repo, sha, source_digest_cache=cache))
    assert first is not None
    assert "src/a.py" in cache
    # Second call reuses the cache for the same path; still resolves fine.
    second = compute_content_hash(**_hash_kwargs(repo, sha, source_digest_cache=cache))
    assert second == first


# --- Unit tests: review_sort_key (fixed fixture ordering contract) ----------


def test_review_sort_key_orders_by_category_then_reason_then_id():
    items = [
        {"id": 5, "review_category": "batch_reviewable", "reason_code": "routine_update"},
        {"id": 1, "review_category": "must_review", "reason_code": "low_confidence"},
        {"id": 2, "review_category": "must_review", "reason_code": "security_related"},
        {"id": 3, "review_category": "no_review_required", "reason_code": "no_change"},
        {"id": 4, "review_category": "must_review", "reason_code": "core_intent"},
        {"id": 6, "review_category": "informational", "reason_code": "informational_only"},
        {"id": 7, "review_category": "must_review", "reason_code": "security_related"},
    ]
    ordered = sorted(items, key=lambda it: review_sort_key(
        review_category=it["review_category"], reason_code=it["reason_code"], item_id=it["id"],
    ))
    # must_review first (security < core_intent < low_confidence, tie->id
    # ascending), then batch_reviewable, then no_review_required, then
    # informational.
    assert [it["id"] for it in ordered] == [2, 7, 4, 1, 5, 3, 6]


# --- Unit tests: generate_alignment_proposal (fail-closed) -------------------


class FakeLLMClient(LLMClient):
    def __init__(self, response=None, raw: Optional[str] = None, error: Optional[str] = None):
        self._response = response
        self._raw = raw
        self._error = error

    def generate_text(self, messages, *, temperature=None, max_tokens=None) -> str:
        if self._error:
            raise LLMError(self._error)
        if self._raw is not None:
            return self._raw
        return json.dumps(self._response)


def _valid_item(**overrides):
    base = {
        "intent_field": "goal",
        "intent_ref_hint": "トレース収集を効率化したい",
        "current_claim": "現在は手動でトレースを確認している",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 3, "summary": "手動確認箇所"}],
        "alignment_state": "gap",
        "risk_flags": [],
        "confidence": "likely",
        "gap_summary": "自動化されていない",
        "proposed_interpretation": "自動収集の追加を検討",
    }
    base.update(overrides)
    return base


def test_generate_alignment_proposal_fails_closed_on_mock_client():
    result = generate_alignment_proposal(
        MockLLMClient(), _mock_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None
    assert result.items == []


def test_generate_alignment_proposal_fails_closed_on_non_reasoning_model():
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": []}), _make_config(model="claude-3-5-haiku-latest"),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None


def test_generate_alignment_proposal_fails_closed_on_llm_error():
    result = generate_alignment_proposal(
        FakeLLMClient(error="boom"), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error == "boom"


def test_generate_alignment_proposal_fails_closed_on_invalid_json():
    result = generate_alignment_proposal(
        FakeLLMClient(raw="not json"), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None


@pytest.mark.parametrize("field,value", [
    ("alignment_state", "definitely_aligned"),
    ("confidence", "very_sure"),
])
def test_generate_alignment_proposal_fails_closed_on_invalid_enum_field(field, value):
    item = _valid_item(**{field: value})
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": [item]}), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None
    assert result.items == []


def test_generate_alignment_proposal_fails_closed_on_invalid_risk_flag():
    item = _valid_item(risk_flags=["not_a_real_flag"])
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": [item]}), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None


def test_generate_alignment_proposal_fails_closed_on_invalid_intent_field():
    item = _valid_item(intent_field="not_a_real_field")
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": [item]}), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is not None


def test_generate_alignment_proposal_parses_valid_response():
    item = _valid_item()
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": [item]}), _make_config(),
        intent_items=[{"field": "goal", "value_text": "x", "status": "confirmed"}],
        current_understanding={"core_capabilities": []}, gap_analysis=[],
    )
    assert result.error is None
    assert len(result.items) == 1
    parsed = result.items[0]
    assert parsed.alignment_state == "gap"
    assert parsed.evidence[0].path == "src/a.py"
    assert parsed.intent_field == "goal"


def test_generate_alignment_proposal_accepts_null_intent_field():
    item = _valid_item(intent_field=None, alignment_state="not_applicable")
    result = generate_alignment_proposal(
        FakeLLMClient(response={"items": [item]}), _make_config(),
        intent_items=[], current_understanding=None, gap_analysis=None,
    )
    assert result.error is None
    assert result.items[0].intent_field is None


# --- Unit tests: validate_evidence_against_snapshot (real git fixture) ------


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


def test_validate_evidence_against_snapshot_valid_range(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/a.py": "\n".join(f"line{i}" for i in range(1, 11)) + "\n"})
    evidence = [AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=5, summary="s")]
    valid, pruned = validate_evidence_against_snapshot(repo, sha, evidence)
    assert len(valid) == 1
    assert pruned == []


def test_validate_evidence_against_snapshot_out_of_range_is_pruned(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/a.py": "line1\nline2\n"})
    evidence = [AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=100, summary="s")]
    valid, pruned = validate_evidence_against_snapshot(repo, sha, evidence)
    assert valid == []
    assert len(pruned) == 1


def test_validate_evidence_against_snapshot_missing_path_is_pruned(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/a.py": "line1\n"})
    evidence = [AlignmentEvidenceItem(path="src/does_not_exist.py", start_line=1, end_line=1, summary="s")]
    valid, pruned = validate_evidence_against_snapshot(repo, sha, evidence)
    assert valid == []
    assert len(pruned) == 1


def test_validate_evidence_against_snapshot_mixed(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/a.py": "line1\nline2\nline3\n"})
    evidence = [
        AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=2, summary="ok"),
        AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=99, summary="bad"),
    ]
    valid, pruned = validate_evidence_against_snapshot(repo, sha, evidence)
    assert len(valid) == 1
    assert valid[0].summary == "ok"
    assert len(pruned) == 1


def test_validate_evidence_against_snapshot_caches_line_counts(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/a.py": "line1\nline2\n"})
    cache: Dict[str, Optional[int]] = {}
    validate_evidence_against_snapshot(
        repo, sha, [AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=1, summary="")], cache,
    )
    assert cache["src/a.py"] == 2
    # Second call for the same path must reuse the cache, not re-read the file
    # (verified indirectly: still correct even if repo were now unreadable).
    valid, _ = validate_evidence_against_snapshot(
        repo, sha, [AlignmentEvidenceItem(path="src/a.py", start_line=2, end_line=2, summary="")], cache,
    )
    assert len(valid) == 1


# --- Route-level tests (TestClient) ------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-alignment-test.db"))
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


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, ?, ?, 'ready', ?, ?)
            """,
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_revision(session_id, system_id, snapshot_id, *, current_understanding=None, gap_analysis=None):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO understanding_revision
                (session_id, system_id, snapshot_id, intelligence_run_id,
                 current_understanding, gap_analysis, created_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?)""",
            (
                session_id, system_id, snapshot_id,
                json.dumps(current_understanding) if current_understanding is not None else None,
                json.dumps(gap_analysis) if gap_analysis is not None else None,
                now,
            ),
        )
        return cur.lastrowid


def _insert_intent_item(session_id, system_id, *, field, value_text="value", status="confirmed", created_at=None):
    """Insert an Intent Brief row directly with explicit timestamp control,
    so tests can place a 'goal' decision precisely before/after a given
    alignment build (Issue #295's goal-change-blocks-carryover rule)."""
    from app.db import get_conn

    now = created_at if created_at is not None else time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 decision_method, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'user', 'manual', ?, ?)""",
            (session_id, system_id, field, value_text, status, now, now),
        )
        return cur.lastrowid


def _setup(client, tmp_path, name="System A", files=None):
    token = _login(client)
    system = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, files or {"src/a.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"})
    snapshot_id = _insert_snapshot(system["id"], repo, sha)
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _stub_build(monkeypatch, *, items=None, error=None, is_mock=False):
    from app.routes import interview_alignment as alignment_routes

    def fake_create_llm_client(config):
        return object()

    def fake_generate_alignment_proposal(client, config, **kwargs):
        return AlignmentProposalResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=is_mock,
            items=[AlignmentProposalItem(**i) for i in (items or [])],
            error=error,
        )

    monkeypatch.setattr(alignment_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(alignment_routes, "generate_alignment_proposal", fake_generate_alignment_proposal)


def _proposal_item(**overrides):
    # NOTE: intent_field defaults to "pain" (neutral), NOT "goal" -- the
    # rule table's core_intent branch also fires for
    # (intent_field=="goal" AND alignment_state in (gap, conflict)), so a
    # "goal" default would silently upgrade every default gap item to
    # must_review/core_intent and break tests that expect the plain
    # gap -> batch_reviewable/routine_update classification. Tests that
    # specifically want the goal+gap/conflict branch pass
    # intent_field="goal" explicitly.
    base = dict(
        intent_field="pain",
        intent_ref_hint="トレース収集を効率化したい",
        current_claim="現在は手動でトレースを確認している",
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=3, summary="手動確認箇所")],
        alignment_state="gap",
        risk_flags=[],
        confidence="likely",
        gap_summary="自動化されていない",
        proposed_interpretation="自動収集の追加を検討",
    )
    base.update(overrides)
    return base


def test_build_requires_understanding_revision(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 409, r.text


def test_build_creates_items_with_deterministic_classification(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={"core_capabilities": []})

    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["alignment_state"] == "gap"
    assert item["review_category"] == "batch_reviewable"
    assert item["reason_code"] == "routine_update"
    assert item["user_reason"] == USER_REASON_TEMPLATES["routine_update"]
    assert item["status"] == "open"
    assert item["user_decision"] is None
    assert item["intelligence_run_id"] is not None
    assert item["is_mock"] is False
    assert item["current_evidence"][0]["path"] == "src/a.py"


def test_build_resolves_intent_item_id_deterministically_by_field(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})
    intent = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "自動化したい"},
        headers=headers,
    ).json()

    _stub_build(monkeypatch, items=[_proposal_item(intent_field="goal")])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["intent_item_id"] == intent["id"]


def test_build_fails_closed_on_llm_error_and_creates_no_rows(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, error="reasoning model call failed")
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 502, r.text

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    assert all(v == [] for v in listing["items_by_category"].values())

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'alignment_build'"
        ).fetchone()
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_details"] == "reasoning model call failed"
    assert run["decision_method"] == "reasoning_llm"
    assert run["prompt_version"] == "alignment-v1"


def test_build_fails_closed_when_every_items_evidence_is_invalid(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, files={"src/a.py": "line1\n"})
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    bad_item = _proposal_item(
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=999, summary="bad")],
    )
    _stub_build(monkeypatch, items=[bad_item])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 502, r.text

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    assert all(v == [] for v in listing["items_by_category"].values())


def test_build_drops_item_with_partially_invalid_evidence_but_keeps_others(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, files={"src/a.py": "line1\nline2\n"})
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    good_item = _proposal_item(
        current_claim="有効な項目",
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=2, summary="ok")],
    )
    all_bad_item = _proposal_item(
        current_claim="無効な項目",
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=999, summary="bad")],
    )
    _stub_build(monkeypatch, items=[good_item, all_bad_item])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    claims = [it["current_claim"] for it in r.json()["items"]]
    assert claims == ["有効な項目"]


def test_build_never_auto_sets_user_decision(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(), _proposal_item(alignment_state="aligned", confidence="confirmed")])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    for item in r.json()["items"]:
        assert item["user_decision"] is None
        assert item["status"] == "open"


def test_rebuild_preserves_items_with_user_progress_and_refreshes_untouched_open(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="未対応のまま残る項目"),
        _proposal_item(current_claim="回答されて保護される項目"),
    ])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    untouched_id = next(it["id"] for it in first["items"] if it["current_claim"] == "未対応のまま残る項目")
    answered_id = next(it["id"] for it in first["items"] if it["current_claim"] == "回答されて保護される項目")

    answer = admin_client.post(
        f"/interview/alignment/{answered_id}/answer",
        json={"decision": "accept_current", "note": "確認済み"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text

    # Second build: only proposes a brand-new claim.
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="新しい項目")])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    claims_and_ids = {(it["current_claim"], it["id"]) for it in second["items"]}

    # The untouched open item is gone (replaced); a fresh row exists for the
    # new claim; the answered item survives unchanged (same id, same
    # decision) even though this build did not repropose it.
    assert ("未対応のまま残る項目", untouched_id) not in claims_and_ids
    assert any(claim == "新しい項目" for claim, _id in claims_and_ids)
    kept = next(it for it in second["items"] if it["id"] == answered_id)
    assert kept["current_claim"] == "回答されて保護される項目"
    assert kept["status"] == "answered"
    assert kept["user_decision"]["action"] == "accept_current"
    assert kept["user_decision"]["note"] == "確認済み"


def test_held_item_is_also_preserved_across_rebuild(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="保留される項目")])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    held_id = first["items"][0]["id"]
    admin_client.post(f"/interview/alignment/{held_id}/hold", headers=headers)

    _stub_build(monkeypatch, items=[])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    ids = [it["id"] for it in second["items"]]
    assert held_id in ids


def test_review_queue_only_returns_actionable_categories_in_order(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A: aligned", alignment_state="aligned", confidence="confirmed"),
        _proposal_item(current_claim="B: security", risk_flags=["security"]),
        _proposal_item(current_claim="C: gap", alignment_state="gap", confidence="likely"),
        _proposal_item(current_claim="D: not_applicable", alignment_state="not_applicable", confidence="confirmed", intent_field=None),
        _proposal_item(current_claim="E: unknown", alignment_state="unknown", confidence="uncertain"),
    ])
    admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)

    queue = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    claims = [it["current_claim"] for it in queue["items"]]
    categories = {it["review_category"] for it in queue["items"]}
    assert categories <= {"must_review", "batch_reviewable"}
    # must_review items (security, unknown) sort before batch_reviewable (gap);
    # within must_review, security_related (rank 0) before low_confidence (rank 4).
    assert claims == ["B: security", "E: unknown", "C: gap"]
    assert "A: aligned" not in claims
    assert "D: not_applicable" not in claims

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    assert listing["counts"]["no_review_required"] == 1
    assert listing["counts"]["informational"] == 1
    assert listing["counts"]["must_review"] == 2
    assert listing["counts"]["batch_reviewable"] == 1


def test_answer_correct_hold_endpoints(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A"), _proposal_item(current_claim="B"), _proposal_item(current_claim="C"),
    ])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    a_id, b_id, c_id = (it["id"] for it in built["items"])

    r = admin_client.post(
        f"/interview/alignment/{a_id}/answer", json={"decision": "needs_change", "note": "要修正"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "answered"
    assert r.json()["user_decision"]["action"] == "needs_change"

    r = admin_client.post(
        f"/interview/alignment/{b_id}/correct", json={"corrected_interpretation": "こちらが正しい解釈"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "corrected"
    assert r.json()["user_decision"]["note"] == "こちらが正しい解釈"

    r = admin_client.post(f"/interview/alignment/{c_id}/hold", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "held"


def test_answer_rejects_invalid_decision_value(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})
    _stub_build(monkeypatch, items=[_proposal_item()])
    item_id = admin_client.post(
        f"/interview/sessions/{session_id}/alignment/build", headers=headers,
    ).json()["items"][0]["id"]

    r = admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "not_a_real_decision"}, headers=headers,
    )
    assert r.status_code == 422, r.text


def test_review_item_inquiry_round_trip_through_real_build(admin_client, tmp_path, monkeypatch):
    """End-to-end: build a real alignment_item, open a review_item Inquiry
    on it, resolve, and confirm the item is back to 'open' (never
    'answered') until the developer explicitly answers it."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})
    _stub_build(monkeypatch, items=[_proposal_item()])
    item_id = admin_client.post(
        f"/interview/sessions/{session_id}/alignment/build", headers=headers,
    ).json()["items"][0]["id"]

    from app.routes import interview_inquiry as inquiry_routes
    from app.inquiry_answering import InquiryAnswerResult

    def fake_create_llm_client(config):
        return object()

    def fake_generate_inquiry_answer(client, config, **kwargs):
        return InquiryAnswerResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            conclusion="この点は根拠を再確認する必要があります。", answerable=True,
        )

    monkeypatch.setattr(inquiry_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(inquiry_routes, "generate_inquiry_answer", fake_generate_inquiry_answer)

    created = admin_client.post(
        f"/interview/sessions/{session_id}/inquiries",
        json={"origin_kind": "review_item", "origin_id": item_id, "question_text": "根拠が薄いのでは?"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    inquiry_id = created.json()["inquiry"]["id"]

    blocked = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    blocked_item = next(it for cat in blocked["items_by_category"].values() for it in cat if it["id"] == item_id)
    assert blocked_item["status"] == "inquiry"

    resolve = admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)
    assert resolve.status_code == 200, resolve.text

    after = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    after_item = next(it for cat in after["items_by_category"].values() for it in cat if it["id"] == item_id)
    assert after_item["status"] == "open"
    assert after_item["user_decision"] is None

    answer = admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "answered"


# --- Issue #290: runtime_check rule table + build integration ---------------


@pytest.mark.parametrize("runtime_check", ["mismatch"])
def test_classify_alignment_item_runtime_mismatch_is_must_review(runtime_check):
    category, reason = classify_alignment_item(
        alignment_state="aligned", risk_flags=[], confidence="confirmed",
        intent_field=None, runtime_check=runtime_check,
    )
    assert (category, reason) == ("must_review", "runtime_mismatch")


@pytest.mark.parametrize("runtime_check", ["stale", "unobserved", None])
def test_classify_alignment_item_stale_unobserved_do_not_force_must_review(runtime_check):
    """stale/unobserved alone must not trigger must_review -- an aligned,
    confirmed item with no risk flags stays no_review_required regardless."""
    category, reason = classify_alignment_item(
        alignment_state="aligned", risk_flags=[], confidence="confirmed",
        intent_field=None, runtime_check=runtime_check,
    )
    assert (category, reason) == ("no_review_required", "no_change")


def test_classify_alignment_item_runtime_mismatch_priority_after_conflict_before_low_confidence():
    """runtime_check='mismatch' must not override conflict_detected (checked
    first) but must win over low_confidence (checked after)."""
    # conflict state wins regardless of runtime_check.
    category, reason = classify_alignment_item(
        alignment_state="conflict", risk_flags=[], confidence="confirmed",
        intent_field=None, runtime_check="mismatch",
    )
    assert (category, reason) == ("must_review", "conflict_detected")

    # runtime_check='mismatch' wins over a merely-uncertain confidence.
    category, reason = classify_alignment_item(
        alignment_state="gap", risk_flags=[], confidence="uncertain",
        intent_field=None, runtime_check="mismatch",
    )
    assert (category, reason) == ("must_review", "runtime_mismatch")


def test_classify_alignment_item_runtime_check_default_is_none_backward_compatible():
    """Every pre-#290 call site (no runtime_check kwarg) keeps working."""
    category, reason = classify_alignment_item(
        alignment_state="gap", risk_flags=[], confidence="likely", intent_field="pain",
    )
    assert (category, reason) == ("batch_reviewable", "routine_update")


def _insert_code_symbol(system_id, snapshot_id, *, path, start_line, end_line, component_id, name="fn"):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO code_symbols
                (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, component_id)
            VALUES (?, ?, ?, ?, 'function', ?, ?, ?)""",
            (snapshot_id, system_id, path, name, start_line, end_line, component_id),
        )


def _insert_trace(system_id, component_id, *, timestamp, error=None, environment=None, git_sha=None):
    from app.db import get_conn
    import uuid

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO traces
                (system_id, trace_id, component_id, mode, input_json, output_text, error,
                 duration_ms, timestamp, environment, git_sha)
            VALUES (?, ?, ?, 'trace', '{}', 'ok', ?, 5.0, ?, ?, ?)""",
            (system_id, str(uuid.uuid4()), component_id, error, timestamp, environment, git_sha),
        )


def _stub_runtime_match_judge(monkeypatch, *, verdicts=None, error=None, is_mock=False):
    """Stub app/runtime_match_judge.judge_runtime_match as imported into
    routes/interview_alignment.py, mirroring _stub_build's pattern.

    ``verdicts`` maps the caller-assigned item ``index`` (position among the
    items actually offered to the judge, i.e. only baseline-'match' items)
    to a runtime_check ("match"/"mismatch"); items not present default to
    "match". Pass ``error=`` to simulate a judge failure instead.
    """
    from app.routes import interview_alignment as alignment_routes
    from app.runtime_match_judge import RuntimeMatchJudgeItemResult, RuntimeMatchJudgeResult

    def fake_judge_runtime_match(client, config, items, *, language):
        if error is not None:
            return RuntimeMatchJudgeResult(
                provider="anthropic", model="claude-sonnet-4-5", is_mock=False, error=error,
            )
        results = [
            RuntimeMatchJudgeItemResult(
                index=it.index, runtime_check=(verdicts or {}).get(it.index, "match"),
            )
            for it in items
        ]
        return RuntimeMatchJudgeResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=is_mock, items=results,
        )

    monkeypatch.setattr(alignment_routes, "judge_runtime_match", fake_judge_runtime_match)


def test_build_sets_runtime_check_match_when_fresh_traces_exist(admin_client, tmp_path, monkeypatch):
    """Deterministic baseline is 'match' (fresh, no environment conflict);
    the Runtime Match Judge (Issue #290 Finding 5 Part 2) agrees, so the
    persisted runtime_check stays 'match'."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    _insert_trace(system_id, "src_a_fn", timestamp=time.time())

    _stub_runtime_match_judge(monkeypatch)
    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["runtime_check"] == "match"


def test_build_sets_runtime_check_unobserved_when_no_traces(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    # No traces inserted for src_a_fn.

    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["runtime_check"] == "unobserved"


def test_build_sets_runtime_check_stale_when_traces_are_old(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    # Old timestamp, but still inside the default RUNTIME_REALITY_CHECK_WINDOW_DAYS
    # aggregation window so aggregate_component_facts still finds it.
    monkeypatch.setenv("RUNTIME_REALITY_CHECK_WINDOW_DAYS", "365")
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "3600")
    _insert_trace(system_id, "src_a_fn", timestamp=time.time() - 10 * 86_400)

    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["runtime_check"] == "stale"


def test_build_leaves_runtime_check_none_when_no_deterministic_component_mapping(admin_client, tmp_path, monkeypatch):
    """No code_symbols component_id maps to the evidence path -- never guessed."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["runtime_check"] is None


def test_build_static_vs_runtime_mismatch_forces_must_review(admin_client, tmp_path, monkeypatch):
    """End-to-end through the real build pipeline with REAL trace data
    (Issue #290 Finding 5(a) regression -- previously only reachable by
    monkeypatching build_provenance's output, since traces carried no
    environment column): evidence maps to a component deterministically, a
    real trace reports environment='staging' while the System declares
    environment='production' (the one deterministic mismatch signal, see
    app/runtime_alignment.py), and the resulting item lands in must_review
    with reason_code=runtime_mismatch -- an aligned/confirmed state that
    would otherwise be no_review_required. A deterministic env-mismatch
    baseline is never sent to the Runtime Match Judge (Part 2), so no judge
    stub is needed here."""
    token = _login(admin_client)
    r = admin_client.post(
        "/systems",
        json={"name": "System Prod", "environment": "production", "description": "d"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    system_id = r.json()["id"]
    repo, sha = _init_repo(tmp_path, {"src/a.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"})
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    _insert_trace(system_id, "src_a_fn", timestamp=time.time(), environment="staging")

    _stub_build(monkeypatch, items=[
        _proposal_item(alignment_state="aligned", confidence="confirmed", risk_flags=[]),
    ])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["runtime_check"] == "mismatch"
    assert item["review_category"] == "must_review"
    assert item["reason_code"] == "runtime_mismatch"
    assert item["user_reason"] == USER_REASON_TEMPLATES["runtime_mismatch"]
    assert item["user_reason"] == "コード上の理解と実行時の観測が一致していません"


def test_new_traces_update_runtime_check_on_rebuild(admin_client, tmp_path, monkeypatch):
    """Simulates 'observation import': runtime_check is unobserved on the
    first build (no traces yet), then updates to match once traces arrive
    and the item is rebuilt -- lineage (intelligence_run_id) advances to a
    new audit row for the new build, per Issue #288's refresh reusing this
    exact build path."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})
    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="対象クレーム")])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    first_item = next(it for it in first["items"] if it["current_claim"] == "対象クレーム")
    assert first_item["runtime_check"] == "unobserved"
    first_run_id = first_item["intelligence_run_id"]

    # "Observation import": a new trace arrives for the mapped component.
    _insert_trace(system_id, "src_a_fn", timestamp=time.time())

    _stub_runtime_match_judge(monkeypatch)
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="対象クレーム")])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    second_item = next(it for it in second["items"] if it["current_claim"] == "対象クレーム")
    assert second_item["runtime_check"] == "match"
    # Revision lineage: the rebuild is a new, independently-audited
    # intelligence_runs row -- never overwriting the first build's record.
    assert second_item["intelligence_run_id"] != first_run_id


# --- Runtime Match Judge integration (Issue #290 Finding 5, Part 2) ------------


def _latest_run(system_id, run_type):
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM intelligence_runs WHERE system_id = ? AND run_type = ? "
            "ORDER BY id DESC LIMIT 1",
            (system_id, run_type),
        ).fetchone()


def test_judge_mismatch_verdict_forces_must_review(admin_client, tmp_path, monkeypatch):
    """A deterministic 'match' baseline (fresh trace, no environment
    conflict) is overridden by the judge's semantic 'mismatch' verdict --
    the item lands in must_review/runtime_mismatch even though nothing
    structural conflicts."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    _insert_trace(system_id, "src_a_fn", timestamp=time.time())

    _stub_runtime_match_judge(monkeypatch, verdicts={0: "mismatch"})
    _stub_build(monkeypatch, items=[
        _proposal_item(alignment_state="aligned", confidence="confirmed", risk_flags=[]),
    ])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["runtime_check"] == "mismatch"
    assert item["review_category"] == "must_review"
    assert item["reason_code"] == "runtime_mismatch"

    run = _latest_run(system_id, "runtime_match")
    assert run is not None
    assert run["status"] == "completed"
    assert run["decision_method"] == "reasoning_llm"
    assert run["prompt_version"] == "runtime-match-v1"
    assert run["schema_version"] == "runtime-match-v1"


def test_judge_failure_persists_null_runtime_check_but_build_still_succeeds(
    admin_client, tmp_path, monkeypatch,
):
    """A judge failure (LLM error / invalid structured output) must never
    fall back to the deterministic 'match' baseline -- the item's
    runtime_check is persisted NULL, a failed 'runtime_match' run is
    recorded, and the overall build still succeeds (200, not 502) since the
    Alignment proposal itself succeeded."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    _insert_trace(system_id, "src_a_fn", timestamp=time.time())

    _stub_runtime_match_judge(monkeypatch, error="Failed to parse structured response")
    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["runtime_check"] is None
    # A None runtime_check does not itself force must_review -- the
    # deterministic rule table only reacts to 'mismatch'.
    assert item["reason_code"] != "runtime_mismatch"

    run = _latest_run(system_id, "runtime_match")
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_details"] == "Failed to parse structured response"
    assert run["decision_method"] == "reasoning_llm"


def test_judge_never_called_for_stale_or_unobserved_items(admin_client, tmp_path, monkeypatch):
    """Deterministic stale/unobserved baselines are never sent to the judge
    (no eligible items -> no judge run row at all) and keep their
    deterministic value verbatim."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _insert_code_symbol(
        system_id, snapshot_id, path="src/a.py", start_line=1, end_line=20, component_id="src_a_fn",
    )
    # No traces inserted at all -- baseline is 'unobserved'.

    def _fail_if_called(client, config, items, *, language):
        raise AssertionError("judge_runtime_match must not be called for an unobserved baseline")

    from app.routes import interview_alignment as alignment_routes

    monkeypatch.setattr(alignment_routes, "judge_runtime_match", _fail_if_called)
    _stub_build(monkeypatch, items=[_proposal_item()])
    r = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["runtime_check"] == "unobserved"
    assert _latest_run(system_id, "runtime_match") is None


def test_system_isolation(admin_client, tmp_path, monkeypatch):
    token_a, system_a, snapshot_a = _setup(admin_client, tmp_path, name="System A")
    headers_a = _headers(token_a, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _insert_revision(session_a, system_a, snapshot_a, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="System Aの項目")])
    admin_client.post(f"/interview/sessions/{session_a}/alignment/build", headers=headers_a)

    system_b = _create_system(admin_client, token_a, "System B")
    headers_b = _headers(token_a, system_b["id"])

    r = admin_client.get(f"/interview/sessions/{session_a}/alignment", headers=headers_b)
    assert r.status_code == 404, r.text


# --- Review finding 4: review-queue excludes terminal/superseded items -------
#
# Covers: answered/corrected items disappear from the Review Queue but stay
# visible (as history) via the full GET .../alignment listing; held/inquiry
# items remain actionable in the queue; a rebuild marks surviving terminal
# rows superseded=1 while the fresh replacement row is superseded=0 and
# appears exactly once; held/inquiry rows survive a rebuild with
# superseded=0; System isolation and the additive-column migration/backfill.


def _open_review_item_inquiry(admin_client, monkeypatch, session_id, item_id, headers):
    """Open a real review_item Inquiry (mirrors
    test_review_item_inquiry_round_trip_through_real_build's stubbing) so the
    target alignment_item's status becomes 'inquiry'."""
    from app.routes import interview_inquiry as inquiry_routes
    from app.inquiry_answering import InquiryAnswerResult

    def fake_create_llm_client(config):
        return object()

    def fake_generate_inquiry_answer(client, config, **kwargs):
        return InquiryAnswerResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            conclusion="確認します。", answerable=True,
        )

    monkeypatch.setattr(inquiry_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(inquiry_routes, "generate_inquiry_answer", fake_generate_inquiry_answer)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/inquiries",
        json={"origin_kind": "review_item", "origin_id": item_id, "question_text": "根拠を確認したい"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["inquiry"]["id"]


def test_answered_and_corrected_items_are_excluded_from_review_queue(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A: 回答される", risk_flags=["security"]),
        _proposal_item(current_claim="B: 修正される", risk_flags=["security"]),
    ])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    a_id = next(it["id"] for it in built["items"] if it["current_claim"] == "A: 回答される")
    b_id = next(it["id"] for it in built["items"] if it["current_claim"] == "B: 修正される")

    queue_before = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    assert {it["id"] for it in queue_before["items"]} == {a_id, b_id}

    admin_client.post(
        f"/interview/alignment/{a_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )
    admin_client.post(
        f"/interview/alignment/{b_id}/correct",
        json={"corrected_interpretation": "正しい解釈"}, headers=headers,
    )

    queue_after = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    assert queue_after["items"] == []

    # Still visible as history via the full listing, just not action cards.
    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    ids_in_listing = {it["id"] for cat in listing["items_by_category"].values() for it in cat}
    assert {a_id, b_id} <= ids_in_listing


def test_held_and_inquiry_items_remain_in_review_queue(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A: 保留される", risk_flags=["security"]),
        _proposal_item(current_claim="B: 疑問がある", risk_flags=["security"]),
    ])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    held_id = next(it["id"] for it in built["items"] if it["current_claim"] == "A: 保留される")
    inquiry_item_id = next(it["id"] for it in built["items"] if it["current_claim"] == "B: 疑問がある")

    admin_client.post(f"/interview/alignment/{held_id}/hold", headers=headers)
    _open_review_item_inquiry(admin_client, monkeypatch, session_id, inquiry_item_id, headers)

    queue = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    by_id = {it["id"]: it for it in queue["items"]}
    assert set(by_id) == {held_id, inquiry_item_id}
    assert by_id[held_id]["status"] == "held"
    assert by_id[held_id]["superseded"] is False
    assert by_id[inquiry_item_id]["status"] == "inquiry"
    assert by_id[inquiry_item_id]["superseded"] is False


def test_rebuild_marks_terminal_rows_superseded_and_queue_shows_only_fresh_row(
    admin_client, tmp_path, monkeypatch,
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="回答される項目", risk_flags=["security"]),
        _proposal_item(current_claim="修正される項目", risk_flags=["security"]),
    ])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    answered_id = next(it["id"] for it in first["items"] if it["current_claim"] == "回答される項目")
    corrected_id = next(it["id"] for it in first["items"] if it["current_claim"] == "修正される項目")
    assert all(it["superseded"] is False for it in first["items"])

    admin_client.post(
        f"/interview/alignment/{answered_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )
    admin_client.post(
        f"/interview/alignment/{corrected_id}/correct",
        json={"corrected_interpretation": "修正しました"}, headers=headers,
    )

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="新しい項目", risk_flags=["security"])])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()

    by_id = {it["id"]: it for it in second["items"]}
    assert by_id[answered_id]["status"] == "answered"
    assert by_id[answered_id]["superseded"] is True
    assert by_id[corrected_id]["status"] == "corrected"
    assert by_id[corrected_id]["superseded"] is True

    new_items = [it for it in second["items"] if it["current_claim"] == "新しい項目"]
    assert len(new_items) == 1
    assert new_items[0]["status"] == "open"
    assert new_items[0]["superseded"] is False

    queue = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    queue_ids = {it["id"] for it in queue["items"]}
    assert answered_id not in queue_ids
    assert corrected_id not in queue_ids
    assert new_items[0]["id"] in queue_ids
    assert len([i for i in queue_ids if i == new_items[0]["id"]]) == 1


def test_rebuild_keeps_held_and_inquiry_rows_not_superseded(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="保留される項目", risk_flags=["security"]),
        _proposal_item(current_claim="疑問がある項目", risk_flags=["security"]),
    ])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    held_id = next(it["id"] for it in first["items"] if it["current_claim"] == "保留される項目")
    inquiry_item_id = next(it["id"] for it in first["items"] if it["current_claim"] == "疑問がある項目")

    admin_client.post(f"/interview/alignment/{held_id}/hold", headers=headers)
    _open_review_item_inquiry(admin_client, monkeypatch, session_id, inquiry_item_id, headers)

    _stub_build(monkeypatch, items=[])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    by_id = {it["id"]: it for it in second["items"]}
    assert by_id[held_id]["status"] == "held"
    assert by_id[held_id]["superseded"] is False
    assert by_id[inquiry_item_id]["status"] == "inquiry"
    assert by_id[inquiry_item_id]["superseded"] is False


# --- Unchanged-item carry-over (Issue #295) ----------------------------------


def test_unchanged_item_carried_over_and_excluded_from_review_queue(admin_client, tmp_path, monkeypatch):
    """A rebuild that reproposes byte-identical content for an already
    answered item marks the fresh replacement row 'unchanged' and records
    which prior row it was carried over from, and that fresh row never
    appears in the actionable Review Queue (must_review/batch_reviewable)."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    item_id = first["items"][0]["id"]
    assert first["items"][0]["content_hash"]
    assert first["items"][0]["carried_over_from"] is None

    answer = admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )
    assert answer.status_code == 200, answer.text

    # Second build reproposes the exact same content (claim + evidence +
    # every classification-relevant field unchanged).
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()

    unchanged = [it for it in second["items"] if it["review_category"] == "unchanged"]
    assert len(unchanged) == 1
    assert unchanged[0]["reason_code"] == "unchanged_since_confirmation"
    assert unchanged[0]["carried_over_from"] == item_id
    assert unchanged[0]["current_claim"] == "変わらない項目"
    assert unchanged[0]["status"] == "open"
    assert unchanged[0]["id"] != item_id

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    assert listing["counts"]["unchanged"] == 1
    assert len(listing["items_by_category"]["unchanged"]) == 1

    queue = admin_client.get(f"/interview/sessions/{session_id}/review-queue", headers=headers).json()
    queue_ids = {it["id"] for it in queue["items"]}
    assert unchanged[0]["id"] not in queue_ids


def test_changed_item_content_is_not_carried_over(admin_client, tmp_path, monkeypatch):
    """A changed classification-relevant field (alignment_state here) must
    produce a different content_hash, so the item goes through normal
    classification instead of being marked 'unchanged'."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="最初の内容", alignment_state="gap", confidence="likely"),
    ])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    item_id = first["items"][0]["id"]
    admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )

    # Same claim text, but the current-system understanding of it has
    # genuinely changed (now aligned instead of a gap) -- must not carry over.
    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="最初の内容", alignment_state="aligned", confidence="confirmed"),
    ])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()

    fresh = next(it for it in second["items"] if it["id"] != item_id)
    assert fresh["review_category"] == "no_review_required"
    assert fresh["reason_code"] == "no_change"
    assert fresh["carried_over_from"] is None


def test_goal_change_in_batch_blocks_unchanged_carryover(admin_client, tmp_path, monkeypatch):
    """When the batch that triggers a rebuild includes a decision on the
    'goal' Intent Brief field, this rebuild must reclassify every item
    through the normal rule table -- never carry anything over, even if an
    item's content is otherwise byte-identical to a prior answered row."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    item_id = first["items"][0]["id"]
    admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )

    # A goal decision lands strictly after the first build's timestamp,
    # simulating "this rebuild's triggering batch included a goal answer".
    _insert_intent_item(
        session_id, system_id, field="goal", value_text="新しい目標", created_at=time.time() + 1,
    )

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()

    fresh = next(it for it in second["items"] if it["id"] != item_id)
    assert fresh["review_category"] != "unchanged"
    assert fresh["review_category"] == "must_review"
    assert fresh["reason_code"] == "security_related"
    assert fresh["carried_over_from"] is None


def test_goal_unchanged_since_last_build_still_allows_carryover(admin_client, tmp_path, monkeypatch):
    """A confirmed 'goal' item that predates the prior build (i.e. the
    triggering batch did NOT touch goal) must not block carry-over -- the
    guard is specific to goal changing IN this batch, not to goal simply
    existing."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    # Goal confirmed well before the first alignment build.
    _insert_intent_item(
        session_id, system_id, field="goal", value_text="既存の目標", created_at=time.time() - 100,
    )

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    item_id = first["items"][0]["id"]
    admin_client.post(
        f"/interview/alignment/{item_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="変わらない項目", risk_flags=["security"])])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()

    fresh = next(it for it in second["items"] if it["id"] != item_id)
    assert fresh["review_category"] == "unchanged"
    assert fresh["carried_over_from"] == item_id


def test_unchanged_carryover_survives_three_consecutive_builds(admin_client, tmp_path, monkeypatch):
    """Review fix (PR #296, Finding 2): before the fix, a prior build's
    'unchanged' row (status='open') was NOT itself an eligible carry
    candidate -- only 'answered'/'corrected' rows were -- so a THIRD build
    of byte-identical content lost the carry-over chain and fell back to
    fresh (re-)classification. This must not happen: the 3rd build's fresh
    row must still be 'unchanged' and its carried_over_from must still point
    at the ORIGINAL answered row (never the 2nd build's now-superseded
    'unchanged' row), so the audit trail always terminates at the real human
    decision."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="不変の項目", risk_flags=["security"])])
    first = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    original_id = first["items"][0]["id"]
    admin_client.post(
        f"/interview/alignment/{original_id}/answer", json={"decision": "accept_current"}, headers=headers,
    )

    # 2nd build: byte-identical content -> carried over from the original
    # answered row.
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="不変の項目", risk_flags=["security"])])
    second = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    second_unchanged = next(it for it in second["items"] if it["id"] != original_id)
    assert second_unchanged["review_category"] == "unchanged"
    assert second_unchanged["carried_over_from"] == original_id

    # 3rd build: still byte-identical content -> must STILL carry over, and
    # must still point at the original answered row, not the 2nd build's
    # (now superseded-on-delete) unchanged row.
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="不変の項目", risk_flags=["security"])])
    third = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    third_unchanged = next(
        it for it in third["items"] if it["id"] not in (original_id, second_unchanged["id"])
    )
    assert third_unchanged["review_category"] == "unchanged"
    assert third_unchanged["carried_over_from"] == original_id
    assert third_unchanged["reason_code"] == "unchanged_since_confirmation"

    # The original answered row is still present (superseded=1 history) --
    # the FK it is referenced by was never NULLed out by an ON DELETE SET
    # NULL cascade, because the DELETE only ever removes status='open' rows
    # and the original row's status is 'answered'.
    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    original_in_history = next(it for it in listing["superseded_items"] if it["id"] == original_id)
    assert original_in_history["status"] == "answered"


def test_content_hash_columns_migration_backfills_existing_rows_to_null(tmp_path, monkeypatch):
    """A pre-Issue-#295 database (no content_hash/carried_over_from columns)
    gains them via ALTER TABLE and existing rows backfill to NULL -- same
    additive-column migration pattern as the 'superseded' column above."""
    import sqlite3

    db_path = tmp_path / "pre-295.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE alignment_item (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              INTEGER NOT NULL,
            system_id               INTEGER NOT NULL,
            revision_id             INTEGER,
            snapshot_id             INTEGER NOT NULL,
            intent_item_id          INTEGER,
            intent_summary          TEXT,
            current_claim           TEXT NOT NULL,
            current_evidence        TEXT NOT NULL DEFAULT '[]',
            gap_summary             TEXT,
            proposed_interpretation TEXT,
            alignment_state         TEXT NOT NULL,
            risk_flags              TEXT NOT NULL DEFAULT '[]',
            confidence              TEXT NOT NULL,
            review_category         TEXT NOT NULL,
            reason_code             TEXT NOT NULL,
            user_reason             TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'open',
            user_decision           TEXT,
            intelligence_run_id     INTEGER NOT NULL,
            is_mock                 INTEGER NOT NULL DEFAULT 0,
            created_at              REAL NOT NULL,
            updated_at              REAL NOT NULL
        )
        """
    )
    conn.execute(
        """INSERT INTO alignment_item
            (id, session_id, system_id, snapshot_id, current_claim,
             alignment_state, confidence, review_category, reason_code,
             user_reason, intelligence_run_id, created_at, updated_at)
        VALUES (1, 1, 1, 1, '既存の項目', 'gap', 'likely',
                'batch_reviewable', 'routine_update', '既存の理由', 1, ?, ?)""",
        (time.time(), time.time()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    from app.main import app

    with TestClient(app):
        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        cols = {r["name"] for r in check.execute("PRAGMA table_info(alignment_item)")}
        assert "content_hash" in cols
        assert "carried_over_from" in cols
        row = check.execute("SELECT * FROM alignment_item WHERE id = 1").fetchone()
        assert row["content_hash"] is None
        assert row["carried_over_from"] is None


def test_system_isolation_for_superseded_column(admin_client, tmp_path, monkeypatch):
    """A rebuild's superseded marking must never cross a System boundary."""
    from app.db import get_conn

    token, system_a, snapshot_a = _setup(admin_client, tmp_path, name="System A3")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _insert_revision(session_a, system_a, snapshot_a, current_understanding={})

    system_b = _create_system(admin_client, token, "System B3")["id"]
    headers_b = _headers(token, system_b)
    with get_conn() as conn:
        snap_row = conn.execute(
            "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ?", (snapshot_a,),
        ).fetchone()
    snapshot_b = _insert_snapshot(system_b, snap_row["repo_path"], snap_row["commit_sha"])
    session_b = _create_session(admin_client, headers_b, snapshot_b)
    _insert_revision(session_b, system_b, snapshot_b, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="共通クレーム", risk_flags=["security"])])
    built_a = admin_client.post(f"/interview/sessions/{session_a}/alignment/build", headers=headers_a).json()
    built_b = admin_client.post(f"/interview/sessions/{session_b}/alignment/build", headers=headers_b).json()
    a_item_id = built_a["items"][0]["id"]
    b_item_id = built_b["items"][0]["id"]

    admin_client.post(
        f"/interview/alignment/{a_item_id}/answer", json={"decision": "accept_current"}, headers=headers_a,
    )

    # Rebuild only System A -- System B's answered-equivalent row must be
    # untouched (it wasn't even answered, so this also indirectly proves the
    # UPDATE ... WHERE system_id=? scoping in run_alignment_build).
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="新しいクレームA", risk_flags=["security"])])
    admin_client.post(f"/interview/sessions/{session_a}/alignment/build", headers=headers_a)

    # Review fix (PR #296, Finding 3): a superseded row is history, not a
    # current row -- it now surfaces only via the additive
    # superseded_items field, never items_by_category/counts.
    listing_a = admin_client.get(f"/interview/sessions/{session_a}/alignment", headers=headers_a).json()
    assert not any(
        it["id"] == a_item_id for cat in listing_a["items_by_category"].values() for it in cat
    )
    a_item = next(it for it in listing_a["superseded_items"] if it["id"] == a_item_id)
    assert a_item["superseded"] is True

    listing_b = admin_client.get(f"/interview/sessions/{session_b}/alignment", headers=headers_b).json()
    assert listing_b["superseded_items"] == []
    b_item = next(it for cat in listing_b["items_by_category"].values() for it in cat if it["id"] == b_item_id)
    assert b_item["status"] == "open"
    assert b_item["superseded"] is False


def test_superseded_column_migration_backfills_existing_rows_to_zero(tmp_path, monkeypatch):
    """A pre-Finding-4 database (no ``superseded`` column) gains it via
    ALTER TABLE and existing rows backfill to 0 -- mirroring this repo's
    established additive-column migration pattern (see e.g.
    test_replay_capture_api.py's Phase A migration test)."""
    import sqlite3

    db_path = tmp_path / "pre-finding4.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE alignment_item (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              INTEGER NOT NULL,
            system_id               INTEGER NOT NULL,
            revision_id             INTEGER,
            snapshot_id             INTEGER NOT NULL,
            intent_item_id          INTEGER,
            intent_summary          TEXT,
            current_claim           TEXT NOT NULL,
            current_evidence        TEXT NOT NULL DEFAULT '[]',
            gap_summary             TEXT,
            proposed_interpretation TEXT,
            alignment_state         TEXT NOT NULL,
            risk_flags              TEXT NOT NULL DEFAULT '[]',
            confidence              TEXT NOT NULL,
            review_category         TEXT NOT NULL,
            reason_code             TEXT NOT NULL,
            user_reason             TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'open',
            user_decision           TEXT,
            intelligence_run_id     INTEGER NOT NULL,
            is_mock                 INTEGER NOT NULL DEFAULT 0,
            created_at              REAL NOT NULL,
            updated_at              REAL NOT NULL
        )
        """
    )
    conn.execute(
        """INSERT INTO alignment_item
            (id, session_id, system_id, snapshot_id, current_claim,
             alignment_state, confidence, review_category, reason_code,
             user_reason, intelligence_run_id, created_at, updated_at)
        VALUES (1, 1, 1, 1, '既存の項目', 'gap', 'likely',
                'batch_reviewable', 'routine_update', '既存の理由', 1, ?, ?)""",
        (time.time(), time.time()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    from app.main import app

    with TestClient(app):
        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        cols = {r["name"] for r in check.execute("PRAGMA table_info(alignment_item)")}
        assert "superseded" in cols
        row = check.execute("SELECT * FROM alignment_item WHERE id = 1").fetchone()
        assert row["superseded"] == 0
        check.close()


# --- Batch answer endpoint (PR #296 review fix, Finding 5) -------------------
#
# POST /interview/sessions/{session_id}/alignment/answers-batch: answers
# several alignment_item rows in one call and triggers Issue #288's
# request_refresh exactly once (not once per item), with per-item fail-closed
# error reporting on partial failure.


def _spy_request_refresh(monkeypatch):
    """Replace app.interview_refresh.request_refresh with a call-recording
    stub. The batch/single endpoints do `from ..interview_refresh import
    request_refresh` INSIDE the function body, so patching the attribute on
    the home module (not the routes module) is picked up correctly -- the
    import statement re-resolves the name from the module namespace on every
    call, it is not cached at route-module import time."""
    import app.interview_refresh as refresh_module

    calls = []

    def fake_request_refresh(session_id, system_id, trigger_kind):
        calls.append((session_id, system_id, trigger_kind))
        return None

    monkeypatch.setattr(refresh_module, "request_refresh", fake_request_refresh)
    return calls


def _batch_answer(client, headers, session_id, answers):
    return client.post(
        f"/interview/sessions/{session_id}/alignment/answers-batch",
        json={"answers": answers},
        headers=headers,
    )


def test_answers_batch_all_success_saves_every_item_and_refreshes_once(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A"), _proposal_item(current_claim="B"), _proposal_item(current_claim="C"),
    ])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    a_id, b_id, c_id = (it["id"] for it in built["items"])

    refresh_calls = _spy_request_refresh(monkeypatch)

    r = _batch_answer(admin_client, headers, session_id, [
        {"item_id": a_id, "decision": "accept_current", "note": "確認済み"},
        {"item_id": b_id, "decision": "needs_change"},
        {"item_id": c_id, "decision": "reject_interpretation"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id
    assert body["system_id"] == system_id
    assert body["refreshed"] is True
    assert len(body["results"]) == 3
    by_id = {res["item_id"]: res for res in body["results"]}
    assert by_id[a_id]["success"] is True
    assert by_id[a_id]["item"]["status"] == "answered"
    assert by_id[a_id]["item"]["user_decision"]["action"] == "accept_current"
    assert by_id[a_id]["item"]["user_decision"]["note"] == "確認済み"
    assert by_id[b_id]["success"] is True
    assert by_id[b_id]["item"]["user_decision"]["action"] == "needs_change"
    assert by_id[c_id]["success"] is True
    assert by_id[c_id]["item"]["user_decision"]["action"] == "reject_interpretation"

    # Exactly one refresh for the whole batch, not one per item.
    assert len(refresh_calls) == 1
    assert refresh_calls[0] == (session_id, system_id, "alignment_answer")

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    all_items = [it for cat in listing["items_by_category"].values() for it in cat]
    for item_id in (a_id, b_id, c_id):
        item = next(it for it in all_items if it["id"] == item_id)
        assert item["status"] == "answered"


def test_answers_batch_partial_failure_saves_valid_items_and_reports_errors(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="A")])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    a_id = built["items"][0]["id"]
    missing_id = a_id + 999_999

    refresh_calls = _spy_request_refresh(monkeypatch)

    r = _batch_answer(admin_client, headers, session_id, [
        {"item_id": a_id, "decision": "accept_current"},
        {"item_id": missing_id, "decision": "accept_current"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refreshed"] is True
    by_id = {res["item_id"]: res for res in body["results"]}
    assert by_id[a_id]["success"] is True
    assert by_id[missing_id]["success"] is False
    assert by_id[missing_id]["error"]
    assert by_id[missing_id]["item"] is None

    # A single valid item still triggers exactly one refresh.
    assert len(refresh_calls) == 1

    listing = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    all_items = [it for cat in listing["items_by_category"].values() for it in cat]
    assert next(it for it in all_items if it["id"] == a_id)["status"] == "answered"


def test_answers_batch_all_failure_never_refreshes(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    refresh_calls = _spy_request_refresh(monkeypatch)

    r = _batch_answer(admin_client, headers, session_id, [
        {"item_id": 999_999, "decision": "accept_current"},
        {"item_id": 999_998, "decision": "accept_current"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refreshed"] is False
    assert all(res["success"] is False for res in body["results"])
    assert refresh_calls == []


def test_answers_batch_rejects_duplicate_item_id_within_batch(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="A")])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    a_id = built["items"][0]["id"]

    _spy_request_refresh(monkeypatch)
    r = _batch_answer(admin_client, headers, session_id, [
        {"item_id": a_id, "decision": "accept_current"},
        {"item_id": a_id, "decision": "needs_change"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"][0]["success"] is True
    assert body["results"][1]["success"] is False
    assert "duplicate" in body["results"][1]["error"].lower()


def test_answers_batch_item_from_another_session_is_a_per_item_error(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_1 = _create_session(admin_client, headers, snapshot_id)
    session_2 = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_1, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="A")])
    built = admin_client.post(f"/interview/sessions/{session_1}/alignment/build", headers=headers).json()
    item_id = built["items"][0]["id"]

    refresh_calls = _spy_request_refresh(monkeypatch)
    r = _batch_answer(admin_client, headers, session_2, [
        {"item_id": item_id, "decision": "accept_current"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"][0]["success"] is False
    assert body["refreshed"] is False
    assert refresh_calls == []


def test_answers_batch_inquiry_locked_item_is_a_per_item_error(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _insert_revision(session_id, system_id, snapshot_id, current_understanding={})

    _stub_build(monkeypatch, items=[
        _proposal_item(current_claim="A: 疑問あり"), _proposal_item(current_claim="B: 通常"),
    ])
    built = admin_client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers).json()
    inquiry_id = next(it["id"] for it in built["items"] if it["current_claim"] == "A: 疑問あり")
    normal_id = next(it["id"] for it in built["items"] if it["current_claim"] == "B: 通常")

    _open_review_item_inquiry(admin_client, monkeypatch, session_id, inquiry_id, headers)

    refresh_calls = _spy_request_refresh(monkeypatch)
    r = _batch_answer(admin_client, headers, session_id, [
        {"item_id": inquiry_id, "decision": "accept_current"},
        {"item_id": normal_id, "decision": "accept_current"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {res["item_id"]: res for res in body["results"]}
    assert by_id[inquiry_id]["success"] is False
    assert by_id[normal_id]["success"] is True
    assert body["refreshed"] is True
    assert len(refresh_calls) == 1


def test_answers_batch_system_isolation(admin_client, tmp_path, monkeypatch):
    token_a, system_a, snapshot_a = _setup(admin_client, tmp_path, name="System Batch A")
    headers_a = _headers(token_a, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _insert_revision(session_a, system_a, snapshot_a, current_understanding={})

    _stub_build(monkeypatch, items=[_proposal_item(current_claim="System Aの項目")])
    built_a = admin_client.post(f"/interview/sessions/{session_a}/alignment/build", headers=headers_a).json()
    a_item_id = built_a["items"][0]["id"]

    system_b = _create_system(admin_client, token_a, "System Batch B")
    headers_b = _headers(token_a, system_b["id"])

    _spy_request_refresh(monkeypatch)
    r = _batch_answer(admin_client, headers_b, session_a, [
        {"item_id": a_item_id, "decision": "accept_current"},
    ])
    assert r.status_code == 404, r.text


def test_answers_batch_rejects_unknown_fields(admin_client, tmp_path, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/alignment/answers-batch",
        json={"answers": [{"item_id": 1, "decision": "accept_current", "unexpected": "x"}]},
        headers=headers,
    )
    assert r.status_code == 422, r.text
