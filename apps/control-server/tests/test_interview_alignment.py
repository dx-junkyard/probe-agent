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
