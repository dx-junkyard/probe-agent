"""Tests for Issue #308: Inquiry premise tracking and the `superseded` state.

Covers the three server-side sub-issues:

1. #320 -- the immutable premise bundle captured when an Inquiry is created
   (snapshot / revision / content hash / Capability digest / linked Intent
   digest / tracking version / captured_at), the pinned snapshot that
   follow-up answers keep using, the explicit `untrackable` value when no
   comparable premise exists, and the additive NULL migration.
2. #321 -- `review_subject_id` / `subject_state` / `replaces_item_id`: the
   stable discussion-point identity and physical lineage assigned by an
   Alignment build, including split/merge -> `ambiguous` and anchor-less ->
   `untrackable` with no guessed successor.
3. #323 -- the premise evaluation that runs inside the Alignment rebuild
   transaction: the finite verdict set, the `superseded` system transition
   with a finite reason code, the preserved `resolved` timestamp, the
   successor becoming a Review Queue recheck target, idempotency,
   transaction safety, and System isolation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from app.alignment import (
    AlignmentEvidenceItem,
    AlignmentProposalItem,
    AlignmentProposalResult,
    compute_intent_item_digest,
)
from app.inquiry_premise import (
    PREMISE_EVALUATIONS,
    PREMISE_REASON_CODES,
    PREMISE_TRACKING_STATES,
    PREMISE_TRACKING_VERSION,
    SUBJECT_STATES,
    SuccessorFacts,
    classify_subject_lineage,
    compute_capability_scope_digest,
    compute_review_subject_id,
    evaluate_premise,
    premise_tracking_state,
)


# --- Unit tests: review subject identity (Issue #321) -------------------------


def _subject(**overrides):
    base = dict(
        system_id=1,
        session_id=2,
        intent_field="pain",
        capability_entity_ids=[],
        capability_relation_ids=[],
    )
    base.update(overrides)
    return compute_review_subject_id(**base)


def test_review_subject_id_is_deterministic_and_order_independent():
    first = _subject(capability_entity_ids=[3, 1], capability_relation_ids=[9, 4])
    second = _subject(capability_entity_ids=[1, 3, 3], capability_relation_ids=[4, 9])
    assert first == second
    assert first == _subject(capability_entity_ids=[3, 1], capability_relation_ids=[9, 4])


def test_review_subject_id_is_scoped_per_system_and_session():
    assert _subject(system_id=1) != _subject(system_id=2)
    assert _subject(session_id=2) != _subject(session_id=3)


def test_review_subject_id_distinguishes_anchor_components():
    assert _subject(intent_field="pain") != _subject(intent_field="constraints")
    assert _subject(capability_entity_ids=[1]) != _subject(capability_entity_ids=[2])
    assert _subject(capability_entity_ids=[1]) != _subject(capability_relation_ids=[1])


def test_review_subject_id_is_none_without_any_stable_anchor():
    assert _subject(intent_field=None) is None
    assert _subject(intent_field="") is None
    # A Capability dependency alone is a sufficient anchor.
    assert _subject(intent_field=None, capability_entity_ids=[7]) is not None


@pytest.mark.parametrize(
    "kwargs,expected_state,expected_replaces",
    [
        (dict(review_subject_id=None, same_subject_in_build=0, predecessors=[], base_content_hash="h"),
         "untrackable", None),
        (dict(review_subject_id="s", same_subject_in_build=1, predecessors=[], base_content_hash="h"),
         "new", None),
        (dict(review_subject_id="s", same_subject_in_build=1, predecessors=[(4, "h")], base_content_hash="h"),
         "unchanged", 4),
        (dict(review_subject_id="s", same_subject_in_build=1, predecessors=[(4, "old")], base_content_hash="h"),
         "changed", 4),
        # A split (two rows of this build share the anchor) and a merge (two
        # predecessor rows share it) are both ambiguous, and neither binds.
        (dict(review_subject_id="s", same_subject_in_build=2, predecessors=[(4, "h")], base_content_hash="h"),
         "ambiguous", None),
        (dict(review_subject_id="s", same_subject_in_build=1, predecessors=[(4, "h"), (5, "h")], base_content_hash="h"),
         "ambiguous", None),
        # An unreadable-evidence item (content_hash is None) can never claim
        # to be unchanged.
        (dict(review_subject_id="s", same_subject_in_build=1, predecessors=[(4, "h")], base_content_hash=None),
         "changed", 4),
    ],
)
def test_classify_subject_lineage_finite_results(kwargs, expected_state, expected_replaces):
    state, replaces = classify_subject_lineage(**kwargs)
    assert (state, replaces) == (expected_state, expected_replaces)
    assert state in SUBJECT_STATES


# --- Unit tests: premise digests + evaluation (Issues #320 / #323) ------------


def test_capability_scope_digest_is_order_independent_and_scope_sensitive():
    dependencies = [
        {"target_kind": "entity", "entity_id": 2, "relation_id": None, "captured_digest": "d2"},
        {"target_kind": "relation", "entity_id": None, "relation_id": 5, "captured_digest": "d5"},
        {"target_kind": "entity", "entity_id": 1, "relation_id": None, "captured_digest": "d1"},
    ]
    digest = compute_capability_scope_digest(confirmation_id=7, dependencies=dependencies)
    assert digest == compute_capability_scope_digest(
        confirmation_id=7, dependencies=list(reversed(dependencies)),
    )
    # The confirmed composition and each captured digest are both part of it.
    assert digest != compute_capability_scope_digest(confirmation_id=8, dependencies=dependencies)
    changed = [dict(d) for d in dependencies]
    changed[0]["captured_digest"] = "other"
    assert digest != compute_capability_scope_digest(confirmation_id=7, dependencies=changed)
    # An empty scope is a real, comparable fact -- never None.
    assert isinstance(
        compute_capability_scope_digest(confirmation_id=None, dependencies=[]), str
    )


@pytest.mark.parametrize(
    "origin_kind,version,content_hash,subject,expected",
    [
        ("qa", PREMISE_TRACKING_VERSION, "h", "s", "not_applicable"),
        ("intent", PREMISE_TRACKING_VERSION, "h", "s", "not_applicable"),
        ("review_item", None, None, None, "untrackable"),
        ("review_item", PREMISE_TRACKING_VERSION, None, "s", "untrackable"),
        ("review_item", PREMISE_TRACKING_VERSION, "h", None, "untrackable"),
        ("review_item", PREMISE_TRACKING_VERSION, "h", "s", "tracked"),
    ],
)
def test_premise_tracking_state_is_finite(origin_kind, version, content_hash, subject, expected):
    state = premise_tracking_state(
        origin_kind=origin_kind,
        tracking_version=version,
        content_hash=content_hash,
        review_subject_id=subject,
    )
    assert state == expected
    assert state in PREMISE_TRACKING_STATES


def _facts(**overrides):
    base = dict(item_id=11, base_content_hash="hash", capability_digest="cap", intent_digest="intent")
    base.update(overrides)
    return SuccessorFacts(**base)


def _evaluate(successors, **overrides):
    base = dict(
        premise_content_hash="hash",
        premise_capability_digest="cap",
        premise_intent_digest="intent",
    )
    base.update(overrides)
    return evaluate_premise(successors=successors, **base)


def test_evaluate_premise_unchanged_keeps_the_conversation():
    result = _evaluate([_facts()])
    assert (result.result, result.reason_code, result.successor_item_id) == ("unchanged", None, 11)


def test_evaluate_premise_reason_codes_report_the_root_cause_first():
    # The content hash already covers the linked Intent and is affected by a
    # Capability change, so the more specific cause must win.
    assert _evaluate([_facts(base_content_hash="x", intent_digest="other")]).reason_code == (
        "linked_intent_changed"
    )
    assert _evaluate([_facts(base_content_hash="x", capability_digest="other")]).reason_code == (
        "capability_scope_changed"
    )
    assert _evaluate([_facts(base_content_hash="x")]).reason_code == "review_item_content_changed"


def test_evaluate_premise_never_guesses_a_successor():
    removed = _evaluate([])
    assert (removed.result, removed.reason_code, removed.successor_item_id) == (
        "removed", "origin_removed", None,
    )
    ambiguous = _evaluate([_facts(item_id=1), _facts(item_id=2)])
    assert (ambiguous.result, ambiguous.reason_code, ambiguous.successor_item_id) == (
        "ambiguous", "successor_ambiguous", None,
    )


def test_evaluate_premise_results_and_reasons_stay_in_the_finite_sets():
    cases = [
        _evaluate([]),
        _evaluate([_facts(item_id=1), _facts(item_id=2)]),
        _evaluate([_facts()]),
        _evaluate([_facts(base_content_hash="x")]),
    ]
    for case in cases:
        assert case.result in PREMISE_EVALUATIONS
        assert case.reason_code is None or case.reason_code in PREMISE_REASON_CODES


def test_evaluate_premise_ignores_an_unreadable_successor_hash():
    """A successor whose evidence could not be re-read (content_hash NULL) is
    never silently treated as identical to the captured premise."""
    result = _evaluate([_facts(base_content_hash=None)])
    assert result.result == "changed"


# --- Route-level fixtures -----------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-premise-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _init_repo(tmp_path, files: Dict[str, str]):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True, capture_output=True)
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as handle:
            handle.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _insert_revision(session_id, system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO understanding_revision
                (session_id, system_id, snapshot_id, intelligence_run_id,
                 current_understanding, gap_analysis, created_at)
               VALUES (?, ?, ?, NULL, ?, NULL, ?)""",
            (session_id, system_id, snapshot_id, json.dumps({}), now),
        )
        return cur.lastrowid


