"""Tests for Issue #429/#430 -- Product Gap (Epic #427).

`docs/product-objective-lineage.md` §12 is the acceptance list this file is
organized (together with `test_product_objective.py`) around. This file
covers Gap-specific behaviour:

1. identity + belonging to a Milestone (§5.2, never changeable afterward).
2. append-only revisions; `content_digest` excludes `suggested_priority_note`
   (§8) so an AI's own updated suggestion never stales a human's decision.
3. §5.6's lifecycle transition table, including the terminal-state 422 on
   `prioritize`.
4. §5.7's `priority_band` axis is independent of `lifecycle` -- it survives
   past a `resolve` decision (audit), and the two decisions maintain
   SEPARATE "current" pointers on the shared `product_gap_decision` table.
5. source-ref federation (§5.4/§5.10): duplicate rejection, and partial
   failure -- one resolver raising (or the module not existing at all, since
   `product_gap_sources.py` may not be written yet in this session) degrades
   only that ONE entry to `source_state='unavailable'`, never the whole
   request.
6. §6 "no auto success": nothing (a resolved source, an issue-draft link, a
   Milestone reaching `met`) ever moves `lifecycle` except an explicit human
   `product_gap_decision`.
7. no numeric score/severity/priority/confidence field anywhere in a
   response.
8. `decision_method` on the decision ledger is fixed to `manual`.

Shares the same router-registration / login / system-creation helpers as
`test_product_objective.py` (duplicated locally rather than imported, the
same way `test_ux_design.py` and its sibling contract-test files each carry
their own copy rather than share a test-only module).
"""

from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import product_objective


def _register_routers(app) -> None:
    from app.auth import get_principal
    from app.routes import product_gaps, product_objectives

    _auth = [Depends(get_principal)]
    existing_prefixes = {getattr(r, "path", "") for r in app.routes}
    if not any(p.startswith("/product-objectives") for p in existing_prefixes):
        app.include_router(product_objectives.router, dependencies=_auth)
        app.include_router(product_objectives.milestone_router, dependencies=_auth)
    if not any(p.startswith("/product-gaps") for p in existing_prefixes):
        app.include_router(product_gaps.router, dependencies=_auth)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-gap-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "100000")
    from app import resource_limits  # noqa: WPS433
    from app.main import app  # noqa: WPS433

    resource_limits.reset_in_memory_rate_limits()
    _register_routers(app)
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
        "/systems", json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client, name="System Gap"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


def _make_objective_and_milestone(client, headers, objective_key="o1", milestone_key="m1"):
    r = client.post("/product-objectives", json={"objective_key": objective_key}, headers=headers)
    assert r.status_code == 201, r.text
    r = client.post(
        "/product-milestones", json={"objective_key": objective_key, "milestone_key": milestone_key}, headers=headers
    )
    assert r.status_code == 201, r.text
    return milestone_key


