"""Issue #336: the single 「わからない」 flow and the shared evidence feed.

Two defects are covered here, and they are the same defect seen from both ends.

1. **The entry point never existed.** A real 「わからない」 went through the
   #142 answer flow, which knew nothing about Joint Understanding, while the
   Dashboard separately called a one-shot `route-and-investigate`. No Joint
   Understanding session was ever opened from an actual unknown answer, so the
   whole feature was reachable only by explicitly asking for it.

2. **The exit never existed either.** Reflux attached a verified fact, but for
   every origin except `qa` the attachment WAS the
   `joint_understanding_reflux` row -- and no rebuild read that table. The fact
   was recorded, attributable, and invisible to every consumer that could have
   used it.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from joint_understanding_helpers import insert_producer_finding

from app.joint_understanding import (
    ACTION_FORMAL_OPERATIONS,
    ACTION_KINDS,
    EXTERNAL_FORMAL_OPERATIONS,
    FORMAL_OPERATIONS,
    completes_outside_session,
    formal_operation_for,
)
from app.understanding_evidence_feed import (
    FEED_CONSUMER_KINDS,
    compute_evidence_digest,
    compute_publication_digest,
)


# --- The action -> formal operation catalog (pure) ------------------------------


def test_every_action_maps_to_a_known_formal_operation():
    assert set(ACTION_FORMAL_OPERATIONS) == set(ACTION_KINDS)
    for action_kind in ACTION_KINDS:
        assert formal_operation_for(action_kind) in FORMAL_OPERATIONS


def test_actions_whose_operation_lives_elsewhere_are_marked():
    """Recording an action is intent, never completion.

    The three operations performed by an endpoint OUTSIDE this feature are the
    ones that must never be read as done because an action row exists -- that
    distinction had no machine-readable form before Issue #336.
    """
    outside = {k for k in ACTION_KINDS if completes_outside_session(k)}
    assert outside == {"revise_intent", "handoff"}
    for action_kind in outside:
        assert formal_operation_for(action_kind) in EXTERNAL_FORMAL_OPERATIONS
    # `decide` closes THIS session with a typed outcome; the origin item's own
    # answer stays a separate operation, which is why closing never writes it.
    assert formal_operation_for("decide") == "joint_close_decided"
    assert not completes_outside_session("decide")


def test_evidence_digest_is_content_addressed():
    """Two rounds establishing the same statement from the same span are one
    fact, so republication is idempotent without the caller tracking state."""
    args = dict(
        source_kind="joint_understanding",
        statement="リトライは3回で打ち切られる",
        evidence=[{"path": "app/retry.py", "start_line": 1, "end_line": 5}],
        runtime_evidence=[],
    )
    assert compute_evidence_digest(**args) == compute_evidence_digest(**args)
    # Order of citations must not change the identity.
    reordered = dict(args)
    reordered["evidence"] = [
        {"path": "app/retry.py", "start_line": 1, "end_line": 5},
    ]
    assert compute_evidence_digest(**reordered) == compute_evidence_digest(**args)
    # A different claim is a different fact.
    other = dict(args)
    other["statement"] = "リトライは5回で打ち切られる"
    assert compute_evidence_digest(**other) != compute_evidence_digest(**args)


def test_publication_digest_preserves_independent_source_currency():
    """A fact re-established under a new premise is a new publication.

    A semantic-only UNIQUE key makes the first (later stale) source suppress
    the second (still current) source. Exact retries remain idempotent, but
    provenance must not be collapsed across source sessions or findings.
    """
    args = dict(
        source_kind="joint_understanding",
        source_id=10,
        finding_id=20,
        statement="リトライは3回で打ち切られる",
        evidence=[{"path": "app/retry.py", "start_line": 8, "end_line": 9}],
        runtime_evidence=[],
    )
    first = compute_publication_digest(**args)
    assert compute_publication_digest(**args) == first
    assert compute_publication_digest(**{**args, "source_id": 11}) != first
    assert compute_publication_digest(**{**args, "finding_id": 21}) != first


def test_consumer_kinds_contain_no_human_action():
    """A consumption row says a rebuild used a fact. The human's acceptance of
    the result is a different fact with its own record, and collapsing them
    would let "the AI fed this in" read as "the developer agreed with it"."""
    assert FEED_CONSUMER_KINDS == (
        "understanding_build", "alignment_build", "inquiry_answer",
    )


# --- Route-level ----------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-flow-test.db"))
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


def _qa_row(qa_id):
    from app.db import get_conn

    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone())


class _StubRoute:
    """A stand-in for one ``route_question`` result."""

    def __init__(self, category, *, error=None):
        self.provider = "anthropic"
        self.model = "claude-sonnet-5"
        self.is_mock = False
        self.prompt_version = "question-router-v3"
        self.schema_version = "question-router-v3"
        self.category = category
        self.reason = "テスト用の分類"
        self.research_focus = "retry limit" if category != "human_only" else None
        self.knowledge_area = "implementation"
        self.search_keywords = ["retry"] if category != "human_only" else []
        self.error = error


def _stub_router(monkeypatch, category, *, error=None):
    from app.routes import joint_understanding as ju_routes
    import app.question_router as question_router

    monkeypatch.setattr(
        ju_routes, "create_llm_client", lambda config: object(),
    )
    monkeypatch.setattr(
        question_router, "route_question",
        lambda *args, **kwargs: _StubRoute(category, error=error),
    )


def _stub_investigation(monkeypatch, *, findings: int = 1, raises=None):
    """Replace the investigation endpoint the unknown flow delegates to."""
    from fastapi import HTTPException

    from app.routes import joint_understanding as ju_routes
    from app.models import JointUnderstandingInvestigateOut

    def _fake(ju_id, payload=None, system_id=None):
        if raises is not None:
            raise raises
        return JointUnderstandingInvestigateOut(
            joint_understanding_id=ju_id, system_id=system_id or 1,
            stop_reason="answered" if findings else "unresolved",
            rounds=[],
            findings=[],
            error=None,
        )

    if findings:
        # The endpoint's contract is "findings present means it produced
        # something"; a stub that returns none must not be read as success.
        def _fake_with_findings(ju_id, payload=None, system_id=None):
            out = _fake(ju_id, payload, system_id)
            from app.db import get_conn

            with get_conn() as conn:
                row = conn.execute(
                    "SELECT system_id FROM joint_understanding_session WHERE id = ?",
                    (ju_id,),
                ).fetchone()
            finding_id = insert_producer_finding(
                ju_id=ju_id, system_id=row["system_id"],
            )
            with get_conn() as conn:
                finding = conn.execute(
                    "SELECT * FROM joint_understanding_finding WHERE id = ?",
                    (finding_id,),
                ).fetchone()
            out.findings = [ju_routes._finding_out(finding)]
            return out

        monkeypatch.setattr(
            ju_routes, "investigate_joint_understanding", _fake_with_findings,
        )
        return
    monkeypatch.setattr(ju_routes, "investigate_joint_understanding", _fake)


def test_unknown_answer_starts_a_joint_investigation(admin_client, monkeypatch):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, "system_researchable")
    _stub_investigation(monkeypatch, findings=1)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "根拠が分からない", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route_category"] == "system_researchable"
    assert body["next_step"] == "joint_investigation_started"
    assert body["joint_understanding_id"] is not None

    # The unknown answer is recorded, and 'unconfirmed' -- never 'answered'.
    row = _qa_row(qa["id"])
    assert row["answer_unknown"] == 1
    assert row["status"] == "unconfirmed"
    assert row["route_category"] == "system_researchable"

    # The session records that it started automatically, and it did not
    # fabricate a developer finding out of 「わからない」.
    detail = admin_client.get(
        f"/joint-understanding/{body['joint_understanding_id']}", headers=headers,
    ).json()
    assert detail["session"]["trigger"] == "unknown_answer"
    assert detail["session"]["origin_kind"] == "qa"
    assert detail["session"]["origin_id"] == qa["id"]
    assert detail["session"]["premise_state"] == "current"
    assert all(f["origin_role"] != "developer" for f in detail["findings"])


def test_hybrid_investigates_before_it_asks(admin_client, monkeypatch):
    """Issue #336: the researchable part is consumed first. 'hybrid' opens the
    same session and runs the same investigation as 'system_researchable' --
    what is left for the developer is decided afterwards by the translation
    gate, not by the classification alone."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, "hybrid")
    _stub_investigation(monkeypatch, findings=1)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route_category"] == "hybrid"
    assert body["joint_understanding_id"] is not None
    assert body["next_step"] == "joint_investigation_started"


