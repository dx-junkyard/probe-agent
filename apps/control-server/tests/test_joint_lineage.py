"""Issue #338: outcome lineage and the quality/burden metrics built on it.

The existing `joint_understanding` metrics (#334) count start rate, close
outcomes, finding kinds, and reflux volume — utilization and self-reported
state. None of them can answer whether understanding improved, because an
outcome label is a claim about the conversation, not an observation of what
happened afterwards: a session that closed `understood` and one whose
hypothesis was reversed two rounds later both count as a conclusion.

These tests cover the derived lineage that CAN answer it, the separation of
quality from burden from efficiency, and the deliberate refusal to turn #311's
start condition into a self-reported verdict.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from joint_understanding_helpers import insert_producer_finding

from app.joint_lineage import (
    BULK_APPROVAL_CRITERIA,
    BULK_APPROVAL_VERDICTS,
    LINEAGE_EVENT_KINDS,
    LINEAGE_SUBJECT_KINDS,
    subject_kind_for,
)


def test_every_event_kind_has_a_subject():
    for event_kind in LINEAGE_EVENT_KINDS:
        assert subject_kind_for(event_kind) in LINEAGE_SUBJECT_KINDS


def test_an_unknown_event_kind_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        subject_kind_for("hypothesis_probably_fine")


# --- Route-level ----------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-lineage-test.db"))
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


def _create_system(client, token, name):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        ).lastrowid


def _insert_run(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'joint_investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system_id, snapshot_id, now),
        ).lastrowid


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    headers = _headers(token, system["id"])
    snapshot_id = _insert_snapshot(system["id"])
    session_id = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    return token, system["id"], headers, snapshot_id, session_id


def _create_qa(client, headers, session_id, question_text="リトライ回数の根拠は?"):
    r = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": question_text}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _open_ju(client, headers, session_id, origin_kind, origin_id):
    r = client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": origin_kind, "origin_id": origin_id,
            "trigger": "explicit_request", "question_text": "根拠が分かりません",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


def _lineage(client, headers, **params):
    r = client.get(
        "/interview/joint-understanding/lineage", headers=headers, params=params,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(body) -> List[str]:
    return [e["event_kind"] for e in body["events"]]


def _metrics(client, headers) -> Dict[str, dict]:
    r = client.get("/interview/metrics", headers=headers)
    assert r.status_code == 200, r.text
    return {m["key"]: m for m in r.json()["metrics"]}


def test_an_unknown_and_its_resolution_are_one_lineage(admin_client):
    """The thing the close label cannot express: a gap was recorded, and later
    something closed it. Two counts of unrelated rows cannot answer "how many
    gaps got closed"."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)

    unknown_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="unknown",
        statement="通知先は特定できなかった", evidence=[],
        intelligence_run_id=run_id,
    )
    resolution_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="fact",
        statement="通知先は settings.NOTIFY_URL", supersedes_finding_id=unknown_id,
        intelligence_run_id=run_id,
    )

    body = _lineage(admin_client, headers)
    kinds = _kinds(body)
    assert "unknown_created" in kinds
    assert "unknown_resolved" in kinds
    resolved = next(e for e in body["events"] if e["event_kind"] == "unknown_resolved")
    assert resolved["subject_id"] == unknown_id
    assert resolved["supersedes_subject_id"] == resolution_id
    assert resolved["subject_kind"] == "unknown"

    metrics = _metrics(admin_client, headers)
    rate = metrics["joint_understanding_unknown_resolution_rate"]
    assert rate["category"] == "joint_understanding_quality"
    assert rate["value"] == 1.0


def test_an_unresolved_unknown_is_a_real_result_not_a_missing_one(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="unknown",
        statement="特定できなかった", evidence=[],
        intelligence_run_id=_insert_run(system_id, snapshot_id),
    )
    # While the session is open the unknown has no verdict yet: counting it
    # either way would report an outcome that has not happened.
    assert "unknown_remained" not in _kinds(_lineage(admin_client, headers))
    assert _metrics(admin_client, headers)[
        "joint_understanding_unknown_resolution_rate"
    ]["status"] == "unmeasured"

    admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={"outcome": "abandoned", "outcome_reason": "今回は打ち切る"},
        headers=headers,
    )
    assert "unknown_remained" in _kinds(_lineage(admin_client, headers))
    rate = _metrics(admin_client, headers)[
        "joint_understanding_unknown_resolution_rate"
    ]
    assert rate["value"] == 0.0
    assert rate["denominator"] == 1


def test_replacing_an_unknown_with_an_unknown_is_not_resolution(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    first = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="unknown",
        statement="通知先は特定できなかった", evidence=[],
        intelligence_run_id=run_id,
    )
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="unknown",
        statement="設定と環境変数のどちらか特定できない", evidence=[],
        supersedes_finding_id=first, intelligence_run_id=run_id,
    )
    assert "unknown_resolved" not in _kinds(_lineage(admin_client, headers))


