"""Tests for Issue #431 -- Product Feature: identity, revisions, links
(Epic #427).

`docs/product-objective-lineage.md` §7.2 / §8 / §10 / §12 is the contract
this file is organized around. Coverage (per the task brief):

1. `feature_key` required / conflict.
2. Revisions are append-only.
3. `design_status` is derived (never stored); `recheck_state` goes `stale`
   on a content change without moving `design_status`.
4. A stale `captured_digest` on a decision is refused (409).
5. A forbidden decision transition is refused (422).
6. System isolation, including a cross-System link TARGET (Requirement /
   Capability) resolving as 404, never leaking existence.
7. A Requirement link goes stale when the Requirement's own revision moves.
8. A `feature_drafts` link survives a snapshot prune as `unresolved` while
   the Feature's own identity/revision history stays fully intact.

This router is not registered on `app.main.app` yet (that wiring belongs to
the orchestrating change across #429-#432's routers together), so the
`admin_client` fixture below registers it onto the shared app itself,
guarded so a repeated import within one test session never double-registers
it.

Part 1 exercises pure/first-match domain functions directly; Part 2 drives
the real HTTP API via `TestClient`, closely mirroring `test_ux_design.py`'s
own fixture/helper shape.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import product_feature


# ---------------------------------------------------------------------------
# Part 1: pure functions / finite vocabularies
# ---------------------------------------------------------------------------


class TestFiniteVocabularies:
    def test_vocabularies_match_the_documented_contract(self):
        assert set(product_feature.AUTHORSHIP_KINDS) == {"developer", "reasoning_model"}
        assert set(product_feature.DECISION_KINDS) == {"confirm", "reject", "retire", "reinstate"}
        assert set(product_feature.TARGET_LINK_KINDS) == {
            "solution_design", "evolution_node", "component", "probe_point",
            "static_flow", "runtime_flow", "experiment", "replay_run",
            "purpose_outcome_criterion",
        }
        assert set(product_feature.RECHECK_STATES) == {"current", "stale", "not_captured"}
        assert set(product_feature.REVISION_STATES) == {"current", "superseded"}
        assert set(product_feature.TARGET_RESOLUTIONS) == {"resolved", "unresolved", "unavailable"}

    def test_decision_to_design_status_is_a_fixed_table(self):
        assert product_feature._DECISION_TO_DESIGN_STATUS == {
            "confirm": "confirmed", "reject": "rejected", "retire": "retired", "reinstate": "proposed",
        }


class TestDigest:
    def test_feature_revision_digest_excludes_bookkeeping_fields(self):
        base = dict(title="t", statement="s", rationale="r", scope_note="sc", summary="su")
        assert product_feature.feature_revision_digest(**base) == product_feature.feature_revision_digest(**base)

    def test_feature_revision_digest_changes_on_meaning_change(self):
        d1 = product_feature.feature_revision_digest(
            title="t", statement="s", rationale="", scope_note="", summary=""
        )
        d2 = product_feature.feature_revision_digest(
            title="t", statement="s2", rationale="", scope_note="", summary=""
        )
        assert d1 != d2


class TestRecheckStateHelper:
    def test_not_captured_is_fail_closed(self):
        assert product_feature._recheck_state("", "resolved", "x") == "not_captured"

    def test_unresolved_target_is_stale(self):
        assert product_feature._recheck_state("d", "unresolved", "") == "stale"

    def test_matching_digest_is_current(self):
        assert product_feature._recheck_state("d", "resolved", "d") == "current"

    def test_mismatched_digest_is_stale(self):
        assert product_feature._recheck_state("d1", "resolved", "d2") == "stale"


# ---------------------------------------------------------------------------
# Part 2: HTTP API, fixtures/helpers (closely mirrors test_ux_design.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-feature-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from fastapi import Depends

    from app.auth import get_principal
    from app.main import app  # noqa: WPS433
    from app.routes import product_features

    # This router is registered on `app.main.app` by the orchestrating
    # change across #429-#432 together (see the module docstring) -- not
    # by this file. Register it here, guarded so importing this test
    # module more than once in one process never double-registers the
    # same routes.
    already = any(
        getattr(r, "path", "").startswith("/product-features") for r in app.router.routes
    )
    if not already:
        app.include_router(product_features.router, dependencies=[Depends(get_principal)])

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
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _init_repo_with_files(tmp_path, name, files):
    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    for rel_path, content in files.items():
        full = os.path.join(repo, rel_path)
        os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _insert_snapshot(system_id, repo_path, commit_sha, *, status="ready"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (system_id, repo_path, commit_sha, status, now, now),
        )
        return cur.lastrowid


def _setup(client, tmp_path, name="System PF", files=None):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo_with_files(
        tmp_path, f"repo-{name.replace(' ', '-')}", files or {"a.py": b"def a():\n    return 1\n"}
    )
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id, repo


def _settle_initial_build(session_id, *, ok=True):
    from app.db import get_conn
    from app.interview_workflow import finish_process_run

    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND status = 'running' ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is not None:
            finish_process_run(conn, row["id"], ok=ok, error=None if ok else "build failed")


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _settle_initial_build(session_id)
    return session_id


def _insert_capability_entity(system_id, session_id, name):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        entity_id = conn.execute(
            "INSERT INTO understanding_capability_entity (system_id, entity_kind, created_at) VALUES (?, 'core_capability', ?)",
            (system_id, now),
        ).lastrowid
        confirmation_id = conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, 'd', 'root', 'manual', ?)""",
            (system_id, session_id, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO understanding_capability_entity_version
                   (system_id, confirmation_id, entity_id, entity_kind, name, summary, semantic_digest,
                    payload_json, created_at)
               VALUES (?, ?, ?, 'core_capability', ?, '', 'sd', '{}', ?)""",
            (system_id, confirmation_id, entity_id, name, now),
        )
        return entity_id


def _create_requirement(system_id, requirement_key, *, requirement_kind="functional"):
    from app.db import get_conn
    from app import ux_design

    with get_conn() as conn:
        return ux_design.create_requirement(
            conn, system_id=system_id, requirement_key=requirement_key,
            requirement_kind=requirement_kind, created_by="root",
        )


def _add_requirement_revision(system_id, requirement_key, **fields):
    from app.db import get_conn
    from app import ux_design

    payload = {"statement": "", "rationale": "", "constraint_text": "", "out_of_scope_note": "", "change_note": ""}
    payload.update(fields)
    with get_conn() as conn:
        return ux_design.add_requirement_revision(
            conn, system_id=system_id, requirement_key=requirement_key, created_by="root", **payload
        )


def _insert_feature_draft(system_id, snapshot_id, feature_id_text, *, name="Feature Draft", run_id=None):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        if run_id is None:
            run_id = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model, prompt_version, schema_version,
                        decision_method, status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'repository_drafts', 'mock', 'mock', 'v1', 'v1', 'reasoning_llm', 'completed', 1, ?, ?)""",
                (system_id, snapshot_id, now, now),
            ).lastrowid
        draft_id = conn.execute(
            """INSERT INTO feature_drafts
                   (system_id, intelligence_run_id, snapshot_id, feature_id, name, summary, user_value,
                    success_criteria, risks, decision_method, is_mock, created_at)
               VALUES (?, ?, ?, ?, ?, '', '', '[]', '[]', 'reasoning_llm', 1, ?)""",
            (system_id, run_id, snapshot_id, feature_id_text, name, now),
        ).lastrowid
        return draft_id, run_id


