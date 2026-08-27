"""Tests for Issue #422 -- Stakeholder Value Network projection (Epic #418).

`docs/stakeholder-value-network.md` §7.1/§7.2/§15 is the contract this file
verifies:

1. total/stable ordering for nodes and edges (§7.1).
2. every §7.2 notice code is reachable.
3. per-section degradation (`degraded_sections`) is independent -- injecting
   a failure into one section leaves the others intact.
4. System isolation.
5. no coordinate/layout field anywhere in the response (invariant 10).
6. no score/percentage/centrality field anywhere in the response (invariant 7).
7. no LLM import or call anywhere in this module or its route.
8. a fixture where payer != beneficiary, a feedback edge exists, and
   money/service flow in both directions.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from app import stakeholder_value_network as svn


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirrors tests/test_stakeholder_network.py's own, kept
# local since #422 owns this test file exclusively)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-value-network-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

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


def _setup(client, name="System Value Network"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


def _create_stakeholder(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {
        "stakeholder_key": stakeholder_key, "display_name": stakeholder_key, "stakeholder_kind": "other",
        "description": "", "context_note": "",
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/stakeholders", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_role(client, headers, stakeholder_key, role, *, scope_kind="system", scope_ref="", note="", expect=201):
    r = client.post(
        f"/stakeholder-network/stakeholders/{stakeholder_key}/roles",
        json={"role": role, "scope_kind": scope_kind, "scope_ref": scope_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_need(client, headers, need_key, stakeholder_key, *, expect=201, **fields):
    payload = {"need_key": need_key, "stakeholder_key": stakeholder_key, "need_kind": "unmet_need",
               "statement": "statement", "rationale": ""}
    payload.update(fields)
    r = client.post("/stakeholder-network/needs", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_exchange(client, headers, exchange_key, provider, receiver, exchange_kind, *, expect=201, **fields):
    payload = {
        "exchange_key": exchange_key, "provider_stakeholder_key": provider,
        "receiver_stakeholder_key": receiver, "exchange_kind": exchange_kind,
        "value_statement": "value", "consideration_state": "unknown", "consideration_kind": None,
        "consideration_statement": "", "channel": "", "trigger": "", "cadence": "unknown",
        "valid_from": None, "valid_to": None,
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/exchanges", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_ref(client, headers, source_kind, source_key, ref_kind, target_ref, *, note="", expect=201):
    r = client.post(
        "/stakeholder-network/refs",
        json={"source_kind": source_kind, "source_key": source_key, "ref_kind": ref_kind,
              "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_evidence_ref(client, headers, subject_kind, subject_key, evidence_kind, *, evidence_ref="", statement="", expect=201):
    r = client.post(
        "/stakeholder-network/evidence-refs",
        json={"subject_kind": subject_kind, "subject_key": subject_key, "evidence_kind": evidence_kind,
              "evidence_ref": evidence_ref, "statement": statement},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide(client, headers, subject_kind, subject_key, decision, *, rationale="", captured_digest="", expect=201):
    r = client.post(
        "/stakeholder-network/decisions",
        json={"subject_kind": subject_kind, "subject_key": subject_key, "decision": decision,
              "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_value_network(client, headers, expect=200):
    r = client.get("/stakeholder-value-network", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1 + 8: an E2E-shaped fixture -- 利用者(user) / 購入責任者(payer) /
# 運用担当者(operator) / 提供者(provider), payer != beneficiary, a feedback
# edge, money+service flowing both directions.
# ---------------------------------------------------------------------------


def _build_fixture(client, headers):
    _create_stakeholder(client, headers, "user", display_name="利用者", stakeholder_kind="end_user")
    _create_stakeholder(client, headers, "payer", display_name="購入責任者", stakeholder_kind="customer_organization")
    _create_stakeholder(client, headers, "operator", display_name="運用担当者", stakeholder_kind="internal_operator")
    _create_stakeholder(client, headers, "provider", display_name="提供者", stakeholder_kind="provider_team")

    _add_role(client, headers, "user", "beneficiary")
    _add_role(client, headers, "payer", "payer")
    _add_role(client, headers, "operator", "operator")
    _add_role(client, headers, "provider", "supplier")

    _create_need(client, headers, "need-1", "user", statement="使いやすくしてほしい")

    # provider -> user: service (payer != beneficiary of the service)
    _create_exchange(
        client, headers, "svc-1", "provider", "user", "service",
        value_statement="運用サービスを提供する",
    )
    # payer -> provider: money (the payer pays, not the user)
    _create_exchange(
        client, headers, "money-1", "payer", "provider", "money",
        value_statement="利用料を支払う",
    )
    # user -> provider: information (feedback path for provider's service to user)
    _create_exchange(
        client, headers, "info-1", "user", "provider", "information",
        value_statement="利用状況を報告する",
    )
    # provider -> operator: obligation
    _create_exchange(
        client, headers, "obl-1", "provider", "operator", "obligation",
        value_statement="SLAを遵守する",
    )
    return {
        "user": "user", "payer": "payer", "operator": "operator", "provider": "provider",
        "svc": "svc-1", "money": "money-1", "info": "info-1", "obl": "obl-1",
    }


class TestOrdering:
    def test_nodes_and_edges_are_totally_and_stably_ordered(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)

        first = _get_value_network(admin_client, headers)
        second = _get_value_network(admin_client, headers)

        node_keys_1 = [n["stakeholder_key"] for n in first["nodes"]]
        node_keys_2 = [n["stakeholder_key"] for n in second["nodes"]]
        assert node_keys_1 == node_keys_2
        expected_node_order = sorted(
            first["nodes"], key=lambda n: (n["display_name"], n["stakeholder_key"])
        )
        assert first["nodes"] == expected_node_order

        edge_keys_1 = [e["exchange_key"] for e in first["edges"]]
        edge_keys_2 = [e["exchange_key"] for e in second["edges"]]
        assert edge_keys_1 == edge_keys_2
        expected_edge_order = sorted(
            first["edges"],
            key=lambda e: (
                e["provider_stakeholder_key"], e["receiver_stakeholder_key"],
                e["exchange_kind"] or "", e["exchange_key"],
            ),
        )
        assert first["edges"] == expected_edge_order

    def test_notices_are_totally_ordered(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        notices = result["notices"]
        expected = sorted(notices, key=lambda n: (n["code"], n["subject_kind"], n["subject_key"]))
        assert notices == expected


class TestNoticeCodesReachable:
    """Every §7.2 code is reachable by some fixture."""

    def test_payer_differs_from_beneficiary(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "payer_differs_from_beneficiary" and n["subject_key"] == keys["svc"]
        ]
        assert matches, result["notices"]

    def test_feedback_path_present_means_no_feedback_path_missing_for_provider(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        provider_feedback_missing = [
            n for n in result["notices"]
            if n["code"] == "feedback_path_missing" and n["subject_key"] == keys["provider"]
        ]
        assert not provider_feedback_missing

    def test_feedback_path_missing_when_no_information_returns(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "p1")
        _create_stakeholder(admin_client, headers, "r1")
        _create_exchange(admin_client, headers, "ex-1", "p1", "r1", "service")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "feedback_path_missing" and n["subject_key"] == "p1"
        ]
        assert matches

    def test_stakeholder_without_exchange(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "lonely")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "stakeholder_without_exchange" and n["subject_key"] == "lonely"
        ]
        assert matches

    def test_stakeholder_without_role(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "roleless")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "stakeholder_without_role" and n["subject_key"] == "roleless"
        ]
        assert matches

    def test_stakeholder_without_need(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "stakeholder_without_need" and n["subject_key"] == keys["provider"]
        ]
        assert matches  # provider has no Need attributed to it

    def test_exchange_without_need_journey_outcome(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        for code in ("exchange_without_need", "exchange_without_journey", "exchange_without_outcome"):
            matches = [
                n for n in result["notices"]
                if n["code"] == code and n["subject_key"] == keys["svc"]
            ]
            assert matches, (code, result["notices"])

    def test_exchange_without_need_clears_once_referenced(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        _create_ref(admin_client, headers, "value_exchange", keys["svc"], "stakeholder_need", "need-1")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "exchange_without_need" and n["subject_key"] == keys["svc"]
        ]
        assert not matches

    def test_confirmed_without_evidence_for_exchange(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        _decide(admin_client, headers, "value_exchange", keys["svc"], "confirm")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "confirmed_without_evidence" and n["subject_key"] == keys["svc"]
        ]
        assert matches

    def test_confirmed_without_evidence_clears_once_evidence_attached(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        _create_evidence_ref(admin_client, headers, "value_exchange", keys["svc"], "human_report", statement="observed")
        _decide(admin_client, headers, "value_exchange", keys["svc"], "confirm")
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "confirmed_without_evidence" and n["subject_key"] == keys["svc"]
        ]
        assert not matches

    def test_stale_confirmation_and_stale_link(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        _decide(admin_client, headers, "value_exchange", keys["svc"], "confirm")
        # Change the exchange's content -> its captured_digest goes stale.
        admin_client.post(
            f"/stakeholder-network/exchanges/{keys['svc']}/revisions",
            json={
                "provider_stakeholder_key": "provider", "receiver_stakeholder_key": "user",
                "exchange_kind": "service", "value_statement": "changed statement",
                "consideration_state": "unknown", "consideration_kind": None,
                "consideration_statement": "", "channel": "", "trigger": "", "cadence": "unknown",
                "valid_from": None, "valid_to": None, "change_note": "",
            },
            headers=headers,
        )
        result = _get_value_network(admin_client, headers)
        stale_confirmations = [
            n for n in result["notices"]
            if n["code"] == "stale_confirmation" and n["subject_key"] == keys["svc"]
        ]
        assert stale_confirmations

    def test_stale_link_on_exchange_ref(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        keys = _build_fixture(admin_client, headers)
        _create_ref(admin_client, headers, "value_exchange", keys["svc"], "stakeholder_need", "need-1")
        # Change the Need's content so the ref's captured_digest goes stale.
        admin_client.post(
            f"/stakeholder-network/needs/need-1/revisions",
            json={
                "need_kind": "unmet_need", "statement": "changed", "rationale": "",
                "stakeholder_key": "user", "change_note": "",
            },
            headers=headers,
        )
        result = _get_value_network(admin_client, headers)
        matches = [
            n for n in result["notices"]
            if n["code"] == "stale_link" and n["subject_key"] == keys["svc"]
        ]
        assert matches


class TestPerSectionDegradation:
    def test_edges_failure_does_not_take_down_nodes(self, admin_client, monkeypatch):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)

        def _boom(conn, system_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(svn.sn, "list_exchanges", _boom)
        result = _get_value_network(admin_client, headers)
        assert "edges" in result["degraded_sections"]
        assert "notices" in result["degraded_sections"]
        assert "nodes" not in result["degraded_sections"]
        assert result["nodes"], "nodes should still be present when only edges fails"
        assert result["edges"] == []

    def test_nodes_failure_does_not_take_down_edges(self, admin_client, monkeypatch):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)

        def _boom(conn, system_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(svn.sn, "list_stakeholders", _boom)
        result = _get_value_network(admin_client, headers)
        assert "nodes" in result["degraded_sections"]
        assert "notices" in result["degraded_sections"]
        assert "edges" not in result["degraded_sections"]
        assert result["edges"], "edges should still be present when only nodes fails"
        assert result["nodes"] == []


class TestSystemIsolation:
    def test_value_network_is_scoped_to_its_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System A")
        system_b = _create_system(admin_client, token, "System B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _build_fixture(admin_client, headers_a)

        result_a = _get_value_network(admin_client, headers_a)
        result_b = _get_value_network(admin_client, headers_b)

        assert result_a["nodes"]
        assert result_a["edges"]
        assert result_b["nodes"] == []
        assert result_b["edges"] == []
        assert result_b["notices"] == []


class TestNoCoordinateField:
    FORBIDDEN_KEYS = {"x", "y", "layout", "position", "pos_x", "pos_y", "coordinates"}

    def _walk(self, value, path=""):
        if isinstance(value, dict):
            for key, sub in value.items():
                assert key not in self.FORBIDDEN_KEYS, f"coordinate-shaped field at {path}.{key}"
                self._walk(sub, f"{path}.{key}")
        elif isinstance(value, list):
            for i, sub in enumerate(value):
                self._walk(sub, f"{path}[{i}]")

    def test_no_coordinate_field_anywhere_in_response(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        self._walk(result)


class TestNoSyntheticScore:
    FORBIDDEN_SUBSTRINGS = ("score", "percent", "confidence_pct", "importance", "centrality", "ranking")

    def _walk(self, value, path=""):
        if isinstance(value, dict):
            for key, sub in value.items():
                lowered = key.lower()
                assert not any(s in lowered for s in self.FORBIDDEN_SUBSTRINGS), (
                    f"synthetic-score-shaped field at {path}.{key}"
                )
                self._walk(sub, f"{path}.{key}")
        elif isinstance(value, list):
            for i, sub in enumerate(value):
                self._walk(sub, f"{path}[{i}]")

    def test_no_score_field_anywhere_in_response(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _build_fixture(admin_client, headers)
        result = _get_value_network(admin_client, headers)
        self._walk(result)

    def test_no_score_field_in_response_models(self):
        from app import models

        checked = 0
        for name in ("ValueNetworkNodeOut", "ValueNetworkEdgeOut", "ValueNetworkNoticeOut", "ValueNetworkOut"):
            obj = getattr(models, name)
            checked += 1
            for field_name in obj.model_fields:
                lowered = field_name.lower()
                assert not any(s in lowered for s in self.FORBIDDEN_SUBSTRINGS), (
                    f"{name}.{field_name} looks like a synthetic score field"
                )
        assert checked == 4


class TestNoLLMImportOrCall:
    def _module_source(self, module) -> str:
        return inspect.getsource(module)

    def _assert_no_llm_reference(self, module) -> None:
        source = self._module_source(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "llm" not in alias.name.lower(), f"unexpected LLM import: {alias.name}"
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "llm" not in mod.lower(), f"unexpected LLM import: {mod}"
                for alias in node.names:
                    assert "create_llm_client" not in alias.name, f"unexpected LLM import: {alias.name}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "create_llm_client"
            if isinstance(node, ast.Name):
                assert node.id != "create_llm_client"

    def test_domain_module_has_no_llm_reference(self):
        from app import stakeholder_value_network

        self._assert_no_llm_reference(stakeholder_value_network)

    def test_routes_module_has_no_llm_reference(self):
        from app.routes import stakeholder_value_network as routes_module

        self._assert_no_llm_reference(routes_module)


class TestEmptySystemNeverErrors:
    def test_system_with_no_stakeholders_returns_empty_projection(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        result = _get_value_network(admin_client, headers)
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["notices"] == []
        assert result["degraded_sections"] == []
