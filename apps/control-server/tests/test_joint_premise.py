"""Issue #337: premise, provenance, and decision audit for Joint Understanding.

The bug this issue closes is a gate that could not fail. `_premise_state`
compared the session's pinned `snapshot_id` against the interview session's
current one and returned `"fresh"` for everything else -- including a session
that pinned nothing, an interview session that no longer resolved, and a
pinned snapshot row that had been deleted. It also could not see any of the
changes that do not move a snapshot id: an Intent correction, an Alignment
rebuild replacing the review item, an Inquiry being superseded.

These tests are organised as: the pure verdict table, then the capture and
evaluation against real rows per origin kind, then the provenance boundary,
then the decision audit (close actor / basis rules / adoption lineage), then
migration compatibility.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from joint_understanding_helpers import insert_producer_finding

from app.joint_premise import (
    ASSERTABLE_PREMISE_STATES,
    JOINT_PREMISE_VERSION,
    PREMISE_REASON_CODES,
    PREMISE_STATES,
    PRODUCER_KINDS,
    PRODUCER_ROLES,
    BasisFinding,
    JointPremiseError,
    PremiseBundle,
    PremiseFacts,
    adoption_state,
    compute_intent_content_hash,
    compute_qa_content_hash,
    developer_producer,
    evaluate_joint_premise,
    system_producer,
    validate_basis,
    validate_provenance,
)


# --- The pure verdict table ----------------------------------------------------


def _bundle(**overrides) -> PremiseBundle:
    base = dict(
        tracking_version=JOINT_PREMISE_VERSION,
        snapshot_id=1,
        commit_sha="abc123",
        revision_id=7,
        content_hash="hash-A",
        capability_digest="cap-A",
        intent_digest=None,
        review_subject_id=None,
    )
    base.update(overrides)
    return PremiseBundle(**base)


def _facts(**overrides) -> PremiseFacts:
    base = dict(
        snapshot_exists=True,
        commit_sha="abc123",
        origin_exists=True,
        origin_superseded=False,
        current_origin_id=42,
        revision_id=7,
        content_hash="hash-A",
        capability_digest="cap-A",
        intent_digest=None,
    )
    base.update(overrides)
    return PremiseFacts(**base)


def test_every_verdict_and_reason_belongs_to_its_finite_set():
    cases = [
        (_bundle(tracking_version=None), _facts()),
        (_bundle(commit_sha=None), _facts()),
        (_bundle(snapshot_id=None), _facts()),
        (_bundle(), None),
        (_bundle(), _facts(snapshot_exists=False)),
        (_bundle(), _facts(origin_exists=False)),
        (_bundle(), _facts(origin_superseded=True)),
        (_bundle(), _facts(commit_sha="def456")),
        (_bundle(), _facts(content_hash="hash-B")),
        (_bundle(), _facts(capability_digest="cap-B")),
        (_bundle(), _facts(intent_digest="intent-B")),
        (_bundle(), _facts()),
    ]
    for bundle, facts in cases:
        verdict = evaluate_joint_premise(bundle=bundle, facts=facts)
        assert verdict.state in PREMISE_STATES
        assert verdict.reason_code is None or verdict.reason_code in PREMISE_REASON_CODES


def test_a_matching_premise_is_current_and_assertable():
    verdict = evaluate_joint_premise(bundle=_bundle(), facts=_facts())
    assert verdict.state == "current"
    assert verdict.reason_code is None
    assert verdict.is_assertable


def test_an_uncaptured_premise_is_invalid_not_fresh():
    """The first completion gate: a premise that was never recorded is not a
    premise that still holds.

    Both of these returned 'fresh' before Issue #337 and were therefore
    accepted as the basis of an adoption or a decision.
    """
    not_captured = evaluate_joint_premise(bundle=_bundle(tracking_version=None), facts=_facts())
    assert (not_captured.state, not_captured.reason_code) == ("invalid", "premise_not_captured")
    assert not not_captured.is_assertable

    # A session opened while the interview session had no snapshot: the commit
    # could not be captured, so nothing is comparable.
    incomplete = evaluate_joint_premise(
        bundle=_bundle(snapshot_id=None, commit_sha=None), facts=_facts(),
    )
    assert (incomplete.state, incomplete.reason_code) == ("invalid", "premise_incomplete")


def test_a_disappeared_premise_is_missing_not_invalid():
    """A complete bundle whose ground is gone is 'missing' -- a different
    recovery path from 'invalid', which was never comparable at all."""
    # ON DELETE SET NULL nulled the pinned snapshot id on a complete bundle.
    deleted_row = evaluate_joint_premise(bundle=_bundle(snapshot_id=None), facts=_facts())
    assert (deleted_row.state, deleted_row.reason_code) == ("missing", "pinned_snapshot_removed")

    # The interview session itself no longer resolves. `joint_understanding_
    # session.session_id` is ON DELETE CASCADE, so today this is unreachable
    # through the API -- the branch stays because the pre-#337 code returned
    # 'fresh' here, and a gate whose unreachable path is the permissive one is
    # one schema change away from being wrong.
    no_session = evaluate_joint_premise(bundle=_bundle(), facts=None)
    assert (no_session.state, no_session.reason_code) == ("missing", "pinned_snapshot_removed")

    gone = evaluate_joint_premise(bundle=_bundle(), facts=_facts(origin_exists=False))
    assert (gone.state, gone.reason_code) == ("missing", "origin_removed")


def test_the_same_commit_repinned_under_a_new_snapshot_is_not_stale():
    """Issue #323's rule on the snapshot axis: re-pinning identical source
    must not expire a conversation, so the COMMIT decides, not the id."""
    verdict = evaluate_joint_premise(
        bundle=_bundle(snapshot_id=1), facts=_facts(commit_sha="abc123"),
    )
    assert verdict.state == "current"


def test_stale_reasons_report_the_root_cause_not_the_symptom():
    """Most specific cause first, the same ordering Issue #323 uses."""
    superseded = evaluate_joint_premise(bundle=_bundle(), facts=_facts(origin_superseded=True))
    assert (superseded.state, superseded.reason_code) == ("stale", "origin_superseded")

    moved = evaluate_joint_premise(bundle=_bundle(), facts=_facts(commit_sha="def456"))
    assert (moved.state, moved.reason_code) == ("stale", "pinned_commit_changed")

    # The linked Intent and the Capability scope are inputs to (or siblings of)
    # the content hash, so they are named ahead of the generic content change.
    intent = evaluate_joint_premise(
        bundle=_bundle(), facts=_facts(intent_digest="intent-B", content_hash="hash-B"),
    )
    assert intent.reason_code == "linked_intent_changed"

    capability = evaluate_joint_premise(
        bundle=_bundle(), facts=_facts(capability_digest="cap-B", content_hash="hash-B"),
    )
    assert capability.reason_code == "capability_scope_changed"

    content = evaluate_joint_premise(bundle=_bundle(), facts=_facts(content_hash="hash-B"))
    assert content.reason_code == "origin_content_changed"