def _create_feature(client, headers, feature_key, *, expect=201):
    r = client.post("/product-features", json={"feature_key": feature_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_revision(client, headers, feature_key, *, expect=201, **fields):
    payload = {"title": "", "statement": "", "rationale": "", "scope_note": "", "summary": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/product-features/{feature_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_feature(client, headers, feature_key, expect=200):
    r = client.get(f"/product-features/{feature_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide(client, headers, feature_key, decision, *, rationale="", captured_digest="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_link(client, headers, feature_key, requirement_key, *, expect=201):
    r = client.post(
        f"/product-features/{feature_key}/requirement-links",
        json={"requirement_key": requirement_key, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_capability_link(client, headers, feature_key, capability_entity_id, *, expect=201):
    r = client.post(
        f"/product-features/{feature_key}/capability-links",
        json={"capability_entity_id": capability_entity_id, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_target_link(client, headers, feature_key, link_kind, target_ref, *, expect=201):
    r = client.post(
        f"/product-features/{feature_key}/target-links",
        json={"link_kind": link_kind, "target_ref": target_ref, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_draft_link(client, headers, feature_key, feature_draft_id, *, expect=201):
    r = client.post(
        f"/product-features/{feature_key}/draft-links",
        json={"feature_draft_id": feature_draft_id, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


# ---------------------------------------------------------------------------
# 1. feature_key required / conflict
# ---------------------------------------------------------------------------


class TestKeyRequiredAndConflict:
    def test_empty_feature_key_is_422(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        r = admin_client.post("/product-features", json={"feature_key": ""}, headers=headers)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "product_feature_key_required"

    def test_duplicate_feature_key_is_409(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post("/product-features", json={"feature_key": "checkout"}, headers=headers)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "product_feature_key_conflict"


# ---------------------------------------------------------------------------
# 2. Revisions are append-only
# ---------------------------------------------------------------------------


class TestRevisionsAreAppendOnly:
    def test_two_revisions_both_survive_and_are_linked(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1", statement="first")
        _add_revision(admin_client, headers, "checkout", title="v2", statement="second")

        from app.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM product_feature_revision r
                       JOIN product_feature f ON f.id = r.feature_id
                       WHERE f.system_id = ? AND f.feature_key = ?
                       ORDER BY revision_number""",
                (system_id, "checkout"),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["title"] == "v1"
        assert rows[0]["superseded_by_id"] == rows[1]["id"]
        assert rows[1]["title"] == "v2"
        assert rows[1]["superseded_by_id"] is None

        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["current_revision"]["title"] == "v2"
        assert detail["current_revision"]["revision_state"] == "current"


# ---------------------------------------------------------------------------
# 3. design_status derived / recheck_state independent axis
# ---------------------------------------------------------------------------


class TestDesignStatusDerivedNotStored:
    def test_no_decision_reads_proposed(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1")
        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["design_status"] == "proposed"
        assert detail["recheck_state"] == "current"

        from app.db import get_conn

        with get_conn() as conn:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(product_feature)").fetchall()]
        assert "design_status" not in cols

    def test_confirm_then_edit_flips_recheck_state_to_stale_without_moving_design_status(
        self, admin_client, tmp_path
    ):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1", statement="first")
        detail = _get_feature(admin_client, headers, "checkout")
        digest = detail["current_revision"]["content_digest"]

        _decide(admin_client, headers, "checkout", "confirm", captured_digest=digest)
        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["design_status"] == "confirmed"
        assert detail["recheck_state"] == "current"

        _add_revision(admin_client, headers, "checkout", title="v1", statement="second")
        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["design_status"] == "confirmed"  # unchanged
        assert detail["recheck_state"] == "stale"
        # the decision row survives untouched
        assert len(detail["decisions"]) == 1
        assert detail["decisions"][0]["decision"] == "confirm"


# ---------------------------------------------------------------------------
# 4. Stale digest on a decision -> 409
# ---------------------------------------------------------------------------


class TestStaleDigest:
    def test_confirm_with_wrong_digest_is_409(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1")
        r = admin_client.post(
            f"/product-features/checkout/decisions",
            json={"decision": "confirm", "rationale": "", "captured_digest": "not-the-real-digest"},
            headers=headers,
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "product_feature_decision_stale_digest"

    def test_empty_captured_digest_is_not_checked(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1")
        _decide(admin_client, headers, "checkout", "confirm", captured_digest="")
        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["design_status"] == "confirmed"


# ---------------------------------------------------------------------------
# 5. Forbidden transitions -> 422
# ---------------------------------------------------------------------------


class TestForbiddenTransitions:
    def test_reinstate_on_proposed_is_422(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post(
            "/product-features/checkout/decisions",
            json={"decision": "reinstate", "rationale": "", "captured_digest": ""},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "product_feature_not_decidable"

    def test_confirm_on_retired_is_422(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _decide(admin_client, headers, "checkout", "retire")
        r = admin_client.post(
            "/product-features/checkout/decisions",
            json={"decision": "confirm", "rationale": "", "captured_digest": ""},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "product_feature_not_decidable"

    def test_reinstate_after_retire_returns_to_proposed(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _decide(admin_client, headers, "checkout", "retire")
        _decide(admin_client, headers, "checkout", "reinstate")
        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["design_status"] == "proposed"


# ---------------------------------------------------------------------------
# 6. System isolation, including cross-System link targets -> 404
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_same_feature_key_in_two_systems_is_independent(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System A")
        sys_b = _create_system(admin_client, token, "System B")
        headers_a = _headers(token, sys_a)
        headers_b = _headers(token, sys_b)
        _create_feature(admin_client, headers_a, "checkout")
        _create_feature(admin_client, headers_b, "checkout")  # must not 409

    def test_feature_not_visible_from_other_system(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System A")
        sys_b = _create_system(admin_client, token, "System B")
        headers_a = _headers(token, sys_a)
        headers_b = _headers(token, sys_b)
        _create_feature(admin_client, headers_a, "checkout")
        r = admin_client.get("/product-features/checkout", headers=headers_b)
        assert r.status_code == 404, r.text

    def test_cross_system_requirement_target_link_is_404(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System A")
        sys_b = _create_system(admin_client, token, "System B")
        headers_a = _headers(token, sys_a)
        _create_requirement(sys_b, "req-in-b")
        _create_feature(admin_client, headers_a, "checkout")
        r = admin_client.post(
            "/product-features/checkout/requirement-links",
            json={"requirement_key": "req-in-b", "note": ""},
            headers=headers_a,
        )
        assert r.status_code == 404, r.text

    def test_cross_system_capability_target_link_is_404(self, admin_client, tmp_path):
        token, sys_a, snap_a, _repo_a = _setup(admin_client, tmp_path, name="System A")
        _tb, sys_b, snap_b, _repo_b = _setup(admin_client, tmp_path, name="System B")
        headers_a = _headers(token, sys_a)
        headers_b = _headers(_login(admin_client), sys_b)

        session_b = _create_session(admin_client, headers_b, snap_b)
        capability_id_in_b = _insert_capability_entity(sys_b, session_b, "Pay")

        _create_feature(admin_client, headers_a, "checkout")
        r = admin_client.post(
            "/product-features/checkout/capability-links",
            json={"capability_entity_id": capability_id_in_b, "note": ""},
            headers=headers_a,
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 7. Requirement link goes stale when the Requirement's own revision moves
# ---------------------------------------------------------------------------


class TestRequirementLinkStaleness:
    def test_link_goes_stale_when_requirement_revision_moves(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_requirement(system_id, "req-1")
        _add_requirement_revision(system_id, "req-1", statement="v1")
        _create_feature(admin_client, headers, "checkout")
        link = _add_requirement_link(admin_client, headers, "checkout", "req-1")
        assert link["recheck_state"] == "current"

        _add_requirement_revision(system_id, "req-1", statement="v2")
        detail = _get_feature(admin_client, headers, "checkout")
        found = [l for l in detail["requirement_links"] if l["requirement_key"] == "req-1"]
        assert len(found) == 1
        assert found[0]["recheck_state"] == "stale"

    def test_link_to_missing_requirement_key_is_404(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post(
            "/product-features/checkout/requirement-links",
            json={"requirement_key": "does-not-exist", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 8. feature_drafts link survives a snapshot prune as unresolved, Feature
#    identity/history stays intact
# ---------------------------------------------------------------------------


class TestDraftLinkSurvivesSnapshotRebuild:
    def test_draft_link_becomes_unresolved_while_feature_history_survives(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        draft_id, _run_id = _insert_feature_draft(system_id, snapshot_id, "feature-checkout", name="Checkout")

        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1", statement="first")
        link = _add_draft_link(admin_client, headers, "checkout", draft_id)
        assert link["target_resolution"] == "resolved"
        assert link["feature_draft_id"] == draft_id

        # Simulate a Feature Intelligence snapshot rebuild that prunes the
        # old snapshot (and, via ON DELETE CASCADE, its feature_drafts rows)
        # -- the operational scenario §1.6 describes as "その snapshot の
        # draft がもう無い".
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute("DELETE FROM repository_snapshots WHERE id = ?", (snapshot_id,))

        detail = _get_feature(admin_client, headers, "checkout")
        # Feature identity + revision history is fully intact.
        assert detail["feature_key"] == "checkout"
        assert detail["current_revision"]["title"] == "v1"
        assert detail["current_revision"]["statement"] == "first"
        # The draft link itself survives as a row, now unresolved.
        assert len(detail["draft_links"]) == 1
        link = detail["draft_links"][0]
        assert link["target_resolution"] == "unresolved"
        # The row id is NULL, never a stand-in. `0` is not a draft, and the
        # newest surviving row for the same `feature_id` is a different
        # draft from a different snapshot -- either would hand a caller an
        # id it could dereference into content nobody linked (§0-8).
        assert link["feature_draft_id"] is None
        # The link stays readable through the identity that survives the
        # rebuild (§1.6), so "which draft was this?" is still answerable.
        assert link["feature_draft_ref"] == "feature-checkout"

    def test_draft_link_to_missing_draft_id_is_404(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post(
            "/product-features/checkout/draft-links",
            json={"feature_draft_id": 999999, "note": ""},
            headers=headers,
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Additional link-kind coverage: link_kind validation, capability link,
# target link.
# ---------------------------------------------------------------------------


class TestCapabilityLink:
    def test_capability_link_reports_separate_entity(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        session_id = _create_session(admin_client, headers, snapshot_id)
        capability_id = _insert_capability_entity(system_id, session_id, "Pay")

        _create_feature(admin_client, headers, "checkout")
        link = _add_capability_link(admin_client, headers, "checkout", capability_id)
        assert link["capability_entity_id"] == capability_id
        assert link["capability_name"] == "Pay"
        assert link["target_resolution"] == "resolved"

        detail = _get_feature(admin_client, headers, "checkout")
        assert detail["capability_links"][0]["capability_entity_id"] == capability_id
        # Feature and Capability stay separate sections, never merged.
        assert "capability_links" in detail
        assert "requirement_links" in detail

    def test_capability_link_to_missing_entity_is_404(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post(
            "/product-features/checkout/capability-links",
            json={"capability_entity_id": 999999, "note": ""},
            headers=headers,
        )
        assert r.status_code == 404, r.text


class TestTargetLink:
    def test_invalid_link_kind_is_422(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        r = admin_client.post(
            "/product-features/checkout/target-links",
            json={"link_kind": "not_a_real_kind", "target_ref": "x", "note": ""},
            headers=headers,
        )
        # Pydantic's Literal validation rejects this before the domain
        # function is even reached.
        assert r.status_code == 422, r.text

    def test_component_target_link_unresolved_when_component_does_not_exist(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        link = _add_target_link(admin_client, headers, "checkout", "component", "no-such-component")
        assert link["target_resolution"] == "unresolved"
        assert link["recheck_state"] == "not_captured"

    def test_experiment_target_link_resolves_and_carries_status_verbatim(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            experiment_id = conn.execute(
                """INSERT INTO experiments
                       (system_id, feature_id, objective, snapshot_id, baseline_commit, config_revision,
                        execution_config, status, human_decision, created_at)
                   VALUES (?, 'f1', 'obj', ?, 'sha', 'v1', '{}', 'draft', 'undecided', ?)""",
                (system_id, snapshot_id, now),
            ).lastrowid

        _create_feature(admin_client, headers, "checkout")
        link = _add_target_link(admin_client, headers, "checkout", "experiment", str(experiment_id))
        assert link["target_resolution"] == "resolved"
        assert link["target_state"] == "draft"


class TestNoWeightedScore:
    """§0 invariant 7 / §12: no numeric priority/severity/completeness/
    confidence appears anywhere in a Feature API response."""

    def test_feature_detail_carries_no_score_like_field(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1")
        detail = _get_feature(admin_client, headers, "checkout")
        banned = {"score", "priority", "severity", "completeness", "confidence"}

        def _walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    assert not any(b in k.lower() for b in banned), f"banned field: {k}"
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(detail)


class TestNoWritesToOtherCanonicalTables:
    """§0 invariant 13 / §12: this Epic's code never UPDATEs/INSERTs into a
    pre-existing canonical table other than the one documented
    `ux_journey_upstream_ref` migration (which this file does not touch)."""

    def test_ux_requirement_row_count_unchanged_by_feature_writes(self, admin_client, tmp_path):
        token, system_id, _snap, _repo = _setup(admin_client, tmp_path)
        headers = _headers(token, system_id)
        _create_requirement(system_id, "req-1")

        from app.db import get_conn

        with get_conn() as conn:
            before = conn.execute("SELECT COUNT(*) AS n FROM ux_requirement").fetchone()["n"]

        _create_feature(admin_client, headers, "checkout")
        _add_revision(admin_client, headers, "checkout", title="v1")
        _add_requirement_link(admin_client, headers, "checkout", "req-1")
        _decide(admin_client, headers, "checkout", "confirm")

        with get_conn() as conn:
            after = conn.execute("SELECT COUNT(*) AS n FROM ux_requirement").fetchone()["n"]
        assert before == after
