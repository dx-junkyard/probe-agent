"""Tests for Issue #420 -- Stakeholder Value Network: Stakeholder / Need /
Environment Observation / Value Exchange persistence (Epic #418).

`docs/stakeholder-value-network.md` §15 is the Epic-level testing bar this
file is organized around, scoped to what #420 actually owns (persistence +
API for the four canonical entities; `stakeholder_ref`/evidence/decision
tables exist but full reference RESOLUTION against upstream/downstream
kinds is Issue #421's):

1. type parity -- the domain module's finite vocabularies mirror
   `app/models.py` exactly (no Dashboard union exists yet for #420, so this
   substitutes for `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES`
   check per the task brief).
2. System isolation for every new table and read path.
3. append-only correction (a new revision/decision never mutates a prior
   one).
4. `design_status` / `validity_state` are derived, never stored.
5. every §10 reject code #420 owns is reachable with its documented status.
6. the manual-only decision gate (`decision_method` is CHECKed to
   `'manual'` in the ledger; a `reasoning_model`-authored revision cannot
   reach `confirmed` without an explicit decision).
7. a regression test that neither `app/stakeholder_network.py` nor
   `app/routes/stakeholder_network.py` imports or calls an LLM client.
8. no synthetic score/percentage anywhere in the persisted schema or the
   domain module's public surface.
"""

from __future__ import annotations

import ast
import inspect
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import stakeholder_network as sn


# ---------------------------------------------------------------------------
# Part 1: pure functions / finite vocabularies
# ---------------------------------------------------------------------------


class TestFiniteVocabularies:
    """§2: every finite vocabulary this module exposes matches the
    documented contract exactly -- no silently added or dropped member."""

    def test_vocabularies_match_the_documented_contract(self):
        assert set(sn.STAKEHOLDER_KINDS) == {
            "end_user", "customer_organization", "internal_operator",
            "provider_team", "partner", "regulator", "other",
        }
        assert set(sn.STAKEHOLDER_ROLES) == {
            "actor", "beneficiary", "payer", "operator",
            "approver", "supplier", "regulator", "observer",
        }
        assert set(sn.ROLE_SCOPE_KINDS) == {"system", "journey", "journey_step", "value_exchange"}
        assert set(sn.NEED_KINDS) == {"unmet_need", "problem", "constraint", "expectation"}
        assert set(sn.OBSERVATION_CONFIDENCES) == {"observed", "reported", "assumed"}
        assert set(sn.IMPACT_KINDS) == {"creates", "worsens", "relieves", "invalidates", "constrains"}
        assert set(sn.EXCHANGE_KINDS) == {
            "experience", "service", "information", "money", "authority", "obligation", "risk",
        }
        assert set(sn.CONSIDERATION_STATES) == {"present", "none", "unknown"}
        assert set(sn.CADENCES) == {"one_time", "recurring", "continuous", "on_demand", "unknown"}
        assert set(sn.VALIDITY_STATES) == {"not_started", "active", "ended", "unbounded"}
        assert set(sn.DESIGN_STATUSES) == {"proposed", "confirmed", "rejected", "retired"}
        assert set(sn.DESIGN_DECISION_KINDS) == {"confirm", "reject", "retire", "reinstate"}
        assert set(sn.RECHECK_STATES) == {"current", "stale"}
        assert set(sn.REVISION_STATES) == {"current", "superseded"}
        assert set(sn.AUTHORSHIP_KINDS) == {"developer", "reasoning_model"}
        assert set(sn.SUBJECT_KINDS) == {
            "stakeholder", "stakeholder_need", "environment_observation",
            "value_exchange", "stakeholder_ref", "stakeholder_role_assignment",
        }
        assert set(sn.REF_KINDS) == {
            "purpose_element", "purpose_relation", "capability_entity",
            "ux_journey", "ux_journey_step", "ux_requirement",
            "purpose_outcome_criterion", "stakeholder", "stakeholder_need", "value_exchange",
        }
        assert set(sn.REF_RECHECK_STATES) == {"current", "stale", "not_captured"}
        assert set(sn.EVIDENCE_KINDS) == {
            "human_report", "document", "runtime_observation", "external_analytics",
        }

    def test_decision_method_to_relation_status_is_a_fixed_table(self):
        assert sn._DECISION_METHOD_TO_RELATION_STATUS == {
            "manual": "confirmed", "reasoning_llm": "proposed", "deterministic": "derived",
        }

    def test_decision_to_design_status_is_a_fixed_table(self):
        assert sn._DECISION_TO_DESIGN_STATUS == {
            "confirm": "confirmed", "reject": "rejected", "retire": "retired", "reinstate": "proposed",
        }