def _insert_intent_item(session_id, system_id, *, field, value_text="value"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 decision_method, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'confirmed', 'user', 'manual', ?, ?)""",
            (session_id, system_id, field, value_text, now, now),
        )
        return cur.lastrowid


def _setup(client, tmp_path, name="System A", subdir="a"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo(
        tmp_path / subdir, {"src/a.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"},
    )
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    headers = _headers(token, system_id)
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _insert_revision(session_id, system_id, snapshot_id)
    return headers, system_id, session_id, snapshot_id


def _proposal_item(**overrides):
    base = dict(
        intent_field="pain",
        intent_ref_hint="hint",
        current_claim="現在は手動でトレースを確認している",
        evidence=[AlignmentEvidenceItem(path="src/a.py", start_line=1, end_line=3, summary="根拠")],
        alignment_state="gap",
        risk_flags=[],
        confidence="likely",
        gap_summary="自動化されていない",
        proposed_interpretation="自動収集の追加を検討",
    )
    base.update(overrides)
    return base


def _stub_build(monkeypatch, *, items=None, error=None):
    from app.routes import interview_alignment as alignment_routes

    def fake_create_llm_client(config):
        return object()

    def fake_generate_alignment_proposal(client, config, **kwargs):
        return AlignmentProposalResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            items=[AlignmentProposalItem(**i) for i in (items or [])],
            error=error,
        )

    monkeypatch.setattr(alignment_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        alignment_routes, "generate_alignment_proposal", fake_generate_alignment_proposal,
    )


def _build(client, headers, session_id, monkeypatch, items):
    _stub_build(monkeypatch, items=items)
    r = client.post(f"/interview/sessions/{session_id}/alignment/build", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _stub_inquiry_answering(monkeypatch, *, captured_snapshots=None):
    from app.inquiry_answering import InquiryAnswerResult
    from app.routes import interview_inquiry as inquiry_routes

    def fake_create_llm_client(config):
        return object()

    def fake_generate_inquiry_answer(client, config, **kwargs):
        if captured_snapshots is not None:
            captured_snapshots.append(kwargs.get("context"))
        return InquiryAnswerResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            conclusion="確認しました。", answerable=True,
        )

    monkeypatch.setattr(inquiry_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        inquiry_routes, "generate_inquiry_answer", fake_generate_inquiry_answer,
    )


def _open_inquiry(client, headers, session_id, *, origin_kind, origin_id, monkeypatch):
    _stub_inquiry_answering(monkeypatch)
    r = client.post(
        f"/interview/sessions/{session_id}/inquiries",
        json={
            "origin_kind": origin_kind,
            "origin_id": origin_id,
            "question_text": "根拠を確認したい",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["inquiry"]


def _get_inquiry(client, headers, inquiry_id):
    r = client.get(f"/interview/inquiries/{inquiry_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["inquiry"]


def _transitions(inquiry_id):
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(
            """SELECT from_status, to_status, actor, reason FROM interview_inquiry_transition
               WHERE inquiry_id = ? ORDER BY id""",
            (inquiry_id,),
        ).fetchall()


# --- #320: premise bundle capture --------------------------------------------


def test_review_item_inquiry_captures_the_full_premise_bundle(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, snapshot_id = _setup(admin_client, tmp_path)
    intent_id = _insert_intent_item(session_id, system_id, field="pain")
    items = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    item = items[0]

    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=item["id"], monkeypatch=monkeypatch,
    )

    assert inquiry["premise_snapshot_id"] == snapshot_id
    assert inquiry["premise_revision_id"] == item["revision_id"]
    assert inquiry["premise_review_subject_id"] == item["review_subject_id"]
    assert inquiry["premise_content_hash"] == item["content_hash"]
    assert inquiry["premise_capability_digest"]
    assert inquiry["premise_intent_digest"] == compute_intent_item_digest(
        field="pain", value_text="value", status="confirmed",
    )
    assert inquiry["premise_tracking_version"] == PREMISE_TRACKING_VERSION
    assert inquiry["premise_captured_at"] == pytest.approx(inquiry["created_at"])
    assert inquiry["premise_tracking_state"] == "tracked"
    # Nothing has been evaluated yet -- creation alone never expires anything.
    assert inquiry["premise_evaluation"] is None
    assert inquiry["superseded_at"] is None
    assert item["intent_item_id"] == intent_id


def test_qa_origin_inquiry_is_not_auto_tracked(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, snapshot_id = _setup(admin_client, tmp_path)
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_qa
                (session_id, system_id, question_text, question_category,
                 question_source, status, created_at)
               VALUES (?, ?, '質問', 'purpose', 'dialogue', 'open', ?)""",
            (session_id, system_id, now),
        )
        qa_id = cur.lastrowid

    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="qa", origin_id=qa_id, monkeypatch=monkeypatch,
    )
    assert inquiry["premise_tracking_state"] == "not_applicable"
    assert inquiry["premise_content_hash"] is None
    assert inquiry["premise_review_subject_id"] is None
    # The snapshot is still pinned, so follow-ups stay on one premise.
    assert inquiry["premise_snapshot_id"] == snapshot_id


