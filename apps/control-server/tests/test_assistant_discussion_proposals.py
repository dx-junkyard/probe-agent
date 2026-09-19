"""Tests for Issue #439 (Epic #436): conversation-to-proposal changesets.

`docs/01-specifications/capabilities/assistant-discussion.md` §2 is the canonical contract. Acceptance
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

from app import (
    assistant_discussion_proposal, journey_blueprint, product_feature, product_objective,
    solution_design, ux_design,
)


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

    def test_product_objective_fields_are_add_objective_revision_parameters(self):
        params = set(inspect.signature(product_objective.add_objective_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["product_objective"]["fields"])
        assert fields <= params

    def test_product_milestone_fields_are_add_milestone_revision_parameters(self):
        params = set(inspect.signature(product_objective.add_milestone_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["product_milestone"]["fields"])
        assert fields <= params

    def test_product_gap_fields_are_add_gap_revision_parameters(self):
        params = set(inspect.signature(product_objective.add_gap_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["product_gap"]["fields"])
        assert fields <= params

    def test_product_feature_fields_are_add_feature_revision_parameters(self):
        params = set(inspect.signature(product_feature.add_feature_revision).parameters)
        fields = set(assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["product_feature"]["fields"])
        assert fields <= params

    def test_no_human_judgement_axis_is_registered_anywhere(self):
        """§5.2's Decisions: `priority_band`/`achievement`/`lifecycle`/
        `design_status`/`option_status`/`resolved`/`adopted` must never
        appear in ANY adapter's `fields`/`relations`/`children` -- the
        enforcement IS that nothing registers them (never a filter applied
        somewhere else)."""
        forbidden = {
            "priority_band", "achievement", "lifecycle", "design_status",
            "option_status", "resolved", "adopted",
        }
        for kind, schema in assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA.items():
            assert not (forbidden & set(schema["fields"])), kind
            assert not (forbidden & set(schema["relations"])), kind
            for child in schema["children"]:
                assert not (forbidden & set(child.fields)), (kind, child.child_kind)

    def test_acceptance_criterion_child_fields_are_add_requirement_revision_criterion_keys(self):
        """`add_requirement_revision` reads each criterion dict via
        `criterion.get("<name>", ...)` -- every proposable child field must
        be one of those literal keys (mirrors `test_ux_journey_step_fields_
        are_read_from_the_same_named_key`'s discipline for the Journey's own
        nested Step fields). `criterion_key`/`criterion_order` are
        deliberately EXCLUDED -- they are addressed through `child_key`/
        `child_order`, never through `ChildSpec.fields` (§5.1)."""
        source = Path(ux_design.__file__).read_text(encoding="utf-8")
        spec = next(
            c for c in assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA["ux_requirement"]["children"]
            if c.child_kind == "acceptance_criterion"
        )
        assert "criterion_key" not in spec.fields
        assert "criterion_order" not in spec.fields
        for f in spec.fields:
            # `verification_method` is read via an intermediate local (then
            # membership-checked) rather than inline in the dict literal the
            # way `statement`/`verification_note` are -- so this only
            # requires `criterion.get("<name>", ...)` to appear SOMEWHERE in
            # the function, not at one fixed dict-literal shape.
            assert re.search(rf'criterion\.get\("{f}"', source), (
                f"criterion field {f!r} is not read via criterion.get({f!r}, ...) in "
                "add_requirement_revision"
            )

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
        """Registry is keyed by `(target_kind, relation_kind)`, NOT
        `relation_kind` alone -- Issue #454 makes `"upstream_ref"` and
        `"requirement_link"`/`"target_link"` each resolve through TWO
        different domain functions depending on which target_kind carries
        them (`ux_journey`'s own `ux_design.add_upstream_ref` vs.
        `product_objective`'s `add_objective_upstream_ref`;
        `solution_design`'s own requirement/target link functions vs.
        `product_feature`'s), so a single relation_kind -> function map can
        no longer express the correspondence."""
        registry = {
            ("ux_journey", "upstream_ref"): (ux_design, "add_upstream_ref"),
            ("ux_requirement", "journey_step_link"): (ux_design, "add_requirement_step_link"),
            ("solution_design", "requirement_link"): (solution_design, "add_requirement_link"),
            ("solution_design", "target_link"): (solution_design, "add_target_link"),
            ("blueprint_lane_cell", "delivery_link"): (journey_blueprint, "add_delivery_link"),
            ("blueprint_lane_cell", "stakeholder_link"): (journey_blueprint, "add_stakeholder_link"),
            ("blueprint_lane_cell", "exchange_link"): (journey_blueprint, "add_exchange_link"),
            ("product_objective", "upstream_ref"): (product_objective, "add_objective_upstream_ref"),
            ("product_milestone", "milestone_dependency"): (product_objective, "add_milestone_dependency"),
            ("product_gap", "source_ref"): (product_objective, "add_gap_source_ref"),
            ("product_gap", "evidence_ref"): (product_objective, "add_gap_evidence_ref"),
            ("product_gap", "artifact_link"): (product_objective, "add_gap_artifact_link"),
            ("product_feature", "requirement_link"): (product_feature, "add_requirement_link"),
            ("product_feature", "capability_link"): (product_feature, "add_capability_link"),
            ("product_feature", "target_link"): (product_feature, "add_target_link"),
        }
        all_pairs = set()
        for kind, schema in assistant_discussion_proposal.PROPOSAL_TARGET_SCHEMA.items():
            for relation_kind in schema["relations"]:
                all_pairs.add((kind, relation_kind))
        assert all_pairs == set(registry)
        for _pair, (module, func_name) in registry.items():
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

        from dataclasses import replace
        from app import discussion_adapters

        adapter = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        dispatched = []

        def registered_field(*args):
            dispatched.append("field")
            return adapter.field_applier(*args)

        def registered_relation(*args):
            dispatched.append("relation")
            return adapter.relation_applier(*args)

        monkeypatch.setitem(
            discussion_adapters.DISCUSSION_ADAPTERS, "ux_journey",
            replace(adapter, field_applier=registered_field, relation_applier=registered_relation),
        )
        applied = _apply(admin_client, headers, proposal["id"], [field_item["id"], relation_item["id"]])
        assert set(applied["applied_item_ids"]) == {field_item["id"], relation_item["id"]}
        assert dispatched == ["field", "relation"]

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
        from dataclasses import replace
        from app import discussion_adapters

        journey = discussion_adapters.DISCUSSION_ADAPTERS["ux_journey"]
        monkeypatch.setitem(
            discussion_adapters.DISCUSSION_ADAPTERS, "ux_journey",
            replace(journey, fields=tuple(f for f in journey.fields if f != "summary")),
        )

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


@pytest.mark.parametrize("missing", ["adapter", "field_applier", "relation_applier", "fields", "relations"])
def test_apply_refuses_missing_adapter_registration_before_any_write(admin_client, monkeypatch, missing):
    """A previously generated proposal cannot bypass a removed registry entry.

    The field is first in the batch, so a missing relation handler also
    proves that no earlier item was written before the refusal.
    """
    from dataclasses import replace
    from app import discussion_adapters

    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    _create_journey(admin_client, headers, "checkout")
    _add_journey_revision(admin_client, headers, "checkout", title="Checkout")
    thread = _create_thread(
        admin_client, headers, scope="entity", screen_id="ux-design-studio",
        target_kind="ux_journey", target_ref="checkout",
    )["thread"]
    client = _FixedResponseClient({
        "summary": "Clarify checkout", "confirmed_points": [], "unresolved_questions": [],
        "assumptions": [], "evidence_refs": [],
        "field_changes": [{"field_name": "beneficiary", "subject_ref": "", "current_value": "",
                           "proposed_value": "Shoppers", "rationale": "discussed"}],
        "relation_changes": [{"relation_kind": "upstream_ref", "subject_ref": "",
                              "relation_target_kind": "purpose_element", "relation_target_ref": "cap-checkout",
                              "proposed_value": "", "rationale": "discussed"}],
    })
    _enable_real_llm(monkeypatch, client)
    proposal = _generate_proposal(admin_client, headers, thread["id"])
    before = _get_journey(admin_client, headers, "checkout")
    registry = discussion_adapters.DISCUSSION_ADAPTERS
    if missing == "adapter":
        monkeypatch.delitem(registry, "ux_journey")
    else:
        value = () if missing in ("fields", "relations") else None
        monkeypatch.setitem(registry, "ux_journey", replace(registry["ux_journey"], **{missing: value}))

    rejected = _apply(admin_client, headers, proposal["id"], [i["id"] for i in proposal["items"]], expect=422)
    assert "proposal_item_forbidden" in rejected.text
    after = _get_journey(admin_client, headers, "checkout")
    assert after["current_revision_id"] == before["current_revision_id"]
    assert after["upstream_refs"] == before["upstream_refs"]
    assert all(i["status"] == "proposed" for i in _get_proposal(admin_client, headers, proposal["id"])["items"])


# ---------------------------------------------------------------------------
# Issue #454 (Epic #443 §5): ChildSpec + Objective/Milestone/Gap/Feature
# ---------------------------------------------------------------------------


def _add_objective(client, headers, objective_key, *, expect=201):
    r = client.post("/product-objectives", json={"objective_key": objective_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_objective_revision(client, headers, objective_key, *, expect=201, **fields):
    payload = {"title": "", "intent": "", "contribution": "", "scope_note": "", "summary": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/product-objectives/{objective_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_objective(client, headers, objective_key, expect=200):
    r = client.get(f"/product-objectives/{objective_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_milestone(client, headers, objective_key, milestone_key, *, expect=201):
    r = client.post(
        "/product-milestones",
        json={"objective_key": objective_key, "milestone_key": milestone_key},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_milestone_revision(client, headers, milestone_key, *, expect=201, **fields):
    payload = {
        "title": "", "target_state": "", "verification_method": "unavailable", "verification_note": "",
        "sequence_hint": 0, "summary": "", "change_note": "",
    }
    payload.update(fields)
    r = client.post(f"/product-milestones/{milestone_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_milestone(client, headers, milestone_key, expect=200):
    r = client.get(f"/product-milestones/{milestone_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_gap(client, headers, milestone_key, gap_key, *, expect=201):
    r = client.post("/product-gaps", json={"milestone_key": milestone_key, "gap_key": gap_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_gap_revision(client, headers, gap_key, *, expect=201, **fields):
    payload = {
        "title": "", "current_state": "", "target_state": "", "target_state_mode": "unknown",
        "interpretation": "", "suggested_priority_note": "", "change_note": "",
    }
    payload.update(fields)
    r = client.post(f"/product-gaps/{gap_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_gap(client, headers, gap_key, expect=200):
    r = client.get(f"/product-gaps/{gap_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature(client, headers, feature_key, *, expect=201):
    r = client.post("/product-features", json={"feature_key": feature_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_feature_revision(client, headers, feature_key, *, expect=201, **fields):
    payload = {"title": "", "statement": "", "rationale": "", "scope_note": "", "summary": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/product-features/{feature_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_feature(client, headers, feature_key, expect=200):
    r = client.get(f"/product-features/{feature_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _child_items(proposal, child_kind=None):
    return [
        i for i in proposal["items"]
        if i["item_kind"] == "field" and (i.get("child_kind") or "")
        and (child_kind is None or i["child_kind"] == child_kind)
    ]


def _child_item(proposal, *, child_key, field_name=None, order_only=None):
    for i in _child_items(proposal):
        if i["child_key"] != child_key:
            continue
        if field_name is not None and i["field_name"] != field_name:
            continue
        if order_only is not None and (i["child_order"] is not None and not i["field_name"]) != order_only:
            continue
        return i
    raise AssertionError(
        f"no child item child_key={child_key!r} field_name={field_name!r} order_only={order_only!r} in {proposal}"
    )


def _child_change(child_kind, child_intent, *, child_key="", client_temp_key="", child_order=None,
                   field_name="", proposed_value="", rationale="discussed"):
    return {
        "child_kind": child_kind, "child_intent": child_intent, "child_key": child_key,
        "client_temp_key": client_temp_key, "child_order": child_order, "field_name": field_name,
        "current_value": "", "proposed_value": proposed_value, "rationale": rationale,
    }


class TestAcceptanceCriterionChildProposal:
    """§5.1's Acceptance Criteria: add/update/remove and reorder-vs-content
    are separately selectable proposal items."""

    def test_add_update_remove_and_reorder_are_separate_items(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(
            admin_client, headers, "req-1",
            acceptance_criteria=[
                {"criterion_key": "c-keep", "criterion_order": 0, "statement": "Keep me",
                 "verification_method": "manual_review", "verification_note": ""},
                {"criterion_key": "c-remove", "criterion_order": 1, "statement": "Remove me",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]

        client = _FixedResponseClient({
            "summary": "Tidy up acceptance criteria", "confirmed_points": [], "unresolved_questions": [],
            "assumptions": [], "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                # Content change on an EXISTING key.
                _child_change(
                    "acceptance_criterion", "update", child_key="c-keep",
                    field_name="statement", proposed_value="Keep me, reworded",
                ),
                # Pure reorder of the SAME existing key -- a SEPARATE item.
                _child_change("acceptance_criterion", "update", child_key="c-keep", child_order=5),
                # Remove an existing key.
                _child_change("acceptance_criterion", "remove", child_key="c-remove"),
                # Add a brand new criterion, split across two field rows that
                # share one client_temp_key.
                _child_change(
                    "acceptance_criterion", "add", client_temp_key="tmp-1",
                    field_name="statement", proposed_value="New criterion",
                ),
                _child_change(
                    "acceptance_criterion", "add", client_temp_key="tmp-1",
                    field_name="verification_method", proposed_value="replay",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])

        update_content = _child_item(proposal, child_key="c-keep", field_name="statement")
        update_order = _child_item(proposal, child_key="c-keep", field_name="")
        assert update_content["id"] != update_order["id"]
        assert update_content["child_intent"] == "update"
        assert update_order["child_intent"] == "update"
        assert update_order["child_order"] == 5

        remove_item = _child_item(proposal, child_key="c-remove")
        assert remove_item["child_intent"] == "remove"

        add_items = [i for i in _child_items(proposal) if i["child_intent"] == "add"]
        assert len(add_items) == 2
        # Issue #454's reserved-key Decisions: both rows of the SAME
        # client_temp_key share one server-assigned child_key, distinct from
        # the human-authored keys.
        reserved_keys = {i["child_key"] for i in add_items}
        assert len(reserved_keys) == 1
        reserved_key = next(iter(reserved_keys))
        assert reserved_key not in ("c-keep", "c-remove", "")

        # Select the content update and BOTH add rows (sharing the SAME
        # reserved key) in one apply batch -- the reorder and the remove
        # are left unselected, proving all four kinds of child item were
        # independently selectable. (Applying items from the SAME proposal
        # across two SEPARATE requests is deliberately not exercised here:
        # the first apply's new revision moves the Requirement's digest, so
        # a later attempt on the SAME proposal's remaining items correctly
        # sees `proposal_item_stale` -- the identical, intentional rule
        # `TestStaleApplyRefused` already covers for a plain field.)
        add_stmt_item = next(i for i in add_items if i["field_name"] == "statement")
        add_method_item = next(i for i in add_items if i["field_name"] == "verification_method")
        applied = _apply(
            admin_client, headers, proposal["id"],
            [update_content["id"], add_stmt_item["id"], add_method_item["id"]],
        )
        assert set(applied["applied_item_ids"]) == {
            update_content["id"], add_stmt_item["id"], add_method_item["id"],
        }

        detail = _get_requirement(admin_client, headers, "req-1")
        criteria = {c["criterion_key"]: c for c in detail["current_revision"]["acceptance_criteria"]}
        assert criteria["c-keep"]["statement"] == "Keep me, reworded"
        assert criteria["c-keep"]["criterion_order"] == 0  # reorder item was NOT applied
        assert "c-remove" in criteria  # remove item was NOT applied
        assert reserved_key in criteria
        # Both add rows shared the SAME reserved child_key and compounded
        # onto the SAME new criterion, rather than creating two rows.
        assert criteria[reserved_key]["statement"] == "New criterion"
        assert criteria[reserved_key]["verification_method"] == "replay"
        assert len(criteria) == 3  # c-keep, c-remove (unremoved), reserved_key -- no extra row

    def test_remove_then_apply_removes_the_criterion(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(
            admin_client, headers, "req-1",
            acceptance_criteria=[
                {"criterion_key": "c-remove", "criterion_order": 0, "statement": "Remove me",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [_child_change("acceptance_criterion", "remove", child_key="c-remove")],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        remove_item = _child_item(proposal, child_key="c-remove")
        _apply(admin_client, headers, proposal["id"], [remove_item["id"]])
        detail = _get_requirement(admin_client, headers, "req-1")
        assert detail["current_revision"]["acceptance_criteria"] == []


class TestChildProposalFailClosed:
    def test_unknown_child_kind_fails_the_whole_proposal(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change("totally_invented_child", "add", client_temp_key="tmp-1", proposed_value="x"),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        r = _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert r.json()["detail"]["code"] == "discussion_proposal_generation_failed"
        assert _list_proposals(admin_client, headers, thread["id"]) == []

    def test_unknown_field_name_for_a_known_child_kind_fails_the_whole_proposal(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(
            admin_client, headers, "req-1",
            acceptance_criteria=[
                {"criterion_key": "c-1", "criterion_order": 0, "statement": "s",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "update", child_key="c-1",
                    field_name="criterion_key", proposed_value="renamed",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        r = _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert r.json()["detail"]["code"] == "discussion_proposal_generation_failed"

    def test_update_on_unknown_child_key_fails_the_whole_proposal(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(admin_client, headers, "req-1")  # no criteria at all
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "update", child_key="does-not-exist",
                    field_name="statement", proposed_value="x",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        r = _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert r.json()["detail"]["code"] == "discussion_proposal_generation_failed"

    def test_a_legitimate_add_is_not_caught_up_in_the_unknown_key_rejection(self, admin_client, monkeypatch):
        """§5.4's own carve-out: an `add` never supplies `child_key`, so it
        must never be refused by the SAME check that refuses an
        update/remove naming a key that does not exist."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(admin_client, headers, "req-1")  # no criteria yet
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "add", client_temp_key="tmp-1",
                    field_name="statement", proposed_value="Brand new",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        add_items = [i for i in _child_items(proposal) if i["child_intent"] == "add"]
        assert len(add_items) == 1
        assert add_items[0]["child_key"]  # server-reserved, non-empty

    def test_add_with_a_caller_supplied_child_key_fails_the_whole_proposal(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(admin_client, headers, "req-1")
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "add", child_key="self-chosen-key",
                    field_name="statement", proposed_value="x",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        r = _generate_proposal(admin_client, headers, thread["id"], expect=502)
        assert r.json()["detail"]["code"] == "discussion_proposal_generation_failed"


class TestChildStaleApplyRefused:
    def test_concurrent_edit_refuses_apply_and_writes_nothing(self, admin_client, monkeypatch):
        """A concurrent edit to the Requirement (through the normal write
        path, not this proposal) changes the Requirement's overall content
        digest -- caught by the SAME digest-staleness gate every other
        target_kind already relies on, so a child update/remove/add is
        refused all-or-nothing exactly like a stale plain field."""
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _add_requirement_revision(
            admin_client, headers, "req-1",
            acceptance_criteria=[
                {"criterion_key": "c-1", "criterion_order": 0, "statement": "s",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )
        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "update", child_key="c-1",
                    field_name="statement", proposed_value="reworded",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        item = _child_item(proposal, child_key="c-1")

        # A concurrent, unrelated edit through the normal write path.
        _add_requirement_revision(
            admin_client, headers, "req-1", rationale="someone else edited this",
            acceptance_criteria=[
                {"criterion_key": "c-1", "criterion_order": 0, "statement": "s",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )

        r = _apply(admin_client, headers, proposal["id"], [item["id"]], expect=422)
        assert r.json()["detail"]["code"] == "proposal_item_stale"
        revisions = admin_client.get("/ux-design/requirements/req-1/revisions", headers=headers).json()
        assert len(revisions["revisions"]) == 2  # the manual concurrent edit only


class TestProductObjectiveMilestoneGapFeatureProposal:
    def test_objective_field_and_upstream_ref_relation_apply(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _add_objective(admin_client, headers, "obj-1")
        before = _get_objective(admin_client, headers, "obj-1")

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_objective", target_ref="obj-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "intent", "subject_ref": "", "current_value": "",
                 "proposed_value": "Reduce cart abandonment.", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "upstream_ref", "subject_ref": "", "relation_target_kind": "vision_claim",
                 "relation_target_ref": "v-1", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "intent")
        relation_item = _item_by_relation(proposal, "upstream_ref")

        applied = _apply(admin_client, headers, proposal["id"], [field_item["id"], relation_item["id"]])
        assert set(applied["applied_item_ids"]) == {field_item["id"], relation_item["id"]}

        detail = _get_objective(admin_client, headers, "obj-1")
        assert detail["current_revision"]["intent"] == "Reduce cart abandonment."
        # `objective_state` (the confirm/decision axis) is untouched by apply.
        assert detail["objective_state"] == before["objective_state"]

    def test_milestone_field_apply_and_duplicate_dependency_is_refused(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _add_objective(admin_client, headers, "obj-1")
        _add_milestone(admin_client, headers, "obj-1", "m-1")
        _add_milestone(admin_client, headers, "obj-1", "m-2")
        before = _get_milestone(admin_client, headers, "m-1")
        admin_client.post(
            "/product-milestones/m-1/dependencies",
            json={"depends_on_milestone_key": "m-2", "rationale": "m-1 needs m-2 first"},
            headers=headers,
        )

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_milestone", target_ref="m-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "target_state", "subject_ref": "", "current_value": "",
                 "proposed_value": "Checkout under 60s.", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "milestone_dependency", "subject_ref": "", "relation_target_kind": "",
                 "relation_target_ref": "m-2", "proposed_value": "", "rationale": "already depends on m-2"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "target_state")
        dep_item = _item_by_relation(proposal, "milestone_dependency")

        # The field applies fine on its own.
        applied = _apply(admin_client, headers, proposal["id"], [field_item["id"]])
        assert applied["applied_item_ids"] == [field_item["id"]]
        detail = _get_milestone(admin_client, headers, "m-1")
        assert detail["current_revision"]["target_state"] == "Checkout under 60s."
        # `achievement` (the assessment axis) is untouched by apply.
        assert detail["achievement"] == before["achievement"]

        # The DUPLICATE dependency is refused, cleanly, with no partial
        # write for THIS item.
        r = _apply(admin_client, headers, proposal["id"], [dep_item["id"]], expect=422)
        assert r.status_code == 422
        refreshed = _get_proposal(admin_client, headers, proposal["id"])
        refreshed_dep = next(i for i in refreshed["items"] if i["id"] == dep_item["id"])
        assert refreshed_dep["status"] == "proposed"

    def test_gap_field_apply_never_touches_lifecycle_or_priority(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _add_objective(admin_client, headers, "obj-1")
        _add_milestone(admin_client, headers, "obj-1", "m-1")
        _add_gap(admin_client, headers, "m-1", "gap-1")
        before = _get_gap(admin_client, headers, "gap-1")

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="objective-map",
            target_kind="product_gap", target_ref="gap-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "current_state", "subject_ref": "", "current_value": "",
                 "proposed_value": "No retry on failed payment.", "rationale": "discussed"},
            ],
            "relation_changes": [],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "current_state")
        _apply(admin_client, headers, proposal["id"], [field_item["id"]])

        after = _get_gap(admin_client, headers, "gap-1")
        assert after["current_revision"]["current_state"] == "No retry on failed payment."
        assert after["lifecycle"] == before["lifecycle"]
        assert after["priority_band"] == before["priority_band"]

    def test_feature_field_and_three_link_kinds_apply(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _add_feature(admin_client, headers, "feat-1")
        _create_requirement(admin_client, headers, "req-1")

        thread = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="product_feature", target_ref="feat-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [],
            "field_changes": [
                {"field_name": "statement", "subject_ref": "", "current_value": "",
                 "proposed_value": "One-click reorder.", "rationale": "discussed"},
            ],
            "relation_changes": [
                {"relation_kind": "requirement_link", "subject_ref": "", "relation_target_kind": "",
                 "relation_target_ref": "req-1", "proposed_value": "", "rationale": "discussed"},
                {"relation_kind": "target_link", "subject_ref": "", "relation_target_kind": "component",
                 "relation_target_ref": "checkout-svc", "proposed_value": "", "rationale": "discussed"},
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers, thread["id"])
        field_item = _item_by_field(proposal, "statement")
        req_link_item = _item_by_relation(proposal, "requirement_link", "req-1")
        target_link_item = _item_by_relation(proposal, "target_link", "checkout-svc")

        applied = _apply(
            admin_client, headers, proposal["id"],
            [field_item["id"], req_link_item["id"], target_link_item["id"]],
        )
        assert len(applied["applied_item_ids"]) == 3

        detail = _get_feature(admin_client, headers, "feat-1")
        assert detail["current_revision"]["statement"] == "One-click reorder."
        req_links = {l["requirement_key"] for l in detail["requirement_links"]}
        assert "req-1" in req_links
        target_links = [l for l in detail["target_links"] if l["target_ref"] == "checkout-svc"]
        assert len(target_links) == 1
        assert target_links[0]["link_kind"] == "component"


class TestChildSystemIsolation:
    def test_a_child_item_from_another_system_is_unreachable(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, name="child-sys-a")
        system_b = _create_system(admin_client, token, name="child-sys-b")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])
        _create_requirement(admin_client, headers_a, "req-1")
        _add_requirement_revision(
            admin_client, headers_a, "req-1",
            acceptance_criteria=[
                {"criterion_key": "c-1", "criterion_order": 0, "statement": "s",
                 "verification_method": "manual_review", "verification_note": ""},
            ],
        )
        thread = _create_thread(
            admin_client, headers_a, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        client = _FixedResponseClient({
            "summary": "x", "confirmed_points": [], "unresolved_questions": [], "assumptions": [],
            "evidence_refs": [], "field_changes": [], "relation_changes": [],
            "child_changes": [
                _child_change(
                    "acceptance_criterion", "update", child_key="c-1",
                    field_name="statement", proposed_value="reworded",
                ),
            ],
        })
        _enable_real_llm(monkeypatch, client)
        proposal = _generate_proposal(admin_client, headers_a, thread["id"])
        item = _child_item(proposal, child_key="c-1")

        r = _apply(admin_client, headers_b, proposal["id"], [item["id"]], expect=404)
        assert r.status_code == 404

class TestEpic457AcceptanceAudit:
    def _gap_proposal(self, client, monkeypatch, *, relations=()):
        token = _login(client)
        system = _create_system(client, token)
        headers = _headers(token, system['id'])
        _add_objective(client, headers, 'audit-objective')
        _add_objective_revision(client, headers, 'audit-objective', title='目的A')
        _add_milestone(client, headers, 'audit-objective', 'audit-milestone')
        _add_gap(client, headers, 'audit-milestone', 'audit-gap')
        thread = _create_thread(client, headers, scope='entity', screen_id='objective-map',
                                target_kind='product_gap', target_ref='audit-gap')['thread']
        llm = _FixedResponseClient({
            'summary': '監査', 'confirmed_points': [], 'unresolved_questions': [], 'assumptions': [],
            'evidence_refs': [], 'field_changes': [
                {'field_name': 'current_state', 'subject_ref': '', 'current_value': '',
                 'proposed_value': '改善対象を確認', 'rationale': '確認済みの範囲'}],
            'relation_changes': list(relations),
        })
        _enable_real_llm(monkeypatch, llm)
        proposal = _generate_proposal(client, headers, thread['id'])
        return headers, proposal, llm

    def test_dependency_change_blocks_proposal_even_when_root_is_unchanged(self, admin_client, monkeypatch):
        headers, proposal, llm = self._gap_proposal(admin_client, monkeypatch)
        assert 'coverage_bundle' in llm.calls[0][1]['content']
        assert 'joint_understanding' in llm.calls[0][1]['content']
        before = _get_gap(admin_client, headers, 'audit-gap')
        _add_objective_revision(admin_client, headers, 'audit-objective', title='目的B')
        updated = _get_proposal(admin_client, headers, proposal['id'])
        assert all(i['eligibility'] == 'stale' for i in updated['items'])
        _apply(admin_client, headers, proposal['id'], [proposal['items'][0]['id']], expect=422)
        assert _get_gap(admin_client, headers, 'audit-gap')['current_revision_id'] == before['current_revision_id']

    def test_gap_evidence_relation_uses_domain_and_does_not_resolve_gap(self, admin_client, monkeypatch):
        headers, proposal, _ = self._gap_proposal(admin_client, monkeypatch, relations=[{
            'relation_kind': 'evidence_ref', 'relation_target_kind': 'human_report',
            'relation_target_ref': 'report:review-1', 'subject_ref': '', 'proposed_value': '', 'rationale': '利用者の報告',
        }])
        before = _get_gap(admin_client, headers, 'audit-gap')
        item = _item_by_relation(proposal, 'evidence_ref')
        _apply(admin_client, headers, proposal['id'], [item['id']])
        after = _get_gap(admin_client, headers, 'audit-gap')
        assert after['lifecycle'] == before['lifecycle']
        assert after['priority_band'] == before['priority_band']
        assert any(e['evidence_ref'] == 'report:review-1' for e in after['evidence_refs'])

    def test_gap_artifact_requirement_is_in_bundle_and_dependency_manifest(self, admin_client, monkeypatch):
        headers, proposal, _ = self._gap_proposal(admin_client, monkeypatch)
        _create_requirement(admin_client, headers, 'linked-requirement')
        _add_requirement_revision(admin_client, headers, 'linked-requirement', statement='入力を守る')
        r = admin_client.post('/product-gaps/audit-gap/artifact-links', headers=headers,
                              json={'link_kind': 'ux_requirement', 'target_ref': 'linked-requirement'})
        assert r.status_code == 201, r.text
        r = admin_client.get(f"/assistant/discussion-threads/{proposal['thread_id']}/context-bundle", headers=headers)
        assert r.status_code == 200, r.text
        bundle = r.json()
        assert any(d['target_kind'] == 'ux_requirement' and d['target_ref'] == 'linked-requirement'
                   for d in bundle['dependencies'])
        source = next(s for s in bundle['sources'] if s['target_kind'] == 'ux_requirement')
        assert 'tab=requirements' in source['deep_link']


def test_translated_verification_label_is_not_an_appliable_enum():
    from app.llm import LLMConfig
    payload = {'summary': 'review', 'confirmed_points': [], 'unresolved_questions': [],
               'assumptions': [], 'evidence_refs': [], 'field_changes': [], 'relation_changes': [],
               'child_changes': [_child_change('acceptance_criterion', 'add', client_temp_key='new',
                    field_name='verification_method', proposed_value='手動検証')]}
    client = _FixedResponseClient(payload)
    result = assistant_discussion_proposal.generate_proposal(
        client, LLMConfig(provider='openai', model='gpt-5', api_key='test', base_url=None, timeout=30),
        target_kind='ux_requirement', target_ref='req', target_title='Requirement', turns=[], target_facts={})
    assert result.error_kind == 'invalid_registry'
    assert 'manual_review' in client.calls[0][1]['content']
    item = {'item_kind': 'field', 'child_kind': 'acceptance_criterion', 'child_intent': 'add',
            'field_name': 'verification_method', 'proposed_value': '手動検証'}
    assert assistant_discussion_proposal.evaluate_item_eligibility(
        {'target_kind': 'ux_requirement'}, item, [item], None) == 'forbidden'