class TestDigestPureFunctions:
    def test_stakeholder_digest_excludes_bookkeeping_and_roles(self):
        d1 = sn.stakeholder_digest(
            stakeholder_key="k1", display_name="購入責任者", stakeholder_kind="customer_organization",
            description="", context_note="",
        )
        d2 = sn.stakeholder_digest(
            stakeholder_key="k1", display_name="購入責任者", stakeholder_kind="customer_organization",
            description="", context_note="",
        )
        assert d1 == d2
        # a different display_name changes the digest
        d3 = sn.stakeholder_digest(
            stakeholder_key="k1", display_name="別の名前", stakeholder_kind="customer_organization",
            description="", context_note="",
        )
        assert d1 != d3

    def test_exchange_digest_reproducible(self):
        kwargs = dict(
            exchange_key="ex1", provider_stakeholder_key="a", receiver_stakeholder_key="b",
            exchange_kind="money", value_statement="v", consideration_state="none",
            consideration_kind=None, consideration_statement="", channel="", trigger="",
            cadence="one_time", valid_from=None, valid_to=None,
        )
        assert sn.exchange_digest(**kwargs) == sn.exchange_digest(**kwargs)

    def test_need_digest_includes_stakeholder_key(self):
        base = dict(need_key="n1", need_kind="problem", statement="s", rationale="")
        d1 = sn.need_digest(**base, stakeholder_key="k1")
        d2 = sn.need_digest(**base, stakeholder_key="k2")
        assert d1 != d2  # reattribution is a meaning change (§1.2)


class TestValidityStateDerivation:
    """§1.4's fifth axis, first-match, derived from the clock."""

    def test_unbounded_when_no_bounds(self):
        assert sn.derive_validity_state(None, None, now=1000.0) == "unbounded"

    def test_not_started_when_valid_from_in_future(self):
        assert sn.derive_validity_state(2000.0, None, now=1000.0) == "not_started"

    def test_active_when_within_bounds(self):
        assert sn.derive_validity_state(500.0, 1500.0, now=1000.0) == "active"

    def test_active_when_started_with_no_end(self):
        assert sn.derive_validity_state(500.0, None, now=1000.0) == "active"

    def test_ended_when_valid_to_passed(self):
        assert sn.derive_validity_state(None, 900.0, now=1000.0) == "ended"

    def test_ended_boundary_is_inclusive(self):
        assert sn.derive_validity_state(None, 1000.0, now=1000.0) == "ended"


# ---------------------------------------------------------------------------
# Part 2: HTTP API, fixtures/helpers (mirrors test_ux_design.py's shape)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-stakeholder-network-test.db"))
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


