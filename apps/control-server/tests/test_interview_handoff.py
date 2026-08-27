"""Tests for Issue #291: answerable knowledge areas + handoff.

Covers:
1. PUT /interview/sessions/{id}/answerable-areas: persists the selection,
   changeable at any time, invalid area value rejected, empty list means
   NO filtering (not "every area").
2. GET /interview/sessions/{id}/qa?view=askable: excludes answered /
   superseded / handoff-pending / out-of-area rows while keeping unrouted
   null-area rows askable; the plain (non-askable) list is unchanged.
3. POST /interview/sessions/{id}/handoffs (routes/interview_handoff.py):
   origin validation, 'qa' origin leaves interview_qa.status untouched,
   'review_item' origin sets alignment_item.status='held', duplicate
   in-flight handoff rejected, invalid origin_kind 422.
4. Handoff lifecycle: pending -> answered -> returned, pending -> cancelled,
   every other transition 409; /answer never writes into the origin row
   (regression); /return + the origin item's own answer endpoint records
   the CONFIRMING user (not the assignee) plus handoff_id provenance;
   'わからない' (or a handoff) is never auto-converted into an answer value.
5. System isolation.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


# --- Fixtures / helpers -------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-handoff-test.db"))
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
        "/systems", json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)
            """,
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_qa(client, headers, session_id, question_text="この関数の目的は?"):
    r = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": question_text},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _set_knowledge_area(qa_id, area):
    """Directly set knowledge_area, bypassing the (separately-tested) LLM
    router call -- these tests only exercise the deterministic filtering
    rule, not routing itself."""
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE interview_qa SET knowledge_area = ? WHERE id = ?", (area, qa_id))


