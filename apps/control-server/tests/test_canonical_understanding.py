"""Issue #464 -- System-canonical Understanding vs Interview premise.

The defect this Epic fixes is one ownership boundary that did not exist: the
Overview could report a Vision while a brand-new Interview about the SAME
System treated it as unknown, because each screen re-derived "the current
understanding" for itself (the Overview from the newest session row, the
Interview from its own in-progress column) and creation order is not
promotion order.

Part 1 covers the pure contract in `app/canonical_understanding.py`: digests,
the four-value premise verdict and its first-match order, and the finite
vocabularies.

Part 2 walks the six scenarios the Issue names, end to end through the API:

1. a promoted Vision appears identically on the Overview and in a brand-new
   Interview's initial context;
2. a session whose System head moved is `stale` and cannot silently continue;
3. rebasing produces a NEW session on today's head and leaves the original
   conversation and its premise byte-for-byte unchanged;
4. two candidates from different heads cannot both win -- the one built on the
   older head is refused, and the refusal is auditable;
5. an unconfirmed Interview result never changes what the Overview reports as
   canonical;
6. a legacy session whose baseline cannot be restored is reported
   `invalid` (legacy-unbased), never `current`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import canonical_understanding as canonical


# ---------------------------------------------------------------------------
# Part 1: the pure contract
# ---------------------------------------------------------------------------


def _item(name, *, summary="説明"):
    return {
        "name": name,
        "summary": summary,
        "confidence": {"level": "likely", "reason": ""},
        "evidence": [
            {"path": "a.py", "start_line": 1, "end_line": 2, "summary": "根拠"}
        ],
        "why_core": "",
        "related_docs": [],
        "related_apis": [],
        "children": [],
    }


def _understanding(**sections):
    base = {
        "vision": [],
        "system_purpose": [],
        "core_capabilities": [],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
    }
    base.update(sections)
    return base


def test_finite_vocabularies_are_closed():
    assert set(canonical.PREMISE_STATES) == {"current", "stale", "missing", "invalid"}
    assert set(canonical.PREMISE_DISPOSITIONS) == {"active", "rebased", "branched"}
    assert set(canonical.REVISION_STATUSES) == {"candidate", "canonical", "superseded"}
    # Every reason code has a message, and every state a label: a verdict must
    # never reach a screen as a bare enum value.
    for code in canonical.PREMISE_REASON_CODES:
        assert canonical.PREMISE_REASON_MESSAGES[code]
    for state in canonical.PREMISE_STATES:
        assert canonical.PREMISE_STATE_LABELS[state]


def test_content_digest_ignores_order_and_confidence_but_not_meaning():
    a = _understanding(core_capabilities=[_item("A"), _item("B")])
    b = _understanding(core_capabilities=[_item("B"), _item("A")])
    assert canonical.compute_content_digest(a) == canonical.compute_content_digest(b)

    wobble = _understanding(core_capabilities=[_item("A"), _item("B")])
    wobble["core_capabilities"][0]["confidence"] = {"level": "uncertain", "reason": "x"}
    assert canonical.compute_content_digest(wobble) == canonical.compute_content_digest(a)

    changed = _understanding(core_capabilities=[_item("A", summary="別"), _item("B")])
    assert canonical.compute_content_digest(changed) != canonical.compute_content_digest(a)


def test_premise_digest_excludes_the_revision_id():
    """The same content re-promoted is the same premise.

    The digest answers 「この会話が確定済みとして扱ってよい内容は同じか」, so
    tying it to a row id would make an identical re-promotion look like a
    change and expire every conversation for nothing.
    """
    understanding = _understanding(system_purpose=[_item("P")])
    first = canonical.PremiseBundle(
        premise_version=canonical.CANONICAL_PREMISE_VERSION,
        revision_id=1,
        content_digest=canonical.compute_content_digest(understanding),
        understanding=understanding,
        intent_items=[],
    )
    second = canonical.PremiseBundle(
        premise_version=canonical.CANONICAL_PREMISE_VERSION,
        revision_id=99,
        content_digest=canonical.compute_content_digest(understanding),
        understanding=understanding,
        intent_items=[],
    )
    assert first.digest == second.digest


def test_premise_digest_moves_with_confirmed_intent():
    understanding = _understanding(system_purpose=[_item("P")])
    without = canonical.PremiseBundle(
        premise_version=canonical.CANONICAL_PREMISE_VERSION,
        revision_id=1,
        content_digest=canonical.compute_content_digest(understanding),
        understanding=understanding,
        intent_items=[],
    )
    with_intent = canonical.PremiseBundle(
        premise_version=canonical.CANONICAL_PREMISE_VERSION,
        revision_id=1,
        content_digest=without.content_digest,
        understanding=understanding,
        intent_items=[
            {
                "field": "goal",
                "value_text": "レビュー時間を半分にする",
                "status": "confirmed",
                "origin": "user",
                "is_mock": False,
            }
        ],
    )
    assert with_intent.digest != without.digest


def test_only_confirmed_intent_enters_the_premise():
    """A per-session hypothesis is never promoted to System-wide state.

    #464 non-goal 1: 「Interview ごとの仮説 ... まで System 共通状態にすること」.
    An unconfirmed Intent proposal is exactly such a hypothesis.
    """
    rows = [
        {"field": "goal", "value_text": "A", "status": "confirmed", "origin": "user", "is_mock": 0},
        {"field": "pain", "value_text": "B", "status": "proposed", "origin": "ai", "is_mock": 0},
    ]
    items = canonical.normalize_intent_items(rows)
    assert [i["field"] for i in items] == ["goal"]


# ---------------------------------------------------------------------------
# Part 2: the six scenarios, through the API
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-canonical-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["token"] if "token" in r.json() else r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _init_repo(tmp_path, name="repo"):
    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True
    )
    with open(os.path.join(repo, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


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


def _setup(client, tmp_path, name="System A"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, f"repo-{name.replace(' ', '-')}")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id


def _settle_initial_build(session_id: int) -> None:
    from app.db import get_conn
    from app.interview_workflow import finish_process_run

    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND status = 'running' ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is not None:
            finish_process_run(conn, row["id"], ok=True, error=None)


def _create_session(client, headers, snapshot_id):
    r = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
    )
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _settle_initial_build(session_id)
    return session_id


def _store_candidate(session_id, system_id, snapshot_id, understanding):
    """Append a candidate revision exactly as a successful rebuild would."""
    from app.db import get_conn

    now = time.time()
    payload = json.dumps(understanding, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET current_understanding = ?, updated_at = ? WHERE id = ?",
            (payload, now, session_id),
        )
        cur = conn.execute(
            """INSERT INTO understanding_revision
                   (session_id, system_id, snapshot_id, current_understanding,
                    gap_analysis, created_at, content_digest, status)
               VALUES (?, ?, ?, ?, '[]', ?, ?, 'candidate')""",
            (
                session_id, system_id, snapshot_id, payload, now,
                canonical.compute_content_digest(understanding),
            ),
        )
        return cur.lastrowid


def _confirm_intent(session_id, system_id, field, value_text):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_intent_item
                   (session_id, system_id, field, value_text, status, origin,
                    decision_method, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'confirmed', 'user', 'manual', ?, ?)""",
            (session_id, system_id, field, value_text, now, now),
        )