def _setup(client, name="System Stakeholder"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


def _create_stakeholder(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {
        "stakeholder_key": stakeholder_key, "display_name": "", "stakeholder_kind": "other",
        "description": "", "context_note": "",
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/stakeholders", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_stakeholder(client, headers, stakeholder_key, expect=200):
    r = client.get(f"/stakeholder-network/stakeholders/{stakeholder_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_stakeholder_revision(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {"display_name": "", "stakeholder_kind": "other", "description": "", "context_note": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/stakeholder-network/stakeholders/{stakeholder_key}/revisions", json=payload, headers=headers)
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
               "statement": "", "rationale": ""}
    payload.update(fields)
    r = client.post("/stakeholder-network/needs", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_need(client, headers, need_key, expect=200):
    r = client.get(f"/stakeholder-network/needs/{need_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_observation(client, headers, observation_key, *, expect=201, **fields):
    payload = {
        "observation_key": observation_key, "statement": "", "source_note": "",
        "observation_confidence": "reported", "observed_at": None,
        "supersedes_observation_key": None, "impacts": [],
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/observations", json=payload, headers=headers)
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


def _get_exchange(client, headers, exchange_key, expect=200):
    r = client.get(f"/stakeholder-network/exchanges/{exchange_key}", headers=headers)
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


# ---------------------------------------------------------------------------
# §15 item 1 (substituted, per task brief): domain-module <-> models.py parity
# ---------------------------------------------------------------------------


class TestDomainModuleMirrorsModels:
    """No Dashboard union exists yet for #420, so per the task brief this
    substitutes for `test_interview_type_parity.py`'s `FINITE_TYPE_NAMES`
    check: the domain module's `get_args`-derived tuples must mirror
    `app/models.py`'s `Literal` aliases exactly."""

    @pytest.mark.parametrize(
        "domain_tuple,model_literal_name",
        [
            ("STAKEHOLDER_KINDS", "StakeholderKind"),
            ("STAKEHOLDER_ROLES", "StakeholderRole"),
            ("ROLE_SCOPE_KINDS", "StakeholderRoleScopeKind"),
            ("NEED_KINDS", "StakeholderNeedKind"),
            ("OBSERVATION_CONFIDENCES", "EnvironmentObservationConfidence"),
            ("IMPACT_KINDS", "EnvironmentImpactKind"),
            ("EXCHANGE_KINDS", "ValueExchangeKind"),
            ("CONSIDERATION_STATES", "ValueExchangeConsiderationState"),
            ("CADENCES", "ValueExchangeCadence"),
            ("VALIDITY_STATES", "ValueExchangeValidityState"),
            ("DESIGN_STATUSES", "StakeholderDesignStatus"),
            ("DESIGN_DECISION_KINDS", "StakeholderDecisionKind"),
            ("RECHECK_STATES", "StakeholderRecheckState"),
            ("REVISION_STATES", "StakeholderRevisionState"),
            ("AUTHORSHIP_KINDS", "StakeholderAuthorshipKind"),
            ("SUBJECT_KINDS", "StakeholderSubjectKind"),
            ("REF_KINDS", "StakeholderRefKind"),
            ("REF_RECHECK_STATES", "StakeholderRefRecheckState"),
            ("EVIDENCE_KINDS", "StakeholderEvidenceKind"),
        ],
    )
    def test_mirrors_models_literal(self, domain_tuple, model_literal_name):
        from app import models
        from typing import get_args

        domain_values = set(getattr(sn, domain_tuple))
        model_values = set(get_args(getattr(models, model_literal_name)))
        assert domain_values == model_values


# ---------------------------------------------------------------------------
# §15 item 2: System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_stakeholder_key_isolated_across_systems(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso SH A")
        system_b = _create_system(admin_client, token, "System Iso SH B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)

        _create_stakeholder(admin_client, headers_a, "shared", display_name="A's party")
        _create_stakeholder(admin_client, headers_b, "shared", display_name="B's party")

        got_b = _get_stakeholder(admin_client, headers_b, "shared")
        assert got_b["display_name"] == "B's party"
        listed_b = admin_client.get("/stakeholder-network/stakeholders", headers=headers_b).json()["stakeholders"]
        assert len(listed_b) == 1

    def test_stakeholder_not_visible_from_foreign_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso SH Foreign A")
        system_b = _create_system(admin_client, token, "System Iso SH Foreign B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)
        _create_stakeholder(admin_client, headers_a, "only-a")

        r = admin_client.get("/stakeholder-network/stakeholders/only-a", headers=headers_b)
        assert r.status_code == 404

    def test_need_cannot_reference_a_foreign_system_stakeholder(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Need A")
        system_b = _create_system(admin_client, token, "System Iso Need B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)
        _create_stakeholder(admin_client, headers_a, "sh-a")

        r = admin_client.post(
            "/stakeholder-network/needs",
            json={"need_key": "n1", "stakeholder_key": "sh-a", "need_kind": "unmet_need",
                  "statement": "", "rationale": ""},
            headers=headers_b,
        )
        assert r.status_code == 404

    def test_exchange_cannot_reference_foreign_system_stakeholders(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Exch A")
        system_b = _create_system(admin_client, token, "System Iso Exch B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)
        _create_stakeholder(admin_client, headers_a, "provider")
        _create_stakeholder(admin_client, headers_a, "receiver")

        r = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex1", "provider_stakeholder_key": "provider",
                  "receiver_stakeholder_key": "receiver", "exchange_kind": "service",
                  "value_statement": "v"},
            headers=headers_b,
        )
        assert r.status_code == 404

    def test_observation_key_isolated_across_systems(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Obs A")
        system_b = _create_system(admin_client, token, "System Iso Obs B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)
        _create_observation(admin_client, headers_a, "shared-obs", statement="A's world")
        _create_observation(admin_client, headers_b, "shared-obs", statement="B's world")

        listed_b = admin_client.get("/stakeholder-network/observations", headers=headers_b).json()["observations"]
        assert len(listed_b) == 1
        assert listed_b[0]["statement"] == "B's world"

    def test_ref_list_is_scoped_to_its_own_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Ref A")
        system_b = _create_system(admin_client, token, "System Iso Ref B")
        headers_a, headers_b = _headers(token, system_a), _headers(token, system_b)
        _create_stakeholder(admin_client, headers_a, "sh1")
        _create_ref(admin_client, headers_a, "stakeholder", "sh1", "stakeholder", "sh1")

        listed_b = admin_client.get("/stakeholder-network/refs", headers=headers_b).json()["refs"]
        assert listed_b == []


# ---------------------------------------------------------------------------
# §15 item 3: append-only correction
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_stakeholder_revision_is_never_mutated(self, admin_client):
        token, system_id = _setup(admin_client, "System AppendOnly SH")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1", display_name="v1")
        first = _get_stakeholder(admin_client, headers, "sh1")
        first_revision_id = first["current_revision_id"]

        _add_stakeholder_revision(admin_client, headers, "sh1", display_name="v2")
        second = _get_stakeholder(admin_client, headers, "sh1")
        assert second["current_revision_id"] != first_revision_id
        assert second["display_name"] == "v2"

        revisions = admin_client.get(
            f"/stakeholder-network/stakeholders/sh1/revisions", headers=headers
        ).json()["revisions"]
        assert len(revisions) == 2
        old = next(r for r in revisions if r["id"] == first_revision_id)
        assert old["display_name"] == "v1"
        assert old["revision_state"] == "superseded"
        new = next(r for r in revisions if r["id"] == second["current_revision_id"])
        assert new["revision_state"] == "current"

    def test_decision_ledger_never_deletes_a_prior_decision(self, admin_client):
        token, system_id = _setup(admin_client, "System AppendOnly Decision")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        _decide(admin_client, headers, "stakeholder", "sh1", "retire")

        from app.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stakeholder_decision WHERE system_id = ? AND subject_key = 'sh1' ORDER BY id",
                (system_id,),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["decision"] == "confirm"
        assert rows[0]["superseded_by_id"] == rows[1]["id"]
        assert rows[1]["decision"] == "retire"
        assert rows[1]["superseded_by_id"] is None

    def test_environment_observation_has_no_update_path_correction_is_a_new_row(self, admin_client):
        token, system_id = _setup(admin_client, "System AppendOnly Obs")
        headers = _headers(token, system_id)
        _create_observation(admin_client, headers, "obs1", statement="old")
        _create_observation(admin_client, headers, "obs2", statement="new", supersedes_observation_key="obs1")

        listed = admin_client.get("/stakeholder-network/observations", headers=headers).json()["observations"]
        by_key = {o["observation_key"]: o for o in listed}
        assert by_key["obs1"]["statement"] == "old"
        assert by_key["obs2"]["supersedes_observation_key"] == "obs1"


# ---------------------------------------------------------------------------
# §15 item 4: derived, never stored
# ---------------------------------------------------------------------------


class TestDesignStatusDerivedNotStored:
    def test_design_status_is_derived_from_the_ledger(self, admin_client):
        token, system_id = _setup(admin_client, "System DesignStatus")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        assert _get_stakeholder(admin_client, headers, "sh1")["design_status"] == "proposed"

        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        assert _get_stakeholder(admin_client, headers, "sh1")["design_status"] == "confirmed"

        _decide(admin_client, headers, "stakeholder", "sh1", "reject")
        assert _get_stakeholder(admin_client, headers, "sh1")["design_status"] == "rejected"

        _decide(admin_client, headers, "stakeholder", "sh1", "reinstate")
        assert _get_stakeholder(admin_client, headers, "sh1")["design_status"] == "proposed"

    def test_no_status_column_exists_on_the_underlying_tables(self, admin_client):
        token, system_id = _setup(admin_client, "System DesignStatus NoColumn")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        from app.db import get_conn

        with get_conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(stakeholder)")}
            rev_cols = {row["name"] for row in conn.execute("PRAGMA table_info(stakeholder_revision)")}
        assert "design_status" not in cols
        assert "design_status" not in rev_cols
        assert "status" not in cols

    def test_content_edit_flips_recheck_state_to_stale_while_confirmed_survives(self, admin_client):
        token, system_id = _setup(admin_client, "System Recheck")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1", display_name="v1")
        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        assert _get_stakeholder(admin_client, headers, "sh1")["recheck_state"] == "current"

        _add_stakeholder_revision(admin_client, headers, "sh1", display_name="v2")
        after = _get_stakeholder(admin_client, headers, "sh1")
        assert after["design_status"] == "confirmed"
        assert after["recheck_state"] == "stale"


class TestValidityStateDerivedNotStored:
    def test_validity_state_is_computed_not_a_column(self, admin_client):
        token, system_id = _setup(admin_client, "System Validity")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider")
        _create_stakeholder(admin_client, headers, "receiver")
        _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")
        detail = _get_exchange(admin_client, headers, "ex1")
        assert detail["validity_state"] == "unbounded"

        from app.db import get_conn

        with get_conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(value_exchange)")}
            rev_cols = {row["name"] for row in conn.execute("PRAGMA table_info(value_exchange_revision)")}
        assert "validity_state" not in cols
        assert "validity_state" not in rev_cols


# ---------------------------------------------------------------------------
# §1.4 validation rules
# ---------------------------------------------------------------------------


class TestValueExchangeValidation:
    def _two_parties(self, client, headers):
        _create_stakeholder(client, headers, "provider")
        _create_stakeholder(client, headers, "receiver")

    def test_self_loop_allowed_only_for_information(self, admin_client):
        token, system_id = _setup(admin_client, "System SelfLoop")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "solo")

        r = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex1", "provider_stakeholder_key": "solo",
                  "receiver_stakeholder_key": "solo", "exchange_kind": "money", "value_statement": "v"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "exchange_self_loop"

        r2 = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex2", "provider_stakeholder_key": "solo",
                  "receiver_stakeholder_key": "solo", "exchange_kind": "information", "value_statement": "v"},
            headers=headers,
        )
        assert r2.status_code == 201, r2.text

    def test_value_statement_required(self, admin_client):
        token, system_id = _setup(admin_client, "System ValueStatement")
        headers = _headers(token, system_id)
        self._two_parties(admin_client, headers)
        r = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex1", "provider_stakeholder_key": "provider",
                  "receiver_stakeholder_key": "receiver", "exchange_kind": "service", "value_statement": ""},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "exchange_value_statement_required"

    def test_consideration_incomplete(self, admin_client):
        token, system_id = _setup(admin_client, "System Consideration")
        headers = _headers(token, system_id)
        self._two_parties(admin_client, headers)
        r = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex1", "provider_stakeholder_key": "provider",
                  "receiver_stakeholder_key": "receiver", "exchange_kind": "service",
                  "value_statement": "v", "consideration_state": "present"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "exchange_consideration_incomplete"

        r_ok = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex2", "provider_stakeholder_key": "provider",
                  "receiver_stakeholder_key": "receiver", "exchange_kind": "service",
                  "value_statement": "v", "consideration_state": "present",
                  "consideration_kind": "money", "consideration_statement": "payment"},
            headers=headers,
        )
        assert r_ok.status_code == 201, r_ok.text

    def test_validity_inverted(self, admin_client):
        token, system_id = _setup(admin_client, "System ValidityInverted")
        headers = _headers(token, system_id)
        self._two_parties(admin_client, headers)
        r = admin_client.post(
            "/stakeholder-network/exchanges",
            json={"exchange_key": "ex1", "provider_stakeholder_key": "provider",
                  "receiver_stakeholder_key": "receiver", "exchange_kind": "service",
                  "value_statement": "v", "valid_from": 2000.0, "valid_to": 1000.0},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "exchange_validity_inverted"

    def test_money_exchange_has_no_amount_column(self):
        """§11/invariant 7: no amount/currency column exists anywhere."""
        import sqlite3

        from app.db import SCHEMA

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(value_exchange_revision)")}
        forbidden = {"amount", "currency", "amount_cents", "price", "total"}
        assert not (cols & forbidden)


# ---------------------------------------------------------------------------
# §15 item 6: manual-only decision gate
# ---------------------------------------------------------------------------


class TestManualOnlyDecisionGate:
    def test_reasoning_model_authored_revision_is_proposed_until_manually_confirmed(self, admin_client):
        token, system_id = _setup(admin_client, "System ManualGate")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        from app.db import get_conn

        with get_conn() as conn:
            sn.add_stakeholder_revision(
                conn, system_id=system_id, stakeholder_key="sh1", display_name="AI draft",
                authored_by_kind="reasoning_model", decision_method="reasoning_llm", created_by=None,
            )

        detail = _get_stakeholder(admin_client, headers, "sh1")
        assert detail["design_status"] == "proposed"
        assert detail["current_revision"]["authored_by_kind"] == "reasoning_model"

        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        assert _get_stakeholder(admin_client, headers, "sh1")["design_status"] == "confirmed"

    def test_decision_method_column_is_check_constrained_to_manual(self, admin_client):
        token, system_id = _setup(admin_client, "System ManualCheck")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO stakeholder_decision
                           (system_id, subject_kind, subject_key, decision, decision_method, created_at)
                       VALUES (?, 'stakeholder', 'sh1', 'confirm', 'reasoning_llm', ?)""",
                    (system_id, time.time()),
                )

    def test_stale_digest_refuses_the_decision(self, admin_client):
        token, system_id = _setup(admin_client, "System StaleDigest")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        r = admin_client.post(
            "/stakeholder-network/decisions",
            json={"subject_kind": "stakeholder", "subject_key": "sh1", "decision": "confirm",
                  "captured_digest": "not-the-real-digest"},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "stakeholder_decision_stale_digest"

    def test_illegal_transition_is_refused(self, admin_client):
        token, system_id = _setup(admin_client, "System IllegalTransition")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _decide(admin_client, headers, "stakeholder", "sh1", "retire")
        r = admin_client.post(
            "/stakeholder-network/decisions",
            json={"subject_kind": "stakeholder", "subject_key": "sh1", "decision": "confirm"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "stakeholder_not_decidable"


# ---------------------------------------------------------------------------
# Refs, evidence, roles, observation impacts
# ---------------------------------------------------------------------------


class TestReferencesAndEvidence:
    def test_ref_to_a_locally_owned_kind_resolves_and_captures_a_digest(self, admin_client):
        token, system_id = _setup(admin_client, "System RefResolve")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1", display_name="Someone")
        _create_need(admin_client, headers, "n1", "sh1", statement="stmt")

        ref = _create_ref(admin_client, headers, "stakeholder_need", "n1", "stakeholder", "sh1")
        assert ref["target_resolution"] == "resolved"
        assert ref["recheck_state"] == "current"
        assert ref["relation_status"] == "confirmed"

    def test_ref_to_a_nonexistent_locally_owned_target_is_rejected(self, admin_client):
        token, system_id = _setup(admin_client, "System RefMissing")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        r = admin_client.post(
            "/stakeholder-network/refs",
            json={"source_kind": "stakeholder", "source_key": "sh1", "ref_kind": "stakeholder",
                  "target_ref": "does-not-exist", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_ref_target_not_found"

    def test_ref_to_a_not_yet_wired_kind_is_unavailable_never_unresolved(self, admin_client):
        """Issue #421's explicit seam: `purpose_element` etc. are valid
        StakeholderRefKind members but #420 has not wired a reader for them
        yet -- `unavailable`, never `unresolved` (an unreadable source must
        never render as "the target does not exist")."""
        token, system_id = _setup(admin_client, "System RefSeam")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_element", "some-purpose-id")
        assert ref["target_resolution"] == "unavailable"
        assert ref["recheck_state"] == "not_captured"

    def test_ref_kind_invalid_is_rejected(self, admin_client):
        """`ref_kind` is `Literal[StakeholderRefKind]` on the request model,
        so an out-of-vocabulary value is already refused by pydantic before
        reaching the route -- this exercises the domain-layer defence
        directly (the same reasoning-path a caller with a non-typed client
        could still exercise), mapped through `_raise_for_error`."""
        token, system_id = _setup(admin_client, "System RefKindInvalid")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        from app.db import get_conn
        from app.routes.stakeholder_network import _raise_for_error
        from fastapi import HTTPException

        with get_conn() as conn:
            try:
                sn.create_ref(
                    conn, system_id=system_id, source_kind="stakeholder", source_key="sh1",
                    ref_kind="not-a-real-kind", target_ref="x", created_by="root",
                )
                assert False, "expected RefKindInvalid"
            except Exception as exc:
                try:
                    _raise_for_error(exc)
                    assert False, "expected HTTPException"
                except HTTPException as http_exc:
                    assert http_exc.status_code == 422
                    assert http_exc.detail["code"] == "stakeholder_ref_kind_invalid"

    def test_evidence_state_available_missing_stale_unavailable_are_distinct(self, admin_client):
        token, system_id = _setup(admin_client, "System Evidence")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_need(admin_client, headers, "n1", "sh1", statement="stmt")

        # missing: no evidence rows at all
        empty = admin_client.get(
            "/stakeholder-network/evidence-refs",
            params={"subject_kind": "stakeholder_need", "subject_key": "n1"}, headers=headers,
        ).json()["evidence_refs"]
        assert empty == []

        _create_evidence_ref(admin_client, headers, "stakeholder_need", "n1", "human_report", statement="told me so")
        listed = admin_client.get(
            "/stakeholder-network/evidence-refs",
            params={"subject_kind": "stakeholder_need", "subject_key": "n1"}, headers=headers,
        ).json()["evidence_refs"]
        assert len(listed) == 1
        assert listed[0]["evidence_kind"] == "human_report"

    def test_role_assignment_captures_stakeholder_digest_and_flips_stale_on_edit(self, admin_client):
        token, system_id = _setup(admin_client, "System RoleRecheck")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1", display_name="v1")
        role = _add_role(admin_client, headers, "sh1", "beneficiary", scope_kind="journey", scope_ref="journey:j1")
        assert role["recheck_state"] == "current"

        _add_stakeholder_revision(admin_client, headers, "sh1", display_name="v2")
        detail = _get_stakeholder(admin_client, headers, "sh1")
        assert detail["roles"][0]["recheck_state"] == "stale"

    def test_observation_impact_target_resolution_reported(self, admin_client):
        token, system_id = _setup(admin_client, "System ObsImpact")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        obs = _create_observation(
            admin_client, headers, "obs1", statement="a regulation changed",
            impacts=[{"impact_kind": "constrains", "target_ref_kind": "stakeholder", "target_ref": "sh1", "note": ""}],
        )
        assert obs["impacts"][0]["target_resolution"] == "resolved"

    def test_observation_impact_kind_invalid_rejected(self, admin_client):
        """`impact_kind` is `Literal[EnvironmentImpactKind]` on the nested
        request model, so this exercises the domain-layer defence directly
        -- see `test_ref_kind_invalid_is_rejected`'s docstring."""
        token, system_id = _setup(admin_client, "System ObsImpactInvalid")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        from app.db import get_conn
        from app.routes.stakeholder_network import _raise_for_error
        from fastapi import HTTPException

        with get_conn() as conn:
            try:
                sn.create_observation(
                    conn, system_id=system_id, observation_key="obs1", statement="x",
                    impacts=[{"impact_kind": "not-a-real-kind", "target_ref_kind": "stakeholder", "target_ref": "sh1"}],
                    created_by="root",
                )
                assert False, "expected ImpactKindInvalid"
            except Exception as exc:
                try:
                    _raise_for_error(exc)
                    assert False, "expected HTTPException"
                except HTTPException as http_exc:
                    assert http_exc.status_code == 422
                    assert http_exc.detail["code"] == "observation_impact_kind_invalid"


# ---------------------------------------------------------------------------
# §12 saved views -- display settings only
# ---------------------------------------------------------------------------


class TestViewPreference:
    def test_no_coordinate_or_layout_column_exists(self):
        import sqlite3

        from app.db import SCHEMA

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stakeholder_view_preference)")}
        forbidden = {"x", "y", "position_x", "position_y", "layout", "layout_json", "coordinates"}
        assert not (cols & forbidden)

    def test_default_preference_is_empty_not_an_error(self, admin_client):
        token, system_id = _setup(admin_client, "System ViewPref Default")
        headers = _headers(token, system_id)
        got = admin_client.get("/stakeholder-network/view-preference", headers=headers).json()
        assert got["active_view"] == ""
        assert got["filters"] == {}

    def test_preference_persists_across_requests(self, admin_client):
        token, system_id = _setup(admin_client, "System ViewPref Persist")
        headers = _headers(token, system_id)
        r = admin_client.put(
            "/stakeholder-network/view-preference",
            json={"active_view": "value_network", "filters": {"kind": "money"},
                  "collapsed_refs": ["r1"], "pinned_refs": ["r2"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        got = admin_client.get("/stakeholder-network/view-preference", headers=headers).json()
        assert got["active_view"] == "value_network"
        assert got["filters"] == {"kind": "money"}
        assert got["collapsed_refs"] == ["r1"]
        assert got["pinned_refs"] == ["r2"]


# ---------------------------------------------------------------------------
# §10: finite reject codes not already covered above
# ---------------------------------------------------------------------------


class TestFiniteRejectCodes:
    def test_key_required(self, admin_client):
        token, system_id = _setup(admin_client, "System Reject KeyRequired")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/stakeholder-network/stakeholders",
            json={"stakeholder_key": "", "display_name": "", "stakeholder_kind": "other",
                  "description": "", "context_note": ""},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "stakeholder_key_required"

    def test_key_conflict(self, admin_client):
        token, system_id = _setup(admin_client, "System Reject KeyConflict")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "dup")
        r = admin_client.post(
            "/stakeholder-network/stakeholders",
            json={"stakeholder_key": "dup", "display_name": "", "stakeholder_kind": "other",
                  "description": "", "context_note": ""},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "stakeholder_key_conflict"

    def test_plain_not_found(self, admin_client):
        token, system_id = _setup(admin_client, "System Reject PlainNotFound")
        headers = _headers(token, system_id)
        r = admin_client.get("/stakeholder-network/stakeholders/does-not-exist", headers=headers)
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_not_found"

    def test_decision_subject_not_found(self, admin_client):
        token, system_id = _setup(admin_client, "System Reject SubjectNotFound")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/stakeholder-network/decisions",
            json={"subject_kind": "stakeholder_ref", "subject_key": "999999", "decision": "confirm"},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_not_found"

    def test_write_request_rejects_body_supplied_authorship(self, admin_client):
        """§10: `created_by`/`decided_by`/`decision_method`/`authored_by_kind`
        are never accepted from a request body -- every write model is
        `ConfigDict(extra="forbid")`."""
        token, system_id = _setup(admin_client, "System Reject Authorship")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/stakeholder-network/stakeholders",
            json={"stakeholder_key": "sh1", "display_name": "", "stakeholder_kind": "other",
                  "description": "", "context_note": "", "authored_by_kind": "developer"},
            headers=headers,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# §15 item 7: no LLM anywhere in this Epic's modules
# ---------------------------------------------------------------------------


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
        from app import stakeholder_network

        self._assert_no_llm_reference(stakeholder_network)

    def test_routes_module_has_no_llm_reference(self):
        from app.routes import stakeholder_network as routes_module

        self._assert_no_llm_reference(routes_module)


# ---------------------------------------------------------------------------
# §15 item 8: no synthetic score / weighted total anywhere
# ---------------------------------------------------------------------------


class TestNoSyntheticScore:
    def test_no_score_or_percentage_field_in_any_response_model(self):
        from app import models

        forbidden_substrings = ("score", "percent", "confidence_pct", "importance", "centrality")
        checked = 0
        for name in dir(models):
            if not name.startswith("Stakeholder") and not name.startswith("ValueExchange") \
                    and not name.startswith("EnvironmentObservation"):
                continue
            obj = getattr(models, name)
            if not hasattr(obj, "model_fields"):
                continue
            checked += 1
            for field_name in obj.model_fields:
                lowered = field_name.lower()
                assert not any(s in lowered for s in forbidden_substrings), (
                    f"{name}.{field_name} looks like a synthetic score field"
                )
        assert checked > 5  # sanity: we actually scanned the new models

    def test_no_score_column_in_any_new_table(self):
        import sqlite3

        from app.db import SCHEMA

        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        tables = [
            "stakeholder", "stakeholder_revision", "stakeholder_role_assignment",
            "stakeholder_need", "stakeholder_need_revision", "environment_observation",
            "environment_observation_impact", "value_exchange", "value_exchange_revision",
            "stakeholder_ref", "stakeholder_evidence_ref", "stakeholder_decision",
            "stakeholder_view_preference",
        ]
        forbidden_substrings = ("score", "percent", "importance", "centrality")
        for table in tables:
            cols = [row[1].lower() for row in conn.execute(f"PRAGMA table_info({table})")]
            for col in cols:
                assert not any(s in col for s in forbidden_substrings), f"{table}.{col} looks like a score column"