def test_follow_up_answers_use_the_pinned_snapshot_not_the_session_snapshot(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, snapshot_id = _setup(admin_client, tmp_path)
    items = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=items[0]["id"], monkeypatch=monkeypatch,
    )

    # The session moves to a newer snapshot mid-conversation.
    repo, sha = _init_repo(tmp_path / "newer", {"src/a.py": "x\n"})
    newer_snapshot_id = _insert_snapshot(system_id, repo, sha)
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET snapshot_id = ? WHERE id = ?",
            (newer_snapshot_id, session_id),
        )

    _stub_inquiry_answering(monkeypatch)
    r = admin_client.post(
        f"/interview/inquiries/{inquiry['id']}/message",
        json={"content": "追加で確認したい"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    with get_conn() as conn:
        run_snapshots = [
            row["snapshot_id"]
            for row in conn.execute(
                """SELECT snapshot_id FROM intelligence_runs
                   WHERE system_id = ? AND run_type = 'inquiry_answer' ORDER BY id""",
                (system_id,),
            ).fetchall()
        ]
    assert run_snapshots == [snapshot_id, snapshot_id]


def test_inquiry_on_an_ambiguous_or_legacy_item_is_untrackable(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    # Two items sharing the only available anchor (same Intent field, no
    # confirmed Capability dependency) -- a split the server refuses to
    # resolve rather than guessing which one a later row succeeds.
    items = _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="A"), _proposal_item(current_claim="B")],
    )
    assert {it["subject_state"] for it in items} == {"ambiguous"}
    assert all(it["replaces_item_id"] is None for it in items)

    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=items[0]["id"], monkeypatch=monkeypatch,
    )
    assert inquiry["premise_review_subject_id"] is None
    assert inquiry["premise_tracking_state"] == "untrackable"

    # An untrackable premise is never evaluated, so the conversation is left
    # exactly as it was even though the whole contrast set changed.
    _build(admin_client, headers, session_id, monkeypatch, [_proposal_item(current_claim="C")])
    after = _get_inquiry(admin_client, headers, inquiry["id"])
    assert after["status"] == "open"
    assert after["premise_evaluation"] is None