def test_rebuilding_the_understanding_alone_does_not_expire_a_conversation():
    """`revision_id` is captured for audit and lineage, never as a staleness
    input -- otherwise every Understanding rebuild would expire every session,
    including the ones whose code never moved (Issue #323)."""
    verdict = evaluate_joint_premise(bundle=_bundle(revision_id=7), facts=_facts(revision_id=99))
    assert verdict.state == "current"


def test_only_current_is_assertable():
    assert ASSERTABLE_PREMISE_STATES == ("current",)


def test_intent_content_hash_ignores_the_confirmation_status():
    """An `intent`-origin session exists to help DECIDE that item, so
    confirming it must not invalidate the session's own premise -- the same
    reasoning Issue #308 used to exclude `confirmation_id`."""
    before = compute_intent_content_hash(field="goal", value_text="速く失敗させたい")
    after = compute_intent_content_hash(field="goal", value_text="速く失敗させたい")
    assert before == after
    assert before != compute_intent_content_hash(field="goal", value_text="別の目的")


def test_qa_content_hash_ignores_the_answer():
    """A `qa`-origin session is premised on the QUESTION. Digesting the answer
    would make the session stale the moment its own reflux lands."""
    assert compute_qa_content_hash(question_text="なぜ3回?") == compute_qa_content_hash(
        question_text="なぜ3回?",
    )
    assert compute_qa_content_hash(question_text="なぜ3回?") != compute_qa_content_hash(
        question_text="なぜ5回?",
    )


# --- Provenance ----------------------------------------------------------------


def test_each_producer_writes_exactly_one_role():
    assert set(PRODUCER_ROLES) | {"legacy"} == set(PRODUCER_KINDS)
    for producer_kind, role in PRODUCER_ROLES.items():
        actor_kind = "user" if producer_kind == "developer_api" else "system"
        actor_user_id = 5 if producer_kind == "developer_api" else None
        validate_provenance(
            producer_kind=producer_kind, origin_role=role,
            actor_kind=actor_kind, actor_user_id=actor_user_id,
        )
        wrong_role = "developer" if role != "developer" else "investigation"
        with pytest.raises(JointPremiseError, match="may only write"):
            validate_provenance(
                producer_kind=producer_kind, origin_role=wrong_role,
                actor_kind=actor_kind, actor_user_id=actor_user_id,
            )