def _create_gap(client, headers, milestone_key, gap_key, expect=201):
    r = client.post("/product-gaps", json={"milestone_key": milestone_key, "gap_key": gap_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_gap_revision(client, headers, gap_key, expect=201, **fields):
    body = {
        "title": "", "current_state": "", "target_state": "", "target_state_mode": "unknown",
        "interpretation": "", "suggested_priority_note": "", "change_note": "",
    }
    body.update(fields)
    r = client.post(f"/product-gaps/{gap_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide_gap(client, headers, gap_key, decision, expect=201, priority_band="unset", rationale="", captured_digest=""):
    r = client.post(
        f"/product-gaps/{gap_key}/decisions",
        json={
            "decision": decision, "priority_band": priority_band, "rationale": rationale,
            "captured_digest": captured_digest,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_source_ref(client, headers, gap_key, source_kind, source_ref="", expect=201, note=""):
    r = client.post(
        f"/product-gaps/{gap_key}/source-refs",
        json={"source_kind": source_kind, "source_ref": source_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


# ---------------------------------------------------------------------------
# §5.2 identity + belonging
# ---------------------------------------------------------------------------


class TestGapIdentity:
    def test_empty_key_is_422(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Key Required")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        r = _create_gap(admin_client, headers, milestone_key, "", expect=422)
        assert r.json()["detail"]["code"] == "product_gap_key_required"

    def test_duplicate_key_is_409(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Key Conflict")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r = _create_gap(admin_client, headers, milestone_key, "g1", expect=409)
        assert r.json()["detail"]["code"] == "product_gap_key_conflict"

    def test_missing_milestone_is_404(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap No Milestone")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/product-gaps", json={"milestone_key": "does-not-exist", "gap_key": "g1"}, headers=headers
        )
        assert r.status_code == 404

    def test_default_lifecycle_is_open_and_priority_unset(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Default")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        row = _create_gap(admin_client, headers, milestone_key, "g1")
        assert row["lifecycle"] == "open"
        assert row["priority_band"] == "unset"
        assert row["milestone_key"] == milestone_key
        assert row["objective_key"] == "o1"
        assert row["read_flags"] == []

    def test_same_key_in_two_systems_does_not_collide(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System Gap Iso A")
        sys_b = _create_system(admin_client, token, "System Gap Iso B")
        ms_a = _make_objective_and_milestone(admin_client, _headers(token, sys_a))
        ms_b = _make_objective_and_milestone(admin_client, _headers(token, sys_b))
        _create_gap(admin_client, _headers(token, sys_a), ms_a, "shared")
        _create_gap(admin_client, _headers(token, sys_b), ms_b, "shared")
        list_a = admin_client.get("/product-gaps", headers=_headers(token, sys_a)).json()
        list_b = admin_client.get("/product-gaps", headers=_headers(token, sys_b)).json()
        assert [g["gap_key"] for g in list_a["gaps"]] == ["shared"]
        assert [g["gap_key"] for g in list_b["gaps"]] == ["shared"]


class TestGapRevisions:
    def test_revision_digest_excludes_suggested_priority_note(self):
        d1 = product_objective.gap_revision_digest(
            title="t", current_state="c", target_state="tg", target_state_mode="own", interpretation="i"
        )
        d2 = product_objective.gap_revision_digest(
            title="t", current_state="c", target_state="tg", target_state_mode="own", interpretation="i"
        )
        assert d1 == d2

    def test_append_only_revision_preserves_prior_decision_and_stales_recheck(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Recheck")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_gap_revision(admin_client, headers, "g1", title="v1")
        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        digest = detail["current_revision"]["content_digest"]

        _decide_gap(admin_client, headers, "g1", "acknowledge", captured_digest=digest)
        after_ack = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert after_ack["lifecycle"] == "acknowledged"
        assert after_ack["recheck_state"] == "current"
        assert len(after_ack["decisions"]) == 1

        _add_gap_revision(admin_client, headers, "g1", title="v2")
        after_edit = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert after_edit["lifecycle"] == "acknowledged"
        assert after_edit["recheck_state"] == "stale"
        assert len(after_edit["decisions"]) == 1


# ---------------------------------------------------------------------------
# §5.6 lifecycle transitions
# ---------------------------------------------------------------------------


class TestGapLifecycleTransitions:
    def test_acknowledge_only_legal_from_open(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Ack")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _decide_gap(admin_client, headers, "g1", "acknowledge")
        r = _decide_gap(admin_client, headers, "g1", "acknowledge", expect=422)
        assert r.json()["detail"]["code"] == "product_gap_not_decidable"

    def test_resolve_legal_from_open_acknowledged_and_deferred(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Resolve")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        for key in ("from-open", "from-ack", "from-defer"):
            _create_gap(admin_client, headers, milestone_key, key)

        _decide_gap(admin_client, headers, "from-open", "resolve")

        _decide_gap(admin_client, headers, "from-ack", "acknowledge")
        _decide_gap(admin_client, headers, "from-ack", "resolve")

        _decide_gap(admin_client, headers, "from-defer", "defer")
        _decide_gap(admin_client, headers, "from-defer", "resolve")

        for key in ("from-open", "from-ack", "from-defer"):
            row = admin_client.get(f"/product-gaps/{key}", headers=headers).json()
            assert row["lifecycle"] == "resolved"

    def test_reopen_legal_from_every_terminal_state_plus_deferred(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Reopen")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        cases = {"was-resolved": "resolve", "was-rejected": "reject", "was-obsolete": "retire", "was-deferred": "defer"}
        for key, decision in cases.items():
            _create_gap(admin_client, headers, milestone_key, key)
            _decide_gap(admin_client, headers, key, decision)
        for key in cases:
            reopened = _decide_gap(admin_client, headers, key, "reopen")
            assert reopened["decision"] == "reopen"
            row = admin_client.get(f"/product-gaps/{key}", headers=headers).json()
            assert row["lifecycle"] == "open"

    def test_reopen_illegal_from_open_or_acknowledged(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Reopen Gate")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r = _decide_gap(admin_client, headers, "g1", "reopen", expect=422)
        assert r.json()["detail"]["code"] == "product_gap_not_decidable"

    def test_prioritize_illegal_on_a_terminal_gap(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Prioritize Gate")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _decide_gap(admin_client, headers, "g1", "resolve")
        r = _decide_gap(admin_client, headers, "g1", "prioritize", priority_band="now", expect=422)
        assert r.json()["detail"]["code"] == "product_gap_not_decidable"


class TestGapPriorityBandIndependentAxis:
    """§5.7/§5.9: `priority_band` maintains its OWN "current" pointer on the
    shared `product_gap_decision` table, separate from `lifecycle` --
    surviving past a `resolve` decision, and never moved by any decision
    except `prioritize` itself."""

    def test_prioritize_never_moves_lifecycle(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Prioritize No Move")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        before = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert before["lifecycle"] == "open"
        _decide_gap(admin_client, headers, "g1", "prioritize", priority_band="now")
        after = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert after["lifecycle"] == "open"
        assert after["priority_band"] == "now"

    def test_priority_band_survives_a_resolve_decision(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Priority Survives")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _decide_gap(admin_client, headers, "g1", "prioritize", priority_band="now")
        _decide_gap(admin_client, headers, "g1", "resolve")
        row = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert row["lifecycle"] == "resolved"
        assert row["priority_band"] == "now"

    def test_lifecycle_decisions_leave_priority_band_unset_on_their_own_row(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Priority Row")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        ack = _decide_gap(admin_client, headers, "g1", "acknowledge")
        assert ack["priority_band"] == "unset"


class TestDecisionMethodFixedToManual:
    def test_decision_method_is_always_manual_in_response(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Manual")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        decision = _decide_gap(admin_client, headers, "g1", "acknowledge")
        assert decision["decision_method"] == "manual"

    def test_decision_method_cannot_be_supplied_by_the_caller(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Manual Forbid")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r = admin_client.post(
            "/product-gaps/g1/decisions",
            json={"decision": "acknowledge", "decision_method": "reasoning_llm"},
            headers=headers,
        )
        assert r.status_code == 422


class TestGapStaleDigest:
    def test_mismatched_captured_digest_is_409(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Stale")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_gap_revision(admin_client, headers, "g1", title="v1")
        r = _decide_gap(admin_client, headers, "g1", "acknowledge", expect=409, captured_digest="not-the-real-digest")
        assert r.json()["detail"]["code"] == "product_gap_decision_stale_digest"

    def test_empty_captured_digest_is_never_checked_and_reads_not_captured(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap NotCaptured")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_gap_revision(admin_client, headers, "g1", title="v1")
        _decide_gap(admin_client, headers, "g1", "acknowledge", captured_digest="")
        row = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert row["lifecycle"] == "acknowledged"
        assert row["recheck_state"] == "not_captured"


# ---------------------------------------------------------------------------
# §5.4/§5.10 source-ref federation
# ---------------------------------------------------------------------------


class TestGapSourceRefs:
    def test_duplicate_source_ref_is_409(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Source Dup")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_source_ref(admin_client, headers, "g1", "manual", "ref-a")
        r = _add_source_ref(admin_client, headers, "g1", "manual", "ref-a", expect=409)
        assert r.json()["detail"]["code"] == "product_gap_source_duplicate"

    def test_source_ref_resolution_failure_degrades_only_that_entry(self, admin_client):
        """`app/product_gap_sources.py` (Issue #430) may not be written yet
        in this session -- the lazy import must fail gracefully into
        `source_state='unavailable'`, and MUST NOT prevent the source ref
        from being created or the Gap from being readable (§5.5/§5.10)."""
        token, system_id = _setup(admin_client, "System Gap Source Degrade")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        out = _add_source_ref(admin_client, headers, "g1", "manual", "ref-a")
        assert out["source_state"] in product_objective.GAP_SOURCE_STATES

        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert len(detail["source_refs"]) == 1
        # The Gap itself must still be fully readable regardless of whether
        # the source resolver module exists.
        assert detail["gap_key"] == "g1"

    def test_two_gaps_can_reference_the_same_detected_fact_independently(self, admin_client):
        """§5.2: the same `(source_kind, source_ref)` may back Gap rows
        under DIFFERENT Milestones -- this is not duplication."""
        token, system_id = _setup(admin_client, "System Gap Source Shared")
        headers = _headers(token, system_id)
        r = admin_client.post("/product-objectives", json={"objective_key": "o1"}, headers=headers)
        assert r.status_code == 201, r.text
        admin_client.post(
            "/product-milestones", json={"objective_key": "o1", "milestone_key": "m1"}, headers=headers
        )
        admin_client.post(
            "/product-milestones", json={"objective_key": "o1", "milestone_key": "m2"}, headers=headers
        )
        _create_gap(admin_client, headers, "m1", "g1")
        _create_gap(admin_client, headers, "m2", "g2")
        _add_source_ref(admin_client, headers, "g1", "manual", "shared-ref")
        _add_source_ref(admin_client, headers, "g2", "manual", "shared-ref")
        d1 = admin_client.get("/product-gaps/g1", headers=headers).json()
        d2 = admin_client.get("/product-gaps/g2", headers=headers).json()
        assert d1["source_refs"][0]["source_ref"] == "shared-ref"
        assert d2["source_refs"][0]["source_ref"] == "shared-ref"

    def test_system_understanding_gap_pin_is_resolver_owned_not_caller_supplied(self, admin_client):
        """§5.10: `add_gap_source_ref` never accepts a pin from the request
        body (`ProductGapSourceRefCreateRequest` has no such field), but the
        resolver still determines and stores its OWN pin
        (`resolved_snapshot_id` -> `captured_snapshot_id`) in the same call
        that computes `captured_digest` -- so a re-check later reads back
        against the exact snapshot this source was created against, not
        `None`."""
        from app import gap_triage
        from app import system_understanding_service as sus
        from app.db import get_conn

        token, system_id = _setup(admin_client, "System Gap Source Pin")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")

        with get_conn() as conn:
            snapshot_id = conn.execute(
                """INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/x', 'sha1', 'ready', 0, 0)""",
                (system_id,),
            ).lastrowid
            conn.execute(
                """INSERT INTO code_entrypoints
                       (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                        handler_path, handler_qualified_name, line_start, line_end, route_method, route_path,
                        created_at)
                   VALUES (?, ?, 'api', 'GET /widgets', 'http', 'widgets', 'app/widgets.py',
                           'app.widgets.list_widgets', 1, 5, 'GET', '/widgets', 0)""",
                (system_id, snapshot_id),
            )
            gaps = sus._load_gaps_from_reconciler(conn, system_id, snapshot_id)
            gap_triage.annotate_gaps(conn, system_id, snapshot_id, gaps)
            assert len(gaps) == 1
            ref = gap_triage.gap_key(gaps[0])

        out = _add_source_ref(admin_client, headers, "g1", "system_understanding_gap", ref)
        assert out["source_state"] == "current"
        assert out["captured_snapshot_id"] == snapshot_id

        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert detail["source_refs"][0]["captured_snapshot_id"] == snapshot_id

    def test_resolve_source_signature_is_never_called_with_a_held_connection_error(self, admin_client):
        """A resolver contract sanity check at the domain layer: calling
        `add_gap_source_ref` for every finite `source_kind` never raises out
        of the function itself (§5.10: `resolve_source` degrades, it never
        propagates)."""
        from app.db import get_conn

        token, system_id = _setup(admin_client, "System Gap Source AllKinds")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        with get_conn() as conn:
            for kind in product_objective.GAP_SOURCE_KINDS:
                out = product_objective.add_gap_source_ref(
                    conn, system_id=system_id, gap_key="g1", source_kind=kind, source_ref=f"ref-{kind}",
                    created_by="root",
                )
                assert out["source_kind"] == kind
                assert out["source_state"] in product_objective.GAP_SOURCE_STATES


# ---------------------------------------------------------------------------
# §1.5 evidence refs + artifact links (downstream, never a detection source)
# ---------------------------------------------------------------------------


class TestGapEvidenceAndArtifactLinks:
    def test_evidence_ref_round_trips(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Evidence")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r = admin_client.post(
            "/product-gaps/g1/evidence-refs",
            json={"evidence_kind": "trace", "evidence_ref": "trace-123", "note": "observed in prod"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert len(detail["evidence_refs"]) == 1
        assert detail["evidence_refs"][0]["evidence_ref"] == "trace-123"

    def test_evidence_and_artifact_deep_links_are_resolved_server_side(self, admin_client):
        """§5.8: the Gap detail carries the screen for each reference kind,
        resolved from `product_gap_sources`' per-kind tables. The Dashboard
        must never build one of these URLs itself -- that would be a second
        answer to "which screen owns this kind" (§0-1)."""
        token, system_id = _setup(admin_client, "System Gap Deep Links")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        for kind, ref in (("trace", "t-1"), ("human_report", "reported by ops")):
            assert admin_client.post(
                "/product-gaps/g1/evidence-refs",
                json={"evidence_kind": kind, "evidence_ref": ref},
                headers=headers,
            ).status_code == 201
        for kind, ref in (("ux_requirement", "req-a"), ("product_feature", "feat-a")):
            assert admin_client.post(
                "/product-gaps/g1/artifact-links",
                json={"link_kind": kind, "target_ref": ref},
                headers=headers,
            ).status_code == 201

        # The 201 body carries the same resolved link the detail read does.
        # Without this, a create response would serialize the model defaults
        # and tell the client "no screen exists" about a `trace`.
        created = admin_client.post(
            "/product-gaps/g1/evidence-refs",
            json={"evidence_kind": "experiment", "evidence_ref": "e-9"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["deep_link"] == "/experiments"
        assert created.json()["deep_link_state"] == "available"
        created_link = admin_client.post(
            "/product-gaps/g1/artifact-links",
            json={"link_kind": "product_feature", "target_ref": "feat-b"},
            headers=headers,
        )
        assert created_link.status_code == 201, created_link.text
        assert created_link.json()["deep_link"] is None
        assert created_link.json()["deep_link_state"] == "unavailable"

        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        evidence = {e["evidence_kind"]: e for e in detail["evidence_refs"]}
        assert evidence["trace"]["deep_link"] == "/components"
        assert evidence["trace"]["deep_link_state"] == "available"
        # No probe-agent screen owns a free-text human report. "No screen"
        # is reported honestly, never as a plausible URL (§5.8).
        assert evidence["human_report"]["deep_link"] is None
        assert evidence["human_report"]["deep_link_state"] == "unavailable"

        artifacts = {a["link_kind"]: a for a in detail["artifact_links"]}
        assert artifacts["ux_requirement"]["deep_link"] == "/ux-design-studio"
        assert artifacts["ux_requirement"]["deep_link_state"] == "available"
        # A Product Feature has an API but no screen of its own -- the same
        # honest `unavailable` `node_anomaly` carries.
        assert artifacts["product_feature"]["deep_link"] is None
        assert artifacts["product_feature"]["deep_link_state"] == "unavailable"

    def test_artifact_link_duplicate_is_409(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap Artifact Dup")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r1 = admin_client.post(
            "/product-gaps/g1/artifact-links",
            json={"link_kind": "issue_draft", "target_ref": "42"},
            headers=headers,
        )
        assert r1.status_code == 201, r1.text
        r2 = admin_client.post(
            "/product-gaps/g1/artifact-links",
            json={"link_kind": "issue_draft", "target_ref": "42"},
            headers=headers,
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["code"] == "product_gap_artifact_duplicate"

    def test_ux_journey_is_rejected_as_an_artifact_link_kind(self, admin_client):
        """§5.11: a Gap's Journey connection has exactly ONE writable home
        (`ux_journey_upstream_ref(ref_kind='product_gap')`, on the Journey
        side). `product_gap_artifact_link` no longer accepts `ux_journey` at
        all -- writing it there too would let the two disagree.

        `ux_journey` is outside `ProductGapArtifactLinkKind` itself now, so
        FastAPI/pydantic reject the request body before it ever reaches
        `add_gap_artifact_link` -- a structural 422 rather than the app's
        `product_link_kind_invalid` code, but still a 422, and the field
        this rejects is named in the response either way."""
        token, system_id = _setup(admin_client, "System Gap Artifact No UxJourney")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        r = admin_client.post(
            "/product-gaps/g1/artifact-links",
            json={"link_kind": "ux_journey", "target_ref": "some-journey"},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert "link_kind" in r.text

    def test_domain_layer_also_rejects_ux_journey_with_the_app_error_code(self, admin_client):
        """The domain-level `LinkKindInvalid` -> `product_link_kind_invalid`
        path stays correct in its own right (exercised directly, since
        pydantic's Literal now blocks the value before an HTTP request could
        reach it -- see the test above)."""
        token, system_id = _setup(admin_client, "System Gap Artifact No UxJourney Domain")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(product_objective.LinkKindInvalid):
                product_objective.add_gap_artifact_link(
                    conn, system_id=system_id, gap_key="g1", link_kind="ux_journey",
                    target_ref="some-journey", created_by="dev",
                )

    def test_closing_the_linked_issue_draft_does_not_resolve_the_gap(self, admin_client):
        """§1.5/§6: linking an Issue Draft is recording a downstream
        candidate, never a lifecycle transition."""
        token, system_id = _setup(admin_client, "System Gap Artifact No Auto Close")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        admin_client.post(
            "/product-gaps/g1/artifact-links",
            json={"link_kind": "issue_draft", "target_ref": "42"},
            headers=headers,
        )
        row = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert row["lifecycle"] == "open"


# ---------------------------------------------------------------------------
# §6 "no auto success"
# ---------------------------------------------------------------------------


class TestNoAutoSuccess:
    def test_all_gaps_resolved_does_not_move_milestone_achievement(self, admin_client):
        token, system_id = _setup(admin_client, "System No Auto Milestone")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _create_gap(admin_client, headers, milestone_key, "g2")
        _decide_gap(admin_client, headers, "g1", "resolve")
        _decide_gap(admin_client, headers, "g2", "resolve")

        row = admin_client.get(f"/product-milestones/{milestone_key}", headers=headers).json()
        assert row["achievement"] == "unassessed"
        assert row["design_status"] == "proposed"

    def test_source_contradicted_or_disappeared_never_moves_lifecycle(self, admin_client):
        """§5.6: `source_state` only ever produces read-time advisory flags
        (`reopen_candidate`/`close_candidate`), never a lifecycle move."""
        from app.db import get_conn

        token, system_id = _setup(admin_client, "System No Auto Source")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_source_ref(admin_client, headers, "g1", "manual", "ref-a")

        with get_conn() as conn:
            lifecycle_before, _ = product_objective.derive_gap_lifecycle(conn, system_id, "g1")
        assert lifecycle_before == "open"

        # Regardless of what the (possibly absent) resolver module reports
        # for this source, the Gap's lifecycle must still read exactly what
        # the decision ledger says -- nothing here writes to
        # `product_gap_decision`.
        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert detail["lifecycle"] == "open"


# ---------------------------------------------------------------------------
# No numeric score anywhere in a response
# ---------------------------------------------------------------------------


_SCORE_LIKE_KEY_SUBSTRINGS = ("priority", "severity", "score", "confidence")


def _assert_no_numeric_score_fields(payload, path=""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = key.lower()
            new_path = f"{path}.{key}" if path else key
            if any(s in key_lower for s in _SCORE_LIKE_KEY_SUBSTRINGS) and isinstance(value, (int, float)) and not isinstance(value, bool):
                raise AssertionError(f"numeric score-like field {new_path!r} = {value!r} in response")
            _assert_no_numeric_score_fields(value, new_path)
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            _assert_no_numeric_score_fields(item, f"{path}[{idx}]")


class TestNoWeightedScore:
    def test_gap_responses_carry_no_numeric_score_field(self, admin_client):
        token, system_id = _setup(admin_client, "System Gap No Score")
        headers = _headers(token, system_id)
        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_source_ref(admin_client, headers, "g1", "manual", "ref-a")
        _decide_gap(admin_client, headers, "g1", "prioritize", priority_band="now")

        detail = admin_client.get("/product-gaps/g1", headers=headers).json()
        gap_list = admin_client.get("/product-gaps", headers=headers).json()
        for payload in (detail, gap_list):
            _assert_no_numeric_score_fields(payload)


# ---------------------------------------------------------------------------
# No write to existing canonical rows
# ---------------------------------------------------------------------------


class TestNoWriteToExistingCanonicalTables:
    def test_full_gap_api_walk_touches_no_pre_existing_table(self, admin_client):
        from app.db import get_conn

        token, system_id = _setup(admin_client, "System No Canonical Write")
        headers = _headers(token, system_id)

        watched_tables = (
            "interview_session", "understanding_capability_entity", "ux_journey",
            "solution_design", "stakeholder_need", "evolution_node", "components", "probe_points",
            "feature_drafts",
        )
        with get_conn() as conn:
            before = {}
            for table in watched_tables:
                try:
                    before[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
                except Exception:
                    before[table] = None

        milestone_key = _make_objective_and_milestone(admin_client, headers)
        _create_gap(admin_client, headers, milestone_key, "g1")
        _add_gap_revision(admin_client, headers, "g1", title="v1")
        _add_source_ref(admin_client, headers, "g1", "manual", "ref-a")
        admin_client.post(
            "/product-gaps/g1/evidence-refs",
            json={"evidence_kind": "trace", "evidence_ref": "t1", "note": ""},
            headers=headers,
        )
        admin_client.post(
            "/product-gaps/g1/artifact-links", json={"link_kind": "issue_draft", "target_ref": "1"}, headers=headers
        )
        _decide_gap(admin_client, headers, "g1", "acknowledge")

        with get_conn() as conn:
            after = {}
            for table in watched_tables:
                try:
                    after[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
                except Exception:
                    after[table] = None

        assert before == after