def test_premise_columns_are_added_additively_and_leave_existing_rows_null(tmp_path, monkeypatch):
    """A pre-#308 database migrates by adding NULL columns only: a past
    snapshot/revision/hash is never reconstructed by guesswork."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE interview_inquiry (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    INTEGER NOT NULL,
            system_id     INTEGER NOT NULL,
            origin_kind   TEXT NOT NULL,
            origin_id     INTEGER NOT NULL,
            held_draft    TEXT,
            status        TEXT NOT NULL DEFAULT 'open',
            status_reason TEXT,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL,
            closed_at     REAL
        );
        INSERT INTO interview_inquiry
            (session_id, system_id, origin_kind, origin_id, status, created_at, updated_at)
        VALUES (1, 1, 'review_item', 5, 'resolved', 1.0, 1.0);
        """
    )
    conn.commit()
    conn.close()

    from app.db import get_conn, init_db

    init_db()
    with get_conn() as db:
        row = db.execute("SELECT * FROM interview_inquiry WHERE id = 1").fetchone()
        columns = {r["name"] for r in db.execute("PRAGMA table_info(interview_inquiry)")}
    for column in (
        "premise_snapshot_id", "premise_revision_id", "premise_review_subject_id",
        "premise_content_hash", "premise_capability_digest", "premise_intent_digest",
        "premise_tracking_version", "premise_captured_at", "premise_evaluation",
        "premise_successor_item_id", "superseded_at",
    ):
        assert column in columns
        assert row[column] is None
    assert row["status"] == "resolved"
    assert premise_tracking_state(
        origin_kind=row["origin_kind"],
        tracking_version=row["premise_tracking_version"],
        content_hash=row["premise_content_hash"],
        review_subject_id=row["premise_review_subject_id"],
    ) == "untrackable"


