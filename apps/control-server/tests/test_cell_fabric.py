"""Tests for Issue #298: Probe Cell Fabric Sub 1 -- Cell contract / Role
Card / common state schema.

Covers:
1. Contract layer schema round-trip (unknown fields rejected fail-closed) for
   Agent Role Card and Cell Definition.
2. Task status transition table: every allowed transition passes, every
   disallowed pair raises, `done` requires acceptance_met + evidence refs.
3. Task delegation payload: missing/empty acceptance is a validation error.
4. Role Card semver compatibility (same-major and >= pinned only), unknown
   status/enum and mismatched schema_version rejected fail-closed.
5. Worker vs orchestrator Cells sharing the one contract (roster null vs
   list), including roster distinctness.
6. Model alias resolution: env-var resolution, provider fail-closed
   validation, fallback to intelligence_from_env, and that changing the env
   var never touches a stored card.
7. API: create/list/get role cards, duplicate version 409, deprecate,
   create cell requiring an ACTIVE card, unique cell_id per System.
8. System isolation.
9. Additive migration: new tables appear via init_db() without touching
   pre-existing data; init_db() stays idempotent.
10. The JSON schema files structurally validate example payloads
    (additionalProperties: false, required fields) -- jsonschema is not an
    installed dependency in this environment, so validation is structural.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-fabric-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _card_payload(**overrides):
    payload = {
        "role_key": "worker-summarizer",
        "version": "1.0.0",
        "status": "active",
        "mission": "Summarize things",
        "scope": ["summarize"],
        "out_of_scope": ["payment"],
        "model_alias": "worker-default",
        "tool_policy": {"allowed_tools": ["read"], "network_allowed": False},
        "acceptance_template": ["must be concise"],
        "rubric_ref": None,
        "changelog": "initial",
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _cell_payload(**overrides):
    payload = {
        "cell_id": "sys1-summarize-comp",
        "roster": None,
        "role_card_ref": {"role_key": "worker-summarizer", "version": "1.0.0"},
        "status": "active",
        "mission": None,
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _create_card(client, headers, **overrides):
    r = client.post("/cell-fabric/role-cards", json=_card_payload(**overrides), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Contract layer: schema round-trip / unknown-field rejection
# ---------------------------------------------------------------------------


class TestContractRoundTrip:
    def test_valid_role_card_accepted(self):
        from app.cell_fabric import AgentRoleCard

        card = AgentRoleCard(**_card_payload())
        assert card.role_key == "worker-summarizer"
        assert card.tool_policy.network_allowed is False

    def test_role_card_unknown_field_rejected(self):
        from app.cell_fabric import AgentRoleCard

        with pytest.raises(ValidationError):
            AgentRoleCard(**_card_payload(unexpected_field="nope"))

    def test_role_card_tool_policy_unknown_field_rejected(self):
        from app.cell_fabric import AgentRoleCard

        payload = _card_payload()
        payload["tool_policy"] = {
            "allowed_tools": [], "network_allowed": False, "extra": 1,
        }
        with pytest.raises(ValidationError):
            AgentRoleCard(**payload)

    def test_valid_cell_definition_accepted(self):
        from app.cell_fabric import CellDefinitionContract

        cell = CellDefinitionContract(**_cell_payload())
        assert cell.roster is None

    def test_cell_definition_unknown_field_rejected(self):
        from app.cell_fabric import CellDefinitionContract

        with pytest.raises(ValidationError):
            CellDefinitionContract(**_cell_payload(unexpected_field="nope"))

    def test_cell_state_unknown_field_rejected(self):
        from app.cell_fabric import CellStateContract

        with pytest.raises(ValidationError):
            CellStateContract(
                cell_id="c1",
                role_card={"role_key": "r", "version": "1.0.0", "model_alias": "a"},
                mission="m",
                tasks={"counts": {}},
                schema_version="1.0",
                unexpected_field="nope",
            )


# ---------------------------------------------------------------------------
# Worker vs orchestrator: same contract, roster is the only distinguisher
# ---------------------------------------------------------------------------


class TestWorkerOrchestratorSharedContract:
    def test_worker_has_null_roster(self):
        from app.cell_fabric import CellDefinitionContract

        cell = CellDefinitionContract(**_cell_payload(roster=None))
        assert cell.roster is None

    def test_orchestrator_has_roster_list(self):
        from app.cell_fabric import CellDefinitionContract

        cell = CellDefinitionContract(
            **_cell_payload(cell_id="orch-1", roster=["child-a", "child-b"])
        )
        assert cell.roster == ["child-a", "child-b"]

    def test_orchestrator_empty_roster_is_still_an_orchestrator(self):
        from app.cell_fabric import CellDefinitionContract

        cell = CellDefinitionContract(**_cell_payload(cell_id="orch-2", roster=[]))
        assert cell.roster == []

    def test_roster_duplicate_cell_ids_rejected(self):
        from app.cell_fabric import CellDefinitionContract

        with pytest.raises(ValidationError):
            CellDefinitionContract(
                **_cell_payload(cell_id="orch-3", roster=["child-a", "child-a"])
            )


# ---------------------------------------------------------------------------
# Task status transition table
# ---------------------------------------------------------------------------


class TestTaskTransitions:
    def test_every_allowed_transition_passes(self):
        from app.cell_fabric import TASK_TRANSITIONS, validate_task_transition

        for current, allowed in TASK_TRANSITIONS.items():
            for new in allowed:
                if new == "done":
                    validate_task_transition(
                        current, new, acceptance_met=True, evidence_refs=["trace:1"]
                    )
                else:
                    validate_task_transition(current, new)

    def test_every_disallowed_pair_raises(self):
        from app.cell_fabric import (
            TASK_STATUSES, TASK_TRANSITIONS, CellContractError,
            validate_task_transition,
        )

        for current in TASK_STATUSES:
            for new in TASK_STATUSES:
                if new in TASK_TRANSITIONS[current]:
                    continue
                with pytest.raises(CellContractError):
                    validate_task_transition(
                        current, new, acceptance_met=True, evidence_refs=["trace:1"]
                    )

    def test_done_requires_acceptance_met(self):
        from app.cell_fabric import CellContractError, validate_task_transition

        with pytest.raises(CellContractError):
            validate_task_transition(
                "review", "done", acceptance_met=False, evidence_refs=["trace:1"]
            )

    def test_done_requires_evidence_refs(self):
        from app.cell_fabric import CellContractError, validate_task_transition

        with pytest.raises(CellContractError):
            validate_task_transition("review", "done", acceptance_met=True, evidence_refs=[])
        with pytest.raises(CellContractError):
            validate_task_transition(
                "review", "done", acceptance_met=True, evidence_refs=None
            )

    def test_done_with_acceptance_and_evidence_passes(self):
        from app.cell_fabric import validate_task_transition

        validate_task_transition(
            "review", "done", acceptance_met=True, evidence_refs=["trace:1"]
        )

    def test_unknown_status_rejected(self):
        from app.cell_fabric import CellContractError, validate_task_transition

        with pytest.raises(CellContractError):
            validate_task_transition("todo", "bogus")
        with pytest.raises(CellContractError):
            validate_task_transition("bogus", "todo")

    def test_done_is_terminal(self):
        from app.cell_fabric import TASK_TRANSITIONS

        assert TASK_TRANSITIONS["done"] == set()

    def test_failed_only_retries_to_todo(self):
        from app.cell_fabric import TASK_TRANSITIONS

        assert TASK_TRANSITIONS["failed"] == {"todo"}


# ---------------------------------------------------------------------------
# Task delegation payload
# ---------------------------------------------------------------------------


class TestTaskDelegation:
    def test_valid_delegation_accepted(self):
        from app.cell_fabric import TaskDelegation

        delegation = TaskDelegation(
            goal_ref="goal:1", acceptance=["output matches spec"],
        )
        assert delegation.acceptance == ["output matches spec"]

    def test_missing_acceptance_is_validation_error(self):
        from app.cell_fabric import TaskDelegation

        with pytest.raises(ValidationError):
            TaskDelegation(goal_ref="goal:1")

    def test_empty_acceptance_is_validation_error(self):
        from app.cell_fabric import TaskDelegation

        with pytest.raises(ValidationError):
            TaskDelegation(goal_ref="goal:1", acceptance=[])

    def test_unknown_field_rejected(self):
        from app.cell_fabric import TaskDelegation

        with pytest.raises(ValidationError):
            TaskDelegation(goal_ref="goal:1", acceptance=["a"], extra_field="nope")


# ---------------------------------------------------------------------------
# Role Card semver compatibility
# ---------------------------------------------------------------------------


class TestRoleCardSemver:
    def test_same_major_higher_minor_is_compatible(self):
        from app.cell_fabric import role_card_versions_compatible

        assert role_card_versions_compatible("1.0.0", "1.2.0") is True

    def test_same_major_same_version_is_compatible(self):
        from app.cell_fabric import role_card_versions_compatible

        assert role_card_versions_compatible("1.0.0", "1.0.0") is True

    def test_same_major_lower_candidate_is_incompatible(self):
        from app.cell_fabric import role_card_versions_compatible

        assert role_card_versions_compatible("1.2.0", "1.0.0") is False

    def test_major_bump_is_incompatible(self):
        from app.cell_fabric import role_card_versions_compatible

        assert role_card_versions_compatible("1.0.0", "2.0.0") is False

    def test_malformed_version_fails_closed(self):
        from app.cell_fabric import CellContractError, role_card_versions_compatible

        with pytest.raises(CellContractError):
            role_card_versions_compatible("1.0", "1.0.0")
        with pytest.raises(CellContractError):
            role_card_versions_compatible("1.0.0", "not-a-version")

    def test_unknown_status_rejected_fail_closed(self):
        from app.cell_fabric import AgentRoleCard

        with pytest.raises(ValidationError):
            AgentRoleCard(**_card_payload(status="published"))

    def test_mismatched_schema_version_rejected_fail_closed(self):
        from app.cell_fabric import AgentRoleCard

        with pytest.raises(ValidationError):
            AgentRoleCard(**_card_payload(schema_version="9.9"))

    def test_malformed_version_field_rejected(self):
        from app.cell_fabric import AgentRoleCard

        with pytest.raises(ValidationError):
            AgentRoleCard(**_card_payload(version="v1"))


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------


class TestModelAliasResolution:
    def test_env_var_resolution(self, monkeypatch):
        from app.cell_fabric import resolve_model_alias

        monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "anthropic:claude-3-5-haiku-latest")
        cfg = resolve_model_alias("worker-default")
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-3-5-haiku-latest"

    def test_unset_env_var_falls_back_to_intelligence_from_env(self, monkeypatch):
        from app.cell_fabric import resolve_model_alias
        from app.llm import LLMConfig

        monkeypatch.delenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        cfg = resolve_model_alias("worker-default")
        expected = LLMConfig.intelligence_from_env()
        assert cfg.provider == expected.provider
        assert cfg.model == expected.model

    def test_unknown_provider_fails_closed(self, monkeypatch):
        from app.cell_fabric import CellContractError, resolve_model_alias

        monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "unknownprovider:some-model")
        with pytest.raises(CellContractError):
            resolve_model_alias("worker-default")

    def test_malformed_value_fails_closed(self, monkeypatch):
        from app.cell_fabric import CellContractError, resolve_model_alias

        monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "no-colon-here")
        with pytest.raises(CellContractError):
            resolve_model_alias("worker-default")

    def test_empty_alias_fails_closed(self):
        from app.cell_fabric import CellContractError, resolve_model_alias

        with pytest.raises(CellContractError):
            resolve_model_alias("")

    def test_changing_alias_env_does_not_touch_stored_card(
        self, admin_client, monkeypatch
    ):
        """The whole point of model_alias indirection: changing the real
        model name behind an alias never requires (or implies) a Role Card
        revision -- the stored card row is untouched."""
        from app.cell_fabric import resolve_model_alias

        token = _login(admin_client)
        system = _create_system(admin_client, token, "AliasSys")
        headers = _headers(token, system["id"])
        card = _create_card(admin_client, headers)

        monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "openai:gpt-4o-mini")
        cfg1 = resolve_model_alias(card["model_alias"])
        assert cfg1.model == "gpt-4o-mini"

        monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "anthropic:claude-3-5-haiku-latest")
        cfg2 = resolve_model_alias(card["model_alias"])
        assert cfg2.model == "claude-3-5-haiku-latest"

        # Re-fetch the card: model_alias string itself never changed.
        r = admin_client.get(f"/cell-fabric/role-cards/{card['id']}", headers=headers)
        assert r.json()["model_alias"] == "worker-default"
        assert r.json()["changelog"] == "initial"


# ---------------------------------------------------------------------------
# API: Role Cards
# ---------------------------------------------------------------------------


class TestRoleCardApi:
    def test_create_list_get(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiSys")
        headers = _headers(token, system["id"])

        card = _create_card(admin_client, headers)
        assert card["status"] == "active"
        assert card["decision_method"] == "manual"
        assert card["created_by"] == "root"

        r = admin_client.get("/cell-fabric/role-cards", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["role_cards"]) == 1

        r = admin_client.get("/cell-fabric/role-cards", params={"role_key": "worker-summarizer"}, headers=headers)
        assert len(r.json()["role_cards"]) == 1
        r = admin_client.get("/cell-fabric/role-cards", params={"role_key": "no-such-role"}, headers=headers)
        assert len(r.json()["role_cards"]) == 0

        r = admin_client.get(f"/cell-fabric/role-cards/{card['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["role_key"] == "worker-summarizer"

    def test_get_missing_card_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiMissing")
        headers = _headers(token, system["id"])
        r = admin_client.get("/cell-fabric/role-cards/999999", headers=headers)
        assert r.status_code == 404

    def test_duplicate_version_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiDup")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/role-cards", json=_card_payload(), headers=headers,
        )
        assert r.status_code == 409

    def test_new_version_of_same_role_key_is_allowed(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiNewVer")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/role-cards",
            json=_card_payload(version="1.1.0", changelog="second revision"),
            headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.get("/cell-fabric/role-cards", headers=headers)
        assert len(r.json()["role_cards"]) == 2

    def test_missing_changelog_rejected(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiNoChangelog")
        headers = _headers(token, system["id"])
        payload = _card_payload()
        del payload["changelog"]
        r = admin_client.post("/cell-fabric/role-cards", json=payload, headers=headers)
        assert r.status_code == 422

    def test_unknown_field_rejected_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiUnknown")
        headers = _headers(token, system["id"])
        r = admin_client.post(
            "/cell-fabric/role-cards",
            json=_card_payload(unexpected_field="nope"),
            headers=headers,
        )
        assert r.status_code == 422

    def test_deprecate_transitions_status(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiDeprecate")
        headers = _headers(token, system["id"])
        card = _create_card(admin_client, headers)
        r = admin_client.post(
            f"/cell-fabric/role-cards/{card['id']}/deprecate", headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "deprecated"
        # Content (mission/changelog/etc.) is untouched -- only status moved.
        assert r.json()["changelog"] == "initial"

    def test_deprecate_already_deprecated_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiDeprecateTwice")
        headers = _headers(token, system["id"])
        card = _create_card(admin_client, headers)
        admin_client.post(f"/cell-fabric/role-cards/{card['id']}/deprecate", headers=headers)
        r = admin_client.post(f"/cell-fabric/role-cards/{card['id']}/deprecate", headers=headers)
        assert r.status_code == 409

    def test_versioned_rows_are_append_only(self, admin_client):
        """There is no PATCH/PUT endpoint for role card content -- only
        status may change (deprecate), never mission/changelog/etc."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardApiAppendOnly")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        r = admin_client.put(
            "/cell-fabric/role-cards/1", json=_card_payload(mission="different"),
            headers=headers,
        )
        assert r.status_code in (404, 405)