def test_human_only_never_starts_an_investigation(admin_client, monkeypatch):
    """There is nothing in the code to find, so opening a conversation would
    only be a slower way of handing the same question back."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, "human_only")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "優先度は決めていない", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route_category"] == "human_only"
    assert body["joint_understanding_id"] is None
    assert body["next_step"] == "developer_answer_required"

    # The answer still stands, so the developer's own answer / handoff path is
    # exactly where they are left.
    assert _qa_row(qa["id"])["status"] == "unconfirmed"
    assert admin_client.get(
        f"/interview/sessions/{session_id}/joint-understanding", headers=headers,
    ).json()["items"] == []


def test_a_routing_failure_keeps_the_recorded_answer(admin_client, monkeypatch):
    """Issue #336's failure contract: nothing the developer already has may be
    lost because a later step failed."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, None, error="reasoning model call failed")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "分からない", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_step"] == "routing_unavailable"
    assert body["joint_understanding_id"] is None
    assert body["error"] == "reasoning model call failed"

    row = _qa_row(qa["id"])
    assert row["answer_unknown"] == 1
    assert row["status"] == "unconfirmed"
    assert row["answer_text"] == "分からない"
    # And the correction path is still open.
    assert admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "3回でした", "actor": "root"}, headers=headers,
    ).status_code == 200