# --- #321: subject lineage across builds --------------------------------------


def test_subject_survives_a_content_change_and_records_physical_lineage(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    assert first[0]["subject_state"] == "new"

    # Same discussion point (same Intent field anchor), edited claim.
    second = _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="現在は自動でトレースを収集している")],
    )
    assert second[0]["review_subject_id"] == first[0]["review_subject_id"]
    assert second[0]["subject_state"] == "changed"
    assert second[0]["content_hash"] != first[0]["content_hash"]

    third = _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="現在は自動でトレースを収集している")],
    )
    assert third[0]["subject_state"] == "unchanged"
    assert third[0]["review_subject_id"] == first[0]["review_subject_id"]


def test_unrelated_review_items_never_share_a_subject(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    items = _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(intent_field="pain"), _proposal_item(intent_field="constraints")],
    )
    subjects = {it["review_subject_id"] for it in items}
    assert len(subjects) == 2
    assert all(it["subject_state"] == "new" for it in items)


def test_item_without_a_stable_anchor_is_untrackable(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    items = _build(
        admin_client, headers, session_id, monkeypatch, [_proposal_item(intent_field=None)],
    )
    assert items[0]["review_subject_id"] is None
    assert items[0]["subject_state"] == "untrackable"
    assert items[0]["replaces_item_id"] is None


def test_lineage_does_not_break_unchanged_carry_over(admin_client, tmp_path, monkeypatch):
    """Issue #295's accept_current carry-over keeps working alongside the new
    subject columns (they answer different questions)."""
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    r = admin_client.post(
        f"/interview/alignment/{first[0]['id']}/answer",
        json={"decision": "accept_current"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    second = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    fresh = [it for it in second if not it["superseded"]]
    assert len(fresh) == 1
    assert fresh[0]["review_category"] == "unchanged"
    assert fresh[0]["carried_over_from"] == first[0]["id"]
    assert fresh[0]["subject_state"] == "unchanged"
    assert fresh[0]["replaces_item_id"] == first[0]["id"]


# --- #323: premise evaluation and the superseded transition -------------------


def _resolved_inquiry_on_first_item(admin_client, headers, session_id, item_id, monkeypatch):
    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=item_id, monkeypatch=monkeypatch,
    )
    r = admin_client.post(f"/interview/inquiries/{inquiry['id']}/resolve", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_identical_rebuild_keeps_a_resolved_inquiry(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "resolved"
    assert after["premise_evaluation"] == "unchanged"
    assert after["superseded_at"] is None
    assert after["closed_at"] == resolved["closed_at"]
    assert [t["to_status"] for t in _transitions(resolved["id"])] == ["resolved"]


def test_content_change_supersedes_and_makes_the_successor_a_recheck_target(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    second = _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="現在は自動でトレースを収集している")],
    )
    successor = next(it for it in second if not it["superseded"] and it["id"] != first[0]["id"])

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "superseded"
    assert after["premise_evaluation"] == "changed"
    assert after["status_reason"] == "review_item_content_changed"
    assert after["premise_successor_item_id"] == successor["id"]
    # The original resolved moment is preserved; expiry is recorded apart.
    assert after["closed_at"] == resolved["closed_at"]
    assert after["superseded_at"] is not None and after["superseded_at"] >= after["closed_at"]

    transitions = _transitions(resolved["id"])
    assert [(t["from_status"], t["to_status"], t["actor"], t["reason"]) for t in transitions] == [
        ("open", "resolved", "user", None),
        ("resolved", "superseded", "system", "review_item_content_changed"),
    ]

    queue = admin_client.get(
        f"/interview/sessions/{session_id}/review-queue", headers=headers,
    ).json()["items"]
    queue_by_id = {it["id"]: it for it in queue}
    assert successor["id"] in queue_by_id
    assert queue_by_id[successor["id"]]["manual_recheck_required"] is True
    # The old physical row becomes history and is never reopened.
    assert first[0]["id"] not in queue_by_id


def test_linked_intent_change_supersedes_with_its_own_reason_code(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    _insert_intent_item(session_id, system_id, field="pain", value_text="旧テキスト")
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_intent_item SET value_text = '新テキスト' WHERE session_id = ?",
            (session_id,),
        )
    _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "superseded"
    assert after["status_reason"] == "linked_intent_changed"


def test_removed_discussion_point_supersedes_without_a_successor(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(
        admin_client, headers, session_id, monkeypatch,
        [
            _proposal_item(intent_field="pain", current_claim="痛みの論点"),
            _proposal_item(intent_field="constraints", current_claim="制約の論点"),
        ],
    )
    target = next(it for it in first if it["current_claim"] == "痛みの論点")
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, target["id"], monkeypatch,
    )

    # Only the OTHER discussion point survives this build.
    _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(intent_field="constraints", current_claim="制約の論点")],
    )

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "superseded"
    assert after["premise_evaluation"] == "removed"
    assert after["status_reason"] == "origin_removed"
    assert after["premise_successor_item_id"] is None


