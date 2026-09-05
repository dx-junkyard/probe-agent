"""Tests for the `DiscussionAdapter` registry (Issue #444, Epic #443 Phase 1).

`docs/ai-discussion-adapter.md` §1 is the canonical contract. This is the
test that proves the Phase 1 move -- six parallel per-kind tables collapsed
into one `app/discussion_adapters.py` registry -- did not change behaviour
by one bit: `SCOPE_TARGET_KINDS` and `PROPOSAL_TARGET_SCHEMA` are now DERIVED
from the registry, and the pre-refactor values are hard-coded here (not
imported from the modules under test) so a regression in the derivation
cannot silently rewrite its own expectation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import assistant_discussion, assistant_discussion_proposal, discussion_adapters


# Fixture style mirrors `tests/test_assistant_discussion_threads.py`
# (`admin_client` / `_login` / `_create_system` / `_headers`).


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-discussion-registry-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _create_system(client, token, name="discussion-registry-sys"):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


# --- every DISCUSSION_TARGET_KINDS member has exactly one adapter ------------


def test_every_target_kind_has_exactly_one_adapter():
    assert set(discussion_adapters.DISCUSSION_ADAPTERS.keys()) == set(
        discussion_adapters.DISCUSSION_TARGET_KINDS
    )
    # No duplicate registration under a different key: each adapter's own
    # `target_kind` field agrees with the key it is registered under.
    for kind, adapter in discussion_adapters.DISCUSSION_ADAPTERS.items():
        assert adapter.target_kind == kind

    # `assistant_discussion.DISCUSSION_TARGET_KINDS` (the pre-#444 public
    # name other modules/tests import) still equals the registry's key set.
    assert set(assistant_discussion.DISCUSSION_TARGET_KINDS) == set(
        discussion_adapters.DISCUSSION_TARGET_KINDS
    )


def test_target_kinds_are_exactly_the_nine_epic_436_shipped():
    # Pinned so Phase 1 cannot silently grow or shrink the registered set --
    # #447 adds new kinds in a LATER phase, additively.
    assert set(discussion_adapters.DISCUSSION_TARGET_KINDS) == {
        "screen",
        "interview_session",
        "understanding_claim",
        "overview_finding",
        "ux_journey",
        "ux_journey_step",
        "ux_requirement",
        "solution_design",
        "blueprint_lane_cell",
    }


# --- SCOPE_TARGET_KINDS is unchanged by the move -----------------------------


def test_scope_target_kinds_equals_pre_refactor_value():
    # Hard-coded from the pre-#444 `assistant_discussion.py` (the literal
    # dict this test replaces) -- NOT read back from either module under
    # test, so a derivation bug cannot rewrite its own expectation.
    expected = {
        "screen": ("screen",),
        "entity": ("interview_session", "ux_journey", "ux_requirement", "solution_design"),
        "element": (
            "understanding_claim", "overview_finding", "ux_journey_step", "blueprint_lane_cell",
        ),
    }
    actual = {
        scope: tuple(sorted(kinds))
        for scope, kinds in assistant_discussion.SCOPE_TARGET_KINDS.items()
    }
    expected_sorted = {scope: tuple(sorted(kinds)) for scope, kinds in expected.items()}
    assert actual == expected_sorted

    # Every target kind is reachable from exactly one scope (first-match, no
    # overlap) -- the same invariant `test_assistant_discussion_threads.py`
    # already asserted on the pre-refactor dict.
    all_kinds = [k for kinds in assistant_discussion.SCOPE_TARGET_KINDS.values() for k in kinds]
    assert sorted(all_kinds) == sorted(discussion_adapters.DISCUSSION_TARGET_KINDS)
    assert len(all_kinds) == len(set(all_kinds))


# --- PROPOSAL_TARGET_SCHEMA is unchanged by the move -------------------------


def test_proposal_target_schema_equals_pre_refactor_value():
    # Hard-coded from the pre-#444 `assistant_discussion_proposal.py` (the
    # literal dict this test replaces).
    expected = {
        "ux_journey": {
            "fields": (
                "title", "beneficiary", "usage_context", "entry_trigger",
                "value_arrival", "summary",
            ),
            "relations": ("upstream_ref",),
        },
        "ux_journey_step": {
            "fields": (
                "user_intent", "system_response", "success_criteria",
                "failure_mode", "recovery_path", "evidence_expectation",
            ),
            "relations": (),
        },
        "ux_requirement": {
            "fields": ("statement", "rationale", "constraint_text", "out_of_scope_note"),
            "relations": ("journey_step_link",),
        },
        "solution_design": {
            "fields": ("title", "approach", "tradeoffs", "risks"),
            "relations": ("requirement_link", "target_link"),
        },
        "blueprint_lane_cell": {
            "fields": (),
            "relations": ("delivery_link", "stakeholder_link", "exchange_link"),
        },
        "understanding_claim": {
            "fields": ("summary", "why_core", "name"),
            "relations": (),
        },
        "overview_finding": {"fields": (), "relations": ()},
        "interview_session": {"fields": (), "relations": ()},
        "screen": {"fields": (), "relations": ()},
    }
    actual = assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA
    assert set(actual.keys()) == set(expected.keys())
    for kind, spec in expected.items():
        assert set(actual[kind]["fields"]) == set(spec["fields"]), kind
        assert set(actual[kind]["relations"]) == set(spec["relations"]), kind


# --- capabilities are derived correctly --------------------------------------


def test_capabilities_derived_for_screen_adapter_is_discussion_only():
    adapter = discussion_adapters.DISCUSSION_ADAPTERS["screen"]
    # `screen` has no context provider, no fields, no relations, no ui_draft
    # forms, no JU bridge -- Phase 1 gives it zero capabilities.
    assert discussion_adapters.capabilities_for(adapter) == ()


def test_capabilities_derived_for_ux_journey_has_read_and_propose_both():
    adapter = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
    caps = discussion_adapters.capabilities_for(adapter)
    assert "read_canonical" in caps
    assert "propose_fields" in caps
    assert "propose_relations" in caps
    # Phase 1: no adapter has ui_draft_forms, so prefill_form/read_ui_draft
    # are never derived true yet.
    assert "read_ui_draft" not in caps
    assert "prefill_form" not in caps
    assert "promote_joint_understanding" not in caps


def test_capabilities_derived_for_blueprint_lane_cell_relations_only():
    adapter = discussion_adapters.DISCUSSION_ADAPTERS["blueprint_lane_cell"]
    caps = discussion_adapters.capabilities_for(adapter)
    assert "propose_relations" in caps
    assert "propose_fields" not in caps  # no fields, no children
    assert "read_canonical" in caps  # has a context_provider


def test_capabilities_derived_for_interview_session_and_overview_finding_discussion_only():
    for kind in ("interview_session", "overview_finding"):
        adapter = discussion_adapters.DISCUSSION_ADAPTERS[kind]
        caps = discussion_adapters.capabilities_for(adapter)
        assert caps == (), f"{kind} should have zero capabilities in Phase 1, got {caps}"


def test_every_adapter_capabilities_are_a_subset_of_the_finite_vocabulary():
    for adapter in discussion_adapters.DISCUSSION_ADAPTERS.values():
        caps = discussion_adapters.capabilities_for(adapter)
        assert set(caps) <= set(discussion_adapters.DISCUSSION_CAPABILITIES)


def test_phase_1_children_and_ui_draft_forms_and_ju_bridge_are_all_empty():
    # #444's explicit Phase 1 scope: children/ui_draft_forms stay empty and
    # joint_understanding_bridge stays False for every adapter -- later
    # phases populate these ADDITIVELY rather than reshaping the dataclass.
    for adapter in discussion_adapters.DISCUSSION_ADAPTERS.values():
        assert adapter.children == ()
        assert adapter.ui_draft_forms == ()
        assert adapter.joint_understanding_bridge is False


# --- fail-closed: unregistered kind / screen mismatch ------------------------


def test_unregistered_target_kind_is_refused_fail_closed():
    with pytest.raises(assistant_discussion.UnregisteredTargetKind) as exc:
        assistant_discussion.resolve_or_create_thread(
            1, scope="entity", screen_id="interview",
            target_kind="totally_unknown_kind", target_ref="x",
        )
    assert "discussion_target_kind_unregistered" in str(exc.value)


def test_screen_mismatch_is_refused_fail_closed(admin_client):
    # `ux_journey_step` lives on ux-design-studio / journey-blueprint
    # (docs/ai-discussion-adapter.md §1.4) -- "overview" must be refused
    # rather than silently accepted. Needs the DB fixture because the gate
    # deliberately sits on the CREATE branch, after the existing-thread
    # lookup (see the next test).
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    with pytest.raises(assistant_discussion.ScreenMismatch) as exc:
        assistant_discussion.resolve_or_create_thread(
            system["id"], scope="element", screen_id="overview",
            target_kind="ux_journey_step", target_ref="j1#s1",
        )
    assert "discussion_target_screen_mismatch" in str(exc.value)


def test_an_existing_thread_on_a_now_disallowed_screen_stays_reachable(admin_client):
    """The screen gate is NEW in #444; before it, any discussion-enabled
    screen could open any kind. A row already stored under a combination the
    registry now narrows must still reopen -- #337's compatibility rule:
    a legacy row stays READABLE, and is never promoted to something it did
    not satisfy. Refusing it would protect nothing and lose a real
    conversation."""
    import time

    from app.db import get_conn

    token = _login(admin_client)
    system = _create_system(admin_client, token)
    system_id = system["id"]
    key = assistant_discussion.thread_key(
        "overview", "element", "ux_journey_step", "j1#s1"
    )
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO assistant_discussion_thread
                   (system_id, thread_key, scope, screen_id, target_kind, target_ref,
                    target_title, captured_target_revision_id, captured_target_digest,
                    status, created_by, created_at, updated_at, schema_version)
               VALUES (?, ?, 'element', 'overview', 'ux_journey_step', 'j1#s1',
                       'legacy', NULL, '', 'open', 'legacy', ?, ?, ?)""",
            (system_id, key, now, now, assistant_discussion.THREAD_SCHEMA_VERSION),
        )

    data = assistant_discussion.resolve_or_create_thread(
        system_id, scope="element", screen_id="overview",
        target_kind="ux_journey_step", target_ref="j1#s1",
    )
    assert data["thread"]["thread_key"] == key
    assert data["thread"]["created_by"] == "legacy"