def test_an_investigation_failure_keeps_the_answer_and_the_session(
    admin_client, monkeypatch,
):
    from fastapi import HTTPException

    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, "system_researchable")
    _stub_investigation(
        monkeypatch, findings=0,
        raises=HTTPException(
            status_code=502,
            detail={"code": "joint_investigation_failed", "message": "API failure"},
        ),
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "分からない", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The session is open and retryable rather than hidden behind a 502.
    assert body["joint_understanding_id"] is not None
    assert body["next_step"] == "joint_understanding_opened"
    assert body["error"] == "API failure"
    assert _qa_row(qa["id"])["status"] == "unconfirmed"


def test_the_unknown_flow_refuses_an_already_answered_question(admin_client, monkeypatch):
    """A real answer must not be silently replaced by 「わからない」 -- correcting
    it is the answer endpoint's job, which supersedes rather than overwrites."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "3回です", "actor": "root"}, headers=headers,
    )
    _stub_router(monkeypatch, "system_researchable")

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "", "actor": "root"}, headers=headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "qa_already_answered"


def test_saying_unknown_again_retries_the_same_conversation(admin_client, monkeypatch):
    """A developer whose first attempt failed must not be stranded.

    Repeating 「わからない」 keeps the recorded answer as it is and gives them
    another attempt at the routing and the investigation -- continuing the SAME
    session, because a retry has to carry the earlier rounds' leads (#330) and
    two open sessions on one question would each hold half the history.
    """
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)

    # First attempt: the router is unavailable, so nothing is investigated.
    _stub_router(monkeypatch, None, error="reasoning model call failed")
    first = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "分からない", "actor": "root"}, headers=headers,
    )
    assert first.json()["next_step"] == "routing_unavailable"
    assert first.json()["joint_understanding_id"] is None

    # Second attempt: it works, and the recorded answer is untouched.
    _stub_router(monkeypatch, "system_researchable")
    _stub_investigation(monkeypatch, findings=1)
    second = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "上書きしない", "actor": "root"}, headers=headers,
    )
    assert second.status_code == 200, second.text
    ju_id = second.json()["joint_understanding_id"]
    assert ju_id is not None
    row = _qa_row(qa["id"])
    assert row["answer_text"] == "分からない"
    assert row["status"] == "unconfirmed"

    # A third attempt continues that same conversation rather than opening a
    # second one.
    third = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "", "actor": "root"}, headers=headers,
    )
    assert third.json()["joint_understanding_id"] == ju_id
    listed = admin_client.get(
        f"/interview/sessions/{session_id}/joint-understanding", headers=headers,
    ).json()["items"]
    assert [item["id"] for item in listed] == [ju_id]


# --- The shared evidence feed ---------------------------------------------------


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


def _feed_rows(system_id):
    from app.db import get_conn

    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM understanding_evidence_feed WHERE system_id = ? ORDER BY id",
                (system_id,),
            ).fetchall()
        ]


def _current_feed(system_id, session_id=None):
    from app.db import get_conn
    from app.understanding_evidence_feed import current_entries

    with get_conn() as conn:
        return current_entries(conn, system_id=system_id, session_id=session_id)


def test_a_non_qa_origins_facts_reach_the_shared_feed(admin_client):
    """The orphaning this issue fixes: an `intent` origin's verified facts
    landed in the reflux ledger, which no rebuild read."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    intent = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "失敗を早く見せたい"}, headers=headers,
    ).json()
    ju_id = _open_ju(admin_client, headers, session_id, "intent", intent["id"])
    run_id = _insert_run(system_id, snapshot_id)
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )

    r = admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["target_kind"] == "session_ledger"

    rows = _feed_rows(system_id)
    assert len(rows) == 1
    assert rows[0]["origin_kind"] == "intent"
    # Its own provenance: never 'manual', because nobody answered anything.
    assert rows[0]["decision_method"] == "reasoning_llm"
    assert rows[0]["intelligence_run_id"] == run_id
    assert rows[0]["premise_commit_sha"] == "abc123"
    assert len(_current_feed(system_id)) == 1


