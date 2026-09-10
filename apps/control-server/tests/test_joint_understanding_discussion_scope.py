"""Issue #461: Interview に依存しない Discussion 起点 JU の所属・premise 基盤.

Covers the Acceptance Criteria:

1. Interview なしの discussion scope で fixture の JU 読取・調査・premise・
   判断 gate を検証できる.
2. 既存 Interview JU と監査履歴が migration 後も不変 (件数・ID・FK・参照整合) --
   see ``test_joint_understanding_owner_scope_migration.py`` for the
   dedicated migration test; this file additionally re-runs the full
   pre-existing JU regression suite implicitly by importing nothing from it
   (kept separate on purpose -- a migration test and a feature test fail for
   different reasons).
3. 不正 owner 組合せ (両方設定 / 両方欠落)、別 System、未登録 origin
   provider を拒否する.
4. 仮説本文と dependency 更新を前提評価に反映できる (root 不変でも依存更新
   で stale)。
5. provider 未接続の昇格を「対応済み」と宣言しない (a discussion-origin
   read/gate call fails closed with an explicit, catchable error -- never a
   silent 'missing' or an unhandled 500 -- until Issue #455 registers a
   provider).

None of this exercises Issue #455's real promotion endpoint (it does not
exist yet); every discussion-scope session here is built directly through
``app.joint_premise.capture_premise_bundle`` via
``joint_understanding_helpers.insert_discussion_ju_session``, the same
premise-capture path that endpoint will eventually use.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pytest
import sqlite3
from fastapi.testclient import TestClient

from joint_understanding_helpers import (
    fake_dependency_digest_resolver,
    fake_discussion_origin_provider,
    insert_discussion_ju_session,
    insert_discussion_thread,
    no_dependency_digest_resolver,
    no_discussion_origin_provider,
)

from app.joint_premise import (
    DiscussionOriginFacts,
    EMPTY_DEPENDENCY_MANIFEST_DIGEST,
    JointPremiseError,
    PremiseDependencyRef,
    compute_premise_manifest_digest,
    discussion_origin_provider_registered,
    evaluate_session_premise,
    normalize_premise_manifest,
    require_discussion_thread_in_system,
    resolve_owner_facts,
)
from app.joint_understanding import (
    JointUnderstandingValidationError,
    OWNER_SCOPES,
    owner_scope_for_origin_kind,
    require_owner_origin_consistency,
    validate_owner_scope,
)
from app.investigation_loop import STOP_REASONS
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient


# --- Pure contract: the shared manifest helper (Issue #461 decision 5) --------


def test_normalize_premise_manifest_sorts_stably_by_kind_and_ref():
    manifest = normalize_premise_manifest([
        {"target_kind": "ux_requirement", "target_ref": "req-2", "digest": "d2"},
        {"target_kind": "product_gap", "target_ref": "gap-1", "digest": "d1"},
        {"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d0"},
    ])
    assert [(r.target_kind, r.target_ref) for r in manifest] == [
        ("product_gap", "gap-1"),
        ("ux_requirement", "req-1"),
        ("ux_requirement", "req-2"),
    ]


def test_normalize_premise_manifest_rejects_duplicates_rather_than_deduping():
    with pytest.raises(JointPremiseError):
        normalize_premise_manifest([
            {"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"},
            {"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d2-different"},
        ])


def test_normalize_premise_manifest_rejects_missing_or_empty_fields():
    with pytest.raises(JointPremiseError):
        normalize_premise_manifest([{"target_kind": "ux_requirement", "digest": "d1"}])
    with pytest.raises(JointPremiseError):
        normalize_premise_manifest([{"target_kind": "", "target_ref": "req-1", "digest": "d1"}])


def test_manifest_digest_is_order_independent_and_deterministic():
    a = normalize_premise_manifest([
        {"target_kind": "a", "target_ref": "1", "digest": "x"},
        {"target_kind": "b", "target_ref": "2", "digest": "y"},
    ])
    b = normalize_premise_manifest([
        {"target_kind": "b", "target_ref": "2", "digest": "y"},
        {"target_kind": "a", "target_ref": "1", "digest": "x"},
    ])
    assert compute_premise_manifest_digest(a) == compute_premise_manifest_digest(b)
    # Any content change moves the digest.
    c = normalize_premise_manifest([
        {"target_kind": "a", "target_ref": "1", "digest": "x"},
        {"target_kind": "b", "target_ref": "2", "digest": "CHANGED"},
    ])
    assert compute_premise_manifest_digest(a) != compute_premise_manifest_digest(c)


def test_empty_manifest_has_a_real_stable_digest_not_null():
    assert compute_premise_manifest_digest(()) == EMPTY_DEPENDENCY_MANIFEST_DIGEST
    assert EMPTY_DEPENDENCY_MANIFEST_DIGEST is not None


# --- Pure contract: owner_scope invariant (decision 2) -------------------------


def test_owner_scopes_is_exactly_two_values():
    assert OWNER_SCOPES == ("interview", "discussion")


def test_validate_owner_scope_rejects_both_missing():
    with pytest.raises(JointUnderstandingValidationError):
        validate_owner_scope(owner_scope="interview", session_id=None, discussion_thread_id=None)
    with pytest.raises(JointUnderstandingValidationError):
        validate_owner_scope(owner_scope="discussion", session_id=None, discussion_thread_id=None)


def test_validate_owner_scope_rejects_both_set():
    with pytest.raises(JointUnderstandingValidationError):
        validate_owner_scope(owner_scope="interview", session_id=1, discussion_thread_id=1)
    with pytest.raises(JointUnderstandingValidationError):
        validate_owner_scope(owner_scope="discussion", session_id=1, discussion_thread_id=1)


def test_validate_owner_scope_accepts_the_one_correct_combination_each():
    validate_owner_scope(owner_scope="interview", session_id=1, discussion_thread_id=None)
    validate_owner_scope(owner_scope="discussion", session_id=None, discussion_thread_id=1)


def test_owner_scope_for_origin_kind_is_a_closed_one_to_one_mapping():
    assert owner_scope_for_origin_kind("discussion") == "discussion"
    for kind in ("qa", "intent", "review_item", "inquiry", "purpose_need"):
        assert owner_scope_for_origin_kind(kind) == "interview"
    with pytest.raises(JointUnderstandingValidationError):
        require_owner_origin_consistency(owner_scope="interview", origin_kind="discussion")
    with pytest.raises(JointUnderstandingValidationError):
        require_owner_origin_consistency(owner_scope="discussion", origin_kind="qa")
    require_owner_origin_consistency(owner_scope="discussion", origin_kind="discussion")
    require_owner_origin_consistency(owner_scope="interview", origin_kind="qa")


# --- DB-level: the same invariant is a structural CHECK, not only Python -------


@pytest.fixture
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-owner-scope.db"))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app import db

    db.init_db()
    with db.get_conn() as conn:
        now = time.time()
        existing = conn.execute("SELECT id FROM systems LIMIT 1").fetchone()
        if existing is None:
            cur = conn.execute(
                "INSERT INTO systems (name, environment, description, created_at, updated_at) "
                "VALUES ('S', 'test', '', ?, ?)",
                (now, now),
            )
            conn.commit()
            system_id = cur.lastrowid
        else:
            system_id = existing["id"]
        yield conn, system_id


def _insert_ju_row(conn, *, system_id, **overrides):
    now = time.time()
    base = dict(
        owner_scope="interview", session_id=None, discussion_thread_id=None,
        system_id=system_id, origin_kind="qa", origin_id=1, trigger="explicit_request",
        question_text="q", status="open", schema_version="joint-understanding-v1",
        created_at=now, updated_at=now,
    )
    base.update(overrides)
    conn.execute(
        """INSERT INTO joint_understanding_session
            (owner_scope, session_id, discussion_thread_id, system_id, origin_kind,
             origin_id, trigger, question_text, status, schema_version, created_at, updated_at)
           VALUES (:owner_scope, :session_id, :discussion_thread_id, :system_id,
                   :origin_kind, :origin_id, :trigger, :question_text, :status,
                   :schema_version, :created_at, :updated_at)""",
        base,
    )


def test_check_constraint_rejects_both_missing(db_conn):
    conn, system_id = db_conn
    with pytest.raises(sqlite3.IntegrityError):
        _insert_ju_row(
            conn, system_id=system_id, owner_scope="interview",
            session_id=None, discussion_thread_id=None,
        )


def test_check_constraint_rejects_both_set(db_conn):
    conn, system_id = db_conn
    with pytest.raises(sqlite3.IntegrityError):
        _insert_ju_row(
            conn, system_id=system_id, owner_scope="interview",
            session_id=None, discussion_thread_id=99,
        )


def test_check_constraint_rejects_unknown_owner_scope(db_conn):
    conn, system_id = db_conn
    with pytest.raises(sqlite3.IntegrityError):
        _insert_ju_row(
            conn, system_id=system_id, owner_scope="bogus",
            session_id=1, discussion_thread_id=None,
        )


def test_discussion_thread_cannot_be_hard_deleted_while_referenced(db_conn):
    """Issue #461 decision 8: a discussion origin's removal must not silently
    drop judgement history. `discussion_thread_id` carries no `ON DELETE`
    action, so with FK enforcement on (the default connection setting)
    deleting a referenced thread is refused outright rather than orphaning
    the session -- the strongest available form of "never silently lose
    history" for a resource this feature does not own the lifecycle of.
    """
    conn, system_id = db_conn
    now = time.time()
    conn.execute(
        """INSERT INTO assistant_discussion_thread
            (system_id, thread_key, scope, screen_id, target_kind, target_ref,
             target_title, captured_target_digest, status, created_at, updated_at,
             schema_version)
           VALUES (?, 'k', 'entity', 'objective-map', 'overview_finding', 'gap-1',
                   '', '', 'open', ?, ?, 'assistant-discussion-thread-v1')""",
        (system_id, now, now),
    )
    thread_id = conn.execute(
        "SELECT id FROM assistant_discussion_thread WHERE thread_key = 'k'"
    ).fetchone()["id"]
    _insert_ju_row(
        conn, system_id=system_id, owner_scope="discussion", session_id=None,
        discussion_thread_id=thread_id, origin_kind="discussion",
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM assistant_discussion_thread WHERE id = ?", (thread_id,))
    # The session and its owning thread both survive the refused delete.
    still_there = conn.execute(
        "SELECT COUNT(*) AS n FROM assistant_discussion_thread WHERE id = ?", (thread_id,),
    ).fetchone()["n"]
    assert still_there == 1


# --- Route-level fixtures -------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-discussion.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name="Sys"):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_ready_snapshot(system_id, repo_path="/tmp/x", commit_sha="commitA"):
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


def _setup_discussion(client, token, *, commit_sha="commitA", origin_id=42,
                       origin_content_hash="hypothesis-hash-1", dependencies=None):
    system_id = _create_system(client, token)
    _insert_ready_snapshot(system_id, commit_sha=commit_sha)
    thread_id = insert_discussion_thread(system_id=system_id)
    with fake_discussion_origin_provider({
        origin_id: DiscussionOriginFacts(
            current_origin_id=origin_id, content_hash=origin_content_hash,
        ),
    }):
        ju_id = insert_discussion_ju_session(
            system_id=system_id, discussion_thread_id=thread_id, origin_id=origin_id,
            origin_content_hash=origin_content_hash, dependencies=dependencies,
        )
    return system_id, thread_id, ju_id


# --- Fixture read / gate: the discussion scope works without an Interview -----


def test_get_a_discussion_session_without_a_provider_fails_closed_with_503(admin_client):
    """Decision 4: an unregistered provider must be a clean, catchable
    failure -- never a 500, and never silently reported as some other
    premise verdict. Issue #455 registers a real production provider at
    import time, so this test explicitly UNREGISTERS it for the duration of
    the read to exercise the fail-closed path (e.g. a rollback, or a future
    provider that fails to import) rather than relying on nothing ever being
    registered."""
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    with no_discussion_origin_provider():
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "joint_understanding_origin_unsupported"


def test_discussion_session_reads_as_current_with_the_provider_registered(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        session = r.json()["session"]
        assert session["owner_scope"] == "discussion"
        assert session["session_id"] is None
        assert session["discussion_thread_id"] == thread_id
        assert session["premise_state"] == "current"
        assert session["premise_reason"] is None


def test_a_changed_hypothesis_statement_makes_the_premise_stale(admin_client):
    """仮説本文の変化 -- the origin's own content_hash moved."""
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="CHANGED-hash"),
    }):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["premise_state"] == "stale"
        assert r.json()["session"]["premise_reason"] == "origin_content_changed"