def test_screen_mismatch_over_http(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    r = admin_client.post(
        "/assistant/discussion-threads",
        json={
            "scope": "element", "screen_id": "overview",
            "target_kind": "ux_journey_step", "target_ref": "j1#s1",
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "discussion_target_screen_mismatch" in r.text


def test_every_adapters_screen_ids_are_within_the_4_discussion_screens():
    for adapter in discussion_adapters.DISCUSSION_ADAPTERS.values():
        assert set(adapter.screen_ids) <= set(discussion_adapters.DISCUSSION_SCREEN_IDS)
        assert len(adapter.screen_ids) >= 1


def test_screen_kind_is_reachable_from_all_4_discussion_screens():
    # "screen" is the whole-screen fallback conversation, so it must be
    # reachable from every discussion-enabled screen -- not a subset.
    adapter = discussion_adapters.DISCUSSION_ADAPTERS["screen"]
    assert set(adapter.screen_ids) == set(discussion_adapters.DISCUSSION_SCREEN_IDS)


def test_shared_kinds_are_reachable_from_both_their_screens():
    # docs/ai-discussion-adapter.md §1.4's verification note: `ux_journey`
    # and `ux_journey_step` are reachable from BOTH ux-design-studio and
    # journey-blueprint.
    for kind in ("ux_journey", "ux_journey_step"):
        adapter = discussion_adapters.DISCUSSION_ADAPTERS[kind]
        assert set(adapter.screen_ids) == {"ux-design-studio", "journey-blueprint"}