def test_a_fact_successor_does_not_invent_hypothesis_reversal(admin_client):
    """A fact may confirm or refute the hypothesis; supersession alone cannot say."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)

    reversed_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="設定由来と思われる", intelligence_run_id=run_id,
    )
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="fact",
        statement="実際は定数で決まっていた", supersedes_finding_id=reversed_id,
        intelligence_run_id=run_id,
    )
    corrected_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="呼び出し側が上限を渡していると思われる", intelligence_run_id=run_id,
    )
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="いや、ミドルウェアが渡していると思われる",
        supersedes_finding_id=corrected_id, intelligence_run_id=run_id,
    )

    body = _lineage(admin_client, headers)
    kinds = _kinds(body)
    assert kinds.count("hypothesis_reversed") == 0
    assert kinds.count("hypothesis_superseded") == 1
    assert kinds.count("hypothesis_corrected") == 1

    metrics = _metrics(admin_client, headers)
    reversal = metrics["joint_understanding_hypothesis_reversal_rate"]
    correction = metrics["joint_understanding_hypothesis_correction_rate"]
    # Reversal requires an explicit observation contract and therefore cannot
    # be reported as a measured zero from the ambiguous successor shape.
    assert reversal["status"] == "unmeasured"
    assert reversal["value"] is None
    assert reversal["unmeasured_reason"] == "explicit_observation_contract_pending"
    assert correction["value"] == 0.5
    # Both are guardrails, and both live in the quality category.
    assert reversal["guardrail"] is True
    assert correction["guardrail"] is True
    assert reversal["category"] == "joint_understanding_quality"


def test_a_maintained_adoption_is_confirmed_and_a_moved_premise_needs_recheck(
    admin_client,
):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    hypothesis_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="設定由来と思われる",
        intelligence_run_id=_insert_run(system_id, snapshot_id),
    )
    admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "hypothesis_adopted", "outcome_reason": "暫定でこの説を採る",
            "outcome_finding_ids": [hypothesis_id],
        },
        headers=headers,
    )
    # Provisional adoption is not evidence confirmation.
    assert "hypothesis_confirmed" not in _kinds(_lineage(admin_client, headers))
    recheck = _metrics(admin_client, headers)[
        "joint_understanding_adoption_reconfirmation_rate"
    ]
    assert recheck["value"] == 0.0

    # Move the premise: the adoption is now something a human must re-check,
    # and that must be visible rather than aging into a clean close.
    from app.db import get_conn

    moved = _insert_snapshot(system_id, "def456")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?",
            (moved, session_id),
        )
    after = _metrics(admin_client, headers)[
        "joint_understanding_adoption_reconfirmation_rate"
    ]
    assert after["value"] == 1.0
    assert after["guardrail"] is True


def test_reopening_an_item_does_not_invent_a_decision_undo(admin_client):
    """A new discussion can clarify a decision without undoing it."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    first = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    finding_id = insert_producer_finding(
        ju_id=first, system_id=system_id,
        intelligence_run_id=_insert_run(system_id, snapshot_id),
    )
    admin_client.post(
        f"/joint-understanding/{first}/actions",
        json={"action_kind": "decide"}, headers=headers,
    )
    admin_client.post(
        f"/joint-understanding/{first}/close",
        json={
            "outcome": "decided", "outcome_reason": "現状維持",
            "outcome_finding_ids": [finding_id],
        },
        headers=headers,
    )
    body = _lineage(admin_client, headers)
    assert "decision_proposed" in _kinds(body)
    assert "decision_adopted" in _kinds(body)
    assert "decision_undone" not in _kinds(body)

    # The developer comes back to the same question.
    _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    body = _lineage(admin_client, headers)
    assert "decision_undone" not in _kinds(body)
    undo_rate = _metrics(admin_client, headers)["joint_understanding_decision_undo_rate"]
    assert undo_rate["status"] == "unmeasured"
    assert undo_rate["unmeasured_reason"] == "explicit_observation_contract_pending"
    assert undo_rate["guardrail"] is True