def test_ambiguous_successor_is_never_guessed(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    # The point splits in two: both new rows share the same anchor.
    _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="分割A"), _proposal_item(current_claim="分割B")],
    )

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "superseded"
    assert after["premise_evaluation"] == "ambiguous"
    assert after["status_reason"] == "successor_ambiguous"
    assert after["premise_successor_item_id"] is None


def test_open_inquiry_on_a_changed_premise_fails_closed(admin_client, tmp_path, monkeypatch):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=first[0]["id"], monkeypatch=monkeypatch,
    )
    assert inquiry["status"] == "open"

    _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="内容が変わった")],
    )

    after = _get_inquiry(admin_client, headers, inquiry["id"])
    assert after["status"] == "superseded"
    assert [t["from_status"] for t in _transitions(inquiry["id"])] == ["open"]
    # It was never resolved by the developer, so it never gains a
    # resolved-looking closed_at -- only the separate expiry timestamp.
    assert after["closed_at"] is None
    assert after["superseded_at"] is not None

    # A superseded Inquiry is terminal: the old premise can neither be
    # discussed further nor be resolved into a current justification.
    _stub_inquiry_answering(monkeypatch)
    assert admin_client.post(
        f"/interview/inquiries/{inquiry['id']}/message",
        json={"content": "続けたい"}, headers=headers,
    ).status_code == 409
    for action in ("resolve", "resume", "reopen-doubt"):
        assert admin_client.post(
            f"/interview/inquiries/{inquiry['id']}/{action}", headers=headers,
        ).status_code == 409

    # It is not counted as active either: a fresh Inquiry may be opened on
    # the current successor item.
    assert admin_client.get(
        f"/interview/sessions/{session_id}/inquiries?status=open", headers=headers,
    ).json()["items"] == []