def _promote(client, headers, session_id, revision_id, *, expected_head, expected_version):
    return client.post(
        f"/interview/sessions/{session_id}/promote-understanding",
        json={
            "revision_id": revision_id,
            "expected_head_revision_id": expected_head,
            "expected_head_version": expected_version,
        },
        headers=headers,
    )


def _premise(client, headers, session_id):
    r = client.get(f"/interview/sessions/{session_id}/premise", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- Scenario 1 --------------------------------------------------------------


def test_overview_and_a_new_interview_share_the_canonical_vision(admin_client, tmp_path):
    """R1 has a Vision; the Overview and a brand-new Interview both show it.

    This is the reported symptom, reduced to its smallest reproduction. Before
    #464 the new session started with `current_understanding = NULL` while the
    Overview read some other session's row, so the two screens disagreed about
    the same project.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path)
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    understanding = _understanding(
        vision=[_item("レビュー時間を半分にする")],
        system_purpose=[_item("コードレビュー支援")],
        core_capabilities=[_item("差分要約")],
    )
    r1 = _store_candidate(s0, system_id, snapshot_id, understanding)
    _confirm_intent(s0, system_id, "goal", "レビュー時間を半分にする")
    promoted = _promote(
        client, headers, s0, r1, expected_head=None, expected_version=None
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["head_version"] == 1

    # A brand-new Interview pins that head and starts from its content.
    s1 = _create_session(client, headers, snapshot_id)
    detail = client.get(f"/interview/sessions/{s1}", headers=headers).json()
    assert detail["current_understanding"]["vision"][0]["name"] == "レビュー時間を半分にする"
    assert detail["base_understanding_revision_id"] == r1
    assert detail["premise_state"] == "current"

    brief = client.get(
        "/interview/understanding-brief", params={"session_id": s1}, headers=headers
    ).json()
    assert brief["vision"]["name"] == "レビュー時間を半分にする"

    overview = client.get("/overview", headers=headers).json()
    assert overview["understanding_source"] == "canonical_head"
    assert overview["canonical_revision_id"] == r1
    assert overview["brief"]["vision"]["name"] == "レビュー時間を半分にする"


def test_confirmed_intent_reaches_a_new_session_through_the_premise(
    admin_client, tmp_path
):
    """The developer's confirmed goal belongs to the System, not to a session.

    Without the premise carrying it, a new Interview reports 「Vision 未設定」
    about a System whose Vision the developer settled long ago -- and it is
    never copied as a row, so its authorship stays with the session that
    recorded it.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Intent")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    understanding = _understanding(
        system_purpose=[_item("コードレビュー支援")],
        core_capabilities=[_item("差分要約")],
    )
    r1 = _store_candidate(s0, system_id, snapshot_id, understanding)
    _confirm_intent(s0, system_id, "goal", "開発者が仕様を5秒で把握できる")
    assert _promote(
        client, headers, s0, r1, expected_head=None, expected_version=None
    ).status_code == 200

    s1 = _create_session(client, headers, snapshot_id)
    brief = client.get(
        "/interview/understanding-brief", params={"session_id": s1}, headers=headers
    ).json()
    assert brief["vision"]["name"] == "開発者が仕様を5秒で把握できる"
    assert brief["vision"]["provenance"] == "developer_intent"

    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_intent_item WHERE session_id = ?",
            (s1,),
        ).fetchone()
    assert rows["n"] == 0, "the premise is read, never copied into the new session"