def test_handoff_does_not_invent_a_router_misclassification(admin_client):
    """Handoff may be operational even when the original route was correct."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_qa SET route_category = 'system_researchable' WHERE id = ?",
            (qa["id"],),
        )
    admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={"outcome": "handed_off", "outcome_reason": "運用チームへ引き継ぐ"},
        headers=headers,
    )
    body = _lineage(admin_client, headers)
    assert "classification_corrected" not in _kinds(body)
    rate = _metrics(admin_client, headers)[
        "joint_understanding_classification_correction_rate"
    ]
    assert rate["status"] == "unmeasured"
    assert rate["unmeasured_reason"] == "explicit_observation_contract_pending"
    assert rate["guardrail"] is True


def test_burden_is_counted_per_session_and_never_mixed_with_quality(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    for _ in range(3):
        admin_client.post(
            f"/joint-understanding/{ju_id}/actions",
            json={"action_kind": "explain_reasoning"}, headers=headers,
        )
    admin_client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "developer", "claim_kind": "inference",
            "statement": "この待ち時間は許容できない", "decision_method": "manual",
        },
        headers=headers,
    )

    body = _lineage(admin_client, headers)
    assert len(body["burdens"]) == 1
    burden = body["burdens"][0]
    assert burden["developer_actions"] == 3
    assert burden["developer_findings"] == 1

    metrics = _metrics(admin_client, headers)
    actions = metrics["joint_understanding_developer_actions_per_session"]
    assert actions["category"] == "joint_understanding_burden"
    assert actions["unit"] == "per_session"
    assert actions["value"] == 3.0
    # The three categories stay apart: an efficiency gain is never displayed as
    # a quality gain, and burden is neither.
    categories = {
        m["category"] for m in metrics.values()
        if m["key"].startswith("joint_understanding_")
    }
    assert categories == {
        "joint_understanding",
        "joint_understanding_quality",
        "joint_understanding_burden",
    }


def test_a_reasked_question_is_counted_as_burden(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        for index in range(2):
            conn.execute(
                """INSERT INTO joint_understanding_translation
                    (joint_understanding_id, system_id, purpose_summary,
                     statements_json, options_json, open_unknowns,
                     decision_question, ask_developer, is_mock, created_at)
                   VALUES (?, ?, '', '[]', '[]', '[]', 'どちらにしますか', 1, 0, ?)""",
                (ju_id, system_id, now + index),
            )

    body = _lineage(admin_client, headers)
    kinds = _kinds(body)
    assert kinds.count("question_asked") == 1
    assert kinds.count("question_reasked") == 1
    assert body["burdens"][0]["reasks"] == 1
    reask = _metrics(admin_client, headers)["joint_understanding_question_reask_rate"]
    assert reask["category"] == "joint_understanding_burden"
    assert reask["value"] == 1.0


def test_a_withdrawn_question_is_distinguished_from_one_never_asked(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        for index, ask in enumerate((1, 0)):
            conn.execute(
                """INSERT INTO joint_understanding_translation
                    (joint_understanding_id, system_id, purpose_summary,
                     statements_json, options_json, open_unknowns,
                     decision_question, ask_developer, is_mock, created_at)
                   VALUES (?, ?, '', '[]', '[]', '[]', NULL, ?, 0, ?)""",
                (ju_id, system_id, ask, now + index),
            )
    kinds = _kinds(_lineage(admin_client, headers))
    assert kinds.count("question_asked") == 1
    assert kinds.count("question_withdrawn") == 1


def test_lineage_is_deterministic_and_system_isolated(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    unknown_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="unknown",
        statement="特定できなかった", evidence=[], intelligence_run_id=run_id,
    )
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="fact",
        statement="判明した", supersedes_finding_id=unknown_id,
        intelligence_run_id=run_id,
    )

    first = _lineage(admin_client, headers)
    second = _lineage(admin_client, headers)
    assert first["events"] == second["events"]
    assert first["burdens"] == second["burdens"]

    other = _create_system(admin_client, token, "Other")
    other_headers = _headers(token, other["id"])
    isolated = _lineage(admin_client, other_headers)
    assert isolated["events"] == []
    assert isolated["burdens"] == []


def test_bulk_approval_readiness_reports_counts_never_a_verdict(admin_client):
    """Issue #338's last gate. The number that should gate #311 has to be
    decided from real data; returning a go/no-go here would be exactly the
    self-reported readiness score this issue forbids."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    empty = _lineage(admin_client, headers)
    assert [c["key"] for c in empty["bulk_approval_readiness"]] == list(
        BULK_APPROVAL_CRITERIA
    )
    assert all(
        c["verdict"] == "unmeasured" for c in empty["bulk_approval_readiness"]
    )

    qa = _create_qa(admin_client, headers, session_id)
    _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    observed = _lineage(admin_client, headers)
    for criterion in observed["bulk_approval_readiness"]:
        assert criterion["verdict"] in BULK_APPROVAL_VERDICTS
        if criterion["key"] == "observed_sessions":
            assert criterion["verdict"] == "threshold_unset"
            assert "unset" in criterion["note"]
        else:
            assert criterion["verdict"] == "unmeasured"
            assert "explicit observation contract" in criterion["note"]
    sessions = next(
        c for c in observed["bulk_approval_readiness"] if c["key"] == "observed_sessions"
    )
    assert sessions["observed"] == 1


def test_lineage_metrics_are_unmeasured_without_observations(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    metrics = _metrics(admin_client, headers)
    lineage_keys = [
        key for key, metric in metrics.items()
        if metric["category"] in (
            "joint_understanding_quality", "joint_understanding_burden",
        )
    ]
    assert len(lineage_keys) == 10
    for key in lineage_keys:
        assert metrics[key]["status"] == "unmeasured", key
        assert metrics[key]["value"] is None
        assert metrics[key]["unmeasured_reason"] in (
            "no_observations", "explicit_observation_contract_pending",
        )


def test_lineage_can_be_scoped_to_one_interview_session(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    other_session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    other_qa = _create_qa(admin_client, headers, other_session, "別の質問")
    _open_ju(admin_client, headers, other_session, "qa", other_qa["id"])

    assert len(_lineage(admin_client, headers)["burdens"]) == 2
    scoped = _lineage(admin_client, headers, session_id=session_id)
    assert len(scoped["burdens"]) == 1
    assert scoped["burdens"][0]["session_id"] == session_id
