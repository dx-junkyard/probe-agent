"""Tests for Epic #328 Phase A / Issue #329: Joint Understanding sessions.

Covers the three things Phase A is responsible for:

1. ``app/joint_understanding.py``'s deterministic contract -- finite
   vocabularies, the session transition table, and the per-role rules that
   keep investigation / translation / developer from authoring each other's
   content.
2. The API lifecycle -- create/list/detail/findings/actions/hold/resume/close,
   unknown vocabulary values -> 422, out-of-table transitions -> 409, and
   System isolation.
3. The boundary Epic #328 exists to protect: 「わからない」 never becomes a
   recorded developer intent. Creating a session, appending findings,
   recording actions, and closing with any outcome all leave the origin
   ``interview_qa`` / ``interview_intent_item`` / ``alignment_item`` /
   ``interview_inquiry`` row completely unchanged -- including ``status``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app.joint_understanding import (
    ACTION_KINDS,
    SCHEMA_VERSION,
    JointUnderstandingValidationError,
    available_action_kinds,
    outcome_is_provisional,
    validate_finding,
    validate_transition,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "shared" / "schemas" / "joint_understanding.schema.json"


# --- Unit tests: the deterministic contract -----------------------------------


def _valid_finding_kwargs(**overrides):
    base = dict(
        origin_role="investigation",
        claim_kind="fact",
        decision_method="reasoning_llm",
        evidence=[{"path": "app/x.py", "start_line": 1, "end_line": 1}],
        runtime_evidence=[],
        supports_finding_ids=[],
        competing_explanations=[],
        refutation_conditions=[],
        next_investigation=None,
        intelligence_run_id=7,
        is_mock=False,
        known_finding_ids=set(),
        supersedes_finding_id=None,
    )
    base.update(overrides)
    if base["origin_role"] != "investigation" and "evidence" not in overrides:
        base["evidence"] = []
    return base


def test_transition_table_is_finite_and_closed_is_terminal():
    validate_transition("open", "held")
    validate_transition("open", "closed")
    validate_transition("held", "open")
    for current, target in (("held", "closed"), ("closed", "open"), ("open", "open")):
        with pytest.raises(JointUnderstandingValidationError):
            validate_transition(current, target)


def test_only_hypothesis_adopted_is_provisional():
    assert outcome_is_provisional("hypothesis_adopted") is True
    for outcome in ("understood", "doubt_resolved", "decided", "handed_off", "abandoned", None):
        assert outcome_is_provisional(outcome) is False


def test_available_actions_only_while_open_and_order_stable():
    assert available_action_kinds("open") == list(ACTION_KINDS)
    assert available_action_kinds("held") == []
    assert available_action_kinds("closed") == []


def test_translation_finding_cannot_carry_evidence():
    with pytest.raises(JointUnderstandingValidationError, match="must not carry evidence"):
        validate_finding(**_valid_finding_kwargs(
            origin_role="translation",
            evidence=[{"path": "a.py", "start_line": 1, "end_line": 2}],
            supports_finding_ids=[1],
            known_finding_ids={1},
        ))


def test_translation_finding_requires_supporting_findings():
    with pytest.raises(JointUnderstandingValidationError, match="at least one finding"):
        validate_finding(**_valid_finding_kwargs(origin_role="translation"))


def test_supports_finding_ids_must_be_in_the_same_session():
    with pytest.raises(JointUnderstandingValidationError, match="outside this session"):
        validate_finding(**_valid_finding_kwargs(
            origin_role="translation", supports_finding_ids=[99], known_finding_ids={1},
        ))
    # A resolvable reference passes.
    validate_finding(**_valid_finding_kwargs(
        origin_role="translation", supports_finding_ids=[1], known_finding_ids={1},
    ))


def test_developer_finding_cannot_be_authored_by_a_reasoning_run():
    with pytest.raises(JointUnderstandingValidationError, match="decision_method"):
        validate_finding(**_valid_finding_kwargs(
            origin_role="developer", decision_method="reasoning_llm",
        ))
    with pytest.raises(JointUnderstandingValidationError, match="intelligence run"):
        validate_finding(**_valid_finding_kwargs(
            origin_role="developer", decision_method="manual", intelligence_run_id=3,
        ))
    with pytest.raises(JointUnderstandingValidationError, match="mock"):
        validate_finding(**_valid_finding_kwargs(
            origin_role="developer", decision_method="manual",
            intelligence_run_id=None, is_mock=True,
        ))
    # The valid shape.
    validate_finding(**_valid_finding_kwargs(
        origin_role="developer", decision_method="manual", intelligence_run_id=None,
    ))


def test_investigation_hypothesis_requires_competing_explanations_and_refutation():
    with pytest.raises(JointUnderstandingValidationError, match="competing explanation"):
        validate_finding(**_valid_finding_kwargs(claim_kind="hypothesis"))
    with pytest.raises(JointUnderstandingValidationError, match="refutation"):
        validate_finding(**_valid_finding_kwargs(
            claim_kind="hypothesis", competing_explanations=["別解釈"],
        ))
    with pytest.raises(JointUnderstandingValidationError, match="next investigation"):
        validate_finding(**_valid_finding_kwargs(
            claim_kind="hypothesis", competing_explanations=["別解釈"],
            refutation_conditions=["逆の観測"],
        ))
    validate_finding(**_valid_finding_kwargs(
        claim_kind="hypothesis",
        competing_explanations=["別解釈"], refutation_conditions=["逆の観測"],
        next_investigation="設定を切り替えて再現を確認する",
    ))


def test_investigation_assertion_requires_evidence():
    with pytest.raises(JointUnderstandingValidationError, match="must carry"):
        validate_finding(**_valid_finding_kwargs(evidence=[], runtime_evidence=[]))
    validate_finding(**_valid_finding_kwargs(
        claim_kind="unknown", evidence=[], runtime_evidence=[],
    ))


def test_reasoning_finding_must_reference_its_run():
    with pytest.raises(JointUnderstandingValidationError, match="intelligence run"):
        validate_finding(**_valid_finding_kwargs(intelligence_run_id=None))


def test_unknown_vocabulary_values_are_rejected():
    for field, value in (
        ("origin_role", "auditor"),
        ("claim_kind", "guess"),
        ("decision_method", "vibes"),
    ):
        with pytest.raises(JointUnderstandingValidationError):
            validate_finding(**_valid_finding_kwargs(**{field: value}))


# --- Shared schema contract ---------------------------------------------------


@pytest.fixture(scope="module")
def ju_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _exchange(**overrides) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "session": {
            "id": 1, "system_id": 1, "session_id": 1,
            "origin_kind": "qa", "origin_id": 1, "trigger": "unknown_answer",
            "question_text": "この経路は誰に影響しますか?",
            "status": "open", "outcome": None, "outcome_is_provisional": False,
            "outcome_reason": None, "outcome_finding_ids": [],
            "outcome_premise_state": None, "premise_state": "fresh",
            "premise_snapshot_id": 1, "schema_version": SCHEMA_VERSION,
            "created_at": 1.0, "updated_at": 1.0, "closed_at": None,
        },
        "findings": [],
        "actions": [],
    }
    payload.update(overrides)
    return payload


def test_schema_accepts_a_full_exchange(ju_schema):
    payload = _exchange(
        findings=[
            {
                "id": 1, "joint_understanding_id": 1, "system_id": 1,
                "origin_role": "investigation", "claim_kind": "fact",
                "statement": "リトライは3回で打ち切られる",
                "evidence": [{"path": "app/x.py", "start_line": 10, "end_line": 20}],
                "decision_method": "reasoning_llm", "intelligence_run_id": 5,
                "is_mock": False, "created_at": 2.0,
            },
            {
                "id": 2, "joint_understanding_id": 1, "system_id": 1,
                "origin_role": "translation", "claim_kind": "inference",
                "statement": "利用者から見ると3回目で失敗が表面化します",
                "supports_finding_ids": [1],
                "decision_method": "reasoning_llm", "intelligence_run_id": 6,
                "is_mock": False, "created_at": 3.0,
            },
        ],
        actions=[
            {
                "id": 1, "joint_understanding_id": 1, "system_id": 1,
                "action_kind": "compare_options", "actor": "dev",
                "note": None, "decision_method": "manual", "created_at": 4.0,
            },
        ],
    )
    jsonschema.validate(payload, ju_schema)


def test_schema_rejects_translation_evidence_and_unknown_enums(ju_schema):
    bad_translation = _exchange(findings=[{
        "id": 1, "joint_understanding_id": 1, "system_id": 1,
        "origin_role": "translation", "claim_kind": "inference",
        "statement": "…", "supports_finding_ids": [1],
        "evidence": [{"path": "a.py", "start_line": 1, "end_line": 2}],
        "decision_method": "reasoning_llm", "intelligence_run_id": 1,
        "is_mock": False, "created_at": 1.0,
    }])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_translation, ju_schema)

    unknown_outcome = _exchange()
    unknown_outcome["session"]["outcome"] = "approved"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown_outcome, ju_schema)

    unknown_action = _exchange(actions=[{
        "id": 1, "joint_understanding_id": 1, "system_id": 1,
        "action_kind": "auto_approve", "decision_method": "manual", "created_at": 1.0,
    }])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown_action, ju_schema)


def test_schema_requires_hypothesis_structure(ju_schema):
    bare_hypothesis = _exchange(findings=[{
        "id": 1, "joint_understanding_id": 1, "system_id": 1,
        "origin_role": "investigation", "claim_kind": "hypothesis",
        "statement": "たぶんキャッシュ由来です",
        "decision_method": "reasoning_llm", "intelligence_run_id": 1,
        "is_mock": False, "created_at": 1.0,
    }])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bare_hypothesis, ju_schema)

    bare_hypothesis["findings"][0]["competing_explanations"] = ["設定由来"]
    bare_hypothesis["findings"][0]["refutation_conditions"] = ["キャッシュ無効でも再現する"]
    bare_hypothesis["findings"][0]["next_investigation"] = "キャッシュ無効時の挙動を調べる"
    bare_hypothesis["findings"][0]["evidence"] = [
        {"path": "app/cache.py", "start_line": 1, "end_line": 2}
    ]
    jsonschema.validate(bare_hypothesis, ju_schema)


def test_schema_rejects_inconsistent_session_outcome_state(ju_schema):
    provisional = _exchange()
    provisional["session"].update({
        "status": "closed", "outcome": "hypothesis_adopted",
        "outcome_is_provisional": False, "outcome_finding_ids": [1],
        "outcome_premise_state": "fresh", "closed_at": 2.0,
    })
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(provisional, ju_schema)

    open_with_outcome = _exchange()
    open_with_outcome["session"]["outcome"] = "understood"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(open_with_outcome, ju_schema)


# --- Route-level tests ---------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-joint-understanding-test.db"))
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
        "/systems", json={"name": name, "environment": "test", "description": f"{name} desc"},
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


def _create_interview_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_qa(client, headers, session_id, question_text="この関数の目的は?"):
    r = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": question_text}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_intent(client, headers, session_id):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "トレース収集を効率化したい"}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _open_session(client, headers, session_id, origin_kind, origin_id, **overrides):
    body = {
        "origin_kind": origin_kind, "origin_id": origin_id,
        "trigger": "unknown_answer",
        "question_text": "この挙動が目的にどう影響するか分かりません",
    }
    body.update(overrides)
    return client.post(
        f"/interview/sessions/{session_id}/joint-understanding", json=body, headers=headers,
    )


def _qa_row(qa_id):
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return dict(row)


def _intent_row(intent_id):
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ?", (intent_id,),
        ).fetchone()
        return dict(row)


def test_create_from_unknown_answer_leaves_the_qa_row_untouched(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    before = _qa_row(qa["id"])

    r = _open_session(admin_client, headers, session_id, "qa", qa["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session"]["status"] == "open"
    assert body["session"]["trigger"] == "unknown_answer"
    assert body["session"]["schema_version"] == SCHEMA_VERSION
    assert body["session"]["premise_snapshot_id"] == snapshot_id
    # 「わからない」 is not a statement about the system: no developer finding
    # is fabricated from it.
    assert body["findings"] == []
    assert body["actions"] == []
    assert body["available_actions"] == list(ACTION_KINDS)

    assert _qa_row(qa["id"]) == before


def test_full_lifecycle_never_touches_the_origin_row(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    intent = _create_intent(admin_client, headers, session_id)
    before = _intent_row(intent["id"])

    ju = _open_session(admin_client, headers, session_id, "intent", intent["id"]).json()
    ju_id = ju["session"]["id"]

    finding = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "investigation", "claim_kind": "fact",
            "statement": "この経路は3回リトライする",
            "evidence": [{"path": "app/x.py", "start_line": 1, "end_line": 5}],
            "decision_method": "reasoning_llm", "intelligence_run_id": None,
        },
        headers=headers,
    )
    # reasoning_llm without a run id is rejected; retry with one.
    assert finding.status_code == 422, finding.text

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system_id, snapshot_id, now),
        )
        run_id = cur.lastrowid

    finding = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "investigation", "claim_kind": "fact",
            "statement": "この経路は3回リトライする",
            "evidence": [{"path": "app/x.py", "start_line": 1, "end_line": 5}],
            "decision_method": "reasoning_llm", "intelligence_run_id": run_id,
        },
        headers=headers,
    )
    assert finding.status_code == 201, finding.text
    finding_id = finding.json()["id"]

    translation = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "translation", "claim_kind": "inference",
            "statement": "利用者から見ると3回目の失敗まで待たされます",
            "supports_finding_ids": [finding_id],
            "decision_method": "reasoning_llm", "intelligence_run_id": run_id,
        },
        headers=headers,
    )
    assert translation.status_code == 201, translation.text

    action = admin_client.post(
        f"/joint-understanding/{ju_id}/actions",
        json={"action_kind": "compare_options", "actor": "dev"}, headers=headers,
    )
    assert action.status_code == 201, action.text
    assert action.json()["actions"][0]["decision_method"] == "manual"

    closed = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "decided", "outcome_reason": "リトライ回数は現状維持",
            # Issue #332: a decision must name the findings it rests on.
            "outcome_finding_ids": [finding_id],
        },
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["outcome"] == "decided"
    assert closed.json()["outcome_is_provisional"] is False
    assert closed.json()["closed_at"] is not None

    # Even a `decided` outcome never writes the origin Intent item.
    assert _intent_row(intent["id"]) == before


def test_hypothesis_adopted_outcome_is_marked_provisional(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_session(admin_client, headers, session_id, "qa", qa["id"]).json()["session"]["id"]

    hypothesis = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "developer", "claim_kind": "hypothesis",
            "statement": "設定由来だと思う", "decision_method": "manual",
        },
        headers=headers,
    )
    assert hypothesis.status_code == 201, hypothesis.text

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "hypothesis_adopted",
            "outcome_finding_ids": [hypothesis.json()["id"]],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["outcome_is_provisional"] is True

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert detail["session"]["outcome_is_provisional"] is True
    assert detail["available_actions"] == []


def test_findings_are_append_only_and_corrections_use_supersedes(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_session(admin_client, headers, session_id, "qa", qa["id"]).json()["session"]["id"]

    first = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "developer", "claim_kind": "hypothesis",
            "statement": "たぶん課金側の都合です", "decision_method": "manual",
        },
        headers=headers,
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    correction = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "developer", "claim_kind": "unknown",
            "statement": "課金側の都合かどうかは判断できません",
            "decision_method": "manual", "supersedes_finding_id": first_id,
        },
        headers=headers,
    )
    assert correction.status_code == 201, correction.text

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert [f["id"] for f in detail["findings"]] == [first_id, correction.json()["id"]]
    # The superseded finding is still readable, unchanged.
    assert detail["findings"][0]["statement"] == "たぶん課金側の都合です"
    assert detail["findings"][1]["supersedes_finding_id"] == first_id

    # There is no update/delete endpoint.
    assert admin_client.put(
        f"/joint-understanding/{ju_id}/findings/{first_id}", json={}, headers=headers,
    ).status_code in (404, 405)
    assert admin_client.delete(
        f"/joint-understanding/{ju_id}/findings/{first_id}", headers=headers,
    ).status_code in (404, 405)


def test_supports_finding_ids_cannot_cross_sessions(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    qa2 = _create_qa(admin_client, headers, session_id, question_text="別の質問")
    ju_a = _open_session(admin_client, headers, session_id, "qa", qa["id"]).json()["session"]["id"]
    ju_b = _open_session(admin_client, headers, session_id, "qa", qa2["id"]).json()["session"]["id"]

    in_a = admin_client.post(
        f"/joint-understanding/{ju_a}/findings",
        json={
            "origin_role": "developer", "claim_kind": "fact",
            "statement": "AのFinding", "decision_method": "manual",
        },
        headers=headers,
    )
    assert in_a.status_code == 201, in_a.text

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system_id, snapshot_id, now),
        )
        run_id = cur.lastrowid

    cross = admin_client.post(
        f"/joint-understanding/{ju_b}/findings",
        json={
            "origin_role": "translation", "claim_kind": "inference",
            "statement": "他セッションの根拠を借用する",
            "supports_finding_ids": [in_a.json()["id"]],
            "decision_method": "reasoning_llm", "intelligence_run_id": run_id,
        },
        headers=headers,
    )
    assert cross.status_code == 422, cross.text
    assert "outside this session" in json.dumps(cross.json(), ensure_ascii=False)


def test_finding_run_provenance_cannot_cross_systems(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_session(
        admin_client, headers, session_id, "qa", qa["id"],
    ).json()["session"]["id"]

    other = _create_system(admin_client, token, "Other")
    other_snapshot = _insert_snapshot(other["id"], "other123")
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        other_run = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (other["id"], other_snapshot, now),
        ).lastrowid

    response = admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "investigation", "claim_kind": "fact",
            "statement": "別Systemのrunを借りる",
            "evidence": [{"path": "app/x.py", "start_line": 1, "end_line": 1}],
            "decision_method": "reasoning_llm", "intelligence_run_id": other_run,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "this System" in response.text


def test_unknown_vocabulary_values_are_422(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    bad_trigger = _open_session(
        admin_client, headers, session_id, "qa", qa["id"], trigger="just_because",
    )
    assert bad_trigger.status_code == 422

    bad_origin = _open_session(admin_client, headers, session_id, "capability", qa["id"])
    assert bad_origin.status_code == 422

    ju_id = _open_session(admin_client, headers, session_id, "qa", qa["id"]).json()["session"]["id"]
    bad_action = admin_client.post(
        f"/joint-understanding/{ju_id}/actions",
        json={"action_kind": "auto_approve"}, headers=headers,
    )
    assert bad_action.status_code == 422

    bad_outcome = admin_client.post(
        f"/joint-understanding/{ju_id}/close", json={"outcome": "approved"}, headers=headers,
    )
    assert bad_outcome.status_code == 422

    bad_filter = admin_client.get(
        f"/interview/sessions/{session_id}/joint-understanding?status=finished", headers=headers,
    )
    assert bad_filter.status_code == 422


def test_missing_origin_item_is_404(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    r = _open_session(admin_client, headers, session_id, "qa", 4242)
    assert r.status_code == 404


def test_transitions_follow_the_finite_table(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_session(admin_client, headers, session_id, "qa", qa["id"]).json()["session"]["id"]

    held = admin_client.post(f"/joint-understanding/{ju_id}/hold", json={}, headers=headers)
    assert held.status_code == 200
    assert held.json()["status"] == "held"

    # held -> closed is not in the table.
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/close", json={"outcome": "abandoned"}, headers=headers,
    ).status_code == 409
    # A held session accepts no findings or actions either.
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/actions", json={"action_kind": "hold"}, headers=headers,
    ).status_code == 409
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "developer", "claim_kind": "unknown",
            "statement": "保留中", "decision_method": "manual",
        },
        headers=headers,
    ).status_code == 409

    resumed = admin_client.post(f"/joint-understanding/{ju_id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "open"

    closed = admin_client.post(
        f"/joint-understanding/{ju_id}/close", json={"outcome": "understood"}, headers=headers,
    )
    assert closed.status_code == 200
    # closed is terminal.
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/resume", headers=headers,
    ).status_code == 409
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/hold", json={}, headers=headers,
    ).status_code == 409


def test_list_filters_and_system_isolation(admin_client):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    headers_a = _headers(token, system_a)
    session_a = _create_interview_session(admin_client, headers_a, snapshot_a)
    qa_a = _create_qa(admin_client, headers_a, session_a)
    ju_a = _open_session(
        admin_client, headers_a, session_a, "qa", qa_a["id"],
    ).json()["session"]["id"]

    system_b = _create_system(admin_client, token, "System B")["id"]
    headers_b = _headers(token, system_b)

    listed = admin_client.get(
        f"/interview/sessions/{session_a}/joint-understanding?status=open", headers=headers_a,
    )
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()["items"]] == [ju_a]
    assert admin_client.get(
        f"/interview/sessions/{session_a}/joint-understanding?origin_kind=intent",
        headers=headers_a,
    ).json()["items"] == []

    # System B can neither read nor write System A's session.
    assert admin_client.get(f"/joint-understanding/{ju_a}", headers=headers_b).status_code == 404
    assert admin_client.post(
        f"/joint-understanding/{ju_a}/actions",
        json={"action_kind": "hold"}, headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/joint-understanding/{ju_a}/close", json={"outcome": "abandoned"}, headers=headers_b,
    ).status_code == 404
    assert admin_client.get(
        f"/interview/sessions/{session_a}/joint-understanding", headers=headers_b,
    ).status_code == 404


def test_review_item_origin_status_is_not_mirrored(admin_client):
    """Unlike Issue #287's Inquiry integration, a Joint Understanding session
    does not write 'inquiry' (or anything else) onto the alignment_item."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_interview_session(admin_client, headers, snapshot_id)

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'alignment_build', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system_id, snapshot_id, now),
        ).lastrowid
        item_id = conn.execute(
            """INSERT INTO alignment_item
                (session_id, system_id, snapshot_id, current_claim, current_evidence,
                 alignment_state, risk_flags, confidence, review_category, reason_code,
                 user_reason, status, intelligence_run_id, created_at, updated_at)
               VALUES (?, ?, ?, '現状はリトライ3回', '[]', 'gap', '[]', 'medium',
                       'must_review', 'gap_high_risk', '要確認', 'open', ?, ?, ?)""",
            (session_id, system_id, snapshot_id, run, now, now),
        ).lastrowid

    r = _open_session(admin_client, headers, session_id, "review_item", item_id)
    assert r.status_code == 201, r.text

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        assert row["status"] == "open"
        assert row["user_decision"] is None
