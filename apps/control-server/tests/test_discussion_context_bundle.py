"""Tests for Issue #458 (Epic #443 §9, DD-CTX-01..05): the cross-target
discussion context bundle, its budget/coverage contract, and the explicit
"追加取得" continuation.

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §9.1-§9.3 is the canonical contract.
Acceptance criteria covered here:

1. `GET /assistant/discussion-threads/{id}/context-bundle` returns the exact
   §9.1 wire shape, built ONLY from registered `discussion_adapters`
   resolvers (`TestBundleShape`).
2. A `target_kind` with no registered relation extractor reports
   `related.operation_state == "unsupported"` (structural "relation未登録",
   never a silently-empty "complete"); a `scope="screen"` root with no
   Overview-style seed reports `not_applicable` (`TestRelationRegistration`).
3. >50 related candidates and an identity-based cycle are both reflected
   correctly in `coverage` -- never an implicit truncation the caller cannot
   see (`TestBudgetAndCycle`).
4. The dependency manifest / sources catalog carry real digests and deep
   links (`TestDependenciesAndSources`).
5. `POST .../context-expansions` resumes a partial section without
   re-returning an already-emitted entry, and rejects an unknown/tampered
   token (404), an expired one (410), a bundle_digest mismatch (409), and a
   dependency that moved since the cursor was minted (409) --
   (`TestContextExpansionEndpoint`).
6. A thread in a foreign System 404s before any cursor logic runs
   (`TestSystemIsolation`).
7. The durable audit table is separate from the short-lived cursor and
   survives a cursor's own cleanup (`TestDurableAudit`).
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import discussion_context_bundle
from app.discussion_context_bundle import ContextBudget


# ---------------------------------------------------------------------------
# Fixtures / low-level helpers (mirrors tests/test_discussion_target_expansion.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-discussion-context-bundle-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app

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


def _create_thread(client, headers, *, scope, screen_id, target_kind, target_ref, expect=200):
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": scope, "screen_id": screen_id, "target_kind": target_kind, "target_ref": target_ref},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_bundle(client, headers, thread_id, expect=200):
    r = client.get(f"/assistant/discussion-threads/{thread_id}/context-bundle", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _expand(client, headers, thread_id, *, bundle_digest, continuation, expect=200):
    r = client.post(
        f"/assistant/discussion-threads/{thread_id}/context-expansions",
        json={"bundle_digest": bundle_digest, "continuation": continuation},
        headers=headers,
    )
    if expect is not None:
        assert r.status_code == expect, r.text
    return r.json() if r.status_code < 300 else r


def _section(bundle, section_id):
    return next(s for s in bundle["sections"] if s["section_id"] == section_id)


# --- Product Objective / Milestone / Gap / Journey fixtures (subset of
# tests/test_discussion_target_expansion.py's own helpers) -------------------


def _create_objective(client, headers, objective_key, *, expect=201):
    r = client.post("/product-objectives", json={"objective_key": objective_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_objective_revision(client, headers, objective_key, *, expect=201, **fields):
    body = {"title": "目標", "intent": "", "contribution": "", "scope_note": "", "summary": "", "change_note": ""}
    body.update(fields)
    r = client.post(f"/product-objectives/{objective_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_milestone(client, headers, objective_key, milestone_key, *, expect=201):
    r = client.post(
        "/product-milestones", json={"objective_key": objective_key, "milestone_key": milestone_key}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_milestone_revision(client, headers, milestone_key, *, expect=201, **fields):
    body = {
        "title": "中間目標", "target_state": "", "verification_method": "unavailable", "verification_note": "",
        "sequence_hint": 0, "summary": "", "change_note": "",
    }
    body.update(fields)
    r = client.post(f"/product-milestones/{milestone_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_gap(client, headers, milestone_key, gap_key, *, expect=201, **fields):
    body = {"milestone_key": milestone_key, "gap_key": gap_key}
    body.update(fields)
    r = client.post("/product-gaps", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_gap_revision(client, headers, gap_key, *, expect=201, **fields):
    body = {
        "title": "ギャップ", "current_state": "", "target_state": "", "target_state_mode": "unknown",
        "interpretation": "", "suggested_priority_note": "", "change_note": "",
    }
    body.update(fields)
    r = client.post(f"/product-gaps/{gap_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_journey(client, headers, journey_key, *, perspective="to_be", baseline_mode="undecided", expect=201):
    r = client.post(
        "/ux-design/journeys",
        json={"journey_key": journey_key, "perspective": perspective, "baseline_mode": baseline_mode},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_journey_revision(client, headers, journey_key, *, steps=None, expect=201, **fields):
    payload = {
        "title": "", "beneficiary": "", "usage_context": "", "entry_trigger": "",
        "value_arrival": "", "summary": "", "change_note": "", "steps": steps or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/journeys/{journey_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _link_journey_to_gap(client, headers, journey_key, gap_key, *, expect=201):
    r = client.post(
        f"/ux-design/journeys/{journey_key}/upstream-refs",
        json={"ref_kind": "product_gap", "target_ref": gap_key, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _setup_objective_milestone_gap(client, headers, prefix):
    """One Objective -> one Milestone -> one Gap, all resolved. Returns the
    three keys."""
    objective_key = f"{prefix}-obj"
    milestone_key = f"{prefix}-mile"
    gap_key = f"{prefix}-gap"
    _create_objective(client, headers, objective_key)
    _add_objective_revision(client, headers, objective_key)
    _create_milestone(client, headers, objective_key, milestone_key)
    _add_milestone_revision(client, headers, milestone_key)
    _create_gap(client, headers, milestone_key, gap_key)
    _add_gap_revision(client, headers, gap_key)
    return objective_key, milestone_key, gap_key


# ---------------------------------------------------------------------------
# 1. Bundle shape
# ---------------------------------------------------------------------------


class TestBundleShape:
    def test_bundle_shape_for_a_gap_root_with_milestone_and_journey_relations(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-bundle-shape")
        headers = _headers(token, system_id)
        _objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers, "shape")
        journey_key = "shape-journey"
        _create_journey(admin_client, headers, journey_key)
        _add_journey_revision(admin_client, headers, journey_key)
        _link_journey_to_gap(admin_client, headers, journey_key, gap_key)

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        thread_id = thread["thread"]["id"]

        bundle = _get_bundle(admin_client, headers, thread_id)
        assert bundle["schema_version"] == discussion_context_bundle.SCHEMA_VERSION
        assert bundle["bundle_digest"]
        assert bundle["root"]["target_kind"] == "product_gap"
        assert bundle["root"]["target_ref"] == gap_key
        assert bundle["root"]["digest"]

        self_section = _section(bundle, "self")
        assert self_section["operation_state"] == "available"
        assert self_section["coverage"]["completeness"] == "complete"
        assert self_section["facts"][0]["target_ref"] == gap_key

        related = _section(bundle, "related")
        assert related["operation_state"] == "available"
        related_refs = {(e["target_kind"], e["target_ref"]) for e in related["facts"]}
        assert ("product_milestone", milestone_key) in related_refs
        assert ("ux_journey", journey_key) in related_refs
        # Small, fully-covered walk -> complete, no continuation offered.
        assert related["coverage"]["completeness"] == "complete"
        assert related["coverage"]["continuation"] is None

        # No Overview root -> no `objective` section at all (never an empty
        # stub standing in for "not applicable here").
        assert not any(s["section_id"] == "objective" for s in bundle["sections"])

        source_refs = {(s["target_kind"], s["target_ref"]) for s in bundle["sources"]}
        assert ("product_gap", gap_key) in source_refs
        assert ("product_milestone", milestone_key) in source_refs

        dep_refs = {(d["target_kind"], d["target_ref"]) for d in bundle["dependencies"]}
        assert ("product_milestone", milestone_key) in dep_refs

        assert bundle["next_action"]["kind"] == "none"
        assert bundle["next_action"]["enabled"] is False

    def test_unknown_thread_id_is_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-bundle-404")
        headers = _headers(token, system_id)
        _get_bundle(admin_client, headers, 999999, expect=404)


# ---------------------------------------------------------------------------
# 2. Relation registration: unsupported / not_applicable
# ---------------------------------------------------------------------------


class TestRelationRegistration:
    def test_unregistered_relation_extractor_is_unsupported_never_empty_complete(self, admin_client, tmp_path):
        """`ux_requirement` has no entry in `_RELATION_EXTRACTORS` -- DD-CTX-
        04's "relation未登録" row must read `unsupported`, never a silently
        empty `complete` (which would misreport "confirmed no relations")."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-unsupported")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/ux-design/requirements",
            json={"requirement_key": "req1", "requirement_kind": "functional"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/ux-design/requirements/req1/revisions",
            json={"statement": "", "rationale": "", "constraint_text": "",
                  "out_of_scope_note": "", "change_note": "", "acceptance_criteria": []},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req1",
        )
        bundle = _get_bundle(admin_client, headers, thread["thread"]["id"])
        related = _section(bundle, "related")
        assert related["operation_state"] == "unsupported"
        assert related["coverage"]["total_count"] is None
        assert related["coverage"]["completeness"] == "unknown"
        assert related["coverage"]["stop_reason"] == "unsupported"
        assert related["facts"] == []

    def test_screen_root_without_overview_seed_is_not_applicable(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-not-applicable")
        headers = _headers(token, system_id)
        thread = _create_thread(
            admin_client, headers, scope="screen", screen_id="capability-map",
            target_kind="screen", target_ref="capability-map",
        )
        bundle = _get_bundle(admin_client, headers, thread["thread"]["id"])
        related = _section(bundle, "related")
        assert related["operation_state"] == "not_applicable"
        assert related["coverage"]["total_count"] == 0
        assert related["coverage"]["completeness"] == "complete"
        assert related["coverage"]["stop_reason"] == "not_applicable"

    def test_overview_root_carries_an_objective_section(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-overview")
        headers = _headers(token, system_id)
        thread = _create_thread(
            admin_client, headers, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )
        bundle = _get_bundle(admin_client, headers, thread["thread"]["id"])
        objective_section = _section(bundle, "objective")
        assert objective_section["operation_state"] == "available"
        # No Objective/Gap exists yet in this fresh System -- the section
        # still reads `available` (the projection itself succeeded), only
        # its embedded facts describe an empty state.
        assert objective_section["facts"][0]["facts"]["active_objective"] is None


# ---------------------------------------------------------------------------
# 3. Budget + cycle (module-level: precise, deterministic candidate counts)
# ---------------------------------------------------------------------------


class TestBudgetAndCycle:
    def _thread_id_for(self, client, headers, target_kind, target_ref, screen_id="objective-map"):
        thread = _create_thread(
            client, headers, scope="entity", screen_id=screen_id,
            target_kind=target_kind, target_ref=target_ref,
        )
        return thread["thread"]["id"]

    def test_51_candidates_at_depth_1_reports_unknown_not_a_guessed_total(self, admin_client, monkeypatch):
        """When the depth-1 sweep itself is cut short by the item budget,
        the depth-2 population is genuinely unknowable -- `total_count` must
        stay `None` (never a lower bound reported as fact) and
        `completeness` must be `unknown`, never `partial`."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-budget-51")
        headers = _headers(token, system_id)
        objective_key, _milestone_key, _gap_key = _setup_objective_milestone_gap(admin_client, headers, "b51")
        thread_id = self._thread_id_for(admin_client, headers, "product_objective", objective_key)

        fake_refs = [("product_milestone", f"fake-milestone-{i}") for i in range(60)]
        monkeypatch.setitem(
            discussion_context_bundle._RELATION_EXTRACTORS, "product_objective", lambda facts: list(fake_refs)
        )

        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_objective", objective_key, thread_id=thread_id,
            budget=ContextBudget(max_depth=2, section_item_limit=50, total_item_limit=200, total_byte_limit=65536),
        )
        related = next(s for s in bundle.sections if s.section_id == "related")
        assert related.coverage.returned_count == 50
        assert related.coverage.total_count is None
        assert related.coverage.completeness == "unknown"
        assert related.coverage.stop_reason == "item_budget"
        # Every fake candidate is genuinely unresolved (never invented data).
        assert all(e.resolution == "unresolved" for e in related.facts)

    def test_51_candidates_split_across_depth_1_and_2_reports_partial_with_a_real_total(
        self, admin_client, monkeypatch,
    ):
        """Depth-1 fully processed (fits under budget) but depth-2 does not
        -> the FULL candidate count is knowable (`total_count` is a real
        number), and completeness is `partial`, distinct from the
        depth-1-incomplete `unknown` case above."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-budget-partial")
        headers = _headers(token, system_id)
        objective_key, milestone_key, _gap_key = _setup_objective_milestone_gap(admin_client, headers, "bpart")
        thread_id = self._thread_id_for(admin_client, headers, "product_objective", objective_key)

        # Depth-1: exactly ONE real, resolvable milestone (fits easily).
        monkeypatch.setitem(
            discussion_context_bundle._RELATION_EXTRACTORS,
            "product_objective", lambda facts: [("product_milestone", milestone_key)],
        )
        # Depth-2 (from that milestone): 55 fake candidates -- more than the
        # section's own item budget of 50 once depth-1's 1 item is counted.
        fake_deps = [("product_gap", f"fake-gap-{i}") for i in range(55)]
        monkeypatch.setitem(
            discussion_context_bundle._RELATION_EXTRACTORS, "product_milestone", lambda facts: list(fake_deps)
        )

        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_objective", objective_key, thread_id=thread_id,
            budget=ContextBudget(max_depth=2, section_item_limit=50, total_item_limit=200, total_byte_limit=65536),
        )
        related = next(s for s in bundle.sections if s.section_id == "related")
        assert related.coverage.returned_count == 50
        assert related.coverage.total_count == 1 + 55
        assert related.coverage.completeness == "partial"
        assert related.coverage.stop_reason == "item_budget"

    def test_identity_cycle_is_cut_never_double_counted(self, admin_client, monkeypatch):
        """A candidate list that re-emits the ROOT's own identity (the
        simplest possible cycle) must never appear twice, and must never
        appear at all -- the root is already `visited` before the walk
        starts (DD-CTX-02: "cycleはidentityで打ち切る")."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-cycle")
        headers = _headers(token, system_id)
        objective_key, milestone_key, _gap_key = _setup_objective_milestone_gap(admin_client, headers, "cyc")
        thread_id = self._thread_id_for(admin_client, headers, "product_objective", objective_key)

        monkeypatch.setitem(
            discussion_context_bundle._RELATION_EXTRACTORS,
            "product_objective",
            lambda facts: [("product_objective", objective_key), ("product_milestone", milestone_key)],
        )

        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_objective", objective_key, thread_id=thread_id,
        )
        related = next(s for s in bundle.sections if s.section_id == "related")
        refs = [(e.target_kind, e.target_ref) for e in related.facts]
        assert ("product_objective", objective_key) not in refs
        assert refs.count(("product_milestone", milestone_key)) == 1
        assert related.coverage.completeness == "complete"
        assert related.coverage.returned_count == 1


# ---------------------------------------------------------------------------
# 4. Dependencies + sources
# ---------------------------------------------------------------------------


class TestDependenciesAndSources:
    def test_dependency_digest_matches_live_resolver_and_deep_link_is_present(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-deps")
        headers = _headers(token, system_id)
        _objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers, "deps")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        bundle = _get_bundle(admin_client, headers, thread["thread"]["id"])

        dep = next(d for d in bundle["dependencies"] if d["target_ref"] == milestone_key)
        source = next(s for s in bundle["sources"] if s["target_ref"] == milestone_key)
        assert dep["digest"] == source["digest"]
        assert dep["digest"] != ""
        assert source["deep_link"] is not None
        assert source["deep_link"].startswith("/objective-map")
        assert source["deep_link_state"] in ("selected", "screen_only")
        assert source["freshness"] == "current"


# ---------------------------------------------------------------------------
# 5. Context expansion endpoint
# ---------------------------------------------------------------------------


class TestContextExpansionEndpoint:
    def _setup_two_item_related_thread(self, client, headers, prefix):
        objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(client, headers, prefix)
        journey_key = f"{prefix}-journey"
        _create_journey(client, headers, journey_key)
        _add_journey_revision(client, headers, journey_key)
        _link_journey_to_gap(client, headers, journey_key, gap_key)
        thread = _create_thread(
            client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        return thread["thread"]["id"], milestone_key, journey_key

    def test_resume_returns_only_the_unseen_batch_never_a_repeat(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-resume")
        headers = _headers(token, system_id)
        thread_id, milestone_key, journey_key = self._setup_two_item_related_thread(
            admin_client, headers, "resume"
        )

        monkeypatch.setattr(
            "app.routes.assistant.discussion_context_bundle.DEFAULT_BUDGET",
            ContextBudget(max_depth=2, section_item_limit=1, total_item_limit=2, total_byte_limit=65536),
        )

        bundle = _get_bundle(admin_client, headers, thread_id)
        related = _section(bundle, "related")
        assert related["coverage"]["returned_count"] == 1
        assert related["coverage"]["completeness"] in ("partial", "unknown")
        continuation = related["coverage"]["continuation"]
        assert continuation
        first_refs = {(e["target_kind"], e["target_ref"]) for e in related["facts"]}

        expansion = _expand(
            admin_client, headers, thread_id,
            bundle_digest=bundle["bundle_digest"], continuation=continuation,
        )
        new_related = _section(expansion["bundle"], "related")
        second_refs = {(e["target_kind"], e["target_ref"]) for e in new_related["facts"]}
        assert not (first_refs & second_refs), "an already-returned entry must never reappear"
        assert first_refs | second_refs == {("product_milestone", milestone_key), ("ux_journey", journey_key)}
        assert new_related["coverage"]["completeness"] == "complete"
        assert new_related["coverage"]["continuation"] is None
        assert expansion["expanded_section_ids"] == ["related"]

    def test_unknown_or_tampered_continuation_is_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-404")
        headers = _headers(token, system_id)
        thread_id, _m, _j = self._setup_two_item_related_thread(admin_client, headers, "tamper")
        bundle = _get_bundle(admin_client, headers, thread_id)
        resp = _expand(
            admin_client, headers, thread_id,
            bundle_digest=bundle["bundle_digest"], continuation="not-a-real-token", expect=404,
        )
        assert resp.json()["detail"]["code"] == "discussion_context_continuation_unknown"

    def test_wrong_bundle_digest_is_409(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-digest")
        headers = _headers(token, system_id)
        thread_id, _m, _j = self._setup_two_item_related_thread(admin_client, headers, "digest")
        monkeypatch.setattr(
            "app.routes.assistant.discussion_context_bundle.DEFAULT_BUDGET",
            ContextBudget(max_depth=2, section_item_limit=1, total_item_limit=2, total_byte_limit=65536),
        )
        bundle = _get_bundle(admin_client, headers, thread_id)
        continuation = _section(bundle, "related")["coverage"]["continuation"]
        assert continuation
        resp = _expand(
            admin_client, headers, thread_id,
            bundle_digest="0" * 64, continuation=continuation, expect=409,
        )
        assert resp.json()["detail"]["code"] == "discussion_context_continuation_stale"

    def test_expired_continuation_is_410(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-expired")
        headers = _headers(token, system_id)
        thread_id, _m, _j = self._setup_two_item_related_thread(admin_client, headers, "expiry")
        monkeypatch.setattr(
            "app.routes.assistant.discussion_context_bundle.DEFAULT_BUDGET",
            ContextBudget(max_depth=2, section_item_limit=1, total_item_limit=2, total_byte_limit=65536),
        )
        bundle = _get_bundle(admin_client, headers, thread_id)
        continuation = _section(bundle, "related")["coverage"]["continuation"]
        assert continuation

        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE discussion_context_cursor SET expires_at = ? WHERE token = ?",
                (time.time() - 1, continuation),
            )

        resp = _expand(
            admin_client, headers, thread_id,
            bundle_digest=bundle["bundle_digest"], continuation=continuation, expect=410,
        )
        assert resp.json()["detail"]["code"] == "discussion_context_continuation_expired"

    def test_dependency_moved_since_mint_is_409_stale(self, admin_client, monkeypatch):
        """DD-CTX-05: "root不変でも使った依存根拠更新はstale" -- the ROOT
        Gap did not change, but the Milestone the first batch already
        returned (and therefore pinned into the dependency manifest) got a
        new revision. Continuing must re-confirm, not silently resume on a
        stale premise."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-stale-dep")
        headers = _headers(token, system_id)
        thread_id, milestone_key, _j = self._setup_two_item_related_thread(
            admin_client, headers, "staledep"
        )
        monkeypatch.setattr(
            "app.routes.assistant.discussion_context_bundle.DEFAULT_BUDGET",
            ContextBudget(max_depth=2, section_item_limit=1, total_item_limit=2, total_byte_limit=65536),
        )
        bundle = _get_bundle(admin_client, headers, thread_id)
        related = _section(bundle, "related")
        continuation = related["coverage"]["continuation"]
        assert continuation
        # The first (only) batch must be the milestone for the manifest to
        # carry it as a dependency -- product_milestone sorts before
        # ux_journey, matching `_dedupe_new`'s stable sort.
        assert related["facts"][0]["target_kind"] == "product_milestone"

        _add_milestone_revision(admin_client, headers, milestone_key, title="改訂後")

        resp = _expand(
            admin_client, headers, thread_id,
            bundle_digest=bundle["bundle_digest"], continuation=continuation, expect=409,
        )
        assert resp.json()["detail"]["code"] == "discussion_context_continuation_stale"

    def test_unknown_thread_is_404_before_any_cursor_logic(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-expand-unknown-thread")
        headers = _headers(token, system_id)
        _expand(
            admin_client, headers, 999999,
            bundle_digest="x", continuation="y", expect=404,
        )


# ---------------------------------------------------------------------------
# 6. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_a_foreign_systems_thread_is_404_never_leaking_its_bundle_or_cursor(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "ctx-iso-a")
        system_b = _create_system(admin_client, token, "ctx-iso-b")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _objective_key, _milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers_a, "iso")
        thread = _create_thread(
            admin_client, headers_a, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        thread_id = thread["thread"]["id"]

        # System B can neither read the bundle nor expand it for A's thread id.
        _get_bundle(admin_client, headers_b, thread_id, expect=404)
        _expand(admin_client, headers_b, thread_id, bundle_digest="x", continuation="y", expect=404)

        # A cursor minted under System A cannot be resolved under System B
        # even if somehow presented with a matching thread id in B.
        monkeypatch.setattr(
            "app.routes.assistant.discussion_context_bundle.DEFAULT_BUDGET",
            ContextBudget(max_depth=2, section_item_limit=1, total_item_limit=2, total_byte_limit=65536),
        )
        bundle = _get_bundle(admin_client, headers_a, thread_id)
        continuation = _section(bundle, "related")["coverage"]["continuation"]
        thread_b = _create_thread(
            admin_client, headers_b, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )
        _expand(
            admin_client, headers_b, thread_b["thread"]["id"],
            bundle_digest=bundle["bundle_digest"], continuation=continuation or "irrelevant", expect=404,
        )


# ---------------------------------------------------------------------------
# 7. Durable audit, separate from the short-lived cursor
# ---------------------------------------------------------------------------


class TestDurableAudit:
    def test_audit_row_survives_cursor_expiry_and_cleanup(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-audit")
        headers = _headers(token, system_id)
        _objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers, "audit")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        thread_id = thread["thread"]["id"]
        bundle_wire = _get_bundle(admin_client, headers, thread_id)

        from app.db import get_conn

        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_gap", gap_key, thread_id=thread_id,
        )
        with get_conn() as conn:
            audit_id = discussion_context_bundle.persist_context_audit(
                conn, system_id=system_id, consumer_kind="turn",
                consumer_ref=f"thread:{thread_id}:turn:1", bundle=bundle,
            )
            conn.commit()
            # Simulate every cursor for this System expiring and being swept.
            conn.execute("DELETE FROM discussion_context_cursor WHERE system_id = ?", (system_id,))
            conn.commit()
            row = discussion_context_bundle.get_context_audit(
                conn, system_id=system_id, consumer_kind="turn", consumer_ref=f"thread:{thread_id}:turn:1",
            )
        assert row is not None
        assert row["id"] == audit_id
        assert row["bundle_digest"] == bundle_wire["bundle_digest"]
        stored_deps = json.loads(row["dependency_manifest_json"])
        assert any(d["target_ref"] == milestone_key for d in stored_deps)

    def test_citation_allow_list_validates_against_bundle_sources_only(self, admin_client):
        """§9.2/DD-CTX-04: a claim's citations are checked against exactly
        `bundle.sources[].source_id` -- a real id passes, an unknown one
        fails, and an empty citation list is itself invalid (a claim citing
        nothing is never treated as trivially valid)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-citations")
        headers = _headers(token, system_id)
        _objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers, "cite")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_gap", gap_key, thread_id=thread["thread"]["id"],
        )
        real_source_id = f"product_milestone:{milestone_key}"
        assert real_source_id in discussion_context_bundle.allowed_source_ids(bundle)

        ok, invalid = discussion_context_bundle.validate_citation_source_ids([real_source_id], bundle)
        assert ok is True
        assert invalid == ()

        ok, invalid = discussion_context_bundle.validate_citation_source_ids(
            [real_source_id, "product_gap:no-such-gap"], bundle,
        )
        assert ok is False
        assert invalid == ("product_gap:no-such-gap",)

        ok, invalid = discussion_context_bundle.validate_citation_source_ids([], bundle)
        assert ok is False

    def test_invalid_consumer_kind_is_rejected(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ctx-audit-invalid")
        headers = _headers(token, system_id)
        _objective_key, _milestone_key, gap_key = _setup_objective_milestone_gap(admin_client, headers, "auditbad")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref=gap_key,
        )
        bundle = discussion_context_bundle.build_context_bundle(
            system_id, "product_gap", gap_key, thread_id=thread["thread"]["id"],
        )

        from app.db import get_conn

        with pytest.raises(discussion_context_bundle.ContextBundleError) as exc_info:
            with get_conn() as conn:
                discussion_context_bundle.persist_context_audit(
                    conn, system_id=system_id, consumer_kind="not_a_real_kind",
                    consumer_ref="whatever", bundle=bundle,
                )
        assert exc_info.value.code == "discussion_context_audit_consumer_kind_invalid"