def test_a_system_producer_can_never_name_a_human_actor():
    with pytest.raises(JointPremiseError, match="must not name a human actor"):
        validate_provenance(
            producer_kind="investigation_loop", origin_role="investigation",
            actor_kind="system", actor_user_id=5,
        )
    with pytest.raises(JointPremiseError, match="actor_kind='system'"):
        validate_provenance(
            producer_kind="translator", origin_role="translation",
            actor_kind="user", actor_user_id=5,
        )


def test_legacy_provenance_is_read_only():
    """'legacy' describes a row written before provenance existed. Writing it
    would be claiming that unknown provenance is a valid new record."""
    with pytest.raises(JointPremiseError, match="read-only"):
        validate_provenance(
            producer_kind="legacy", origin_role="developer",
            actor_kind="user", actor_user_id=1,
        )
    with pytest.raises(JointPremiseError, match="read-only"):
        validate_provenance(
            producer_kind="developer_api", origin_role="developer",
            actor_kind="legacy", actor_user_id=1,
        )


def test_producer_contexts_carry_their_own_role():
    assert system_producer("investigation_loop").origin_role == "investigation"
    assert system_producer("translator").origin_role == "translation"
    with pytest.raises(JointPremiseError):
        system_producer("developer_api")
    developer = developer_producer(user_id=3, username="ura")
    assert (developer.origin_role, developer.actor_kind) == ("developer", "user")


# --- Outcome basis -------------------------------------------------------------


def _basis(**overrides) -> BasisFinding:
    base = dict(
        id=1, origin_role="investigation", claim_kind="hypothesis",
        is_mock=False, superseded=False, intelligence_run_id=9, has_evidence=True,
    )
    base.update(overrides)
    return BasisFinding(**base)


def test_basis_rules_reject_each_disqualifying_property():
    ok = {1: _basis()}
    assert validate_basis(
        outcome="hypothesis_adopted", outcome_finding_ids=[1], findings_by_id=ok,
    ) == ("", "")

    for expected_code, findings in (
        ("basis_foreign_session", {}),
        ("basis_superseded", {1: _basis(superseded=True)}),
        ("basis_mock", {1: _basis(is_mock=True)}),
        ("basis_not_hypothesis", {1: _basis(claim_kind="fact")}),
        ("basis_not_hypothesis", {1: _basis(origin_role="developer", intelligence_run_id=None)}),
        ("basis_unverified", {1: _basis(intelligence_run_id=None)}),
        ("basis_unverified", {1: _basis(has_evidence=False)}),
    ):
        code, message = validate_basis(
            outcome="hypothesis_adopted", outcome_finding_ids=[1], findings_by_id=findings,
        )
        assert code == expected_code, (expected_code, message)
        assert message


def test_a_decision_may_rest_on_any_current_finding():
    """`decided` is the developer's own value judgement, so it is not
    restricted to hypotheses -- but a corrected or mock basis is still refused."""
    assert validate_basis(
        outcome="decided", outcome_finding_ids=[1],
        findings_by_id={1: _basis(claim_kind="fact")},
    ) == ("", "")
    code, _ = validate_basis(
        outcome="decided", outcome_finding_ids=[1],
        findings_by_id={1: _basis(claim_kind="fact", superseded=True)},
    )
    assert code == "basis_superseded"


def test_non_asserting_outcomes_need_no_basis():
    for outcome in ("understood", "doubt_resolved", "handed_off", "abandoned"):
        assert validate_basis(
            outcome=outcome, outcome_finding_ids=[], findings_by_id={},
        ) == ("", "")


def test_adoption_state_is_first_match():
    assert adoption_state(premise_state="current", basis_superseded=False) == "provisional"
    assert adoption_state(premise_state="stale", basis_superseded=False) == "reconfirmation_required"
    assert adoption_state(premise_state="invalid", basis_superseded=False) == "reconfirmation_required"
    # A withdrawn basis outranks the premise: the claim itself is gone.
    assert adoption_state(premise_state="current", basis_superseded=True) == "basis_withdrawn"
    assert adoption_state(premise_state="stale", basis_superseded=True) == "basis_withdrawn"


# --- Route-level: capture and evaluation against real rows ---------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-joint-premise-test.db"))
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


