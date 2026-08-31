"""Tests for Issue #439 (Epic #436): conversation-to-proposal changesets.

`docs/assistant-discussion.md` §2 is the canonical contract. Acceptance
criteria under test:

1. A reviewable proposal can be made for the Overview's Vision / Purpose /
   Capabilities (`TestUnderstandingClaimProposal`).
2. Field and relation proposals can be made for a UX Journey / Requirement /
   Solution Design (`TestUxJourneyProposal` / `TestUxRequirementProposal` /
   `TestSolutionDesignProposal`).
3. A link proposal can be made for a Blueprint `unknown` lane
   (`TestBlueprintLaneCellProposal`).
4. Accepting a proposal causes no publish/policy/runtime change
   (`TestNoSideEffects`).
5. Applying to a stale revision is refused (`TestStaleApplyRefused`).

Plus: the registry cannot silently drift from the domain API it applies
through (`TestRegistryCorrespondence`), an out-of-registry model item fails
the whole call (`TestOutOfRegistryFailsClosed`), a foreign System's proposal
is 404 (`TestSystemIsolation`), and a mock/missing provider is 503 with no
proposal row (`TestReasoningUnavailable`).

Fixture style mirrors `tests/test_assistant_discussion_threads.py`
(`admin_client` / `_login` / `_create_system` / `_headers` / `_create_thread`
/ `_CapturingClient` / `_enable_real_llm`) and `tests/test_journey_blueprint.py`
/ `tests/test_solution_design.py` for the Journey/Requirement/Solution
Design/Stakeholder/Exchange fixture helpers.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import assistant_discussion_proposal, journey_blueprint, solution_design, ux_design


# ---------------------------------------------------------------------------
# Fixtures / low-level helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-assistant-discussion-proposal-test.db"))
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


def _create_system(client, token, name="assistant-discussion-proposal-sys"):
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


def _create_thread(client, headers, *, scope, screen_id, target_kind, target_ref, expect=200):
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": scope, "screen_id": screen_id, "target_kind": target_kind, "target_ref": target_ref},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


class _FixedResponseClient:
    """Returns the same structured-output JSON for every call, mirrors
    `test_assistant_discussion_threads.py`'s `_CapturingClient`."""

    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None, timeout=None):
        self.calls.append(messages)
        return json.dumps(self.response)


def _enable_real_llm(monkeypatch, fake_client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake_client)