def _insert_alignment_item(session_id, system_id, snapshot_id, *, current_claim="claim"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at, completed_at)
            VALUES (?, ?, 'alignment_build', 'mock', 'mock', 'v1', 'v1', 'reasoning_llm',
                    'completed', 1, ?, ?)""",
            (system_id, snapshot_id, now, now),
        )
        run_id = run_cur.lastrowid
        cur = conn.execute(
            """INSERT INTO alignment_item
                (session_id, system_id, revision_id, snapshot_id, intent_item_id,
                 intent_summary, current_claim, current_evidence, gap_summary,
                 proposed_interpretation, alignment_state, risk_flags, confidence,
                 review_category, reason_code, user_reason, status, user_decision,
                 intelligence_run_id, is_mock, created_at, updated_at)
            VALUES (?, ?, NULL, ?, NULL, NULL, ?, '[]', NULL, NULL, 'gap', '[]',
                    'uncertain', 'must_review', 'low_confidence', 'reason', 'open',
                    NULL, ?, 1, ?, ?)""",
            (session_id, system_id, snapshot_id, current_claim, run_id, now, now),
        )
        return cur.lastrowid


def _create_handoff(client, headers, session_id, *, origin_kind="qa", origin_id, assignee="山田太郎"):
    r = client.post(
        f"/interview/sessions/{session_id}/handoffs",
        json={
            "origin_kind": origin_kind,
            "origin_id": origin_id,
            "assignee": assignee,
            "background": "背景の説明",
            "needed_decision": "決めてほしいこと",
            "priority": "high",
        },
        headers=headers,
    )
    return r


# --- Answerable areas ----------------------------------------------------------


def test_answerable_areas_default_empty(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    r = admin_client.get(f"/interview/sessions/{session_id}", headers=headers)
    assert r.json()["answerable_areas"] == []


def test_answerable_areas_update_persists_and_is_changeable(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": ["implementation", "security"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["answerable_areas"]) == ["implementation", "security"]

    # Changeable at any time -- not locked once set.
    r2 = admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": ["operations"]},
        headers=headers,
    )
    assert r2.json()["answerable_areas"] == ["operations"]

    # Empty means no filtering, not "nothing answerable".
    r3 = admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": []},
        headers=headers,
    )
    assert r3.json()["answerable_areas"] == []


def test_answerable_areas_invalid_value_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    r = admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": ["not_a_real_area"]},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# --- Askable view / duplicate suppression --------------------------------------


def test_askable_view_excludes_answered_keeps_full_list_unchanged(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "回答です", "actor": "dev"},
        headers=headers,
    )

    full = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    assert any(i["id"] == qa["id"] for i in full["items"])

    askable = admin_client.get(
        f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
    ).json()
    assert all(i["id"] != qa["id"] for i in askable["items"])


def test_askable_view_out_of_area_excluded_in_area_included(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa_security = _create_qa(admin_client, headers, session_id, "セキュリティの質問")
    qa_impl = _create_qa(admin_client, headers, session_id, "実装の質問")
    qa_unrouted = _create_qa(admin_client, headers, session_id, "未分類の質問")

    _set_knowledge_area(qa_security["id"], "security")
    _set_knowledge_area(qa_impl["id"], "implementation")
    # qa_unrouted stays knowledge_area=NULL.

    admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": ["implementation"]},
        headers=headers,
    )

    askable_ids = {
        i["id"] for i in admin_client.get(
            f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
        ).json()["items"]
    }
    assert qa_impl["id"] in askable_ids
    assert qa_unrouted["id"] in askable_ids  # null area is never hidden
    assert qa_security["id"] not in askable_ids  # out of area

    # Full list is unaffected by the area selection.
    full_ids = {
        i["id"] for i in admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()["items"]
    }
    assert {qa_security["id"], qa_impl["id"], qa_unrouted["id"]} <= full_ids


def test_askable_view_empty_areas_means_no_filtering(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    _set_knowledge_area(qa["id"], "security")
    # answerable_areas left at its default [] (no PUT call).

    askable_ids = {
        i["id"] for i in admin_client.get(
            f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
        ).json()["items"]
    }
    assert qa["id"] in askable_ids


def test_askable_view_excludes_handoff_pending(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    r = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"])
    assert r.status_code == 201, r.text
    handoff_id = r.json()["id"]

    askable_ids = {
        i["id"] for i in admin_client.get(
            f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
        ).json()["items"]
    }
    assert qa["id"] not in askable_ids

    # Full list still shows it (never hidden entirely).
    full_ids = {
        i["id"] for i in admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()["items"]
    }
    assert qa["id"] in full_ids

    # Once cancelled, it becomes askable again.
    admin_client.post(f"/interview/handoffs/{handoff_id}/cancel", headers=headers)
    askable_ids_after = {
        i["id"] for i in admin_client.get(
            f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
        ).json()["items"]
    }
    assert qa["id"] in askable_ids_after


# --- Handoff creation / origin validation --------------------------------------


def test_create_handoff_qa_origin_leaves_qa_status_untouched(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    r = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["origin_kind"] == "qa"
    assert body["assignee"] == "山田太郎"

    qa_after = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item = next(i for i in qa_after["items"] if i["id"] == qa["id"])
    assert item["status"] == "open"  # untouched
    assert item["answer_text"] is None
    assert item["handoff_id"] == body["id"]


def test_create_handoff_review_item_origin_sets_held_status(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(session_id, system_id, snapshot_id)

    r = _create_handoff(admin_client, headers, session_id, origin_kind="review_item", origin_id=item_id)
    assert r.status_code == 201, r.text
    handoff_id = r.json()["id"]

    listed = admin_client.get(f"/interview/sessions/{session_id}/alignment", headers=headers).json()
    all_items = [it for items in listed["items_by_category"].values() for it in items]
    item = next(i for i in all_items if i["id"] == item_id)
    assert item["status"] == "held"
    assert item["handoff_id"] == handoff_id
    assert item["user_decision"] is None  # a handoff is never a decision


def test_create_handoff_invalid_origin_kind_422(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    r = admin_client.post(
        f"/interview/sessions/{session_id}/handoffs",
        json={
            "origin_kind": "not_real", "origin_id": 1, "assignee": "a",
            "background": "b", "needed_decision": "d",
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_create_handoff_origin_not_found_404(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    r = _create_handoff(admin_client, headers, session_id, origin_id=999999)
    assert r.status_code == 404, r.text


def test_create_handoff_duplicate_in_flight_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    first = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"])
    assert first.status_code == 201, first.text

    second = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"])
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "handoff_already_in_flight"


# --- Handoff lifecycle ----------------------------------------------------------


def test_handoff_lifecycle_pending_answered_returned(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]

    r = admin_client.post(
        f"/interview/handoffs/{handoff_id}/answer",
        json={"answer_text": "担当者の回答です", "answered_by": "assignee@example.com"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "answered"
    assert r.json()["answer_text"] == "担当者の回答です"

    # Regression: the assignee's answer must NEVER be written into the
    # origin interview_qa row.
    qa_after = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item = next(i for i in qa_after["items"] if i["id"] == qa["id"])
    assert item["status"] == "open"
    assert item["answer_text"] is None
    assert item["answered_by"] is None

    r2 = admin_client.post(f"/interview/handoffs/{handoff_id}/return", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "returned"

    # Still not written into the origin row until the ORIGINAL user
    # explicitly confirms via the qa item's own answer endpoint.
    qa_after2 = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item2 = next(i for i in qa_after2["items"] if i["id"] == qa["id"])
    assert item2["status"] == "open"
    assert item2["answer_text"] is None


def test_handoff_pending_to_cancelled(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]

    r = admin_client.post(f"/interview/handoffs/{handoff_id}/cancel", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


@pytest.mark.parametrize("bad_transition", ["return", "cancel"])
def test_handoff_invalid_transition_from_pending_409(admin_client, bad_transition):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]

    if bad_transition == "return":
        r = admin_client.post(f"/interview/handoffs/{handoff_id}/return", headers=headers)
        assert r.status_code == 409, r.text
    # 'cancel' from pending is valid -- only assert 'return' is invalid here;
    # the dedicated cancel-path test covers the valid case.


def test_handoff_invalid_transition_from_answered_409(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]
    admin_client.post(
        f"/interview/handoffs/{handoff_id}/answer",
        json={"answer_text": "x", "answered_by": "y"},
        headers=headers,
    )
    r = admin_client.post(f"/interview/handoffs/{handoff_id}/cancel", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invalid_handoff_transition"


def test_handoff_invalid_transition_from_terminal_states_409(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]
    admin_client.post(f"/interview/handoffs/{handoff_id}/cancel", headers=headers)

    for path in ("answer", "return", "cancel"):
        body = {"answer_text": "x", "answered_by": "y"} if path == "answer" else None
        r = admin_client.post(f"/interview/handoffs/{handoff_id}/{path}", json=body, headers=headers)
        assert r.status_code == 409, r.text


# --- Return -> explicit confirm --------------------------------------------------


def test_handoff_return_then_explicit_confirm_records_confirming_user(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]

    admin_client.post(
        f"/interview/handoffs/{handoff_id}/answer",
        json={"answer_text": "担当者の回答", "answered_by": "assignee@example.com"},
        headers=headers,
    )
    admin_client.post(f"/interview/handoffs/{handoff_id}/return", headers=headers)

    # The ORIGINAL developer explicitly confirms via the qa item's own
    # answer endpoint, optionally prefilled with the handoff's answer_text
    # client-side -- here we submit their own (possibly edited) text.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={
            "answer_text": "担当者の回答を確認しました",
            "actor": "original_dev@example.com",
            "handoff_id": handoff_id,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qa"]["answer_text"] == "担当者の回答を確認しました"
    # Confirmed by the ORIGINAL user, never the assignee.
    assert body["qa"]["answered_by"] == "original_dev@example.com"
    assert body["qa"]["status"] == "answered"
    assert body["qa"]["handoff_id"] == handoff_id


def test_handoff_confirm_rejected_before_returned(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]
    # Still 'pending' -- not yet returned.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "早すぎる確認", "actor": "dev", "handoff_id": handoff_id},
        headers=headers,
    )
    assert r.status_code == 409, r.text


def test_handoff_confirm_rejected_for_mismatched_question(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa1 = _create_qa(admin_client, headers, session_id, "質問1")
    qa2 = _create_qa(admin_client, headers, session_id, "質問2")
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa1["id"]).json()["id"]
    admin_client.post(
        f"/interview/handoffs/{handoff_id}/answer",
        json={"answer_text": "x", "answered_by": "y"}, headers=headers,
    )
    admin_client.post(f"/interview/handoffs/{handoff_id}/return", headers=headers)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa2['id']}/answer",
        json={"answer_text": "誤って別の質問に確認", "actor": "dev", "handoff_id": handoff_id},
        headers=headers,
    )
    assert r.status_code == 409, r.text


def test_handoff_never_auto_converts_to_answer_value(admin_client):
    """No code path maps a hold/handoff to an answer value by itself --
    'わからない' semantics are never fabricated from a handoff."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    handoff_id = _create_handoff(admin_client, headers, session_id, origin_id=qa["id"]).json()["id"]
    admin_client.post(
        f"/interview/handoffs/{handoff_id}/answer",
        json={"answer_text": "担当者の回答", "answered_by": "assignee@example.com"},
        headers=headers,
    )
    admin_client.post(f"/interview/handoffs/{handoff_id}/return", headers=headers)

    # Even after the handoff is fully answered+returned, the origin
    # question's own status/answer_text stay untouched until the developer
    # explicitly calls the answer endpoint themselves.
    qa_after = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item = next(i for i in qa_after["items"] if i["id"] == qa["id"])
    assert item["status"] == "open"
    assert item["answer_text"] is None
    assert item["status"] != "unconfirmed"
    assert item["status"] != "answered"