# ---------------------------------------------------------------------------
# API: Cell Definitions
# ---------------------------------------------------------------------------


class TestCellApi:
    def test_create_requires_active_card(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiNeedsActive")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers, status="draft")
        r = admin_client.post(
            "/cell-fabric/cells", json=_cell_payload(), headers=headers,
        )
        assert r.status_code == 400

    def test_create_missing_card_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiMissingCard")
        headers = _headers(token, system["id"])
        r = admin_client.post(
            "/cell-fabric/cells", json=_cell_payload(), headers=headers,
        )
        assert r.status_code == 404

    def test_create_list_get_worker(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiWorker")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)

        r = admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers)
        assert r.status_code == 201, r.text
        assert r.json()["roster"] is None

        r = admin_client.get("/cell-fabric/cells", headers=headers)
        assert len(r.json()["cells"]) == 1

        r = admin_client.get("/cell-fabric/cells/sys1-summarize-comp", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["definition"]["cell_id"] == "sys1-summarize-comp"
        assert body["state"]["role_card"]["role_key"] == "worker-summarizer"
        assert body["state"]["role_card"]["model_alias"] == "worker-default"
        assert body["state"]["tasks"]["counts"]["todo"] == 0
        # Issue #299 (Sub 2) fills the health block from real Trace/
        # Cell-Activation facts on this same endpoint; with no traces or
        # activations recorded yet every stat is None except queue_length
        # (a real 0-count, not an absent fact).
        assert body["state"]["health"]["heartbeat_at"] is None
        assert body["state"]["health"]["queue_length"] == 0
        assert body["state"]["health"]["error_rate"] is None
        assert body["state"]["orchestration"] is None

    def test_create_orchestrator_with_roster(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiOrch")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)

        # Issue #301's guardrails require every roster member to already
        # exist as a cell_definitions row in the same System.
        for child in ("child-a", "child-b"):
            r = admin_client.post(
                "/cell-fabric/cells",
                json=_cell_payload(cell_id=child),
                headers=headers,
            )
            assert r.status_code == 201, r.text

        r = admin_client.post(
            "/cell-fabric/cells",
            json=_cell_payload(cell_id="orch-1", roster=["child-a", "child-b"]),
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["roster"] == ["child-a", "child-b"]

        r = admin_client.get("/cell-fabric/cells/orch-1", headers=headers)
        state = r.json()["state"]
        assert state["orchestration"]["roster"] == ["child-a", "child-b"]

    def test_duplicate_roster_cell_ids_rejected_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiDupRoster")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/cells",
            json=_cell_payload(cell_id="orch-dup", roster=["a", "a"]),
            headers=headers,
        )
        assert r.status_code == 422

    def test_unique_cell_id_per_system(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiUnique")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers)
        r = admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers)
        assert r.status_code == 409

    def test_get_missing_cell_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiMissingCell")
        headers = _headers(token, system["id"])
        r = admin_client.get("/cell-fabric/cells/no-such-cell", headers=headers)
        assert r.status_code == 404

    def test_unknown_field_rejected_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CellApiUnknownField")
        headers = _headers(token, system["id"])
        _create_card(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/cells",
            json=_cell_payload(unexpected_field="nope"),
            headers=headers,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_role_cards_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "IsoA")
        sys_b = _create_system(admin_client, token, "IsoB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])

        card_a = _create_card(admin_client, headers_a)

        r = admin_client.get("/cell-fabric/role-cards", headers=headers_b)
        assert r.json()["role_cards"] == []

        r = admin_client.get(f"/cell-fabric/role-cards/{card_a['id']}", headers=headers_b)
        assert r.status_code == 404

        # System B can create the SAME role_key/version without conflict.
        r = admin_client.post(
            "/cell-fabric/role-cards", json=_card_payload(), headers=headers_b,
        )
        assert r.status_code == 201

    def test_cells_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "IsoCellA")
        sys_b = _create_system(admin_client, token, "IsoCellB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])

        _create_card(admin_client, headers_a)
        _create_card(admin_client, headers_b)
        admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers_a)

        r = admin_client.get("/cell-fabric/cells", headers=headers_b)
        assert r.json()["cells"] == []

        r = admin_client.get("/cell-fabric/cells/sys1-summarize-comp", headers=headers_b)
        assert r.status_code == 404

        # System B can create the SAME cell_id without conflict.
        r = admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers_b)
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# Migration / additive schema
# ---------------------------------------------------------------------------


class TestMigration:
    def test_new_tables_appear_via_init_db(self, admin_client):
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert "agent_role_cards" in tables
        assert "cell_definitions" in tables

    def test_init_db_idempotent_preserves_existing_populated_db(self, admin_client):
        """init_db() re-run on an already-populated DB (with cell-fabric rows
        already present) does not lose data and does not error."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MigrationSys")
        headers = _headers(token, system["id"])
        card = _create_card(admin_client, headers)
        admin_client.post("/cell-fabric/cells", json=_cell_payload(), headers=headers)

        from app.db import init_db

        init_db()
        init_db()  # idempotency: running twice more must not raise or alter data

        r = admin_client.get(f"/cell-fabric/role-cards/{card['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["changelog"] == "initial"
        r = admin_client.get("/cell-fabric/cells/sys1-summarize-comp", headers=headers)
        assert r.status_code == 200

    def test_older_schema_surface_gains_new_tables_without_data_loss(self, tmp_path, monkeypatch):
        """Simulates an older DB (missing the cell-fabric tables entirely,
        but with pre-existing unrelated data) and asserts init_db() adds the
        new tables additively without touching the pre-existing row."""
        db_path = tmp_path / "pre-cell-fabric.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE systems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                environment TEXT,
                description TEXT,
                owner_user_id INTEGER,
                created_at REAL,
                updated_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at) "
            "VALUES ('Pre-existing System', 'test', 'kept as-is', ?, ?)",
            (time.time(), time.time()),
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        from app.main import app

        with TestClient(app):
            check = sqlite3.connect(db_path)
            check.row_factory = sqlite3.Row
            tables = {r["name"] for r in check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            assert "agent_role_cards" in tables
            assert "cell_definitions" in tables
            row = check.execute(
                "SELECT * FROM systems WHERE name = 'Pre-existing System'"
            ).fetchone()
            assert row is not None
            assert row["description"] == "kept as-is"
            check.close()


# ---------------------------------------------------------------------------
# Shared JSON schema files: structural validation (jsonschema is not an
# installed dependency in this environment -- see pyproject.toml's `dev`
# extra; validate structurally instead per the testing skill's fallback).
# ---------------------------------------------------------------------------


class TestSharedSchemaFiles:
    _SCHEMA_DIR = None

    @classmethod
    def _schema_dir(cls):
        if cls._SCHEMA_DIR is None:
            import os

            here = os.path.dirname(os.path.abspath(__file__))
            cls._SCHEMA_DIR = os.path.normpath(
                os.path.join(here, "..", "..", "..", "shared", "schemas")
            )
        return cls._SCHEMA_DIR

    def _load(self, name):
        import os

        path = os.path.join(self._schema_dir(), name)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_agent_role_card_schema_forbids_additional_properties(self):
        schema = self._load("agent_role_card.schema.json")
        assert schema["additionalProperties"] is False
        assert schema["properties"]["tool_policy"]["additionalProperties"] is False
        assert set(schema["required"]) >= {
            "role_key", "version", "status", "mission", "model_alias",
            "tool_policy", "acceptance_template", "changelog", "schema_version",
        }
        assert schema["properties"]["status"]["enum"] == ["draft", "active", "deprecated"]

    def test_agent_role_card_example_payload_matches_required_shape(self):
        schema = self._load("agent_role_card.schema.json")
        payload = _card_payload()
        assert set(payload) - {"role_key", "version", "status", "mission", "scope",
                                "out_of_scope", "model_alias", "tool_policy",
                                "acceptance_template", "rubric_ref", "changelog",
                                "schema_version"} == set()
        for field in schema["required"]:
            assert field in payload

    def test_cell_definition_schema_forbids_additional_properties_and_has_no_kind_field(self):
        schema = self._load("cell_definition.schema.json")
        assert schema["additionalProperties"] is False
        assert "kind" not in schema["properties"]
        assert "roster" in schema["properties"]
        assert schema["properties"]["status"]["enum"] == ["active", "dormant", "retired"]

    def test_cell_definition_example_payloads_match_required_shape(self):
        schema = self._load("cell_definition.schema.json")
        worker = _cell_payload()
        orchestrator = _cell_payload(cell_id="orch-1", roster=["a", "b"])
        for field in schema["required"]:
            assert field in worker
            assert field in orchestrator

    def test_cell_state_schema_has_additive_extension_blocks(self):
        schema = self._load("cell_state.schema.json")
        assert schema["additionalProperties"] is False
        for block in ("health", "quality", "improvement", "orchestration"):
            assert block in schema["properties"]
            assert schema["properties"][block]["additionalProperties"] is False
        counts = schema["properties"]["tasks"]["properties"]["counts"]
        assert set(counts["required"]) == {
            "todo", "doing", "review", "done", "failed", "blocked",
        }
