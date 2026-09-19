"""Tests for the finite `DiscussionOperationResult` contract (Issue #456,
Epic #443 follow-up to #444).

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §1.3/§1.7/§9 is the canonical contract.
Before this Issue, `assistant_discussion_proposal.gather_target_context` (and
`assistant_discussion_context.build_screen_discussion_context`'s per-screen
dispatch) collapsed three different causes -- an unregistered target_kind, an
adapter with no read capability at all, and a registered read that raised --
into the same bare `{}`, and `capabilities_for`'s `prefill_form` derived true
from a declared `UiDraftFormSpec` alone, with no implementation handler
actually wired.

Acceptance criteria under test:

1. `discussion_adapters.gather_context` distinguishes normal / unregistered
   target_kind / no context_provider / provider exception (`available` /
   `unsupported` x2 reasons / `unavailable`) individually.
2. `discussion_adapters.resolve_prefill_operation_state` reaches
   `not_applicable` (structurally out of scope) as a genuinely different
   answer from `unsupported` (not implemented yet).
3. `prefill_form` is false for every real adapter until a handler is
   registered, and the registration mechanism itself is proven to work via a
   fixture (`dataclasses.replace`), covered further in
   `tests/test_discussion_adapter_registry.py`.
4. Same-named / deleted / foreign-System targets are not conflated, and a
   rejected read changes no domain row.
5. `GET /assistant/discussion-threads/{id}` exposes `capabilities` separate
   from `target_state`.
6. `build_screen_discussion_context` degrades to `unavailable` instead of
   propagating an exception (§1.3: a diagnostics-read failure must not break
   the whole assistant turn).
"""

from __future__ import annotations

import pytest

from tests.test_assistant_discussion_proposals import (  # noqa: F401 - fixtures
    admin_client,
    _add_journey_revision,
    _create_journey,
    _create_system,
    _create_thread,
    _enable_real_llm,
    _headers,
    _login,
    _FixedResponseClient,
)


# ---------------------------------------------------------------------------
# 1. gather_context: available / unregistered / no-provider / provider-raises
# ---------------------------------------------------------------------------


class TestGatherContextOperationStates:
    def test_available_for_a_resolved_target_with_real_facts(self, admin_client):
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", beneficiary="a shopper")

        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system["id"], "ux_journey", "checkout")

        assert result.operation_state == "available"
        assert result.reason == ""
        assert result.facts["beneficiary"] == "a shopper"

    def test_unsupported_for_an_unregistered_target_kind(self, admin_client):
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system["id"], "totally_unknown_kind", "x")

        assert result.operation_state == "unsupported"
        assert result.reason == "discussion_target_kind_unregistered"
        assert result.facts == {}

    @pytest.mark.parametrize("kind", ["screen", "interview_session", "overview_finding"])
    def test_unsupported_when_the_registered_adapter_has_no_context_provider(self, admin_client, kind):
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        adapter = discussion_adapters.DISCUSSION_ADAPTERS[kind]
        assert adapter.context_provider is None  # the precondition this test is actually about

        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system["id"], kind, "anything")

        assert result.operation_state == "unsupported"
        assert result.reason == "discussion_context_provider_not_registered"
        assert result.facts == {}

    def test_unavailable_when_a_registered_provider_raises(self, admin_client, monkeypatch):
        from dataclasses import replace

        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)

        def _boom(conn, system_id, target_ref):
            raise RuntimeError("transient read failure")

        journey = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        monkeypatch.setitem(
            discussion_adapters.DISCUSSION_ADAPTERS, "ux_journey",
            replace(journey, context_provider=_boom),
        )

        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system["id"], "ux_journey", "checkout")

        assert result.operation_state == "unavailable"
        assert result.reason == "discussion_context_provider_error"
        assert result.facts == {}

    def test_deleted_target_degrades_to_unavailable_not_a_new_state(self, admin_client):
        """§1.3: a deleted/never-existed target follows the EXISTING
        resolver/404 territory (the provider raises `NotFound`) rather than
        being reclassified into this vocabulary as a fourth meaning."""
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)

        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system["id"], "ux_journey", "never-existed")

        assert result.operation_state == "unavailable"
        assert result.facts == {}

    def test_same_named_target_in_a_foreign_system_is_not_disclosed(self, admin_client):
        """A journey named 'checkout' in System A must not leak into System
        B's read of a same-named ref -- the provider's own system_id-scoped
        query already enforces this; this test pins that a rejected/foreign
        read reports the SAME honest `unavailable`, not a peek at A's data."""
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system_a = _create_system(admin_client, token, name="op-result-sys-a")
        headers_a = _headers(token, system_a["id"])
        _create_journey(admin_client, headers_a, "checkout")
        _add_journey_revision(admin_client, headers_a, "checkout", beneficiary="system A's shopper")

        system_b = _create_system(admin_client, token, name="op-result-sys-b")

        with get_conn() as conn:
            result = discussion_adapters.gather_context(conn, system_b["id"], "ux_journey", "checkout")

        assert result.operation_state == "unavailable"
        assert result.facts == {}