# --- Scenario 2 --------------------------------------------------------------


def test_a_session_whose_head_moved_is_stale_and_cannot_continue(admin_client, tmp_path):
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Stale")
    headers = _headers(token, system_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    assert _promote(
        client, headers, s_head, r1, expected_head=None, expected_version=None
    ).status_code == 200

    s1 = _create_session(client, headers, snapshot_id)
    assert _premise(client, headers, s1)["state"] == "current"

    # A different session promotes R2 while S1 is open.
    s_other = _create_session(client, headers, snapshot_id)
    r2 = _store_candidate(
        s_other, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    assert _promote(
        client, headers, s_other, r2, expected_head=r1, expected_version=1
    ).status_code == 200

    premise = _premise(client, headers, s1)
    assert premise["state"] == "stale"
    assert premise["reason_code"] == "head_moved"
    assert premise["continuable"] is False
    assert set(premise["available_actions"]) == {"start_new_session", "rebase", "branch"}

    # The conversation cannot be silently continued.
    turn = client.post(
        f"/interview/sessions/{s1}/dialogue-turn",
        json={"user_message": "続けて"},
        headers=headers,
    )
    assert turn.status_code == 409, turn.text
    assert turn.json()["detail"]["code"] == "interview_premise_stale"

    update = client.post(
        f"/interview/sessions/{s1}/update-understanding", headers=headers
    )
    assert update.status_code == 409
    assert update.json()["detail"]["code"] == "interview_premise_stale"


def test_branching_acknowledges_the_old_premise_without_restoring_it(
    admin_client, tmp_path
):
    """A branch is an acknowledgement, not a repair.

    The verdict stays `stale` -- nothing about the System changed back. What
    changes is that the developer said so on the record, so the conversation
    may continue; its candidate still cannot be promoted.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Branch")
    headers = _headers(token, system_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s_head, r1, expected_head=None, expected_version=None)

    s1 = _create_session(client, headers, snapshot_id)
    s_other = _create_session(client, headers, snapshot_id)
    r2 = _store_candidate(
        s_other, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    _promote(client, headers, s_other, r2, expected_head=r1, expected_version=1)

    branched = client.post(
        f"/interview/sessions/{s1}/premise/branch", json={"note": "旧前提の検討"},
        headers=headers,
    )
    assert branched.status_code == 200, branched.text
    body = branched.json()
    assert body["state"] == "stale"
    assert body["disposition"] == "branched"
    assert body["continuable"] is True

    # Continuing is allowed again...
    assert client.post(
        f"/interview/sessions/{s1}/update-understanding", headers=headers
    ).status_code != 409
    # ...but promoting from the old premise is still impossible.
    r_old = _store_candidate(
        s1, system_id, snapshot_id,
        _understanding(system_purpose=[_item("Pold")], core_capabilities=[_item("Cold")]),
    )
    refused = _promote(client, headers, s1, r_old, expected_head=r2, expected_version=2)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "premise_not_current"


def test_the_automatic_refresh_stops_at_the_same_boundary(admin_client, tmp_path):
    """An automatic rebuild consumes the premise too.

    The developer's answers are committed before the job is ever enqueued, so
    nothing is lost -- only the rebuild is skipped, and it is recorded as a
    normal terminal note rather than a failure, because nothing went wrong.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Refresh")
    headers = _headers(token, system_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s_head, r1, expected_head=None, expected_version=None)
    s1 = _create_session(client, headers, snapshot_id)
    s_other = _create_session(client, headers, snapshot_id)
    r2 = _store_candidate(
        s_other, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    _promote(client, headers, s_other, r2, expected_head=r1, expected_version=1)

    from app import interview_refresh
    from app.db import get_conn
    from app.state_messages import refresh_job_message

    with get_conn() as conn:
        job_id = conn.execute(
            """INSERT INTO interview_refresh_job
                   (session_id, system_id, trigger_kind, base_answer_marker,
                    status, created_at)
               VALUES (?, ?, 'qa_answer', 0, 'pending', ?)""",
            (s1, system_id, time.time()),
        ).lastrowid
    interview_refresh.run_refresh_job(job_id)
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM interview_refresh_job WHERE id = ?", (job_id,)
        ).fetchone()
    assert job["status"] == "updated"
    assert job["error"] == refresh_job_message("skipped_premise_not_current")


# --- Scenario 3 --------------------------------------------------------------


def test_rebase_creates_a_new_session_and_leaves_the_original_untouched(
    admin_client, tmp_path
):
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Rebase")
    headers = _headers(token, system_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s_head, r1, expected_head=None, expected_version=None)

    s1 = _create_session(client, headers, snapshot_id)
    client.post(
        f"/interview/sessions/{s1}/messages",
        json={"role": "user", "content": "最初の質問"},
        headers=headers,
    )
    before = client.get(f"/interview/sessions/{s1}", headers=headers).json()

    s_other = _create_session(client, headers, snapshot_id)
    r2 = _store_candidate(
        s_other, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    _promote(client, headers, s_other, r2, expected_head=r1, expected_version=1)

    rebased = client.post(
        f"/interview/sessions/{s1}/premise/rebase", json={"note": "最新で続ける"},
        headers=headers,
    )
    assert rebased.status_code == 201, rebased.text
    s2 = rebased.json()["new_session_id"]
    assert rebased.json()["premise"]["state"] == "current"
    assert rebased.json()["premise"]["base_revision_id"] == r2

    after = client.get(f"/interview/sessions/{s1}", headers=headers).json()
    # The original conversation and its premise are unchanged; only the
    # disposition (a new, explicit fact) was added.
    assert [m["content"] for m in after["messages"]] == [
        m["content"] for m in before["messages"]
    ]
    assert after["base_understanding_revision_id"] == r1
    assert after["premise_state"] == "stale"
    assert after["premise_disposition"] == "rebased"

    s2_detail = client.get(f"/interview/sessions/{s2}", headers=headers).json()
    assert s2_detail["current_understanding"]["system_purpose"][0]["name"] == "P2"

    events = client.get("/understanding-canonical-events", headers=headers).json()
    kinds = [e["event_kind"] for e in events["items"]]
    assert "session_rebased" in kinds
    rebase_event = next(e for e in events["items"] if e["event_kind"] == "session_rebased")
    assert rebase_event["decision_method"] == "manual"
    assert rebase_event["session_id"] == s1
    assert rebase_event["related_session_id"] == s2

    # The original now points at where the conversation continued, so the
    # developer is never left on a dead end with no way forward.
    stale_premise = _premise(client, headers, s1)
    assert stale_premise["successor_session_id"] == s2
    assert stale_premise["available_actions"] == ["open_successor_session"]


# --- Scenario 4 --------------------------------------------------------------


def test_a_candidate_from_an_older_head_cannot_overwrite_a_newer_one(
    admin_client, tmp_path
):
    """Acceptance condition 9, and #464 non-goal 3 (no last-write-wins)."""
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Conflict")
    headers = _headers(token, system_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s_head, r1, expected_head=None, expected_version=None)

    # Two sessions, both started while R1 was canonical.
    s_a = _create_session(client, headers, snapshot_id)
    s_b = _create_session(client, headers, snapshot_id)
    cand_a = _store_candidate(
        s_a, system_id, snapshot_id,
        _understanding(system_purpose=[_item("Pa")], core_capabilities=[_item("Ca")]),
    )
    cand_b = _store_candidate(
        s_b, system_id, snapshot_id,
        _understanding(system_purpose=[_item("Pb")], core_capabilities=[_item("Cb")]),
    )

    first = _promote(client, headers, s_a, cand_a, expected_head=r1, expected_version=1)
    assert first.status_code == 200, first.text
    assert first.json()["head_version"] == 2

    second = _promote(client, headers, s_b, cand_b, expected_head=r1, expected_version=1)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "premise_not_current"
    assert detail["head_revision_id"] == cand_a

    head = client.get("/understanding-head", headers=headers).json()
    assert head["revision_id"] == cand_a
    assert head["head_version"] == 2

    # The refusal is auditable and countable.
    metrics = client.get("/understanding-canonical-metrics", headers=headers).json()
    assert metrics["promotion_conflict_codes"]["premise_not_current"] == 1
    assert metrics["session_premise_counts"]["stale"] >= 1


def _rebase(client, headers, session_id):
    r = client.post(
        f"/interview/sessions/{session_id}/premise/rebase", json={}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["new_session_id"]


def test_a_stale_head_expectation_is_refused_even_from_a_current_session(
    admin_client, tmp_path
):
    """The developer's 「これで確定する」 was about a specific head.

    If the head moved between reading the screen and confirming, that
    confirmation was about something else -- so it is refused rather than
    applied to a state nobody looked at.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System CAS")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)

    # Continuing after a promotion is a rebase: the promoting session keeps the
    # premise its whole conversation happened on.
    s1 = _rebase(client, headers, s0)
    r2 = _store_candidate(
        s1, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    stale_expectation = _promote(
        client, headers, s1, r2, expected_head=None, expected_version=0
    )
    assert stale_expectation.status_code == 409
    assert stale_expectation.json()["detail"]["code"] == "head_revision_mismatch"

    ok = _promote(client, headers, s1, r2, expected_head=r1, expected_version=1)
    assert ok.status_code == 200, ok.text


# --- Scenario 5 --------------------------------------------------------------


def test_an_unconfirmed_candidate_does_not_change_the_overview(admin_client, tmp_path):
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Candidate")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(
            vision=[_item("確定した Vision")],
            system_purpose=[_item("P1")],
            core_capabilities=[_item("C1")],
        ),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)

    before = client.get("/overview", headers=headers).json()

    # The SAME session keeps working and produces a new candidate.
    _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(
            vision=[_item("まだ確認していない Vision")],
            system_purpose=[_item("Pdraft")],
            core_capabilities=[_item("Cdraft")],
        ),
    )

    after = client.get("/overview", headers=headers).json()
    assert after["canonical_revision_id"] == r1
    assert after["brief"]["vision"]["name"] == "確定した Vision"
    assert (
        after["brief"]["system_purpose"][0]["name"]
        == before["brief"]["system_purpose"][0]["name"]
    )


def test_an_unpromoted_system_has_no_canonical_understanding_on_the_overview(
    admin_client, tmp_path
):
    """A System nobody has promoted has no canonical Understanding -- at all.

    The in-progress work is still shown, but in its OWN section. Putting it in
    the canonical slot with a caveat still makes the page's central claim
    depend on session creation order, which is the defect this Epic exists to
    remove (#464 acceptance: candidate は別セクション).
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Unpromoted")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(
            vision=[_item("未確定の Vision")],
            system_purpose=[_item("P1")],
            core_capabilities=[_item("C1")],
        ),
    )
    overview = client.get("/overview", headers=headers).json()
    assert overview["brief"] is None
    assert overview["understanding_source"] == "not_promoted"
    assert overview["canonical_revision_id"] is None
    # The work in progress is reported separately, and named as such.
    assert overview["candidate_state"] == "unpromoted"
    assert overview["candidate_session_id"] == s0
    assert overview["candidate_brief"]["vision"]["name"] == "未確定の Vision"
    # No canonical claim means no basis for comparison -- and that is a fact
    # about the System, never a failed read.
    assert overview["findings_state"] == "not_compared"
    assert overview["findings_baseline_state"] == "no_baseline"
    # 「次にやること」 still follows the work in progress.
    assert overview["next_action"] is not None

    head = client.get("/understanding-head", headers=headers).json()
    assert head["revision_id"] is None


def test_a_newer_candidate_is_reported_beside_the_canonical_head(
    admin_client, tmp_path
):
    """Unconfirmed work never edits the canonical claim, but is still visible."""
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Beside")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(
            vision=[_item("確定した Vision")],
            system_purpose=[_item("P1")],
            core_capabilities=[_item("C1")],
        ),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)

    settled = client.get("/overview", headers=headers).json()
    assert settled["understanding_source"] == "canonical_head"
    assert settled["candidate_state"] == "same_as_head"

    s1 = _rebase(client, headers, s0)
    _store_candidate(
        s1, system_id, snapshot_id,
        _understanding(
            vision=[_item("まだ確認していない Vision")],
            system_purpose=[_item("P2")],
            core_capabilities=[_item("C2")],
        ),
    )
    body = client.get("/overview", headers=headers).json()
    assert body["brief"]["vision"]["name"] == "確定した Vision"
    assert body["canonical_revision_id"] == r1
    assert body["candidate_state"] == "newer_than_head"
    assert body["candidate_brief"]["vision"]["name"] == "まだ確認していない Vision"


# --- Scenario 6 --------------------------------------------------------------


def test_a_legacy_session_without_a_captured_premise_is_invalid_not_current(
    admin_client, tmp_path
):
    """The one answer a gate must never give for "we never recorded this"."""
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Legacy")
    headers = _headers(token, system_id)

    s_legacy = _create_session(client, headers, snapshot_id)
    from app.db import get_conn

    with get_conn() as conn:
        # Exactly what a pre-#464 row looks like.
        conn.execute(
            """UPDATE interview_session
               SET premise_tracking_version = NULL, base_premise_json = NULL,
                   base_premise_digest = NULL, base_understanding_revision_id = NULL
               WHERE id = ?""",
            (s_legacy,),
        )

    premise = _premise(client, headers, s_legacy)
    assert premise["state"] == "invalid"
    assert premise["reason_code"] == "premise_not_captured"
    assert premise["continuable"] is False
    assert "adopt_current_premise" in premise["available_actions"]

    turn = client.post(
        f"/interview/sessions/{s_legacy}/dialogue-turn",
        json={"user_message": "続けて"},
        headers=headers,
    )
    assert turn.status_code == 409

    adopted = client.post(
        f"/interview/sessions/{s_legacy}/premise/adopt-current",
        json={"note": "今の前提で続ける"},
        headers=headers,
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["state"] == "current"
    events = client.get("/understanding-canonical-events", headers=headers).json()
    assert any(e["event_kind"] == "session_rebased" for e in events["items"])


def test_a_deleted_base_revision_is_missing_not_stale(admin_client, tmp_path):
    """"The ground disappeared" is a different answer from "it moved"."""
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Missing")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)
    s1 = _create_session(client, headers, snapshot_id)

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("DELETE FROM system_understanding_head WHERE system_id = ?", (system_id,))
        conn.execute("DELETE FROM understanding_revision WHERE id = ?", (r1,))

    premise = _premise(client, headers, s1)
    assert premise["state"] == "missing"
    assert premise["reason_code"] == "base_revision_missing"


# --- Review round 1: premise immutability, revalidation, Intent lineage ------


def test_promotion_never_rewrites_the_promoting_session_premise(
    admin_client, tmp_path
):
    """The conversation keeps the ground it actually happened on.

    Advancing the promoting session's baseline to the revision it just
    produced kept the session continuable, but it rewrote the premise every
    existing message had been reasoned against -- the session would then claim
    it had started from content that did not exist when it started. 「前提が
    同じときだけ続ける」 cannot survive silently changing the premise.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Immutable")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    before = _premise(client, headers, s0)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    assert _promote(
        client, headers, s0, r1, expected_head=None, expected_version=None
    ).status_code == 200

    after = _premise(client, headers, s0)
    assert after["base_revision_id"] == before["base_revision_id"]
    assert after["base_premise_digest"] == before["base_premise_digest"]
    assert after["result_revision_id"] == r1

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT base_premise_json FROM interview_session WHERE id = ?", (s0,)
        ).fetchone()
    assert json.loads(row["base_premise_json"])["revision_id"] is None

    # The session is stale, and says WHY: it settled the head itself. That is a
    # different fact from somebody else having moved on, and the way forward is
    # a rebase rather than three equal choices.
    assert after["state"] == "stale"
    assert after["reason_code"] == "promoted_by_this_session"
    assert after["available_actions"][0] == "rebase"
    assert after["continuable"] is False

    turn = client.post(
        f"/interview/sessions/{s0}/dialogue-turn",
        json={"user_message": "続けて"},
        headers=headers,
    )
    assert turn.status_code == 409

    s1 = _rebase(client, headers, s0)
    assert _premise(client, headers, s1)["base_revision_id"] == r1


def test_confirmed_intent_survives_a_generation_that_never_restated_it(
    admin_client, tmp_path
):
    """A confirmed Intent statement belongs to the System, not to one session.

    Reading it from the premise (rather than re-confirming it) leaves the new
    session with no row of its own, so building the next bundle from that
    session alone silently dropped the Vision the developer had settled two
    generations earlier.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Intent Lineage")
    headers = _headers(token, system_id)

    s0 = _create_session(client, headers, snapshot_id)
    _confirm_intent(s0, system_id, "goal", "レビュー時間を半分にする")
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    assert _promote(
        client, headers, s0, r1, expected_head=None, expected_version=None
    ).status_code == 200

    # Generation 2 never restates the goal.
    s1 = _rebase(client, headers, s0)
    r2 = _store_candidate(
        s1, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    assert _promote(
        client, headers, s1, r2, expected_head=r1, expected_version=1
    ).status_code == 200

    # Generation 3 still sees it.
    s2 = _rebase(client, headers, s1)
    brief = client.get(
        "/interview/understanding-brief", params={"session_id": s2}, headers=headers
    ).json()
    assert brief["vision"]["name"] == "レビュー時間を半分にする"

    overview = client.get("/overview", headers=headers).json()
    assert overview["brief"]["vision"]["name"] == "レビュー時間を半分にする"


def test_an_explicit_not_applicable_is_the_only_way_to_drop_inherited_intent(
    admin_client, tmp_path
):
    """Reopening a question is not the same as retracting a decision.

    `undecided` is one conversation's in-progress state; only the explicit
    `not_applicable` decision removes a System-level confirmed statement.
    """
    from app.canonical_understanding import merge_intent_items

    inherited = [
        {
            "field": "goal",
            "value_text": "A",
            "status": "confirmed",
            "origin": "user",
            "is_mock": False,
        }
    ]
    assert merge_intent_items(inherited, [{"field": "goal", "status": "undecided"}]) == inherited
    assert merge_intent_items(inherited, [{"field": "goal", "status": "proposed"}]) == inherited
    assert merge_intent_items(inherited, [{"field": "goal", "status": "not_applicable"}]) == []
    replaced = merge_intent_items(
        inherited,
        [{"field": "goal", "value_text": "B", "status": "confirmed", "origin": "user"}],
    )
    assert [i["value_text"] for i in replaced] == ["B"]


def _insert_understanding_graph(system_id, snapshot_id):
    from app.documentation_claim_scanner import (
        ChunkScanResult,
        ClaimEvidence,
        DocumentationClaim,
    )
    from app.understanding_graph import build_understanding_graph, save_graph_snapshot
    from app.db import get_conn

    result = ChunkScanResult(
        chunk_id="chunk-1",
        chunk_content_hash="hash-1",
        prompt_version="claim-scanner-v1",
        schema_version="claim-scanner-v1",
        claims=[
            DocumentationClaim(
                claim_type="system_purpose",
                summary="System helps inspect probe agent repositories",
                evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
                confidence=0.9,
            )
        ],
    )
    graph = build_understanding_graph([result])
    with get_conn() as conn:
        return save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)


def _review_response(purpose_name="P-from-llm"):
    return json.dumps({
        "system_purpose": [{
            "name": purpose_name,
            "summary": "s",
            "confidence": {"level": "likely", "reason": "r"},
            "evidence": [
                {"path": "README.md", "start_line": 1, "end_line": 5, "summary": "s"}
            ],
            "why_core": "",
            "related_docs": ["README.md"],
            "related_apis": [],
            "children": [],
        }],
        "core_capabilities": [],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
        "gap_analysis": [],
        "open_questions": [],
        "suggested_next_action": "resolve_open_questions",
    })


def test_a_head_that_moves_during_the_reasoning_call_discards_the_result(
    admin_client, tmp_path, monkeypatch
):
    """Issue #464 structural debt 9: revalidate immediately before storing.

    The connection is released for the whole reasoning call (CLAUDE.md forbids
    holding it across an external call), so a promotion can land mid-call. The
    pre-flight gate cannot see that. Without a second check the rebuilt
    understanding is written against a premise nobody reasoned against, and
    nothing afterwards can tell.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Revalidate")
    headers = _headers(token, system_id)
    _insert_understanding_graph(system_id, snapshot_id)

    s_head = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s_head, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s_head, r1, expected_head=None, expected_version=None)

    target = _create_session(client, headers, snapshot_id)
    assert _premise(client, headers, target)["state"] == "current"

    import app.routes.interview as interview_route

    other = _rebase(client, headers, s_head)

    class PromotingClient:
        """Stands in for the reasoning model, and moves the head while it runs."""

        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            r2 = _store_candidate(
                other, system_id, snapshot_id,
                _understanding(
                    system_purpose=[_item("P2")], core_capabilities=[_item("C2")]
                ),
            )
            promoted = _promote(
                client, headers, other, r2, expected_head=r1, expected_version=1
            )
            assert promoted.status_code == 200, promoted.text
            return _review_response()

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(
        interview_route, "create_llm_client", lambda config: PromotingClient()
    )

    from app.db import get_conn

    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM understanding_revision WHERE session_id = ?",
            (target,),
        ).fetchone()["n"]

    response = client.post(
        f"/interview/sessions/{target}/update-understanding", headers=headers
    )
    assert response.status_code == 200, response.text

    with get_conn() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM understanding_revision WHERE session_id = ?",
            (target,),
        ).fetchone()["n"]
        session_row = conn.execute(
            "SELECT current_understanding, last_error FROM interview_session WHERE id = ?",
            (target,),
        ).fetchone()
        run = conn.execute(
            """SELECT status, error_details FROM intelligence_runs
               WHERE system_id = ? AND run_type = 'understanding_review'
               ORDER BY id DESC LIMIT 1""",
            (system_id,),
        ).fetchone()

    # Nothing the stale run produced was stored, and the attempt is auditable.
    assert after == before
    assert "P-from-llm" not in (session_row["current_understanding"] or "")
    assert run["status"] == "failed"
    assert "正準 Understanding が更新された" in (run["error_details"] or "")
    assert session_row["last_error"]


def test_session_creation_is_one_transaction(admin_client, tmp_path, monkeypatch):
    """A session must never exist without a recorded premise.

    `get_conn()` is autocommit and never rolls back on its own, so the three
    writes only become one act inside an explicit transaction.
    """
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Atomic")
    headers = _headers(token, system_id)

    import app.routes.interview as interview_route

    def boom(*args, **kwargs):
        raise RuntimeError("premise capture failed")

    monkeypatch.setattr(
        interview_route.canonical_understanding, "capture_session_premise", boom
    )
    with pytest.raises(RuntimeError):
        client.post(
            "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers
        )

    from app.db import get_conn

    with get_conn() as conn:
        sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_session WHERE system_id = ?",
            (system_id,),
        ).fetchone()["n"]
        runs = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_process_run WHERE system_id = ?",
            (system_id,),
        ).fetchone()["n"]
    assert sessions == 0
    assert runs == 0


# --- Cross-cutting -----------------------------------------------------------


def test_retention_never_prunes_the_canonical_head(admin_client, tmp_path, monkeypatch):
    """A pruned head is a System that cannot say what it understands."""
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Retention")
    headers = _headers(token, system_id)
    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)

    from app.db import get_conn

    with get_conn() as conn:
        for index in range(5):
            _ = index
            conn.execute(
                """INSERT INTO understanding_revision
                       (session_id, system_id, snapshot_id, current_understanding,
                        gap_analysis, created_at, status)
                   VALUES (?, ?, ?, '{}', '[]', ?, 'candidate')""",
                (s0, system_id, snapshot_id, time.time()),
            )
        # The retention sweep, with the same guard the rebuild path uses.
        conn.execute(
            f"""DELETE FROM understanding_revision
               WHERE session_id = ? AND system_id = ? AND id NOT IN (
                   SELECT id FROM understanding_revision
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC LIMIT 1
               )
               AND NOT ({canonical.PROTECTED_REVISION_SQL})""",
            (s0, system_id, s0, system_id, system_id, system_id, system_id),
        )
        survived = conn.execute(
            "SELECT id FROM understanding_revision WHERE id = ?", (r1,)
        ).fetchone()
    assert survived is not None

    head = client.get("/understanding-head", headers=headers).json()
    assert head["revision_id"] == r1


def test_the_canonical_head_is_system_isolated(admin_client, tmp_path):
    client = admin_client
    token = _login(client)
    system_a = _create_system(client, token, "Iso A")
    system_b = _create_system(client, token, "Iso B")
    repo_a, sha_a = _init_repo(tmp_path, "iso-a")
    repo_b, sha_b = _init_repo(tmp_path, "iso-b")
    snap_a = _insert_snapshot(system_a, repo_a, sha_a)
    snap_b = _insert_snapshot(system_b, repo_b, sha_b)
    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b)

    s_a = _create_session(client, headers_a, snap_a)
    r_a = _store_candidate(
        s_a, system_a, snap_a,
        _understanding(system_purpose=[_item("A だけの目的")], core_capabilities=[_item("Ca")]),
    )
    assert _promote(
        client, headers_a, s_a, r_a, expected_head=None, expected_version=None
    ).status_code == 200

    assert client.get("/understanding-head", headers=headers_b).json()["revision_id"] is None
    s_b = _create_session(client, headers_b, snap_b)
    detail_b = client.get(f"/interview/sessions/{s_b}", headers=headers_b).json()
    assert detail_b["current_understanding"] is None

    # System B cannot promote System A's revision.
    refused = _promote(
        client, headers_b, s_b, r_a, expected_head=None, expected_version=None
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "revision_not_found"


def test_promotion_refuses_an_empty_understanding(admin_client, tmp_path):
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Empty")
    headers = _headers(token, system_id)
    s0 = _create_session(client, headers, snapshot_id)
    empty = _store_candidate(s0, system_id, snapshot_id, _understanding())
    refused = _promote(
        client, headers, s0, empty, expected_head=None, expected_version=None
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "revision_empty"


def test_promotion_marks_the_previous_head_superseded_and_keeps_lineage(
    admin_client, tmp_path
):
    client = admin_client
    token, system_id, snapshot_id = _setup(client, tmp_path, "System Lineage")
    headers = _headers(token, system_id)
    s0 = _create_session(client, headers, snapshot_id)
    r1 = _store_candidate(
        s0, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P1")], core_capabilities=[_item("C1")]),
    )
    _promote(client, headers, s0, r1, expected_head=None, expected_version=None)
    s1 = _rebase(client, headers, s0)
    r2 = _store_candidate(
        s1, system_id, snapshot_id,
        _understanding(system_purpose=[_item("P2")], core_capabilities=[_item("C2")]),
    )
    _promote(client, headers, s1, r2, expected_head=r1, expected_version=1)

    first = client.get(
        f"/interview/sessions/{s0}/understanding-revisions", headers=headers
    ).json()["items"]
    second = client.get(
        f"/interview/sessions/{s1}/understanding-revisions", headers=headers
    ).json()["items"]
    by_id = {r["id"]: r for r in first + second}
    assert by_id[r1]["status"] == "superseded"
    assert by_id[r2]["status"] == "canonical"
    assert by_id[r2]["parent_revision_id"] == r1
    assert by_id[r2]["confirmed_by"]
    assert by_id[r2]["premise_digest"]


def test_migration_restores_a_head_only_from_a_human_confirmation(tmp_path, monkeypatch):
    """Migration step 1, and what it deliberately will NOT do.

    A System where somebody confirmed an Understanding gets that revision as
    its initial canonical head; a System where nobody ever confirmed anything
    gets NO head, because inventing one would be the very "newest row wins"
    rule this Issue exists to delete. Counts and digests are verifiable
    afterwards, as the Issue's migration step 8 requires.
    """
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-migration.db"))
    monkeypatch.setenv("PROBE_AUTH_MODE", "disabled")
    from app import db as db_module

    db_module.init_db()
    now = time.time()
    understanding = _understanding(
        vision=[_item("既存の Vision")],
        system_purpose=[_item("P")],
        core_capabilities=[_item("C")],
    )
    with db_module.get_conn() as conn:
        confirmed_system = conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("confirmed", "test", "", now, now),
        ).lastrowid
        bare_system = conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("bare", "test", "", now, now),
        ).lastrowid
        snapshots = {}
        sessions = {}
        revisions = {}
        for system_id in (confirmed_system, bare_system):
            snapshots[system_id] = conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/x', 'deadbeef', 'ready', ?, ?)""",
                (system_id, now, now),
            ).lastrowid
            sessions[system_id] = conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, title, focus, created_at, updated_at)
                   VALUES (?, ?, '', '', ?, ?)""",
                (system_id, snapshots[system_id], now, now),
            ).lastrowid
            revisions[system_id] = conn.execute(
                """INSERT INTO understanding_revision
                       (session_id, system_id, snapshot_id, current_understanding,
                        gap_analysis, created_at)
                   VALUES (?, ?, ?, ?, '[]', ?)""",
                (
                    sessions[system_id], system_id, snapshots[system_id],
                    json.dumps(understanding, ensure_ascii=False), now,
                ),
            ).lastrowid
        # Only one System has a human confirmation.
        conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, source_revision_id, composition_digest,
                    decided_by, decision_method, created_at)
               VALUES (?, ?, ?, 'd', 'root', 'manual', ?)""",
            (confirmed_system, sessions[confirmed_system], revisions[confirmed_system], now),
        )
        # Emulate the pre-migration state for the premise columns.
        conn.execute(
            "UPDATE interview_session SET premise_tracking_version = NULL, base_premise_json = NULL"
        )
        conn.execute("DELETE FROM system_understanding_head")
        before_counts = conn.execute(
            "SELECT COUNT(*) AS n FROM understanding_revision"
        ).fetchone()["n"]

    with db_module.get_conn() as conn:
        db_module._migrate_canonical_understanding(conn)

    with db_module.get_conn() as conn:
        heads = {
            row["system_id"]: row
            for row in conn.execute("SELECT * FROM system_understanding_head").fetchall()
        }
        after_counts = conn.execute(
            "SELECT COUNT(*) AS n FROM understanding_revision"
        ).fetchone()["n"]
        promoted = conn.execute(
            "SELECT * FROM understanding_revision WHERE id = ?",
            (revisions[confirmed_system],),
        ).fetchone()
        session_row = conn.execute(
            "SELECT * FROM interview_session WHERE id = ?",
            (sessions[confirmed_system],),
        ).fetchone()
        other_session = conn.execute(
            "SELECT * FROM interview_session WHERE id = ?", (sessions[bare_system],)
        ).fetchone()

    # Nothing is lost, and the promoted revision's digests are reproducible.
    assert after_counts == before_counts
    assert confirmed_system in heads
    assert bare_system not in heads
    assert promoted["status"] == "canonical"
    assert promoted["content_digest"] == canonical.compute_content_digest(understanding)
    assert session_row["premise_tracking_version"] == canonical.CANONICAL_PREMISE_VERSION
    assert session_row["base_understanding_revision_id"] == revisions[confirmed_system]
    # A session the migration could not place is left legacy-unbased.
    assert other_session["premise_tracking_version"] is None
    with db_module.get_conn() as conn:
        assert (
            canonical.evaluate_session_premise(conn, other_session).state == "invalid"
        )