def _setup(client, *, commit_sha="abc123", name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    headers = _headers(token, system["id"])
    snapshot_id = _insert_snapshot(system["id"], commit_sha)
    session_id = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    return token, system["id"], headers, snapshot_id, session_id


def _open_ju(client, headers, session_id, origin_kind, origin_id, **overrides):
    body = {
        "origin_kind": origin_kind, "origin_id": origin_id,
        "trigger": "explicit_request", "question_text": "根拠が分かりません",
    }
    body.update(overrides)
    r = client.post(
        f"/interview/sessions/{session_id}/joint-understanding", json=body, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _premise(client, headers, ju_id):
    r = client.get(f"/joint-understanding/{ju_id}", headers=headers)
    assert r.status_code == 200, r.text
    session = r.json()["session"]
    return session["premise_state"], session["premise_reason"]


def test_creation_captures_the_shared_bundle(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    session = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]

    assert session["premise_tracking_version"] == JOINT_PREMISE_VERSION
    assert session["premise_snapshot_id"] == snapshot_id
    # The pinned COMMIT is the addition a code investigation needs.
    assert session["premise_commit_sha"] == "abc123"
    assert session["premise_captured_at"] is not None
    assert session["premise_state"] == "current"
    assert session["premise_reason"] is None


def test_an_intent_correction_in_the_same_snapshot_is_detected(admin_client):
    """The change the pre-#337 gate could not see at all: a correction that
    supersedes the Intent row without touching the snapshot."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    intent = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "失敗を早く見せたい"}, headers=headers,
    ).json()
    ju_id = _open_ju(
        admin_client, headers, session_id, "intent", intent["id"],
    )["session"]["id"]
    assert _premise(admin_client, headers, ju_id) == ("current", None)

    r = admin_client.post(
        f"/interview/intent/{intent['id']}/correct",
        json={"value_text": "失敗を早く直したい"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert _premise(admin_client, headers, ju_id) == ("stale", "origin_content_changed")


def test_confirming_the_origin_intent_does_not_expire_the_session(admin_client):
    """The session exists to help decide that Intent item, so its own success
    must not invalidate its premise and block recording the decision."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    intent = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "失敗を早く見せたい", "status": "undecided"},
        headers=headers,
    ).json()
    ju_id = _open_ju(
        admin_client, headers, session_id, "intent", intent["id"],
    )["session"]["id"]

    r = admin_client.post(f"/interview/intent/{intent['id']}/confirm", headers=headers)
    assert r.status_code == 200, r.text
    assert _premise(admin_client, headers, ju_id) == ("current", None)


def test_a_qa_answer_revision_keeps_the_question_premise_current(admin_client):
    """`interview_qa` corrects additively, so answering (or re-answering)
    leaves the pinned row superseded. The premise is the QUESTION, which the
    successor row still carries, so the session stays usable."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]

    for answer in ("3回です", "実は5回です"):
        r = admin_client.post(
            f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
            json={"answer_text": answer, "actor": "root"}, headers=headers,
        )
        assert r.status_code == 200, r.text
    assert _premise(admin_client, headers, ju_id) == ("current", None)


def test_a_deleted_pinned_snapshot_is_missing(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]

    from app.db import get_conn

    # Re-point the interview session first: the FK is ON DELETE SET NULL, so
    # deleting the snapshot would otherwise cascade the session's own column.
    other = _insert_snapshot(system_id, "abc123")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?", (other, session_id),
        )
        conn.execute("DELETE FROM repository_snapshots WHERE id = ?", (snapshot_id,))

    assert _premise(admin_client, headers, ju_id) == ("missing", "pinned_snapshot_removed")
    # And the gates refuse it, where the pre-#337 code reported 'fresh'.
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/reflux", headers=headers,
    ).status_code == 409


def test_a_legacy_row_without_a_bundle_is_invalid_not_fresh(admin_client):
    """Compatibility means the old row stays readable and keeps its recorded
    outcome -- never that it is promoted to a satisfied premise."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """UPDATE joint_understanding_session
               SET premise_tracking_version = NULL, premise_commit_sha = NULL,
                   premise_content_hash = NULL, premise_capability_digest = NULL,
                   premise_captured_at = NULL
               WHERE id = ?""",
            (ju_id,),
        )

    state, reason = _premise(admin_client, headers, ju_id)
    assert (state, reason) == ("invalid", "premise_not_captured")
    # Still readable, and non-asserting closes still work.
    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={"outcome": "abandoned", "outcome_reason": "前提が追えないので開き直す"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["outcome_premise_state"] == "invalid"


def test_an_invalid_premise_blocks_reflux_and_asserting_outcomes(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]
    run_id = _insert_run(system_id, snapshot_id)
    finding_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE joint_understanding_session SET premise_tracking_version = NULL WHERE id = ?",
            (ju_id,),
        )

    reflux = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert reflux.status_code == 409, reflux.text
    assert reflux.json()["detail"]["code"] == "joint_understanding_premise_not_current"
    assert reflux.json()["detail"]["premise_reason"] == "premise_not_captured"

    closed = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "decided", "outcome_reason": "決めた",
            "outcome_finding_ids": [finding_id],
        },
        headers=headers,
    )
    assert closed.status_code == 409, closed.text


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


# --- The decision audit --------------------------------------------------------


def test_the_close_decision_survives_a_reload(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]
    run_id = _insert_run(system_id, snapshot_id)
    finding_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "decided", "outcome_reason": "現状維持でよいと判断した",
            "outcome_finding_ids": [finding_id],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    reloaded = admin_client.get(
        f"/joint-understanding/{ju_id}", headers=headers,
    ).json()["session"]
    assert reloaded["outcome"] == "decided"
    assert reloaded["outcome_reason"] == "現状維持でよいと判断した"
    assert reloaded["outcome_finding_ids"] == [finding_id]
    assert reloaded["outcome_premise_state"] == "current"
    assert reloaded["closed_by_actor_kind"] == "user"
    assert reloaded["closed_by_username"] == "root"


def test_a_close_without_a_stated_judgement_is_refused(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close", json={"outcome": "understood"}, headers=headers,
    )
    assert r.status_code == 422, r.text


def test_a_request_body_cannot_attribute_an_action_to_someone_else(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/actions",
        json={"action_kind": "compare_options", "actor": "someone-else"}, headers=headers,
    )
    assert r.status_code == 201, r.text
    action = r.json()["actions"][0]
    # The label is kept, but the identity is the authenticated caller.
    assert action["actor"] == "someone-else"
    assert action["actor_kind"] == "user"
    assert action["actor_username"] == "root"


def test_an_adoption_requires_reconfirmation_once_its_premise_moves(admin_client):
    """The lineage Issue #337 adds: a provisional adoption is brought back for
    re-confirmation instead of aging into something indistinguishable from a
    fact."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]
    run_id = _insert_run(system_id, snapshot_id)
    hypothesis_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="打ち切りは設定由来と思われる", intelligence_run_id=run_id,
    )

    r = admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "hypothesis_adopted", "outcome_reason": "暫定でこの説を採る",
            "outcome_finding_ids": [hypothesis_id],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert detail["hypothesis_adoptions"][0]["state"] == "provisional"

    # Move the premise: a new commit under the interview session.
    from app.db import get_conn

    moved = _insert_snapshot(system_id, "def456")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?", (moved, session_id),
        )

    after = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert after["session"]["premise_state"] == "stale"
    assert after["hypothesis_adoptions"][0]["state"] == "reconfirmation_required"
    # The recorded verdict at close time is unchanged -- it is history.
    assert after["session"]["outcome_premise_state"] == "current"


