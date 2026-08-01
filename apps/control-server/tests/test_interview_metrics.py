"""Contract tests for deterministic Interview UX metrics (Issue #309)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-metrics.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as client:
        yield client


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"username": "root", "password": "s3cret"},
    )
    assert response.status_code == 200, response.text
    return response.cookies["probe_session"]


def _setup_system(client: TestClient, token: str, name: str):
    auth = {"Authorization": f"Bearer {token}"}
    system_response = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers=auth,
    )
    assert system_response.status_code == 201, system_response.text
    system_id = system_response.json()["id"]
    headers = {**auth, "X-Probe-System-Id": str(system_id)}

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        snapshot_id = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, f"sha-{name}", now, now),
        ).lastrowid
    session_response = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": name},
        headers=headers,
    )
    assert session_response.status_code == 201, session_response.text
    return system_id, snapshot_id, session_response.json()["id"], headers


def _insert_qa(
    conn,
    *,
    system_id: int,
    session_id: int,
    question_text: str,
    route_category=None,
    answer_unknown=None,
    answered_at=None,
    status="open",
):
    return conn.execute(
        """INSERT INTO interview_qa
            (session_id, system_id, question_text, question_category,
             question_source, answer_unknown, status, route_category,
             created_at, answered_at)
           VALUES (?, ?, ?, 'general', 'dialogue', ?, ?, ?, ?, ?)""",
        (
            session_id,
            system_id,
            question_text,
            answer_unknown,
            status,
            route_category,
            time.time(),
            answered_at,
        ),
    ).lastrowid


def _insert_run(conn, *, system_id: int, snapshot_id: int, is_mock: int = 0):
    now = time.time()
    return conn.execute(
        """INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             is_mock, started_at, completed_at)
           VALUES (?, ?, 'alignment_build', 'test', 'test', 'v1', 'v1',
                   'reasoning_llm', 'completed', ?, ?, ?)""",
        (system_id, snapshot_id, is_mock, now, now),
    ).lastrowid


def _insert_alignment(
    conn,
    *,
    system_id: int,
    session_id: int,
    snapshot_id: int,
    run_id: int,
    runtime_check: str,
    is_mock: int = 0,
    superseded: int = 0,
    review_category: str = "must_review",
):
    now = time.time()
    return conn.execute(
        """INSERT INTO alignment_item
            (session_id, system_id, snapshot_id, current_claim,
             current_evidence, alignment_state, risk_flags, confidence,
             review_category, reason_code, user_reason, runtime_check,
             intelligence_run_id, is_mock, superseded, created_at, updated_at)
           VALUES (?, ?, ?, 'claim', '[]', 'conflict', '[]', 'confirmed',
                   ?, 'conflict_detected', 'reason', ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            system_id,
            snapshot_id,
            review_category,
            runtime_check,
            run_id,
            is_mock,
            superseded,
            now,
            now,
        ),
    ).lastrowid


def _metric(payload, key):
    return next(item for item in payload["metrics"] if item["key"] == key)