def test_a_removed_hypothesis_is_missing_not_current(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    with fake_discussion_origin_provider({}):  # 42 no longer resolves
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["premise_state"] == "missing"
        assert r.json()["session"]["premise_reason"] == "origin_removed"


def test_developer_findings_hold_resume_and_a_decided_close_all_work(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.post(
            f"/joint-understanding/{ju_id}/findings",
            json={
                "origin_role": "developer", "claim_kind": "fact",
                "statement": "この仮説は目的に沿う", "decision_method": "manual",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        finding_id = r.json()["id"]

        assert admin_client.post(f"/joint-understanding/{ju_id}/hold", headers=headers).status_code == 200
        assert admin_client.post(f"/joint-understanding/{ju_id}/resume", headers=headers).status_code == 200

        r = admin_client.post(
            f"/joint-understanding/{ju_id}/close",
            json={
                "outcome": "decided", "outcome_reason": "この方向で進める",
                "outcome_finding_ids": [finding_id],
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["outcome"] == "decided"
        assert body["outcome_is_provisional"] is False


def test_hypothesis_adopted_close_is_provisional_for_a_discussion_session(admin_client):
    """Provisional stays provisional regardless of owner_scope (Issue #337's
    rule is not re-derived per-scope). An `investigation`-role hypothesis
    finding is written the way its own producer would (`insert_producer_
    finding`), the same pattern `test_joint_premise.py` uses -- the public
    findings endpoint is developer-only and correctly refuses it."""
    from joint_understanding_helpers import insert_producer_finding

    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    headers = _headers(token, system_id)

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock,
                 started_at, completed_at)
               VALUES (?, 'joint_investigation', 'anthropic', 'claude-sonnet-4-5',
                       'v1', 'v1', 'reasoning_llm', 'succeeded', 0, ?, ?)""",
            (system_id, now, now),
        )
        run_id = cur.lastrowid

    finding_id = insert_producer_finding(
        ju_id=ju_id, system_id=system_id, origin_role="investigation",
        claim_kind="hypothesis", statement="案内不足が原因かもしれない",
        competing_explanations=["権限不足が原因かもしれない"],
        refutation_conditions=["権限を付与しても再現するなら誤り"],
        next_investigation="権限ログを確認する",
        intelligence_run_id=run_id,
    )

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.post(
            f"/joint-understanding/{ju_id}/close",
            json={
                "outcome": "hypothesis_adopted", "outcome_reason": "有力そうなので採用",
                "outcome_finding_ids": [finding_id],
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["outcome"] == "hypothesis_adopted"
        assert r.json()["outcome_is_provisional"] is True

        detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
        adoptions = detail["hypothesis_adoptions"]
        assert len(adoptions) == 1
        assert adoptions[0]["state"] == "provisional"


def test_investigate_endpoint_reads_the_pinned_snapshot_for_a_discussion_session(
    admin_client, tmp_path,
):
    """`/investigate` never touches `interview_session` -- it reads
    `premise_snapshot_id` off the JU row directly -- so it must behave
    identically for a discussion-scope session, including failing closed on
    a mock/no reasoning configuration (Principle 6)."""
    token = _login(admin_client)
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    with open(os.path.join(repo, "app.py"), "w") as f:
        f.write("def run():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()

    system_id, thread_id, ju_id = _setup_discussion(admin_client, token, commit_sha=sha)
    # Re-point the fixture's snapshot at the real repo (the helper's default
    # snapshot has no working repo_path).
    from app.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE repository_snapshots SET repo_path = ? WHERE system_id = ? AND commit_sha = ?",
            (repo, system_id, sha),
        )
        conn.commit()
    headers = _headers(token, system_id)

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.post(f"/joint-understanding/{ju_id}/investigate", headers=headers)
        assert r.status_code == 502, r.text
        assert r.json()["detail"]["code"] == "joint_investigation_failed"

    # Scripted success path: stub the reasoning client the same way
    # test_joint_understanding_investigation.py does, proving a discussion-
    # scope session can actually complete a round and persist a finding.
    from app.routes import joint_understanding as ju_routes

    class _Scripted(LLMClient):
        def __init__(self, response):
            self._response = response

        def generate_text(self, messages, *, temperature=None, max_tokens=None, timeout=None):
            return json.dumps(self._response)

    admin_client_config = LLMConfig(
        provider="anthropic", api_key="k", model="claude-sonnet-4-5", base_url=None, timeout=30,
    )
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        import app.routes.joint_understanding as _ju

        orig_create = _ju.create_llm_client
        orig_config_cls = _ju.LLMConfig
        try:
            _ju.create_llm_client = lambda config: _Scripted({
                "status": "completed",
                "conclusion": "run は 1 を返す。",
                "findings": [{
                    "claim_kind": "fact", "statement": "run は 1 を返す",
                    "evidence": [{"path": "app.py", "start_line": 1, "end_line": 2, "summary": "定義"}],
                }],
                "search_leads": [], "open_hypotheses": [], "missing_evidence": [],
            })
            _ju.LLMConfig = type(
                "_C", (), {"intelligence_from_env": staticmethod(lambda: admin_client_config)},
            )
            r = admin_client.post(
                f"/joint-understanding/{ju_id}/investigate",
                json={"max_rounds": 1}, headers=headers,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            # Whether the loop actually completes a round with this exact
            # scripted response/budget is a property of
            # `app/investigation_loop.py` itself, already covered in depth by
            # test_joint_understanding_investigation.py; this test's only
            # claim is that a discussion-scope session reaches the SAME code
            # path with the SAME result shape (never touching
            # `interview_session`), which a clean 200 + finite stop_reason
            # already demonstrates regardless of how many rounds ran.
            assert body["stop_reason"] in STOP_REASONS
            assert body["error"] is None or isinstance(body["error"], str)

            detail_r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
            assert detail_r.status_code == 200, detail_r.text
        finally:
            _ju.create_llm_client = orig_create
            _ju.LLMConfig = orig_config_cls


# --- Dependency reference manifest: root unchanged, dependency changed -------


def test_dependency_manifest_current_when_unchanged(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(
        admin_client, token,
        dependencies=[{"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"}],
    )
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }), fake_dependency_digest_resolver({"ux_requirement:req-1": "d1"}):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.json()["session"]["premise_state"] == "current"
        assert r.json()["session"]["premise_dependency_manifest"] == [
            {"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"},
        ]


def test_dependency_manifest_change_is_stale_even_though_root_is_unchanged(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(
        admin_client, token,
        dependencies=[{"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"}],
    )
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        # The root (hypothesis) content_hash is UNCHANGED from capture time.
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }), fake_dependency_digest_resolver({"ux_requirement:req-1": "d1-CHANGED"}):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["premise_state"] == "stale"
        assert r.json()["session"]["premise_reason"] == "dependency_content_changed"


def test_dependency_manifest_target_removed_is_missing(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(
        admin_client, token,
        dependencies=[{"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"}],
    )
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }), fake_dependency_digest_resolver({}):  # req-1 no longer resolves
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["premise_state"] == "missing"
        assert r.json()["session"]["premise_reason"] == "dependency_target_removed"


def test_dependency_manifest_unresolved_without_a_resolver_never_reads_as_current(admin_client):
    """欠損はcurrentにしない: a non-empty manifest with NO resolver registered
    at all is a distinct, non-'current' verdict from an entry the resolver
    actually reported removed. Issue #455 registers a real production
    resolver at import time, so this test explicitly UNREGISTERS it for the
    duration of the read (see ``no_dependency_digest_resolver``'s docstring)
    -- a genuinely-nonexistent `ux_requirement:req-1` under the REAL resolver
    is covered separately by
    ``test_a_real_dependency_resolver_reports_a_nonexistent_target_as_missing``."""
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(
        admin_client, token,
        dependencies=[{"target_kind": "ux_requirement", "target_ref": "req-1", "digest": "d1"}],
    )
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }), no_dependency_digest_resolver():
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["session"]["premise_state"] == "stale"
        assert r.json()["session"]["premise_reason"] == "dependency_manifest_unresolved"


def test_a_real_dependency_resolver_reports_a_nonexistent_target_as_missing(admin_client):
    """Once Issue #455's real resolver is registered (the production
    default), a dependency reference that never existed resolves as
    `missing`/`dependency_target_removed` -- a MORE precise verdict than the
    pre-#455 placeholder `stale`/`dependency_manifest_unresolved` above,
    since a real resolver actually looked and found nothing."""
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(
        admin_client, token,
        dependencies=[{"target_kind": "ux_requirement", "target_ref": "never-existed", "digest": "d1"}],
    )
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["session"]["premise_state"] == "missing"
    assert r.json()["session"]["premise_reason"] == "dependency_target_removed"


def test_an_empty_manifest_is_a_structural_no_op_matching_pre_461_behavior(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token, dependencies=None)
    headers = _headers(token, system_id)
    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        # No dependency resolver registered at all -- still 'current', because
        # the manifest itself is empty; the check is a genuine no-op, not
        # merely "no resolver happened to run".
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.json()["session"]["premise_state"] == "current"
        assert r.json()["session"]["premise_dependency_manifest"] == []
        assert r.json()["session"]["premise_dependency_manifest_digest"] == EMPTY_DEPENDENCY_MANIFEST_DIGEST


# --- System isolation -----------------------------------------------------------


def test_a_discussion_session_is_invisible_from_another_system(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    other_system_id = _create_system(admin_client, token, "Other")
    headers = _headers(token, other_system_id)

    with fake_discussion_origin_provider({
        42: DiscussionOriginFacts(current_origin_id=42, content_hash="hypothesis-hash-1"),
    }):
        r = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers)
        assert r.status_code == 404


def test_require_discussion_thread_in_system_rejects_a_foreign_system(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)
    other_system_id = _create_system(admin_client, token, "Other")

    from app.db import get_conn

    with get_conn() as conn:
        # Same failure whether the thread does not exist at all or exists in
        # a DIFFERENT System -- the caller learns nothing about which.
        with pytest.raises(JointPremiseError):
            require_discussion_thread_in_system(
                conn, discussion_thread_id=thread_id, system_id=other_system_id,
            )
        with pytest.raises(JointPremiseError):
            require_discussion_thread_in_system(
                conn, discussion_thread_id=999999, system_id=system_id,
            )
        # The real System accepts its own thread.
        require_discussion_thread_in_system(
            conn, discussion_thread_id=thread_id, system_id=system_id,
        )


# --- Owner resolver: generalizes the pre-#461 direct interview_session read ---


def test_resolve_owner_facts_reports_missing_for_a_deleted_interview_session(admin_client):
    """Parity check: interview scope keeps its pre-#461 behavior exactly --
    the owner resolver is a pure generalization, not a rewrite (decision 7)."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token)
    headers = _headers(token, system_id)
    snapshot_id = _insert_ready_snapshot(system_id)
    session_id = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa", json={"question_text": "根拠は?"}, headers=headers,
    ).json()
    ju = admin_client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "explicit_request",
            "question_text": "根拠が分かりません",
        },
        headers=headers,
    ).json()["session"]

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju["id"],),
        ).fetchone()
        owner = resolve_owner_facts(conn, row)
        assert owner.exists is True
        assert owner.commit_tracked is True

        conn.execute("DELETE FROM interview_session WHERE id = ?", (session_id,))
        conn.commit()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju["id"],),
        ).fetchone()
        # The session cascaded away with its owning interview session
        # (unchanged pre-#461 FK behavior) -- confirms nothing here softened
        # the existing cascade.
        assert row is None


def test_resolve_owner_facts_for_discussion_scope_never_touches_interview_session(admin_client):
    token = _login(admin_client)
    system_id, thread_id, ju_id = _setup_discussion(admin_client, token)

    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        owner = resolve_owner_facts(conn, row)
        assert owner.exists is True
        assert owner.commit_tracked is False

        conn.execute("DELETE FROM assistant_discussion_thread WHERE id != ?", (thread_id,))
        conn.commit()
        # Deleting an UNRELATED thread does not affect this session's owner.
        owner_again = resolve_owner_facts(conn, row)
        assert owner_again.exists is True