# --- Listing / filtering --------------------------------------------------------


def test_list_handoffs_filters_by_status(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa1 = _create_qa(admin_client, headers, session_id, "質問1")
    qa2 = _create_qa(admin_client, headers, session_id, "質問2")
    h1 = _create_handoff(admin_client, headers, session_id, origin_id=qa1["id"]).json()["id"]
    h2 = _create_handoff(admin_client, headers, session_id, origin_id=qa2["id"]).json()["id"]
    admin_client.post(f"/interview/handoffs/{h2}/cancel", headers=headers)

    pending = admin_client.get(
        f"/interview/sessions/{session_id}/handoffs?status=pending", headers=headers,
    ).json()
    assert [i["id"] for i in pending["items"]] == [h1]

    cancelled = admin_client.get(
        f"/interview/sessions/{session_id}/handoffs?status=cancelled", headers=headers,
    ).json()
    assert [i["id"] for i in cancelled["items"]] == [h2]

    all_items = admin_client.get(f"/interview/sessions/{session_id}/handoffs", headers=headers).json()
    assert {i["id"] for i in all_items["items"]} == {h1, h2}


# --- System isolation ------------------------------------------------------------


def test_handoff_system_isolation(admin_client):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = _create_session(admin_client, headers_a, snapshot_a)
    qa_a = _create_qa(admin_client, headers_a, session_a)
    handoff = _create_handoff(admin_client, headers_a, session_a, origin_id=qa_a["id"])
    assert handoff.status_code == 201, handoff.text
    handoff_id = handoff.json()["id"]

    # System B cannot see or act on System A's session/handoff.
    r = admin_client.post(
        f"/interview/sessions/{session_a}/handoffs",
        json={
            "origin_kind": "qa", "origin_id": qa_a["id"], "assignee": "a",
            "background": "b", "needed_decision": "d",
        },
        headers=headers_b,
    )
    assert r.status_code == 404, r.text

    r2 = admin_client.get(f"/interview/sessions/{session_a}/handoffs", headers=headers_b)
    assert r2.status_code == 404, r2.text

    r3 = admin_client.post(f"/interview/handoffs/{handoff_id}/cancel", headers=headers_b)
    assert r3.status_code == 404, r3.text