def test_republishing_the_same_fact_is_idempotent(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )

    for _ in range(3):
        assert admin_client.post(
            f"/joint-understanding/{ju_id}/reflux", headers=headers,
        ).status_code == 201
    assert len(_feed_rows(system_id)) == 1

    # An incremental reflux publishes only the genuinely new fact.
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
        statement="タイムアウトは5秒で固定されている",
        evidence=[{"path": "app/retry.py", "start_line": 30, "end_line": 34, "summary": ""}],
    )
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert len(_feed_rows(system_id)) == 2


def test_a_corrected_finding_leaves_the_current_feed_but_stays_readable(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    finding_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert len(_current_feed(system_id)) == 1

    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
        statement="実際は設定値で決まる", supersedes_finding_id=finding_id,
        evidence=[{"path": "app/retry.py", "start_line": 8, "end_line": 9, "summary": ""}],
    )
    # The superseded fact drops out of the current feed immediately -- before
    # the correction is even refluxed -- because currency is evaluated at read
    # time. A rebuild must never be handed a claim the investigation withdrew.
    assert _current_feed(system_id) == []
    assert len(_feed_rows(system_id)) == 1  # still stored, still attributable

    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    # Now the correction chain is what is current, and the original remains as
    # history rather than being deleted or rewritten.
    assert [e.statement for e in _current_feed(system_id)] == ["実際は設定値で決まる"]
    assert len(_feed_rows(system_id)) == 2


def test_a_non_current_premise_removes_a_fact_from_the_current_feed(admin_client):
    """Currency is evaluated at READ time (Issue #337's verdict), because
    neither a correction nor a moved premise is knowable when the fact is
    published."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)
    assert len(_current_feed(system_id)) == 1

    from app.db import get_conn

    moved = _insert_snapshot(system_id, "def456")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?", (moved, session_id),
        )

    assert _current_feed(system_id) == []
    # Still stored, still attributable: excluded, not erased.
    assert len(_feed_rows(system_id)) == 1


def test_same_fact_reverified_on_current_premise_survives_stale_old_source(admin_client):
    """An old semantic duplicate must not poison a newer verified source."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    old_ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    insert_producer_finding(
        ju_id=old_ju_id,
        system_id=system_id,
        intelligence_run_id=_insert_run(system_id, snapshot_id),
    )
    old_reflux = admin_client.post(
        f"/joint-understanding/{old_ju_id}/reflux", headers=headers,
    )
    assert old_reflux.status_code == 201, old_reflux.text

    from app.db import get_conn

    current_snapshot_id = _insert_snapshot(system_id, "def456")
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?",
            (current_snapshot_id, session_id),
        )
    assert _current_feed(system_id) == []

    current_ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    insert_producer_finding(
        ju_id=current_ju_id,
        system_id=system_id,
        intelligence_run_id=_insert_run(system_id, current_snapshot_id),
    )
    current_reflux = admin_client.post(
        f"/joint-understanding/{current_ju_id}/reflux", headers=headers,
    )
    assert current_reflux.status_code == 201, current_reflux.text

    current = _current_feed(system_id)
    assert [entry.source_id for entry in current] == [current_ju_id]
    assert [entry.statement for entry in current] == ["リトライは3回で打ち切られる"]
    assert len(_feed_rows(system_id)) == 2


def test_the_feed_is_system_isolated(admin_client):
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id,
        intelligence_run_id=_insert_run(system_id, snapshot_id),
    )
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)

    other = _create_system(admin_client, token, "Other")
    assert _current_feed(other["id"]) == []
    assert len(_current_feed(system_id)) == 1