def test_an_adoption_is_withdrawn_when_the_investigation_corrects_its_basis(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]
    run_id = _insert_run(system_id, snapshot_id)
    hypothesis_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="hypothesis",
        statement="打ち切りは設定由来と思われる", intelligence_run_id=run_id,
    )
    admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "hypothesis_adopted", "outcome_reason": "暫定採用",
            "outcome_finding_ids": [hypothesis_id],
        },
        headers=headers,
    )
    # A later round corrects the hypothesis it rested on.
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, claim_kind="fact",
        statement="設定ではなく定数で決まっていた", intelligence_run_id=run_id,
        supersedes_finding_id=hypothesis_id,
    )

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    assert detail["hypothesis_adoptions"][0]["state"] == "basis_withdrawn"


def test_a_basis_from_another_system_is_unreachable(admin_client):
    """Findings are session-scoped and sessions are System-scoped, so a
    cross-System basis cannot even be named -- the session 404s first."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])["session"]["id"]
    run_id = _insert_run(system_id, snapshot_id)
    finding_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )

    other = _create_system(admin_client, token, "Other")
    other_headers = _headers(token, other["id"])
    for path in (
        f"/joint-understanding/{ju_id}",
        f"/joint-understanding/{ju_id}/reflux",
    ):
        method = admin_client.get if path.endswith(str(ju_id)) else admin_client.post
        assert method(path, headers=other_headers).status_code == 404
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/close",
        json={
            "outcome": "decided", "outcome_reason": "決めた",
            "outcome_finding_ids": [finding_id],
        },
        headers=other_headers,
    ).status_code == 404
