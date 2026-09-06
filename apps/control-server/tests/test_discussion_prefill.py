"""Tests for Issue #446 (Epic #443 Phase 3): proposal prefill audit.

`docs/ai-discussion-adapter.md` §3.4 is the canonical contract. Reuses the
fixtures/helpers already established in `tests/test_assistant_discussion_
proposals.py` (same style pytest already uses elsewhere, e.g.
`test_state_messages.py` importing from `test_system_state.py`) rather than
duplicating them.

Acceptance criteria under test:

1. Prefill writes NO canonical row and does not change item `status`
   (the single most important assertion in this phase).
2. Prefill records the audit row and is idempotent under a repeated
   `patch_token`.
3. A `stale`/`forbidden`/`conflict` item cannot be prefilled, with the SAME
   finite codes `apply` uses.
4. `prefill_unsupported` / `prefill_form_unregistered`.
5. `prefill_count` / `last_prefilled_at` survive a reload.
6. System isolation.
"""

from __future__ import annotations

import json
import time

from tests.test_assistant_discussion_proposals import (  # noqa: F401 - fixture
    admin_client,
    _add_journey_revision,
    _create_journey,
    _create_system,
    _create_thread,
    _enable_real_llm,
    _generate_proposal,
    _get_proposal,
    _headers,
    _item_by_field,
    _item_by_relation,
    _login,
    _step,
    _FixedResponseClient,
)


def _prefill(client, headers, proposal_id, item_ids, *, form_id, patch_token, expect=200):
    r = client.post(
        f"/assistant/discussion-proposals/{proposal_id}/prefill",
        json={"item_ids": item_ids, "form_id": form_id, "patch_token": patch_token},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _make_journey_proposal(admin_client, monkeypatch, *, journey_key="checkout"):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    _create_journey(admin_client, headers, journey_key)
    _add_journey_revision(admin_client, headers, journey_key, steps=[_step("s1", 1)])

    thread = _create_thread(
        admin_client, headers, scope="entity", screen_id="ux-design-studio",
        target_kind="ux_journey", target_ref=journey_key,
    )["thread"]
    client = _FixedResponseClient({
        "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
        "evidence_refs": [],
        "field_changes": [
            {"field_name": "summary", "subject_ref": "", "current_value": "",
             "proposed_value": "One-page checkout.", "rationale": "discussed"},
        ],
        "relation_changes": [],
    })
    _enable_real_llm(monkeypatch, client)
    proposal = _generate_proposal(admin_client, headers, thread["id"])
    return system, headers, proposal


# ---------------------------------------------------------------------------
# 1. No canonical row, item status unchanged (the load-bearing assertion)
# ---------------------------------------------------------------------------


class TestPrefillWritesNoCanonicalRow:
    def test_prefill_creates_no_revision_and_leaves_status_proposed(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        revisions_before = admin_client.get(
            "/ux-design/journeys/checkout/revisions", headers=headers
        ).json()["revisions"]

        result = _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-1",
        )
        assert result["prefilled_item_ids"] == [item["id"]]

        # No new revision was written.
        revisions_after = admin_client.get(
            "/ux-design/journeys/checkout/revisions", headers=headers
        ).json()["revisions"]
        assert len(revisions_after) == len(revisions_before)

        # The journey's own decision ledger / current content is untouched.
        detail = admin_client.get("/ux-design/journeys/checkout", headers=headers).json()
        assert detail["current_revision"]["summary"] != "One-page checkout."

        # The item's `status` stays `proposed` -- prefill is intent, not
        # completion (#412's "recording is not promotion").
        refreshed = _get_proposal(admin_client, headers, proposal["id"])
        refreshed_item = _item_by_field(refreshed, "summary")
        assert refreshed_item["status"] == "proposed"
        assert refreshed_item["decided_by"] is None
        assert refreshed_item["decided_at"] is None
        # Still eligible for apply afterward -- prefill never consumes the
        # item.
        assert refreshed_item["eligibility"] == "appliable"


# ---------------------------------------------------------------------------
# 2. Audit row + idempotency under a repeated patch_token
# ---------------------------------------------------------------------------


class TestPrefillAuditAndIdempotency:
    def test_repeated_patch_token_is_a_no_op_success(self, admin_client, monkeypatch):
        from app.db import get_conn

        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-repeat",
        )
        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-repeat",
        )

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM assistant_discussion_proposal_prefill WHERE item_id = ?",
                (item["id"],),
            ).fetchall()
        assert len(rows) == 1

        detail = _get_proposal(admin_client, headers, proposal["id"])
        refreshed_item = _item_by_field(detail, "summary")
        assert refreshed_item["prefill_count"] == 1

    def test_a_different_patch_token_adds_a_second_audit_row(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-a",
        )
        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-b",
        )

        detail = _get_proposal(admin_client, headers, proposal["id"])
        refreshed_item = _item_by_field(detail, "summary")
        assert refreshed_item["prefill_count"] == 2


# ---------------------------------------------------------------------------
# 3. stale/forbidden/conflict cannot be prefilled, same codes as apply
# ---------------------------------------------------------------------------