def test_the_understanding_rebuild_consumes_the_feed_with_its_own_provenance(
    admin_client, monkeypatch,
):
    """Issue #336's central gate: after reflux, rebuilding the understanding
    uses the verified fact AND keeps it distinguishable from a developer's
    answer."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    ju_id = _open_ju(admin_client, headers, session_id, "qa", qa["id"])
    run_id = _insert_run(system_id, snapshot_id)
    insert_producer_finding(
        ju_id=ju_id, system_id=system_id, intelligence_run_id=run_id,
    )
    admin_client.post(f"/joint-understanding/{ju_id}/reflux", headers=headers)

    captured: List[Optional[list]] = []

    from app.system_understanding_reviewer import ReviewResult
    import app.routes.interview as interview_route

    def _fake_review(client, config, **kwargs):
        captured.append(kwargs.get("verified_evidence"))
        return ReviewResult(
            provider="anthropic", model="claude-sonnet-5", is_mock=False,
            current_understanding={"system_purpose": []},
            gap_analysis=[], open_questions=[],
        )

    monkeypatch.setattr(
        "app.system_understanding_reviewer.generate_understanding_review", _fake_review,
    )
    monkeypatch.setattr(
        interview_route, "create_llm_client", lambda config: object(),
    )
    monkeypatch.setattr(
        "app.system_understanding_service._load_graph_for_snapshot",
        lambda conn, system_id, snapshot_id: _StubGraph(),
    )
    monkeypatch.setattr(
        "app.docs_code_reconciler.reconcile",
        lambda conn, system_id, snapshot_id, graph: _StubReconciliation(),
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/update-understanding", headers=headers,
    )
    assert r.status_code in (200, 201), r.text

    assert captured, "the rebuild did not run"
    facts = captured[-1]
    assert facts, "the rebuild received no verified evidence"
    assert facts[0]["statement"] == "リトライは3回で打ち切られる"
    # The provenance the rebuild must be able to tell apart from an answer.
    assert facts[0]["decision_method"] == "reasoning_llm"
    assert facts[0]["evidence"][0]["path"] == "app/retry.py"

    # The consumption is recorded -- and it is NOT a confirmation.
    from app.db import get_conn

    with get_conn() as conn:
        consumption = conn.execute(
            "SELECT * FROM understanding_evidence_consumption WHERE system_id = ?",
            (system_id,),
        ).fetchall()
        session_row = conn.execute(
            "SELECT understanding_confirmed_at FROM interview_session WHERE id = ?",
            (session_id,),
        ).fetchone()
    assert len(consumption) == 1
    assert consumption[0]["consumer_kind"] == "understanding_build"
    assert consumption[0]["revision_id"] is not None
    assert session_row["understanding_confirmed_at"] is None


class _StubGraph:
    """Minimal stand-in for an UnderstandingGraph."""

    nodes: list = []
    claim_count = 0
    valid_claim_count = 0
    conflicts: list = []
    confidence_summary: dict = {}


class _StubReconciliation:
    matched_count = 0
    docs_only_count = 0
    code_only_count = 0
    mismatch_count = 0
    gaps: list = []


def test_the_action_menu_names_the_operation_each_action_opens(admin_client, monkeypatch):
    """The menu the developer sees says where each choice actually goes, and
    which of them still need their own official operation afterwards."""
    from app.understanding_translator import build_action_menu

    entries = {e.action_kind: e for e in build_action_menu("ja")}
    assert set(entries) == set(ACTION_KINDS)
    assert entries["revise_intent"].formal_operation == "intent_correct"
    assert entries["revise_intent"].completes_outside_session is True
    assert entries["handoff"].formal_operation == "question_handoff_create"
    assert entries["handoff"].completes_outside_session is True
    assert entries["request_investigation"].completes_outside_session is False


def test_an_answer_revision_does_not_lose_the_conversation(admin_client, monkeypatch):
    """`interview_qa` corrects additively, so a revision gives the question a NEW
    row id while the session still points at the row the conversation started
    from. A consumer matching only on `origin_id` would stop finding the session
    -- the conversation would disappear from the card the moment its question got
    an answer revision."""
    token, system_id, headers, snapshot_id, session_id = _setup(admin_client)
    qa = _create_qa(admin_client, headers, session_id)
    _stub_router(monkeypatch, "system_researchable")
    _stub_investigation(monkeypatch, findings=1)
    started = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/unknown",
        json={"answer_text": "分からない", "actor": "root"}, headers=headers,
    ).json()
    ju_id = started["joint_understanding_id"]
    assert ju_id is not None

    revised = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "調べたら3回でした", "actor": "root"}, headers=headers,
    )
    assert revised.status_code == 200, revised.text
    new_qa_id = revised.json()["qa"]["id"]
    assert new_qa_id != qa["id"]

    listed = admin_client.get(
        f"/interview/sessions/{session_id}/joint-understanding", headers=headers,
    ).json()["items"]
    session = next(item for item in listed if item["id"] == ju_id)
    # The session keeps pointing at where the conversation started...
    assert session["origin_id"] == qa["id"]
    # ...and reports the live row a consumer must match on.
    assert session["current_origin_id"] == new_qa_id
    # The premise is still current: the QUESTION did not change.
    assert session["premise_state"] == "current"
