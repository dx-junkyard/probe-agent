"""Tests for Epic #328 Phase D / Issue #332: reflux and the terminal states.

Covers:

1. Reflux: system-verified facts reach the understanding surface WITHOUT
   being recorded as a human answer -- ``interview_qa.investigation_json`` is
   written, ``answer_text``/``status``/``answered_by`` are not, and every
   reflux row carries ``decision_method='reasoning_llm'``, never ``manual``.
2. Only investigation FACTS reflux: inference / hypothesis / unknown /
   conflict stay inside the conversation, and a corrected (superseded)
   finding is not attached.
3. Idempotency: repeating the call attaches nothing twice.
4. Premise staleness: once the interview session moves to a newer snapshot,
   reflux and the two asserting outcomes (``hypothesis_adopted`` /
   ``decided``) are refused.
5. Terminal states: all four meanings persist distinctly, an asserting
   outcome must name its basis, and a provisional adoption is never reported
   as a fact.
6. Reflux touches no policy/execution table and stays System-isolated.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.joint_understanding import (
    JointUnderstandingValidationError,
    can_reflux,
    reflux_target_kind,
    validate_outcome_basis,
)


# --- Unit: the deterministic Phase D rules -------------------------------------


def test_only_investigation_facts_reflux():
    assert can_reflux("investigation", "fact") is True
    for claim_kind in ("inference", "hypothesis", "unknown", "conflict"):
        assert can_reflux("investigation", claim_kind) is False
    for role in ("translation", "developer"):
        assert can_reflux(role, "fact") is False


def test_reflux_target_is_deterministic_per_origin():
    assert reflux_target_kind("qa") == "qa_investigation"
    for origin in ("intent", "review_item", "inquiry"):
        assert reflux_target_kind(origin) == "session_ledger"


def test_outcome_basis_rules():
    # Outcomes that assert nothing forward-looking need no basis.
    for outcome in ("understood", "doubt_resolved", "handed_off", "abandoned"):
        validate_outcome_basis(
            outcome=outcome, outcome_finding_ids=[], known_finding_ids=set(),
            premise_state="fresh",
        )
    # Adoption / decision must name their basis...
    for outcome in ("hypothesis_adopted", "decided"):
        with pytest.raises(JointUnderstandingValidationError, match="must name"):
            validate_outcome_basis(
                outcome=outcome, outcome_finding_ids=[], known_finding_ids={1},
                premise_state="fresh",
            )
        # ...which has to belong to this session...
        with pytest.raises(JointUnderstandingValidationError, match="outside this session"):
            validate_outcome_basis(
                outcome=outcome, outcome_finding_ids=[9], known_finding_ids={1},
                premise_state="fresh",
            )
        # ...and is refused outright on a stale premise.
        with pytest.raises(JointUnderstandingValidationError, match="premise"):
            validate_outcome_basis(
                outcome=outcome, outcome_finding_ids=[1], known_finding_ids={1},
                premise_state="stale",
            )
        validate_outcome_basis(
            outcome=outcome, outcome_finding_ids=[1], known_finding_ids={1},
            premise_state="fresh",
        )


def test_non_asserting_outcomes_are_allowed_on_a_stale_premise():
    """Recording that a conversation was abandoned or handed off does not
    assert anything about the system, so a moved snapshot cannot invalidate
    it -- only adoption and decision are blocked."""
    for outcome in ("understood", "doubt_resolved", "handed_off", "abandoned"):
        validate_outcome_basis(
            outcome=outcome, outcome_finding_ids=[], known_finding_ids=set(),
            premise_state="stale",
        )


# --- Route-level ----------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-reflux-test.db"))
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


def _insert_snapshot(system_id, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        ).lastrowid


def _setup(client, origin_kind="qa"):
    token = _login(client)
    system = client.post(
        "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    headers = _headers(token, system["id"])
    snapshot_id = _insert_snapshot(system["id"], "abc123")

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'joint_investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system["id"], snapshot_id, now),
        ).lastrowid

    session_id = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()

    if origin_kind == "qa":
        origin_id = qa["id"]
    else:
        origin_id = client.post(
            f"/interview/sessions/{session_id}/intent",
            json={"field": "goal", "value_text": "失敗を早く見せたい"}, headers=headers,
        ).json()["id"]

    ju_id = client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": origin_kind, "origin_id": origin_id,
            "trigger": "unknown_answer", "question_text": "根拠が分かりません",
        },
        headers=headers,
    ).json()["session"]["id"]
    return headers, system["id"], session_id, snapshot_id, qa, ju_id, run_id


def _add_finding(client, headers, ju_id, run_id, **overrides):
    body = {
        "origin_role": "investigation", "claim_kind": "fact",
        "statement": "リトライは3回で打ち切られる",
        "evidence": [{"path": "app/retry.py", "start_line": 1, "end_line": 5}],
        "decision_method": "reasoning_llm", "intelligence_run_id": run_id,
    }
    body.update(overrides)
    r = client.post(f"/joint-understanding/{ju_id}/findings", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _qa_row(qa_id):
    from app.db import get_conn

    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone())


def test_reflux_reaches_the_qa_investigation_slot_not_the_answer(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    before = _qa_row(qa["id"])
    finding_id = _add_finding(admin_client, headers, ju_id, run_id)

    r = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["target_kind"] == "qa_investigation"
    assert body["premise_state"] == "fresh"
    assert [x["finding_id"] for x in body["refluxed"]] == [finding_id]
    assert all(x["decision_method"] == "reasoning_llm" for x in body["refluxed"])
    assert body["refluxed"][0]["evidence"][0]["path"] == "app/retry.py"

    after = _qa_row(qa["id"])
    # The system-verified fact reached the understanding surface...
    assert after["investigation_json"] is not None
    payload = json.loads(after["investigation_json"])
    assert payload["source"] == "joint_understanding"
    assert "リトライは3回" in payload["conclusion"]
    assert after["investigation_run_id"] == run_id
    # ...without becoming anyone's answer.
    assert after["answer_text"] == before["answer_text"]
    assert after["status"] == before["status"]
    assert after["answered_by"] == before["answered_by"]


def test_reflux_only_attaches_investigation_facts(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    fact_id = _add_finding(admin_client, headers, ju_id, run_id)
    _add_finding(
        admin_client, headers, ju_id, run_id,
        claim_kind="hypothesis", statement="設定由来かもしれない",
        competing_explanations=["キャッシュ由来"], refutation_conditions=["設定変更でも再現"],
    )
    _add_finding(
        admin_client, headers, ju_id, run_id,
        origin_role="developer", claim_kind="fact", statement="運用上そう決めた",
        decision_method="manual", intelligence_run_id=None, evidence=[],
    )

    r = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert [x["finding_id"] for x in body["refluxed"]] == [fact_id]
    assert body["skipped_not_fact"] == 2


def test_reflux_skips_a_superseded_finding(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    first = _add_finding(admin_client, headers, ju_id, run_id, statement="打ち切りは5回")
    corrected = _add_finding(
        admin_client, headers, ju_id, run_id,
        statement="打ち切りは3回", supersedes_finding_id=first,
    )
    r = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert [x["finding_id"] for x in r.json()["refluxed"]] == [corrected]


def test_reflux_is_idempotent(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    _add_finding(admin_client, headers, ju_id, run_id)
    first = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers).json()
    second = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers).json()
    assert len(first["refluxed"]) == 1
    assert second["refluxed"] == []
    assert second["already_refluxed"] == 1

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert len(detail["reflux"]) == 1


def test_non_qa_origin_uses_the_session_ledger_only(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(
        admin_client, origin_kind="intent",
    )
    _add_finding(admin_client, headers, ju_id, run_id)
    r = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["target_kind"] == "session_ledger"
    assert r.json()["refluxed"][0]["target_id"] is None
    # The unrelated Q&A row is untouched, and so is the Intent item.
    assert _qa_row(qa["id"])["investigation_json"] is None

    from app.db import get_conn

    with get_conn() as conn:
        intent = conn.execute(
            "SELECT * FROM interview_intent_item WHERE session_id = ?", (session_id,),
        ).fetchone()
        assert intent["status"] != "confirmed" or intent["value_text"] == "失敗を早く見せたい"


def _move_session_snapshot(system_id, session_id):
    from app.db import get_conn

    new_snapshot = _insert_snapshot(system_id, "def456")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?",
            (new_snapshot, session_id),
        )
    return new_snapshot


def test_stale_premise_blocks_reflux_and_asserting_outcomes(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    finding_id = _add_finding(admin_client, headers, ju_id, run_id)
    _move_session_snapshot(system_id, session_id)

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert detail["session"]["premise_state"] == "stale"

    assert admin_client.post(
        f"/joint-understanding/{ju_id}/reflux", headers=headers,
    ).status_code == 409

    for outcome in ("hypothesis_adopted", "decided"):
        r = admin_client.post(
            f"/joint-understanding/{ju_id}/close",
            json={"outcome": outcome, "outcome_finding_ids": [finding_id]}, headers=headers,
        )
        assert r.status_code == 409, r.text

    # Closing without asserting anything is still possible.
    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close", json={"outcome": "abandoned"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["outcome_premise_state"] == "stale"


def test_terminal_states_are_distinct_and_record_their_basis(admin_client):
    for outcome, provisional in (
        ("understood", False),
        ("doubt_resolved", False),
        ("hypothesis_adopted", True),
        ("decided", False),
    ):
        headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
        finding_id = _add_finding(admin_client, headers, ju_id, run_id)
        r = admin_client.post(
            f"/joint-understanding/{ju_id}/close",
            json={"outcome": outcome, "outcome_finding_ids": [finding_id]}, headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == outcome
        assert body["outcome_is_provisional"] is provisional
        assert body["outcome_finding_ids"] == [finding_id]
        assert body["outcome_premise_state"] == "fresh"


def test_adoption_stays_provisional_and_does_not_reflux_the_hypothesis(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    hypothesis_id = _add_finding(
        admin_client, headers, ju_id, run_id,
        claim_kind="hypothesis", statement="設定由来だと考えられる",
        competing_explanations=["キャッシュ由来"], refutation_conditions=["設定変更でも再現"],
    )
    reflux = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers).json()
    # Adopting it provisionally never turns it into an attached fact.
    assert reflux["refluxed"] == []
    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={"outcome": "hypothesis_adopted", "outcome_finding_ids": [hypothesis_id]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["outcome_is_provisional"] is True

    from app.db import get_conn

    with get_conn() as conn:
        attached = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_reflux "
            "WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()
        assert attached["n"] == 0


def test_outcome_basis_must_belong_to_this_session(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    _add_finding(admin_client, headers, ju_id, run_id)
    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={"outcome": "decided", "outcome_finding_ids": [9_999]}, headers=headers,
    )
    assert r.status_code == 422, r.text


def test_reflux_touches_no_policy_or_execution_row(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    _add_finding(admin_client, headers, ju_id, run_id)

    from app.db import get_conn

    def _snapshot_of(table):
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]

    # components carries the policy mode; the rest are the execution surfaces
    # reflux must never reach.
    guarded = ["components", "experiments", "probe_plans", "publish_jobs"]
    before = {t: _snapshot_of(t) for t in guarded}
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert {t: _snapshot_of(t) for t in guarded} == before


def test_reflux_is_system_isolated(admin_client):
    headers, system_id, session_id, snapshot_id, qa, ju_id, run_id = _setup(admin_client)
    _add_finding(admin_client, headers, ju_id, run_id)
    token = _login(admin_client)
    other = admin_client.post(
        "/systems", json={"name": "Other", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/reflux", headers=_headers(token, other["id"]),
    ).status_code == 404