class TestPrefillEligibilityGate:
    def test_stale_item_refuses_prefill_with_same_code_as_apply(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        # The journey's content changes -- the proposal's captured digest is
        # now stale relative to the CURRENT revision.
        _add_journey_revision(
            admin_client, headers, "checkout", steps=[_step("s1", 1)], beneficiary="changed",
        )

        r = _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-stale", expect=422,
        )
        assert r.json()["detail"]["code"] == "proposal_item_stale"

        # Nothing was recorded.
        from app.db import get_conn

        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM assistant_discussion_proposal_prefill WHERE item_id = ?",
                (item["id"],),
            ).fetchone()["n"]
        assert n == 0

    def test_forbidden_item_refuses_prefill(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        from app import assistant_discussion_proposal as module

        narrowed = dict(module.PROPOSAL_TARGET_SCHEMA)
        journey = dict(narrowed["ux_journey"])
        journey["fields"] = tuple(f for f in journey["fields"] if f != "summary")
        narrowed["ux_journey"] = journey
        monkeypatch.setattr(module, "PROPOSAL_TARGET_SCHEMA", narrowed)

        r = _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-forbidden", expect=422,
        )
        assert r.json()["detail"]["code"] == "proposal_item_forbidden"

    def test_conflict_item_refuses_prefill(self, admin_client, monkeypatch):
        """Two relation items addressing the SAME (relation_kind,
        relation_target_kind, relation_target_ref): applying a relation
        (unlike a field) never bumps the journey's own content digest, so the
        sibling reaches `conflict` rather than `stale` once one of the pair
        is `applied` -- the same first-match order `apply` itself uses."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)])

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [],
            "relation_changes": [
                {"relation_kind": "upstream_ref", "subject_ref": "", "relation_target_kind": "purpose_element",
                 "relation_target_ref": "cap-checkout", "proposed_value": "", "rationale": "first"},
                {"relation_kind": "upstream_ref", "subject_ref": "", "relation_target_kind": "purpose_element",
                 "relation_target_ref": "cap-checkout", "proposed_value": "", "rationale": "second"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        items = [
            i for i in proposal["items"]
            if i["item_kind"] == "relation" and i["relation_target_ref"] == "cap-checkout"
        ]
        assert len(items) == 2
        first, second = items

        applied = admin_client.post(
            f"/assistant/discussion-proposals/{proposal['id']}/apply",
            json={"item_ids": [first["id"]], "rationale": "ok"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text

        r = _prefill(
            admin_client, headers, proposal["id"], [second["id"]],
            form_id="ux_journey.revision", patch_token="tok-conflict", expect=422,
        )
        assert r.json()["detail"]["code"] == "proposal_item_conflict"


# ---------------------------------------------------------------------------
# 4. prefill_unsupported / prefill_form_unregistered
# ---------------------------------------------------------------------------


class TestPrefillFormRegistration:
    def test_unsupported_target_kind_is_refused(self, admin_client, monkeypatch):
        """`understanding_claim` carries proposable `fields` but is
        deliberately NOT registered with any `ui_draft_forms` -- it has no
        Dashboard form to prefill into at all."""
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        with get_conn() as conn:
            now = time.time()
            conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
                (system["id"], "b" * 40, now, now),
            )
            snapshot_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            understanding = {
                "vision": [{"name": "v1", "summary": "old summary", "why_core": "old reason"}],
                "system_purpose": [], "core_capabilities": [],
            }
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, title, current_understanding, created_at, updated_at)
                   VALUES (?, ?, 'session', ?, ?, ?)""",
                (system["id"], snapshot_id, json.dumps(understanding), now, now),
            )

        thread = _create_thread(
            admin_client, headers, scope="element", screen_id="overview",
            target_kind="understanding_claim", target_ref="vision:v1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "summary", "subject_ref": "", "current_value": "old summary",
                 "proposed_value": "New summary.", "rationale": "discussed"},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_field(proposal, "summary")

        r = _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="whatever", patch_token="tok-x", expect=422,
        )
        assert r.json()["detail"]["code"] == "prefill_unsupported"

    def test_unregistered_form_id_is_refused(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        r = _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="not_a_real_form", patch_token="tok-y", expect=422,
        )
        assert r.json()["detail"]["code"] == "prefill_form_unregistered"


# ---------------------------------------------------------------------------
# 5. prefill_count / last_prefilled_at survive a reload
# ---------------------------------------------------------------------------


class TestPrefillAuditSurvivesReload:
    def test_prefill_count_and_last_prefilled_at_persist(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")
        assert item["prefill_count"] == 0
        assert item["last_prefilled_at"] is None

        before = time.time()
        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-persist",
        )
        after = time.time()

        # A fresh GET (a "reload") of the proposal detail, and of the
        # thread's proposal list, both see the same audit summary.
        detail = _get_proposal(admin_client, headers, proposal["id"])
        detail_item = _item_by_field(detail, "summary")
        assert detail_item["prefill_count"] == 1
        assert detail_item["last_prefilled_at"] is not None
        assert before <= detail_item["last_prefilled_at"] <= after

        listed = admin_client.get(
            f"/assistant/discussion-threads/{proposal['thread_id']}/proposals", headers=headers,
        ).json()["proposals"]
        listed_proposal = next(p for p in listed if p["id"] == proposal["id"])
        listed_item = _item_by_field(listed_proposal, "summary")
        assert listed_item["prefill_count"] == 1
        assert listed_item["last_prefilled_at"] == detail_item["last_prefilled_at"]


# ---------------------------------------------------------------------------
# 6. System isolation
# ---------------------------------------------------------------------------


class TestPrefillSystemIsolation:
    def test_foreign_system_cannot_prefill(self, admin_client, monkeypatch):
        system, headers, proposal = _make_journey_proposal(admin_client, monkeypatch)
        item = _item_by_field(proposal, "summary")

        token = _login(admin_client)
        other_system = _create_system(admin_client, token, name="other-sys-prefill")
        other_headers = _headers(token, other_system["id"])

        r = _prefill(
            admin_client, other_headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-cross", expect=404,
        )
        assert "not found" in r.json()["detail"]

        # System A itself can still prefill.
        _prefill(
            admin_client, headers, proposal["id"], [item["id"]],
            form_id="ux_journey.revision", patch_token="tok-cross",
        )