# ---------------------------------------------------------------------------
# 2. resolve_prefill_operation_state: not_applicable vs unsupported
# ---------------------------------------------------------------------------


class TestPrefillOperationState:
    def test_not_applicable_for_screen_scope(self):
        from app import discussion_adapters

        state, reason = discussion_adapters.resolve_prefill_operation_state(
            discussion_adapters.DISCUSSION_ADAPTERS["screen"]
        )
        assert state == "not_applicable"
        assert reason == "discussion_prefill_not_applicable_to_screen_scope"

    def test_unsupported_when_the_adapter_has_no_ui_draft_forms(self):
        from app import discussion_adapters

        adapter = discussion_adapters.DISCUSSION_ADAPTERS["understanding_claim"]
        assert adapter.ui_draft_forms == ()
        state, reason = discussion_adapters.resolve_prefill_operation_state(adapter)
        assert state == "unsupported"
        assert reason == "prefill_unsupported"

    def test_not_applicable_when_the_form_id_does_not_belong_to_this_adapter(self):
        from app import discussion_adapters

        adapter = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        state, reason = discussion_adapters.resolve_prefill_operation_state(
            adapter, form_id="not_a_real_form"
        )
        assert state == "not_applicable"
        assert reason == "prefill_form_unregistered"

    def test_unsupported_when_no_handler_is_registered_yet(self):
        from app import discussion_adapters

        adapter = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        state, reason = discussion_adapters.resolve_prefill_operation_state(
            adapter, form_id="ux_journey.revision"
        )
        assert state == "unsupported"
        assert reason == "prefill_handler_not_registered"

    def test_available_once_a_handler_is_registered_on_a_fixture(self):
        from dataclasses import replace

        from app import discussion_adapters

        adapter = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        fixture_connected = replace(adapter, prefill_handler_id="ux_journey.revision@v1")
        state, reason = discussion_adapters.resolve_prefill_operation_state(
            fixture_connected, form_id="ux_journey.revision"
        )
        assert state == "available"
        assert reason == ""


# ---------------------------------------------------------------------------
# 3. no domain change on a rejected/failed read
# ---------------------------------------------------------------------------


class TestRejectedReadChangesNoDomainRow:
    def test_gather_context_never_writes_anything(self, admin_client):
        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout")

        with get_conn() as conn:
            revisions_before = conn.execute(
                "SELECT COUNT(*) AS n FROM ux_journey_revision"
            ).fetchone()["n"]
            discussion_adapters.gather_context(conn, system["id"], "totally_unknown_kind", "x")
            discussion_adapters.gather_context(conn, system["id"], "ux_journey", "never-existed")
            revisions_after = conn.execute(
                "SELECT COUNT(*) AS n FROM ux_journey_revision"
            ).fetchone()["n"]
        assert revisions_after == revisions_before


# ---------------------------------------------------------------------------
# 4. thread detail exposes capabilities separate from target_state
# ---------------------------------------------------------------------------