def test_held_inquiry_is_expired_too_and_releases_the_origin_lock(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    inquiry = _open_inquiry(
        admin_client, headers, session_id,
        origin_kind="review_item", origin_id=first[0]["id"], monkeypatch=monkeypatch,
    )
    assert admin_client.post(
        f"/interview/inquiries/{inquiry['id']}/hold", headers=headers,
    ).status_code == 200

    _build(
        admin_client, headers, session_id, monkeypatch,
        [_proposal_item(current_claim="内容が変わった")],
    )

    after = _get_inquiry(admin_client, headers, inquiry["id"])
    assert after["status"] == "superseded"

    from app.db import get_conn

    with get_conn() as conn:
        origin = conn.execute(
            "SELECT status, user_decision, superseded FROM alignment_item WHERE id = ?",
            (first[0]["id"],),
        ).fetchone()
    # Released from the Inquiry lock, marked history, and -- Principle 2 --
    # never silently answered.
    assert origin["status"] == "open"
    assert origin["user_decision"] is None
    assert origin["superseded"] == 1


def test_premise_evaluation_is_idempotent_across_repeated_rebuilds(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    changed = [_proposal_item(current_claim="内容が変わった")]
    _build(admin_client, headers, session_id, monkeypatch, changed)
    _build(admin_client, headers, session_id, monkeypatch, changed)
    _build(admin_client, headers, session_id, monkeypatch, changed)

    superseding = [
        t for t in _transitions(resolved["id"]) if t["to_status"] == "superseded"
    ]
    assert len(superseding) == 1


def test_failed_build_transaction_leaves_inquiries_untouched(
    admin_client, tmp_path, monkeypatch,
):
    headers, system_id, session_id, _snapshot_id = _setup(admin_client, tmp_path)
    first = _build(admin_client, headers, session_id, monkeypatch, [_proposal_item()])
    resolved = _resolved_inquiry_on_first_item(
        admin_client, headers, session_id, first[0]["id"], monkeypatch,
    )

    from app.routes import interview_alignment as alignment_routes

    real_evaluate = alignment_routes.evaluate_inquiry_premises

    def exploding_evaluate(conn, **kwargs):
        real_evaluate(conn, **kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(alignment_routes, "evaluate_inquiry_premises", exploding_evaluate)
    _stub_build(monkeypatch, items=[_proposal_item(current_claim="内容が変わった")])
    with pytest.raises(RuntimeError):
        admin_client.post(
            f"/interview/sessions/{session_id}/alignment/build", headers=headers,
        )

    after = _get_inquiry(admin_client, headers, resolved["id"])
    assert after["status"] == "resolved"
    assert after["premise_evaluation"] is None
    assert [t["to_status"] for t in _transitions(resolved["id"])] == ["resolved"]


def test_premise_evaluation_is_isolated_per_system(admin_client, tmp_path, monkeypatch):
    headers_a, _system_a, session_a, _snapshot_a = _setup(
        admin_client, tmp_path, name="System A", subdir="a",
    )
    headers_b, _system_b, session_b, _snapshot_b = _setup(
        admin_client, tmp_path, name="System B", subdir="b",
    )

    first_a = _build(admin_client, headers_a, session_a, monkeypatch, [_proposal_item()])
    first_b = _build(admin_client, headers_b, session_b, monkeypatch, [_proposal_item()])
    # Identical content in both Systems must still produce distinct subjects.
    assert first_a[0]["review_subject_id"] != first_b[0]["review_subject_id"]

    resolved_a = _resolved_inquiry_on_first_item(
        admin_client, headers_a, session_a, first_a[0]["id"], monkeypatch,
    )
    resolved_b = _resolved_inquiry_on_first_item(
        admin_client, headers_b, session_b, first_b[0]["id"], monkeypatch,
    )

    # Only System A rebuilds with changed content.
    _build(
        admin_client, headers_a, session_a, monkeypatch,
        [_proposal_item(current_claim="内容が変わった")],
    )

    assert _get_inquiry(admin_client, headers_a, resolved_a["id"])["status"] == "superseded"
    after_b = _get_inquiry(admin_client, headers_b, resolved_b["id"])
    assert after_b["status"] == "resolved"
    assert after_b["premise_evaluation"] is None