def _generate_proposal(client, headers, thread_id, expect=201):
    r = client.post(f"/assistant/discussion-threads/{thread_id}/proposals", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _list_proposals(client, headers, thread_id, expect=200):
    r = client.get(f"/assistant/discussion-threads/{thread_id}/proposals", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()["proposals"] if expect < 300 else r


def _get_proposal(client, headers, proposal_id, expect=200):
    r = client.get(f"/assistant/discussion-proposals/{proposal_id}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _apply(client, headers, proposal_id, item_ids, rationale="looks good", expect=200):
    r = client.post(
        f"/assistant/discussion-proposals/{proposal_id}/apply",
        json={"item_ids": item_ids, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _reject(client, headers, proposal_id, item_ids, rationale="not needed", expect=200):
    r = client.post(
        f"/assistant/discussion-proposals/{proposal_id}/reject",
        json={"item_ids": item_ids, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _item_by_field(proposal, field_name):
    for item in proposal["items"]:
        if item["item_kind"] == "field" and item["field_name"] == field_name:
            return item
    raise AssertionError(f"no field item {field_name!r} in {proposal}")


def _item_by_relation(proposal, relation_kind, relation_target_ref=None):
    for item in proposal["items"]:
        if item["item_kind"] == "relation" and item["relation_kind"] == relation_kind and (
            relation_target_ref is None or item["relation_target_ref"] == relation_target_ref
        ):
            return item
    raise AssertionError(f"no relation item {relation_kind!r} in {proposal}")


# --- UX Design Lineage fixtures (mirrors tests/test_journey_blueprint.py) -----


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key,
        "step_order": order,
        "user_intent": f"intent-{step_key}",
        "system_response": f"response-{step_key}",
        "success_criteria": "criteria",
        "failure_mode": "",
        "recovery_path": "",
        "evidence_expectation": "",
        "evidence_source_kind": "none",
    }
    base.update(overrides)
    return base


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


def _get_journey(client, headers, journey_key, expect=200):
    r = client.get(f"/ux-design/journeys/{journey_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _create_requirement(client, headers, requirement_key, requirement_kind="functional", expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": requirement_kind},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_revision(client, headers, requirement_key, *, expect=201, **fields):
    payload = {
        "statement": "", "rationale": "", "constraint_text": "", "out_of_scope_note": "",
        "change_note": "", "acceptance_criteria": [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/requirements/{requirement_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_requirement(client, headers, requirement_key, expect=200):
    r = client.get(f"/ux-design/requirements/{requirement_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _create_design(client, headers, design_key, *, title="", summary="", expect=201):
    r = client.post(
        "/solution-designs", json={"design_key": design_key, "title": title, "summary": summary}, headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_design(client, headers, design_key, expect=200):
    r = client.get(f"/solution-designs/{design_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _create_stakeholder(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {
        "stakeholder_key": stakeholder_key, "display_name": stakeholder_key, "stakeholder_kind": "other",
        "description": "", "context_note": "",
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/stakeholders", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_exchange(client, headers, exchange_key, provider, receiver, exchange_kind, *, expect=201, **fields):
    payload = {
        "exchange_key": exchange_key, "provider_stakeholder_key": provider,
        "receiver_stakeholder_key": receiver, "exchange_kind": exchange_kind,
        "value_statement": "value", "consideration_state": "unknown", "consideration_kind": None,
        "consideration_statement": "", "channel": "web", "trigger": "", "cadence": "unknown",
        "valid_from": None, "valid_to": None,
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/exchanges", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_blueprint(client, headers, journey_key, expect=200):
    r = client.get("/journey-blueprint", params={"journey_key": journey_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _lane(blueprint, step_key, lane_kind):
    for step in blueprint["steps"]:
        if step["step_key"] == step_key:
            return step["lanes"][lane_kind]
    raise AssertionError(f"step {step_key!r} not found in blueprint")


# ---------------------------------------------------------------------------
# Registry / real-API correspondence (Principle 6: no silent drift)
# ---------------------------------------------------------------------------


class TestRegistryCorrespondence:
    def test_ux_journey_fields_are_add_journey_revision_parameters(self):
        params = set(inspect.signature(ux_design.add_journey_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["ux_journey"]["fields"])
        assert fields <= params

    def test_ux_requirement_fields_are_add_requirement_revision_parameters(self):
        params = set(inspect.signature(ux_design.add_requirement_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["ux_requirement"]["fields"])
        assert fields <= params

    def test_solution_design_fields_are_add_option_parameters(self):
        params = set(inspect.signature(solution_design.add_option).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["solution_design"]["fields"])
        assert fields <= params

    def test_ux_journey_step_fields_are_read_from_the_same_named_key(self):
        """`add_journey_revision` builds each Step's stored fields via
        `step.get("<name>", ...)`; every proposable field name must be one
        of those literal keys, so the registry cannot rename a field the
        real applier never reads."""
        source = Path(ux_design.__file__).read_text(encoding="utf-8")
        for f in assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["ux_journey_step"]["fields"]:
            assert re.search(rf'"{f}":\s*step\.get\("{f}"', source), (
                f"step field {f!r} is not read via step.get({f!r}, ...) in add_journey_revision"
            )

    def test_every_relation_kind_resolves_to_a_real_domain_function(self):
        registry = {
            "upstream_ref": (ux_design, "add_upstream_ref"),
            "journey_step_link": (ux_design, "add_requirement_step_link"),
            "requirement_link": (solution_design, "add_requirement_link"),
            "target_link": (solution_design, "add_target_link"),
            "delivery_link": (journey_blueprint, "add_delivery_link"),
            "stakeholder_link": (journey_blueprint, "add_stakeholder_link"),
            "exchange_link": (journey_blueprint, "add_exchange_link"),
        }
        all_relations = set()
        for schema in assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA.values():
            all_relations.update(schema["relations"])
        assert all_relations == set(registry)
        for _relation_kind, (module, func_name) in registry.items():
            assert hasattr(module, func_name) and callable(getattr(module, func_name))

    def test_eligibility_vocabulary_matches_contract(self):
        assert set(assistant_discussion_proposal.PROPOSAL_ITEM_ELIGIBILITY) == {
            "appliable", "forbidden", "stale", "conflict",
        }


# ---------------------------------------------------------------------------
# Acceptance 1: Overview Vision/Purpose/Capabilities (understanding_claim)
# ---------------------------------------------------------------------------


class TestUnderstandingClaimProposal:
    def _make_session(self, system_id):
        from app.db import get_conn

        with get_conn() as conn:
            now = time.time()
            conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
                (system_id, "a" * 40, now, now),
            )
            snapshot_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            understanding = {
                "vision": [{"name": "v1", "summary": "old summary", "why_core": "old reason"}],
                "system_purpose": [],
                "core_capabilities": [],
            }
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, title, current_understanding, created_at, updated_at)
                   VALUES (?, ?, 'session', ?, ?, ?)""",
                (system_id, snapshot_id, json.dumps(understanding), now, now),
            )
            session_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return session_id

    def test_apply_creates_a_proposed_never_confirmed_intent_item(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        session_id = self._make_session(system["id"])

        thread = _create_thread(
            admin_client, headers, scope="element", screen_id="overview",
            target_kind="understanding_claim", target_ref="vision:v1",
        )["thread"]
        assert thread["target_kind"] == "understanding_claim"

        client = _FixedResponseClient({
            "summary": "The developer clarified the Vision.",
            "confirmed_points": ["Vision should mention self-service"],
            "unresolved_questions": [],
            "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {
                    "field_name": "summary", "subject_ref": "",
                    "current_value": "old summary",
                    "proposed_value": "Enable fully self-service onboarding.",
                    "rationale": "developer said this in chat",
                }
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)

        proposal = _generate_proposal(admin_client, headers, thread["id"])
        assert proposal["target_kind"] == "understanding_claim"
        assert proposal["decision_method"] == "reasoning_llm"
        item = _item_by_field(proposal, "summary")
        assert item["status"] == "proposed"
        assert item["eligibility"] == "appliable"

        applied = _apply(admin_client, headers, proposal["id"], [item["id"]])
        assert applied["applied_item_ids"] == [item["id"]]
        applied_item = _item_by_field(applied["proposal"], "summary")
        assert applied_item["status"] == "applied"
        assert applied_item["applied_ref"].startswith("intent_item:")
        assert applied_item["decision_method"] == "manual"

        intent = admin_client.get(f"/interview/sessions/{session_id}/intent", headers=headers)
        assert intent.status_code == 200, intent.text
        goal_items = intent.json()["items_by_field"]["goal"]
        assert len(goal_items) == 1
        assert goal_items[0]["status"] == "proposed"
        assert goal_items[0]["origin"] == "ai_proposed"
        assert goal_items[0]["value_text"] == "Enable fully self-service onboarding."


# ---------------------------------------------------------------------------
# Acceptance 2: UX Journey field + relation
# ---------------------------------------------------------------------------


class TestUxJourneyProposal:
    def test_field_and_relation_apply(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(
            admin_client, headers, "checkout", title="Checkout journey",
            steps=[_step("s1", 1)],
        )

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "Clarify beneficiary and link to a Capability",
            "confirmed_points": [], "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
            "field_changes": [
                {"field_name": "beneficiary", "subject_ref": "", "current_value": "",
                 "proposed_value": "Shoppers checking out", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "upstream_ref", "subject_ref": "", "relation_target_kind": "purpose_element",
                 "relation_target_ref": "cap-checkout", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "beneficiary")
        relation_item = _item_by_relation(proposal, "upstream_ref", "cap-checkout")
        assert field_item["eligibility"] == "appliable"
        assert relation_item["eligibility"] == "appliable"

        applied = _apply(admin_client, headers, proposal["id"], [field_item["id"], relation_item["id"]])
        assert set(applied["applied_item_ids"]) == {field_item["id"], relation_item["id"]}

        detail = _get_journey(admin_client, headers, "checkout")
        assert detail["current_revision"]["beneficiary"] == "Shoppers checking out"
        # The title set on the first revision must survive an unrelated
        # field's edit -- add_journey_revision replaces the WHOLE revision,
        # so this proves the merge-with-current-values step actually ran.
        assert detail["current_revision"]["title"] == "Checkout journey"
        assert detail["current_revision"]["steps"][0]["step_key"] == "s1"
        assert detail["current_revision_number"] == 2
        refs = [r for r in detail["upstream_refs"] if r["target_ref"] == "cap-checkout"]
        assert len(refs) == 1
        assert refs[0]["ref_kind"] == "purpose_element"
        # Applying never confirms the target's own decision ledger.
        assert detail["design_status"] != "confirmed"


# ---------------------------------------------------------------------------
# Acceptance 2: UX Requirement field + relation
# ---------------------------------------------------------------------------


class TestUxRequirementProposal:
    def test_field_and_relation_apply(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(admin_client, headers, "req-1", statement="Must checkout in one page")
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)])

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "Clarify rationale and link the step", "confirmed_points": [],
            "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
            "field_changes": [
                {"field_name": "rationale", "subject_ref": "", "current_value": "",
                 "proposed_value": "Compliance requires this.", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "journey_step_link", "subject_ref": "", "relation_target_kind": "ux_journey_step",
                 "relation_target_ref": "checkout#s1", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "rationale")
        relation_item = _item_by_relation(proposal, "journey_step_link")

        applied = _apply(admin_client, headers, proposal["id"], [field_item["id"], relation_item["id"]])
        assert set(applied["applied_item_ids"]) == {field_item["id"], relation_item["id"]}

        detail = _get_requirement(admin_client, headers, "req-1")
        assert detail["current_revision"]["rationale"] == "Compliance requires this."
        assert detail["current_revision"]["statement"] == "Must checkout in one page"
        step_links = detail.get("step_links", [])
        assert any(l["step_key"] == "s1" and l["journey_key"] == "checkout" for l in step_links)


# ---------------------------------------------------------------------------
# Acceptance 2: Solution Design field (via Option) + relations
# ---------------------------------------------------------------------------


class TestSolutionDesignProposal:
    def test_field_via_option_and_relations_apply(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_design(admin_client, headers, "design-a")
        _create_requirement(admin_client, headers, "req-1")

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="solution_design", target_ref="design-a",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "Draft an approach and link it up", "confirmed_points": [],
            "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
            "field_changes": [
                {"field_name": "approach", "subject_ref": "opt-1", "current_value": "",
                 "proposed_value": "Use an async queue.", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "requirement_link", "subject_ref": "", "relation_target_kind": "ux_requirement",
                 "relation_target_ref": "req-1", "proposed_value": "", "rationale": "discussed"},
                {"relation_kind": "target_link", "subject_ref": "opt-1", "relation_target_kind": "capability",
                 "relation_target_ref": "cap-x", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "approach")
        assert field_item["subject_ref"] == "opt-1"
        req_link_item = _item_by_relation(proposal, "requirement_link", "req-1")
        target_link_item = _item_by_relation(proposal, "target_link", "cap-x")

        applied = _apply(
            admin_client, headers, proposal["id"],
            [field_item["id"], req_link_item["id"], target_link_item["id"]],
        )
        assert len(applied["applied_item_ids"]) == 3

        detail = _get_design(admin_client, headers, "design-a")
        options = {o["option_key"]: o for o in detail["options"]}
        assert options["opt-1"]["approach"] == "Use an async queue."
        assert options["opt-1"]["authored_by_kind"] == "reasoning_model"
        assert options["opt-1"]["option_status"] != "adopted"
        req_links = {l["requirement_key"] for l in detail["requirement_links"]}
        assert "req-1" in req_links
        target_links = [l for l in detail["target_links"] if l["target_ref"] == "cap-x"]
        assert len(target_links) == 1
        assert target_links[0]["target_kind"] == "capability"


# ---------------------------------------------------------------------------
# Acceptance 3: Blueprint unknown lane -> link proposal
# ---------------------------------------------------------------------------


class TestBlueprintLaneCellProposal:
    def test_touchpoint_lane_goes_from_unknown_to_present(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)])
        _create_stakeholder(admin_client, headers, "shopper")
        _create_stakeholder(admin_client, headers, "store")
        _create_exchange(admin_client, headers, "pay-1", "shopper", "store", "money")

        before = _get_blueprint(admin_client, headers, "checkout")
        assert _lane(before, "s1", "touchpoint")["state"] == "unknown"

        thread = _create_thread(
            admin_client, headers, scope="element", screen_id="journey-blueprint",
            target_kind="blueprint_lane_cell", target_ref="checkout#s1#touchpoint",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "Link the payment exchange to this touchpoint", "confirmed_points": [],
            "unresolved_questions": [], "assumptions": [], "evidence_refs": [],
            "field_changes": [],
            "relation_changes": [
                {"relation_kind": "exchange_link", "subject_ref": "", "relation_target_kind": "value_exchange",
                 "relation_target_ref": "pay-1", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_relation(proposal, "exchange_link", "pay-1")
        assert item["eligibility"] == "appliable"

        applied = _apply(admin_client, headers, proposal["id"], [item["id"]])
        assert applied["applied_item_ids"] == [item["id"]]

        after = _get_blueprint(admin_client, headers, "checkout")
        assert _lane(after, "s1", "touchpoint")["state"] == "present"

    def test_relation_kind_not_valid_for_lane_is_refused(self, admin_client, monkeypatch):
        """A `stakeholder_link` cannot land on the `touchpoint` lane -- the
        model naming it fails the whole call (fail-closed)."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)])
        _create_stakeholder(admin_client, headers, "shopper")

        thread = _create_thread(
            admin_client, headers, scope="element", screen_id="journey-blueprint",
            target_kind="blueprint_lane_cell", target_ref="checkout#s1#touchpoint",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [],
            "relation_changes": [
                {"relation_kind": "stakeholder_link", "subject_ref": "", "relation_target_kind": "stakeholder",
                 "relation_target_ref": "shopper", "proposed_value": "actor", "rationale": ""},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert _list_proposals(admin_client, headers, thread["id"]) == []


# ---------------------------------------------------------------------------
# Acceptance 4: no side effects on publish/policy/runtime tables
# ---------------------------------------------------------------------------


CANONICAL_TABLES = ["components", "publish_jobs", "probe_patches", "experiments", "evolution_node"]


def _cell_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'cell\\_%' ESCAPE '\\'"
    ).fetchall()
    return sorted(r["name"] for r in rows)


def _snapshot_tables(conn, system_id):
    tables = CANONICAL_TABLES + _cell_tables(conn)
    snap = {}
    for table in tables:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "system_id" not in cols:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        else:
            order_col = "component_id" if "component_id" in cols and "id" not in cols else (
                "id" if "id" in cols else None
            )
            if order_col:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE system_id = ? ORDER BY {order_col}", (system_id,)
                ).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM {table} WHERE system_id = ?", (system_id,)).fetchall()
        snap[table] = [dict(r) for r in rows]
    return snap


class TestNoSideEffects:
    def test_apply_touches_nothing_outside_ux_design_lineage(self, admin_client, monkeypatch):
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        system_id = system["id"]
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)])

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO components (system_id, component_id, mode, updated_at) VALUES (?, 'svc', 'trace', ?)",
                (system_id, time.time()),
            )

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "summary", "subject_ref": "", "current_value": "",
                 "proposed_value": "One-page checkout.", "rationale": ""},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_field(proposal, "summary")

        with get_conn() as conn:
            before = _snapshot_tables(conn, system_id)

        _apply(admin_client, headers, proposal["id"], [item["id"]])

        with get_conn() as conn:
            after = _snapshot_tables(conn, system_id)

        assert before == after
        # Explicitly the axis CLAUDE.md calls out: component mode.
        before_modes = {r["component_id"]: r["mode"] for r in before["components"]}
        after_modes = {r["component_id"]: r["mode"] for r in after["components"]}
        assert before_modes == after_modes == {"svc": "trace"}


# ---------------------------------------------------------------------------
# Acceptance 5: applying to a stale revision is refused
# ---------------------------------------------------------------------------


class TestStaleApplyRefused:
    def test_stale_digest_refuses_apply_and_writes_nothing(self, admin_client, monkeypatch):
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
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "summary", "subject_ref": "", "current_value": "",
                 "proposed_value": "One-page checkout.", "rationale": ""},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_field(proposal, "summary")

        # The journey's content changes -- the proposal's captured digest is
        # now stale relative to the CURRENT revision.
        _add_journey_revision(admin_client, headers, "checkout", steps=[_step("s1", 1)], beneficiary="changed")

        r = _apply(admin_client, headers, proposal["id"], [item["id"]], expect=422)
        assert r.json()["detail"]["code"] == "proposal_item_stale"

        # Nothing was written: still exactly 2 revisions (the manual one
        # above), and the item is still `proposed`.
        revisions = admin_client.get("/ux-design/journeys/checkout/revisions", headers=headers).json()
        assert len(revisions["revisions"]) == 2
        detail = _get_proposal(admin_client, headers, proposal["id"])
        refreshed_item = _item_by_field(detail, "summary")
        assert refreshed_item["status"] == "proposed"
        assert refreshed_item["eligibility"] == "stale"


# ---------------------------------------------------------------------------
# Out-of-registry model output fails the whole call (fail-closed)
# ---------------------------------------------------------------------------


class TestOutOfRegistryFailsClosed:
    def test_unknown_field_name_fails_the_whole_proposal(self, admin_client, monkeypatch):
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
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "totally_invented_field", "subject_ref": "", "current_value": "",
                 "proposed_value": "x", "rationale": ""},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        r = _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert r.json()["detail"]["code"] == "discussion_proposal_generation_failed"
        assert _list_proposals(admin_client, headers, thread["id"]) == []

    def test_a_narrowed_registry_forbids_an_already_stored_item(self, admin_client, monkeypatch):
        """The apply-time `forbidden` gate is not dead code.

        Generation refuses an out-of-registry field, so nothing can be
        STORED outside the registry as it stands today. The gate exists for
        the case the registry itself narrows afterwards: the stored item was
        legal when it was written and is not any more, and applying it would
        write through a domain function this layer no longer claims to
        support. Without this the branch is never exercised and could rot.
        """
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
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "summary", "subject_ref": "", "current_value": "",
                 "proposed_value": "One-page checkout.", "rationale": ""},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_field(proposal, "summary")

        # The registry narrows: `summary` is no longer a proposable Journey
        # field. The stored item is untouched -- what changes is whether it
        # may still be applied.
        from app import assistant_discussion_proposal as module

        narrowed = dict(module.PROPOSAL_TARGET_SCHEMA)
        journey = dict(narrowed["ux_journey"])
        journey["fields"] = tuple(f for f in journey["fields"] if f != "summary")
        narrowed["ux_journey"] = journey
        monkeypatch.setattr(module, "PROPOSAL_TARGET_SCHEMA", narrowed)

        detail = _get_proposal(admin_client, headers, proposal["id"])
        stored = next(i for i in detail["items"] if i["id"] == item["id"])
        assert stored["eligibility"] == "forbidden"

        r = _apply(admin_client, headers, proposal["id"], [item["id"]], expect=422)
        assert r.json()["detail"]["code"] == "proposal_item_forbidden"


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_foreign_system_proposal_is_404(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, name="sys-a")
        system_b = _create_system(admin_client, token, name="sys-b")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])

        thread = _create_thread(
            admin_client, headers_a, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "nothing to propose", "confirmed_points": [], "unresolved_questions": [],
            "assumptions": [], "evidence_refs": [], "field_changes": [], "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers_a, thread["id"])

        r = admin_client.get(f"/assistant/discussion-proposals/{proposal['id']}", headers=headers_b)
        assert r.status_code == 404, r.text

        r = admin_client.post(
            f"/assistant/discussion-proposals/{proposal['id']}/apply",
            json={"item_ids": [999999], "rationale": "x"}, headers=headers_b,
        )
        assert r.status_code == 404, r.text

        # System A itself can still read it.
        r = admin_client.get(f"/assistant/discussion-proposals/{proposal['id']}", headers=headers_a)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Mock / missing provider is 503, no proposal row
# ---------------------------------------------------------------------------


class TestReasoningUnavailable:
    def test_mock_provider_yields_503_and_no_proposal_row(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        thread = _create_thread(
            admin_client, headers, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )["thread"]

        with get_conn() as conn:
            before_runs = conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_runs WHERE system_id = ? AND run_type = 'discussion_proposal'",
                (system["id"],),
            ).fetchone()["n"]

        r = admin_client.post(f"/assistant/discussion-threads/{thread['id']}/proposals", headers=headers)
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["code"] == "reasoning_unavailable"

        assert _list_proposals(admin_client, headers, thread["id"]) == []

        with get_conn() as conn:
            after_runs = conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_runs WHERE system_id = ? AND run_type = 'discussion_proposal'",
                (system["id"],),
            ).fetchone()["n"]
        # The audit row is written whether the run succeeded or failed.
        assert after_runs == before_runs + 1

    def test_unknown_thread_id_is_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        r = admin_client.post("/assistant/discussion-threads/999999/proposals", headers=headers)
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_reject_marks_item_rejected_and_writes_nothing(self, admin_client, monkeypatch):
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
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "summary", "subject_ref": "", "current_value": "",
                 "proposed_value": "One-page checkout.", "rationale": ""},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _item_by_field(proposal, "summary")

        rejected = _reject(admin_client, headers, proposal["id"], [item["id"]])
        assert rejected["rejected_item_ids"] == [item["id"]]
        rejected_item = _item_by_field(rejected["proposal"], "summary")
        assert rejected_item["status"] == "rejected"
        assert rejected_item["decision_method"] == "manual"

        revisions = admin_client.get("/ux-design/journeys/checkout/revisions", headers=headers).json()
        assert len(revisions["revisions"]) == 1