def test_empty_catalog_marks_missing_or_zero_denominator_unmeasured(admin_client):
    token = _login(admin_client)
    system_id, _snapshot_id, _session_id, headers = _setup_system(
        admin_client, token, "empty",
    )

    response = admin_client.get("/interview/metrics", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["system_id"] == system_id
    assert body["schema_version"] == "interview-metrics-v2"
    assert body["sessions_observed"] == 1
    assert body["events_observed"] == 0

    for key in (
        "answers_per_understanding_update",
        "unknown_answer_rate",
        "evidence_detail_expansion_rate",
        "inquiry_resolution_rate",
        "incorrect_answer_confirmation_rate",
    ):
        metric = _metric(body, key)
        assert metric["status"] == "unmeasured"
        assert metric["value"] is None
        assert metric["unmeasured_reason"]

    assert _metric(body, "answers_per_understanding_update")[
        "unmeasured_reason"
    ] == "answer_to_revision_lineage_not_recorded"
    incorrect = _metric(body, "incorrect_answer_confirmation_rate")
    assert incorrect["guardrail"] is True
    assert "意味的" in incorrect["description"]
    assert incorrect["sources"]
    assert incorrect["formula"]


def test_event_metrics_are_idempotent_and_system_isolated(admin_client):
    token = _login(admin_client)
    system_a, _snapshot_a, session_a, headers_a = _setup_system(
        admin_client, token, "A",
    )
    system_b, _snapshot_b, session_b, headers_b = _setup_system(
        admin_client, token, "B",
    )

    from app.db import get_conn

    with get_conn() as conn:
        qa_a = _insert_qa(
            conn,
            system_id=system_a,
            session_id=session_a,
            question_text="implementation?",
            route_category="system_researchable",
        )
        qa_b = _insert_qa(
            conn,
            system_id=system_b,
            session_id=session_b,
            question_text="intent?",
            route_category="human_only",
        )

    def post(headers, **event):
        payload = {
            "schema_version": "interview-metric-event-v1",
            **event,
        }
        return admin_client.post(
            "/interview/metric-events",
            json=payload,
            headers=headers,
        )

    events_a = [
        {
            "event_key": "a-review-start",
            "session_id": session_a,
            "event_type": "review_started",
            "target_kind": "session",
            "target_id": session_a,
        },
        {
            "event_key": "a-review-abandon",
            "session_id": session_a,
            "event_type": "review_abandoned",
            "target_kind": "session",
            "target_id": session_a,
        },
        {
            "event_key": "a-question",
            "session_id": session_a,
            "event_type": "question_presented",
            "target_kind": "qa",
            "target_id": qa_a,
        },
        {
            "event_key": "a-evidence-available",
            "session_id": session_a,
            "event_type": "evidence_available",
            "target_kind": "qa",
            "target_id": qa_a,
        },
        {
            "event_key": "a-evidence-expanded",
            "session_id": session_a,
            "event_type": "evidence_expanded",
            "target_kind": "qa",
            "target_id": qa_a,
        },
    ]
    for event in events_a:
        response = post(headers_a, **event)
        assert response.status_code == 201, response.text

    # Exact retry returns the original row and does not increase event count.
    retry = post(headers_a, **events_a[0])
    assert retry.status_code == 201
    assert retry.json()["event_key"] == events_a[0]["event_key"]

    # A different transport key for the same semantic observation is also
    # idempotent and returns the first persisted event.
    semantic_retry = post(
        headers_a,
        **{**events_a[0], "event_key": "a-review-start-from-remount"},
    )
    assert semantic_retry.status_code == 201
    assert semantic_retry.json()["id"] == retry.json()["id"]
    assert semantic_retry.json()["event_key"] == events_a[0]["event_key"]

    for event in (
        {
            "event_key": "a-review-start",  # same key is valid in another System
            "session_id": session_b,
            "event_type": "review_started",
            "target_kind": "session",
            "target_id": session_b,
        },
        {
            "event_key": "b-question",
            "session_id": session_b,
            "event_type": "question_presented",
            "target_kind": "qa",
            "target_id": qa_b,
        },
    ):
        assert post(headers_b, **event).status_code == 201

    metrics_a = admin_client.get("/interview/metrics", headers=headers_a).json()
    metrics_b = admin_client.get("/interview/metrics", headers=headers_b).json()
    assert metrics_a["events_observed"] == 5
    assert metrics_b["events_observed"] == 2

    assert _metric(metrics_a, "review_abandonment_rate")["value"] == 1.0
    assert _metric(metrics_b, "review_abandonment_rate")["value"] == 0.0
    assert _metric(metrics_a, "evidence_detail_expansion_rate")["value"] == 1.0
    assert _metric(metrics_b, "evidence_detail_expansion_rate")["status"] == "unmeasured"
    assert _metric(metrics_a, "implementation_question_transfer_rate")["value"] == 1.0
    assert _metric(metrics_b, "implementation_question_transfer_rate")["value"] == 0.0

    # A target in another System is never accepted through A's context.
    cross_system = post(
        headers_a,
        event_key="cross-system",
        session_id=session_a,
        event_type="question_presented",
        target_kind="qa",
        target_id=qa_b,
    )
    assert cross_system.status_code == 404

    conflict = post(
        headers_a,
        event_key="a-review-start",
        session_id=session_a,
        event_type="review_completed",
        target_kind="session",
        target_id=session_a,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "metric_event_key_conflict"


def test_deterministic_db_metrics_and_non_causal_metrics(admin_client):
    token = _login(admin_client)
    system_id, snapshot_id, session_1, headers = _setup_system(
        admin_client, token, "metrics",
    )

    # A second session proves identical questions are compared only within a
    # session, never merely because they share a System.
    second_session_response = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "second"},
        headers=headers,
    )
    assert second_session_response.status_code == 201
    session_2 = second_session_response.json()["id"]

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        qa_1 = _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_1,
            question_text="Exact question",
            answer_unknown=0,
            answered_at=now,
            status="answered",
        )
        _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_1,
            question_text="Exact question",
            answer_unknown=1,
            answered_at=now + 1,
            status="unconfirmed",
        )
        _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_2,
            question_text="Exact question",
        )
        # Legacy revised history has no exact unknown provenance and must be
        # excluded from the denominator instead of guessed as a known answer.
        _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_1,
            question_text="legacy",
            answer_unknown=None,
            answered_at=now,
            status="revised",
        )

        intent_old = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 decision_method, created_at, updated_at)
               VALUES (?, ?, 'goal', 'old', 'confirmed', 'user', 'manual', ?, ?)""",
            (session_1, system_id, now, now),
        ).lastrowid
        intent_new = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 decision_method, created_at, updated_at)
               VALUES (?, ?, 'goal', 'new', 'confirmed', 'user', 'manual', ?, ?)""",
            (session_1, system_id, now + 1, now + 1),
        ).lastrowid
        conn.execute(
            "UPDATE interview_intent_item SET superseded_by_id = ? WHERE id = ?",
            (intent_new, intent_old),
        )

        run_id = _insert_run(
            conn,
            system_id=system_id,
            snapshot_id=snapshot_id,
        )
        _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_1,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="mismatch",
        )
        _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_1,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="match",
        )
        # Neither stale history nor mock output belongs in the measured
        # Runtime Reality Check cohort.
        _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_1,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="mismatch",
            superseded=1,
        )
        _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_1,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="mismatch",
            is_mock=1,
        )

        proposal_ids = []
        for index in range(2):
            proposal_ids.append(
                conn.execute(
                    """INSERT INTO interview_proposal
                        (session_id, system_id, snapshot_id, intelligence_run_id,
                         path, qualified_name, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_1,
                        system_id,
                        snapshot_id,
                        run_id,
                        f"src/p{index}.py",
                        f"p{index}.f",
                        now,
                        now,
                    ),
                ).lastrowid
            )
        for proposal_id in proposal_ids:
            conn.execute(
                """INSERT INTO interview_proposal_decision
                    (proposal_id, session_id, system_id, decision,
                     decision_method, actor, decided_at)
                   VALUES (?, ?, ?, 'approved', 'manual', 'dev', ?)""",
                (proposal_id, session_1, system_id, now + 1),
            )
        conn.execute(
            """INSERT INTO interview_proposal_decision
                (proposal_id, session_id, system_id, decision,
                 decision_method, actor, decided_at)
               VALUES (?, ?, ?, 'rejected', 'manual', 'dev', ?)""",
            (proposal_ids[0], session_1, system_id, now + 2),
        )

        inquiry_id = conn.execute(
            """INSERT INTO interview_inquiry
                (session_id, system_id, origin_kind, origin_id, status,
                 created_at, updated_at, closed_at)
               VALUES (?, ?, 'qa', ?, 'resolved', ?, ?, ?)""",
            (session_1, system_id, qa_1, now + 2, now + 2, now + 2),
        ).lastrowid
        conn.execute(
            """INSERT INTO interview_inquiry_transition
                (inquiry_id, system_id, from_status, to_status, created_at)
               VALUES (?, ?, 'open', 'resolved', ?)""",
            (inquiry_id, system_id, now + 2),
        )
        conn.execute(
            "UPDATE interview_qa SET answered_at = ? WHERE id = ?",
            (now + 3, qa_1),
        )

        # A completed refresh exists, but it stores no incorporated answer
        # IDs. The metric must remain unmeasured rather than divide global
        # counts by this row.
        conn.execute(
            """INSERT INTO interview_refresh_job
                (session_id, system_id, trigger_kind, base_answer_marker,
                 status, result_revision_id, created_at, finished_at)
               VALUES (?, ?, 'qa_answer', ?, 'updated', NULL, ?, ?)""",
            (session_1, system_id, now, now, now + 4),
        )

    body = admin_client.get("/interview/metrics", headers=headers).json()
    assert _metric(body, "unknown_answer_rate")["value"] == 0.5
    assert _metric(body, "repeated_question_rate")["value"] == 0.25
    assert _metric(body, "corrected_confirmed_intent_rate")["value"] == 0.5
    assert _metric(body, "runtime_contradiction_rate")["value"] == 0.5
    assert _metric(body, "post_approval_rejection_rate")["value"] == 0.5
    assert _metric(body, "post_approval_rollback_rate")["status"] == "unmeasured"
    assert _metric(body, "post_approval_rejection_or_rollback_rate")[
        "status"
    ] == "unmeasured"
    assert _metric(body, "inquiry_resolution_rate")["value"] == 1.0
    post_inquiry = _metric(body, "post_inquiry_confirmation_rate")
    assert post_inquiry["value"] == 1.0
    assert "UI" in post_inquiry["description"]
    assert _metric(body, "answers_per_understanding_update")["status"] == "unmeasured"
    assert _metric(body, "operations_per_inquiry")["status"] == "unmeasured"


def test_event_targets_cover_alignment_and_inquiry_evidence(admin_client):
    token = _login(admin_client)
    system_id, snapshot_id, session_id, headers = _setup_system(
        admin_client, token, "targets",
    )

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        qa_id = _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_id,
            question_text="origin",
        )
        inquiry_id = conn.execute(
            """INSERT INTO interview_inquiry
                (session_id, system_id, origin_kind, origin_id, status,
                 created_at, updated_at)
               VALUES (?, ?, 'qa', ?, 'open', ?, ?)""",
            (session_id, system_id, qa_id, now, now),
        ).lastrowid
        message_id = conn.execute(
            """INSERT INTO interview_inquiry_message
                (inquiry_id, system_id, role, content, created_at)
               VALUES (?, ?, 'assistant', 'answer', ?)""",
            (inquiry_id, system_id, now),
        ).lastrowid
        run_id = _insert_run(
            conn,
            system_id=system_id,
            snapshot_id=snapshot_id,
        )
        alignment_id = _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_id,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="match",
        )

    for prefix, target_kind, target_id in (
        ("alignment", "alignment_item", alignment_id),
        ("inquiry", "inquiry_message", message_id),
    ):
        for suffix, event_type in (
            ("available", "evidence_available"),
            ("expand", "evidence_expanded"),
        ):
            response = admin_client.post(
                "/interview/metric-events",
                headers=headers,
                json={
                    "schema_version": "interview-metric-event-v1",
                    "event_key": f"{prefix}-{suffix}",
                    "session_id": session_id,
                    "event_type": event_type,
                    "target_kind": target_kind,
                    "target_id": target_id,
                },
            )
            assert response.status_code == 201, response.text

    # Unchanged events are rejected unless the persisted target itself was
    # deterministically classified unchanged.
    invalid = admin_client.post(
        "/interview/metric-events",
        headers=headers,
        json={
            "schema_version": "interview-metric-event-v1",
            "event_key": "not-unchanged",
            "session_id": session_id,
            "event_type": "unchanged_item_presented",
            "target_kind": "alignment_item",
            "target_id": alignment_id,
        },
    )
    assert invalid.status_code == 422


def test_metric_event_prerequisites_are_fail_closed(admin_client):
    token = _login(admin_client)
    system_id, snapshot_id, session_id, headers = _setup_system(
        admin_client, token, "prerequisites",
    )

    from app.db import get_conn

    with get_conn() as conn:
        qa_id = _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_id,
            question_text="evidence",
        )
        run_id = _insert_run(
            conn,
            system_id=system_id,
            snapshot_id=snapshot_id,
        )
        unchanged_id = _insert_alignment(
            conn,
            system_id=system_id,
            session_id=session_id,
            snapshot_id=snapshot_id,
            run_id=run_id,
            runtime_check="match",
            review_category="unchanged",
        )

    def post(*, event_key, event_type, target_kind, target_id):
        return admin_client.post(
            "/interview/metric-events",
            headers=headers,
            json={
                "schema_version": "interview-metric-event-v1",
                "event_key": event_key,
                "session_id": session_id,
                "event_type": event_type,
                "target_kind": target_kind,
                "target_id": target_id,
            },
        )

    dependent_events = (
        ("completed", "review_completed", "session", session_id, "review_started"),
        ("abandoned", "review_abandoned", "session", session_id, "review_started"),
        ("expanded", "evidence_expanded", "qa", qa_id, "evidence_available"),
        (
            "reconfirmed",
            "unchanged_item_reconfirmed",
            "alignment_item",
            unchanged_id,
            "unchanged_item_presented",
        ),
    )
    for event_key, event_type, target_kind, target_id, prerequisite in dependent_events:
        response = post(
            event_key=event_key,
            event_type=event_type,
            target_kind=target_kind,
            target_id=target_id,
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "metric_event_prerequisite_missing"
        assert response.json()["detail"]["required_event_type"] == prerequisite

    for event_key, event_type, target_kind, target_id in (
        ("started", "review_started", "session", session_id),
        ("available", "evidence_available", "qa", qa_id),
        ("presented", "unchanged_item_presented", "alignment_item", unchanged_id),
    ):
        assert post(
            event_key=event_key,
            event_type=event_type,
            target_kind=target_kind,
            target_id=target_id,
        ).status_code == 201

    for event_key, event_type, target_kind, target_id, _prerequisite in dependent_events:
        assert post(
            event_key=event_key,
            event_type=event_type,
            target_kind=target_kind,
            target_id=target_id,
        ).status_code == 201


def test_event_ratios_count_distinct_paired_semantic_facts(admin_client):
    """Legacy duplicates/orphans cannot inflate an event ratio above one."""

    token = _login(admin_client)
    system_id, _snapshot_id, session_id, headers = _setup_system(
        admin_client, token, "paired-ratios",
    )

    from app.db import get_conn

    with get_conn() as conn:
        paired_qa_id = _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_id,
            question_text="paired",
        )
        orphan_qa_id = _insert_qa(
            conn,
            system_id=system_id,
            session_id=session_id,
            question_text="orphan",
        )
        now = time.time()
        events = (
            ("available", "evidence_available", paired_qa_id),
            ("expanded-1", "evidence_expanded", paired_qa_id),
            ("expanded-2", "evidence_expanded", paired_qa_id),
            ("orphan-expanded", "evidence_expanded", orphan_qa_id),
        )
        for event_key, event_type, target_id in events:
            conn.execute(
                """INSERT INTO interview_metric_event
                    (event_key, system_id, session_id, event_type,
                     target_kind, target_id, recorded_at)
                   VALUES (?, ?, ?, ?, 'qa', ?, ?)""",
                (event_key, system_id, session_id, event_type, target_id, now),
            )

    body = admin_client.get("/interview/metrics", headers=headers).json()
    metric = _metric(body, "evidence_detail_expansion_rate")
    assert metric["status"] == "measured"
    assert metric["numerator"] == 1
    assert metric["denominator"] == 1
    assert metric["value"] == 1.0


# --- Issue #341: 要確認 (attention) evaluation ------------------------------


def test_attention_separates_designation_from_judgement(admin_client):
    """`guardrail` designates; `attention` judges. They are not the same field."""

    token = _login(admin_client)
    _system_id, _snapshot_id, _session_id, headers = _setup_system(
        admin_client, token, "attention-empty",
    )

    body = admin_client.get("/interview/metrics", headers=headers).json()

    # Every metric carries a judgement, and only designated guardrails are watched.
    for metric in body["metrics"]:
        assert metric["attention"] is not None
        assert metric["attention"]["watched"] is False or metric["guardrail"] is True

    watched = [m for m in body["metrics"] if m["attention"]["watched"]]
    assert watched, "the shipped policy must watch at least one guardrail"

    # 定期観測: a non-guardrail metric never lights the entry point.
    observation_only = _metric(body, "joint_understanding_investigation_answered_rate")
    assert observation_only["guardrail"] is False
    assert observation_only["attention"]["state"] == "observation_only"
    assert observation_only["attention"]["reason"] == "not_a_notification_target"

    unknown_answer = _metric(body, "unknown_answer_rate")
    assert unknown_answer["attention"]["state"] == "observation_only"

    summary = body["attention"]
    assert summary["state"] == "insufficient_data"
    assert summary["attention_count"] == 0
    assert summary["watched_count"] == len(watched)
    assert summary["policy_version"] == "interview-metric-attention-v1"
    assert len(summary["policy_digest"]) == 64


def test_attention_distinguishes_no_data_from_no_measurement_path(admin_client):
    """未計測 is never a breach, and 母数不足 is not the same as 計測手段なし."""

    token = _login(admin_client)
    _system_id, _snapshot_id, _session_id, headers = _setup_system(
        admin_client, token, "attention-unmeasured",
    )

    body = admin_client.get("/interview/metrics", headers=headers).json()

    # A calculable metric with an empty denominator will resolve with usage.
    resolution = _metric(body, "inquiry_resolution_rate")
    assert resolution["status"] == "unmeasured"
    assert resolution["value"] is None
    assert resolution["attention"]["state"] == "insufficient_data"
    assert resolution["attention"]["reason"] == "no_observations_yet"

    # A metric whose underlying fact is not recorded at all never will.
    incorrect = _metric(body, "incorrect_answer_confirmation_rate")
    assert incorrect["attention"]["state"] == "not_measurable"
    assert incorrect["attention"]["reason"] == "not_recorded"
    assert body["attention"]["not_measurable_count"] >= 1


def test_attention_criterion_fields_are_reported_per_metric(admin_client):
    token = _login(admin_client)
    _system_id, _snapshot_id, _session_id, headers = _setup_system(
        admin_client, token, "attention-criterion",
    )

    body = admin_client.get("/interview/metrics", headers=headers).json()
    attention = _metric(body, "inquiry_resolution_rate")["attention"]
    assert attention["direction"] == "low_is_bad"
    assert attention["min_sample"] >= 1
    assert attention["window"] == "all_time"
    assert attention["trigger"] == "single_breach"
    assert attention["clear_condition"] == "value_within_threshold"
    # #341 deliberately does not pick numeric thresholds.
    assert attention["threshold"] is None


def test_attention_states_follow_the_policy_thresholds():
    """Direction, minimum sample, and the clear condition are all honoured."""

    from app.interview_metric_attention import (
        InterviewMetricAttentionPolicy,
        MetricAttentionRule,
        apply_attention,
    )
    from app.models import InterviewMetricOut

    def rule(key, direction, threshold, min_sample=5):
        return MetricAttentionRule(
            key=key,
            watch=True,
            direction=direction,
            threshold=threshold,
            min_sample=min_sample,
            window="all_time",
            trigger="single_breach",
            clear="value_within_threshold",
        )

    def metric(key, value, sample_size):
        return InterviewMetricOut(
            key=key,
            category="ux_quality",
            guardrail=True,
            description="d",
            formula="f",
            sources=["s"],
            status="measured",
            value=value,
            unit="ratio",
            numerator=int(value * sample_size),
            denominator=sample_size,
            sample_size=sample_size,
        )

    policy = InterviewMetricAttentionPolicy(
        policy_version="test-v1",
        digest="0" * 64,
        rules={
            "inquiry_resolution_rate": rule("inquiry_resolution_rate", "low_is_bad", 0.5),
            "implementation_question_transfer_rate": rule(
                "implementation_question_transfer_rate", "high_is_bad", 0.5,
            ),
            "post_inquiry_confirmation_rate": rule(
                "post_inquiry_confirmation_rate", "high_is_bad", 0.5,
            ),
        },
    )

    low_breach = metric("inquiry_resolution_rate", 0.4, 10)
    high_ok = metric("implementation_question_transfer_rate", 0.4, 10)
    undersampled = metric("post_inquiry_confirmation_rate", 0.9, 2)

    _metrics, summary = apply_attention(
        [low_breach, high_ok, undersampled], policy=policy,
    )

    assert low_breach.attention.state == "attention"
    assert low_breach.attention.reason == "threshold_breached"
    # 解除条件: value_within_threshold -- the same metric back inside the
    # boundary is no longer 要確認.
    recovered = metric("inquiry_resolution_rate", 0.6, 10)
    _metrics, recovered_summary = apply_attention([recovered], policy=policy)
    assert recovered.attention.state == "ok"
    assert recovered.attention.reason == "within_threshold"
    assert recovered_summary.state == "normal"

    assert high_ok.attention.state == "ok"
    assert undersampled.attention.state == "insufficient_data"
    assert undersampled.attention.reason == "sample_below_minimum"

    assert summary.state == "attention"
    assert summary.attention_count == 1
    assert summary.insufficient_data_count == 1
    assert summary.watched_count == 3


def test_attention_policy_rejects_watching_a_non_guardrail_metric():
    from app.interview_metric_attention import (
        InterviewMetricAttentionPolicy,
        InterviewMetricAttentionPolicyError,
        MetricAttentionRule,
        apply_attention,
    )
    from app.models import InterviewMetricOut

    policy = InterviewMetricAttentionPolicy(
        policy_version="test-v1",
        digest="0" * 64,
        rules={
            "unknown_answer_rate": MetricAttentionRule(
                key="unknown_answer_rate",
                watch=True,
                direction="high_is_bad",
                threshold=0.5,
                min_sample=1,
                window="all_time",
                trigger="single_breach",
                clear="value_within_threshold",
            ),
        },
    )
    metric = InterviewMetricOut(
        key="unknown_answer_rate",
        category="user_burden",
        guardrail=False,
        description="d",
        formula="f",
        sources=[],
        status="measured",
        value=0.9,
        unit="ratio",
        sample_size=10,
    )
    with pytest.raises(InterviewMetricAttentionPolicyError):
        apply_attention([metric], policy=policy)


def test_attention_policy_loader_fails_closed(tmp_path):
    from app.interview_metric_attention import (
        InterviewMetricAttentionPolicyError,
        load_interview_metric_attention_policy,
    )

    missing = tmp_path / "nope.yaml"
    with pytest.raises(InterviewMetricAttentionPolicyError):
        load_interview_metric_attention_policy(missing)

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text(
        "schema_version: interview-metric-attention-policy-v1\n"
        "policy_version: partial\n"
        "metrics:\n"
        "  - key: unknown_answer_rate\n"
        "    watch: false\n",
        encoding="utf-8",
    )
    with pytest.raises(InterviewMetricAttentionPolicyError) as excinfo:
        load_interview_metric_attention_policy(incomplete)
    assert "must cover every metric key" in str(excinfo.value)

    bad_enum = tmp_path / "bad-enum.yaml"
    bad_enum.write_text(
        "schema_version: interview-metric-attention-policy-v1\n"
        "policy_version: bad\n"
        "metrics:\n"
        "  - key: inquiry_resolution_rate\n"
        "    watch: true\n"
        "    direction: sometimes_bad\n"
        "    threshold: null\n"
        "    min_sample: 5\n"
        "    window: all_time\n"
        "    trigger: single_breach\n"
        "    clear: value_within_threshold\n",
        encoding="utf-8",
    )
    with pytest.raises(InterviewMetricAttentionPolicyError):
        load_interview_metric_attention_policy(bad_enum)

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: interview-metric-attention-policy-v1\n"
        "policy_version: dup\n"
        "policy_version: dup\n"
        "metrics: []\n",
        encoding="utf-8",
    )
    with pytest.raises(InterviewMetricAttentionPolicyError):
        load_interview_metric_attention_policy(duplicate)


def test_shipped_attention_policy_watches_only_designated_guardrails(admin_client):
    """The shipped artifact must load and stay consistent with the catalog."""

    from app.interview_metric_attention import load_interview_metric_attention_policy
    from app.models import INTERVIEW_METRIC_KEYS

    policy = load_interview_metric_attention_policy()
    assert set(policy.rules) == set(INTERVIEW_METRIC_KEYS)

    token = _login(admin_client)
    _system_id, _snapshot_id, _session_id, headers = _setup_system(
        admin_client, token, "shipped-policy",
    )
    body = admin_client.get("/interview/metrics", headers=headers).json()
    guardrail_keys = {m["key"] for m in body["metrics"] if m["guardrail"]}
    watched_keys = {key for key, rule in policy.rules.items() if rule.watch}
    assert watched_keys <= guardrail_keys