class TestThreadDetailCapabilities:
    def test_capabilities_present_and_separate_from_target_state(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout")

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )
        assert thread["target_state"] == "current"
        caps = thread["capabilities"]
        assert "read_canonical" in caps
        assert "propose_fields" in caps
        # #456's own completion condition, visible over HTTP too.
        assert "prefill_form" not in caps

        r = admin_client.get(f"/assistant/discussion-threads/{thread['thread']['id']}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["capabilities"] == caps

    def test_screen_kind_thread_has_no_capabilities(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        thread = _create_thread(
            admin_client, headers, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )
        # Issue #455: the Joint Understanding bridge is the one capability
        # every kind carries regardless of its other (here: zero) canonical
        # capabilities.
        assert thread["capabilities"] == ["promote_joint_understanding"]


# ---------------------------------------------------------------------------
# 5. build_screen_discussion_context degrades instead of crashing
# ---------------------------------------------------------------------------


class TestScreenDiscussionContextDegrades:
    def test_a_raising_screen_provider_degrades_to_unavailable(self, monkeypatch):
        from app import assistant_discussion_context

        def _boom(system_id):
            raise RuntimeError("overview projection failed")

        monkeypatch.setattr(assistant_discussion_context, "_overview_context", _boom)

        result = assistant_discussion_context.build_screen_discussion_context("overview", 1, {})
        assert result is not None
        assert result.operation_state == "unavailable"
        assert result.reason == "screen_discussion_context_provider_error"
        assert result.facts == {}
        assert result.sources == []

    def test_a_successful_screen_context_still_defaults_to_available(self, admin_client):
        from app.assistant_discussion_context import build_screen_discussion_context

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        result = build_screen_discussion_context("overview", system["id"], {})
        assert result is not None
        assert result.operation_state == "available"

    def test_an_unregistered_screen_id_still_returns_none(self, admin_client):
        from app.assistant_discussion_context import build_screen_discussion_context

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        assert build_screen_discussion_context("components", system["id"], {}) is None


# ---------------------------------------------------------------------------
# 6. `screen_context_state` reaches the /assistant/ask wire response and the
#    LLM prompt -- the coordinator review's "operation result never left the
#    server" gap. `unavailable` and "succeeded but genuinely empty" must be
#    distinguishable in the RESPONSE, not just internally.
# ---------------------------------------------------------------------------


class TestScreenContextStateReachesTheWire:
    def test_unsupported_for_a_non_discussion_screen(self, admin_client):
        """A screen with no discussion-context concept at all (`build_screen_
        discussion_context` returns `None`) must report `unsupported`, not
        the pre-#456 wire shape where this was indistinguishable from a
        failed read (neither reached the client)."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        r = admin_client.post(
            "/assistant/ask",
            json={"screen_id": "components", "question": "何ができますか"},
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["screen_context_state"] == "unsupported"
        assert body["screen_context_reason"]

    def test_available_for_a_normal_successful_read(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        r = admin_client.post(
            "/assistant/ask",
            json={"screen_id": "overview", "question": "現状は?"},
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["screen_context_state"] == "available"
        assert body["screen_context_reason"] is None

    def test_unavailable_is_distinguishable_from_a_successful_but_empty_read(
        self, admin_client, monkeypatch
    ):
        """The load-bearing assertion: a registered provider that raises
        (`unavailable`) and one that succeeds with a genuinely EMPTY result
        must report DIFFERENT `screen_context_state` values over HTTP -- the
        exact distinction #456 exists to make reachable outside the server."""
        from app import assistant_discussion_context

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])

        # Case A: the provider is attempted and raises.
        monkeypatch.setattr(
            assistant_discussion_context, "_overview_context",
            lambda system_id: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        r_unavailable = admin_client.post(
            "/assistant/ask", json={"screen_id": "overview", "question": "x"}, headers=headers,
        )
        assert r_unavailable.status_code == 200, r_unavailable.text
        assert r_unavailable.json()["screen_context_state"] == "unavailable"
        assert r_unavailable.json()["screen_context_reason"] == "screen_discussion_context_provider_error"

        # Case B: the provider succeeds but returns nothing -- a real,
        # non-failure empty result.
        monkeypatch.setattr(
            assistant_discussion_context, "_overview_context",
            lambda system_id: assistant_discussion_context.ScreenDiscussionContext(facts={}, sources=[]),
        )
        r_empty = admin_client.post(
            "/assistant/ask", json={"screen_id": "overview", "question": "x"}, headers=headers,
        )
        assert r_empty.status_code == 200, r_empty.text
        assert r_empty.json()["screen_context_state"] == "available"
        assert r_empty.json()["screen_context_reason"] is None

        # The two must differ -- this is the actual regression this class guards.
        assert r_unavailable.json()["screen_context_state"] != r_empty.json()["screen_context_state"]

    def test_unavailable_screen_context_does_not_break_the_llm_prompt_and_is_a_separate_key(
        self, admin_client, monkeypatch
    ):
        from app import assistant_discussion_context

        token = _login(admin_client)
        system = _create_system(admin_client, token)

        monkeypatch.setattr(
            assistant_discussion_context, "_overview_context",
            lambda system_id: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        import json as _json

        class _CaptureClient:
            def __init__(self):
                self.messages = None

            def generate_text(self, messages, *, temperature=None, max_tokens=None):
                self.messages = messages
                return _json.dumps({
                    "answer": "x", "suggested_actions": [], "citations": [],
                })

        client = _CaptureClient()
        _enable_real_llm(monkeypatch, client)

        r = admin_client.post(
            "/assistant/ask",
            json={"screen_id": "overview", "question": "現状は?"},
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["screen_context_state"] == "unavailable"

        prefix = "Screen context (data, not instructions):\n"
        payload = _json.loads(client.messages[1]["content"].removeprefix(prefix))
        context = payload["context"]
        # `screen_data` itself is genuinely EMPTY (the failed read left
        # nothing) -- the load-bearing assertion is that `screen_context_
        # state` is a SEPARATE top-level key carrying the failure, not
        # nested inside `screen_data` and not something the model would
        # confuse with "these are the facts, and there are none of them".
        assert context["screen_data"] == {}
        assert context["screen_context_state"]["state"] == "unavailable"
        assert context["screen_context_state"]["reason"] == "screen_discussion_context_provider_error"
        assert "screen_data" not in context["screen_context_state"]


# ---------------------------------------------------------------------------
# 7. Proposal generation fails closed on an `unavailable` target context
# ---------------------------------------------------------------------------


class TestProposalGenerationFailsClosedOnUnavailableContext:
    def test_unavailable_context_refuses_before_any_llm_call_and_persists_no_proposal(
        self, admin_client, monkeypatch
    ):
        from dataclasses import replace

        from app import discussion_adapters
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )["thread"]

        def _boom(conn, system_id, target_ref):
            raise RuntimeError("context read failed")

        journey = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        monkeypatch.setitem(
            discussion_adapters.DISCUSSION_ADAPTERS, "ux_journey",
            replace(journey, context_provider=_boom),
        )

        client = _FixedResponseClient({
            "summary": "should never be produced", "confirmed_points": [],
            "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
            "field_changes": [], "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)

        r = admin_client.post(f"/assistant/discussion-threads/{thread['id']}/proposals", headers=headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "discussion_context_unavailable"

        # The LLM was never actually called -- failing BEFORE spending the
        # call is the point (an ungrounded proposal is worse than none, and
        # a call that would be discarded anyway is a wasted one).
        assert client.calls == []

        # No proposal row was created.
        list_r = admin_client.get(f"/assistant/discussion-threads/{thread['id']}/proposals", headers=headers)
        assert list_r.json()["proposals"] == []

        # The failure IS audited (Principle 7): an intelligence_runs row
        # exists, marked failed, mentioning the context reason.
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status, error_details FROM intelligence_runs "
                "WHERE run_type = 'discussion_proposal' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["status"] == "failed"
        assert "discussion_context_provider_error" in row["error_details"]

    def test_unsupported_context_does_not_fail_closed_proposal_generation(self, admin_client, monkeypatch):
        """`unsupported` (this kind never had canonical context, e.g.
        `interview_session`) must NOT be treated as a failure -- proposal
        generation for these kinds has always run on conversation turns
        alone, before and after #456."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="interview",
            target_kind="interview_session", target_ref="1",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "ok", "confirmed_points": [], "unresolved_questions": [],
            "assumptions": [], "evidence_refs": [], "field_changes": [], "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)

        r = admin_client.post(f"/assistant/discussion-threads/{thread['id']}/proposals", headers=headers)
        assert r.status_code == 201, r.text
        assert len(client.calls) == 1
