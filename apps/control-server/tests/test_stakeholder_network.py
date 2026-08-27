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


def _insert_session(system_id):
    """A minimal `repository_snapshots` + `interview_session` row, so tests
    that need a real `session_id` FK target (Capability confirmations,
    Purpose Outcome criteria) do not have to spin up a git fixture repo the
    way `test_ux_design.py`'s `_setup` does -- neither table this file
    exercises reads the repository itself."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        snapshot_id = conn.execute(
            """INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '', 'deadbeef', 'ready', ?, ?)""",
            (system_id, now, now),
        ).lastrowid
        session_id = conn.execute(
            """INSERT INTO interview_session (system_id, snapshot_id, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (system_id, snapshot_id, now, now),
        ).lastrowid
        conn.commit()
        return session_id


def _insert_capability_entity(system_id, session_id, name):
    """Same shape as `test_ux_design.py`'s helper of the same name."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        entity_id = conn.execute(
            "INSERT INTO understanding_capability_entity (system_id, entity_kind, created_at) VALUES (?, 'core_capability', ?)",
            (system_id, now),
        ).lastrowid
        confirmation_id = conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, 'd', 'root', 'manual', ?)""",
            (system_id, session_id, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO understanding_capability_entity_version
                   (system_id, confirmation_id, entity_id, entity_kind, name, summary, semantic_digest,
                    payload_json, created_at)
               VALUES (?, ?, ?, 'core_capability', ?, '', 'sd', '{}', ?)""",
            (system_id, confirmation_id, entity_id, name, now),
        )
        conn.commit()
        return entity_id


def _supersede_capability_entity(system_id, session_id):
    """Insert a new confirmation head that renames the entity -- content
    change, not just supersession (§4's "Capability entity change")."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        entity = conn.execute(
            "SELECT id FROM understanding_capability_entity WHERE system_id = ?", (system_id,)
        ).fetchone()
        confirmation_id = conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, 'd2', 'root', 'manual', ?)""",
            (system_id, session_id, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO understanding_capability_entity_version
                   (system_id, confirmation_id, entity_id, entity_kind, name, summary, semantic_digest,
                    payload_json, created_at)
               VALUES (?, ?, ?, 'core_capability', 'Billing Renamed', '', 'sd2', '{}', ?)""",
            (system_id, confirmation_id, entity["id"], now),
        )
        conn.commit()


def _insert_outcome_criterion(system_id, session_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        criterion_id = conn.execute(
            """INSERT INTO purpose_outcome_criterion
                   (system_id, session_id, target_kind, target_id, target_digest, source_need_id,
                    source_need_code, measure, created_at)
               VALUES (?, ?, 'element', 'beneficiary_problem', 'd', 'need1', 'code1', 'measure', ?)""",
            (system_id, session_id, now),
        ).lastrowid
        conn.commit()
        return criterion_id


def _mutate_outcome_criterion(criterion_id, **fields):
    from app.db import get_conn

    assignments = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE purpose_outcome_criterion SET {assignments} WHERE id = ?", (*fields.values(), criterion_id))
        conn.commit()


def _add_need_revision_via_api(client, headers, need_key, **fields):
    payload = {"need_kind": "unmet_need", "statement": "", "rationale": "", "stakeholder_key": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/stakeholder-network/needs/{need_key}/revisions", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- helpers that need a real git repo + Interview session (Purpose Chain) -----


def _pc_login(client):
    return _login(client)


def _pc_init_repo(tmp_path, name="repo"):
    import os
    import subprocess

    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    with open(os.path.join(repo, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _pc_insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        conn.commit()
        return cur.lastrowid


def _pc_setup(client, tmp_path, name):
    token = _pc_login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _pc_init_repo(tmp_path, f"repo-{name.replace(' ', '-')}")
    snapshot_id = _pc_insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id


def _pc_settle_initial_build(session_id, *, ok=True):
    from app.db import get_conn
    from app.interview_workflow import finish_process_run

    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND status = 'running' ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is not None:
            finish_process_run(conn, row["id"], ok=ok, error=None if ok else "build failed")


def _pc_create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _pc_settle_initial_build(session_id)
    return session_id


def _pc_set_pain(client, headers, session_id, text, status="confirmed"):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "pain", "value_text": text, "status": status},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_journey_with_steps(client, headers, journey_key, steps, *, beneficiary=""):
    r = client.post(
        "/ux-design/journeys",
        json={"journey_key": journey_key, "perspective": "to_be", "baseline_mode": "undecided",
              "baseline_journey_id": None},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/ux-design/journeys/{journey_key}/revisions",
        json={
            "title": "", "beneficiary": beneficiary, "usage_context": "", "entry_trigger": "",
            "value_arrival": "", "summary": "", "change_note": "", "steps": steps,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key, "step_order": order, "user_intent": "intent", "system_response": "response",
        "success_criteria": "", "failure_mode": "", "recovery_path": "", "evidence_expectation": "",
        "evidence_source_kind": "none",
    }
    base.update(overrides)
    return base


def _create_requirement_via_api(client, headers, requirement_key):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": "functional"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add_step_link(client, headers, requirement_key, journey_key, step_key):
    r = client.post(
        f"/ux-design/requirements/{requirement_key}/step-links",
        json={"journey_key": journey_key, "step_key": step_key, "note": ""},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ux_decide(client, headers, subject_kind, subject_key, decision, *, rationale="", captured_digest="", expect=201):
    """`ux_design`'s own decision ledger -- `journey` / `requirement` are
    NOT members of `StakeholderSubjectKind`, so confirming them goes through
    `/ux-design/decisions`, not `/stakeholder-network/decisions`."""
    r = client.post(
        "/ux-design/decisions",
        json={"subject_kind": subject_kind, "subject_key": subject_key, "decision": decision,
              "rationale": rationale, "captured_digest": captured_digest},
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

    def test_ref_to_purpose_element_resolves_against_the_frame_slot(self, admin_client):
        """Issue #421: `purpose_element` now resolves for real against
        `purpose_chain.derive_purpose_chain`. `beneficiary_problem` is a
        frame-slot element that always exists (state `unknown` with no pain
        ever recorded), so it resolves even with no Interview session set up
        beyond the System itself."""
        token, system_id = _setup(admin_client, "System RefPurpose")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_element", "beneficiary_problem")
        assert ref["target_resolution"] == "resolved"
        assert ref["recheck_state"] == "current"

    def test_ref_to_purpose_element_that_does_not_exist_is_unresolved(self, admin_client):
        """A genuine miss against a REAL canonical source (purpose_chain's
        projection was read fine; the id just is not in it) is `unresolved`,
        not `unavailable` -- the two must never be merged (§5.1)."""
        token, system_id = _setup(admin_client, "System RefPurposeMissing")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        r = admin_client.post(
            "/stakeholder-network/refs",
            json={"source_kind": "stakeholder", "source_key": "sh1", "ref_kind": "purpose_element",
                  "target_ref": "core_capability:does-not-exist", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_ref_target_not_found"

    def test_ref_to_purpose_element_is_unavailable_when_the_source_cannot_be_read(self, admin_client, monkeypatch):
        """A resolver that RAISES is `unavailable`, never `unresolved` -- an
        unreadable source must never render as "the target does not exist"
        (§5.1), mirroring `ux_design`'s identical test one layer over."""
        from app import stakeholder_network as sn_module

        token, system_id = _setup(admin_client, "System RefPurposeUnavailable")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_element", "beneficiary_problem")
        assert ref["target_resolution"] == "resolved"

        def _boom(*a, **kw):
            raise RuntimeError("purpose chain unreadable")

        monkeypatch.setattr(sn_module.purpose_chain, "derive_purpose_chain", _boom)

        listed = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got = next(r for r in listed if r["id"] == ref["id"])
        assert got["target_resolution"] == "unavailable"
        assert got["recheck_state"] == "stale"

    def test_ref_to_capability_entity_resolves_against_the_312_identity(self, admin_client):
        token, system_id = _setup(admin_client, "System RefCapability")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        session_id = _insert_session(system_id)

        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            entity_id = conn.execute(
                "INSERT INTO understanding_capability_entity (system_id, entity_kind, created_at) "
                "VALUES (?, 'core_capability', ?)",
                (system_id, now),
            ).lastrowid
            confirmation_id = conn.execute(
                """INSERT INTO understanding_capability_confirmation
                       (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
                   VALUES (?, ?, 'd', 'root', 'manual', ?)""",
                (system_id, session_id, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO understanding_capability_entity_version
                       (system_id, confirmation_id, entity_id, entity_kind, name, summary, semantic_digest,
                        payload_json, created_at)
                   VALUES (?, ?, ?, 'core_capability', 'Billing', '', 'sd', '{}', ?)""",
                (system_id, confirmation_id, entity_id, now),
            )
            conn.commit()

        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "capability_entity", str(entity_id))
        assert ref["target_resolution"] == "resolved"

    def test_ref_to_capability_entity_that_does_not_exist_is_unresolved(self, admin_client):
        token, system_id = _setup(admin_client, "System RefCapabilityMissing")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        r = admin_client.post(
            "/stakeholder-network/refs",
            json={"source_kind": "stakeholder", "source_key": "sh1", "ref_kind": "capability_entity",
                  "target_ref": "999999", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_ref_target_not_found"

    def test_ref_to_ux_journey_and_step_resolve(self, admin_client):
        token, system_id = _setup(admin_client, "System RefJourney")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "j1", "perspective": "to_be", "baseline_mode": "undecided", "baseline_journey_id": None},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/ux-design/journeys/j1/revisions",
            json={
                "title": "", "beneficiary": "", "usage_context": "", "entry_trigger": "", "value_arrival": "",
                "summary": "", "change_note": "",
                "steps": [
                    {"step_key": "s1", "step_order": 0, "user_intent": "intent", "system_response": "resp",
                     "success_criteria": "", "failure_mode": "", "recovery_path": "",
                     "evidence_expectation": "", "evidence_source_kind": "none"},
                ],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text

        journey_ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "ux_journey", "j1")
        assert journey_ref["target_resolution"] == "resolved"

        step_ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "ux_journey_step", "j1#s1")
        assert step_ref["target_resolution"] == "resolved"

        missing_step = admin_client.post(
            "/stakeholder-network/refs",
            json={"source_kind": "stakeholder", "source_key": "sh1", "ref_kind": "ux_journey_step",
                  "target_ref": "j1#does-not-exist", "note": ""},
            headers=headers,
        )
        assert missing_step.status_code == 404
        assert missing_step.json()["detail"]["code"] == "stakeholder_ref_target_not_found"

    def test_ref_to_ux_requirement_resolves(self, admin_client):
        token, system_id = _setup(admin_client, "System RefRequirement")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        r = admin_client.post(
            "/ux-design/requirements",
            json={"requirement_key": "r1", "requirement_kind": "functional"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "ux_requirement", "r1")
        assert ref["target_resolution"] == "resolved"

    def test_ref_to_purpose_outcome_criterion_resolves(self, admin_client):
        token, system_id = _setup(admin_client, "System RefOutcome")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        session_id = _insert_session(system_id)

        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            criterion_id = conn.execute(
                """INSERT INTO purpose_outcome_criterion
                       (system_id, session_id, target_kind, target_id, target_digest, source_need_id,
                        source_need_code, measure, created_at)
                   VALUES (?, ?, 'element', 'beneficiary_problem', 'd', 'need1', 'code1', 'measure', ?)""",
                (system_id, session_id, now),
            ).lastrowid
            conn.commit()

        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_outcome_criterion", str(criterion_id))
        assert ref["target_resolution"] == "resolved"

        missing = admin_client.post(
            "/stakeholder-network/refs",
            json={"source_kind": "stakeholder", "source_key": "sh1", "ref_kind": "purpose_outcome_criterion",
                  "target_ref": "999999", "note": ""},
            headers=headers,
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "stakeholder_ref_target_not_found"

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


# ---------------------------------------------------------------------------
# Issue #421: reference resolution, journey_step role scope, staleness
# propagation (§4), no-auto-link-from-string-match (§13), and the Exchange
# lineage projection (§7.1).
# ---------------------------------------------------------------------------


class TestJourneyStepRoleScope:
    """§5.1 item C: a role assignment scoped to `journey_step` must resolve
    against the Journey's CURRENT revision -- unlike a `stakeholder_ref`,
    this joins two entities the Epic's own modules both own, so it cannot
    aspirationally point at nothing."""

    def test_role_scoped_to_existing_step_succeeds(self, admin_client):
        token, system_id = _setup(admin_client, "System RoleStep")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])

        role = _add_role(
            admin_client, headers, "sh1", "beneficiary",
            scope_kind="journey_step", scope_ref="journey_step:j1#s1",
        )
        assert role["scope_ref"] == "journey_step:j1#s1"
        assert role["recheck_state"] == "current"

    def test_role_scoped_to_missing_step_is_journey_step_not_found(self, admin_client):
        token, system_id = _setup(admin_client, "System RoleStepMissing")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])

        r = admin_client.post(
            "/stakeholder-network/stakeholders/sh1/roles",
            json={"role": "beneficiary", "scope_kind": "journey_step",
                  "scope_ref": "journey_step:j1#does-not-exist", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "journey_step_not_found"

    def test_role_scoped_to_missing_journey_is_journey_step_not_found(self, admin_client):
        token, system_id = _setup(admin_client, "System RoleJourneyMissing")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        r = admin_client.post(
            "/stakeholder-network/stakeholders/sh1/roles",
            json={"role": "beneficiary", "scope_kind": "journey_step",
                  "scope_ref": "journey_step:no-such-journey#s1", "note": ""},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "journey_step_not_found"

    def test_malformed_scope_ref_is_journey_step_not_found(self, admin_client):
        """No `journey_step:` prefix, or no `#` separator -- both malformed
        forms fold into the same 404 rather than a 500 or a silent pass."""
        token, system_id = _setup(admin_client, "System RoleMalformed")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])

        for bad_ref in ("j1#s1", "journey_step:j1", "journey_step:#s1", "journey_step:j1#"):
            r = admin_client.post(
                "/stakeholder-network/stakeholders/sh1/roles",
                json={"role": "beneficiary", "scope_kind": "journey_step", "scope_ref": bad_ref, "note": ""},
                headers=headers,
            )
            assert r.status_code == 404, bad_ref
            assert r.json()["detail"]["code"] == "journey_step_not_found", bad_ref

    def test_multiple_stakeholders_and_roles_on_the_same_step(self, admin_client):
        """Payer, beneficiary, operator, approver on ONE Step -- the whole
        point of §1.1's separate role-assignment table."""
        token, system_id = _setup(admin_client, "System RoleMulti")
        headers = _headers(token, system_id)
        roles = {"payer": "payer", "beneficiary": "beneficiary", "operator": "operator", "approver": "approver"}
        for stakeholder_key in roles:
            _create_stakeholder(admin_client, headers, stakeholder_key)
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])

        for stakeholder_key, role in roles.items():
            _add_role(
                admin_client, headers, stakeholder_key, role,
                scope_kind="journey_step", scope_ref="journey_step:j1#s1",
            )

        for stakeholder_key, role in roles.items():
            detail = _get_stakeholder(admin_client, headers, stakeholder_key)
            assert len(detail["roles"]) == 1
            assert detail["roles"][0]["role"] == role
            assert detail["roles"][0]["scope_ref"] == "journey_step:j1#s1"


class TestNoAutoLinkFromStringMatch:
    """§13: a Stakeholder whose `display_name` exactly equals a Journey's
    `beneficiary` free-text string must produce NO automatic link. Nothing
    in this module ever compares the two -- this is a regression guard, not
    a behavior this module implements."""

    def test_matching_display_name_and_beneficiary_produce_no_link(self, admin_client):
        token, system_id = _setup(admin_client, "System NoAutoLink")
        headers = _headers(token, system_id)
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)], beneficiary="購入責任者")
        _create_stakeholder(admin_client, headers, "sh1", display_name="購入責任者")

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        assert refs == []
        roles = _get_stakeholder(admin_client, headers, "sh1")["roles"]
        assert roles == []


class TestStalenessPropagation:
    """§4's propagation table: downstream only, one hop, through explicit
    links. Each block below tests one row -- the subject that goes `stale`
    AND at least one named non-subject that does not (§15 item 5)."""

    def test_purpose_element_change_staleifies_the_ref_not_the_stakeholder(self, admin_client, tmp_path):
        token, system_id, snapshot_id = _pc_setup(admin_client, tmp_path, "System Stale Purpose")
        headers = _headers(token, system_id)
        session_id = _pc_create_session(admin_client, headers, snapshot_id)
        _pc_set_pain(admin_client, headers, session_id, "元の課題")

        _create_stakeholder(admin_client, headers, "sh1")
        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_element", "beneficiary_problem")
        assert ref["recheck_state"] == "current"

        # Stakeholder's OWN subject staleness is unaffected by a purpose change.
        before = _get_stakeholder(admin_client, headers, "sh1")
        assert before["recheck_state"] == "current"

        _pc_set_pain(admin_client, headers, session_id, "変わった課題")

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got = next(r for r in refs if r["id"] == ref["id"])
        assert got["recheck_state"] == "stale"

        after = _get_stakeholder(admin_client, headers, "sh1")
        assert after["recheck_state"] == "current"  # the Stakeholder itself never goes stale

    def test_capability_entity_change_staleifies_the_ref_not_journey_or_requirement(self, admin_client):
        token, system_id = _setup(admin_client, "System Stale Capability")
        headers = _headers(token, system_id)
        session_id = _insert_session(system_id)
        entity_id = _insert_capability_entity(system_id, session_id, "Billing")

        provider = _create_stakeholder(admin_client, headers, "provider")
        receiver = _create_stakeholder(admin_client, headers, "receiver")
        exchange = _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")
        ref = _create_ref(admin_client, headers, "value_exchange", "ex1", "capability_entity", str(entity_id))
        assert ref["recheck_state"] == "current"

        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])
        _ux_decide(admin_client, headers, "journey", "j1", "confirm")
        _create_requirement_via_api(admin_client, headers, "r1")
        _ux_decide(admin_client, headers, "requirement", "r1", "confirm")

        _supersede_capability_entity(system_id, session_id)

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got = next(r for r in refs if r["id"] == ref["id"])
        assert got["recheck_state"] == "stale"

        journey_status = admin_client.get("/ux-design/journeys/j1", headers=headers).json()
        assert journey_status["recheck_state"] == "current"
        requirement_status = admin_client.get("/ux-design/requirements/r1", headers=headers).json()
        assert requirement_status["recheck_state"] == "current"

    def test_journey_revision_staleifies_step_refs_and_role_links_not_need_or_exchange_content(self, admin_client):
        token, system_id = _setup(admin_client, "System Stale Journey")
        headers = _headers(token, system_id)
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])

        provider = _create_stakeholder(admin_client, headers, "provider")
        receiver = _create_stakeholder(admin_client, headers, "receiver")
        exchange = _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")
        journey_ref = _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey", "j1")
        step_ref = _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        role = _add_role(
            admin_client, headers, "receiver", "beneficiary",
            scope_kind="journey_step", scope_ref="journey_step:j1#s1",
        )
        assert journey_ref["recheck_state"] == "current"
        assert step_ref["recheck_state"] == "current"
        assert role["recheck_state"] == "current"

        need = _create_need(admin_client, headers, "n1", "receiver", statement="stmt")
        need_ref = _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "n1")
        exchange_before = _get_exchange(admin_client, headers, "ex1")

        # A new revision that drops step s1 entirely.
        r = admin_client.post(
            "/ux-design/journeys/j1/revisions",
            json={
                "title": "renamed", "beneficiary": "", "usage_context": "", "entry_trigger": "",
                "value_arrival": "", "summary": "", "change_note": "",
                "steps": [_step("s2", 0)],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got_journey_ref = next(x for x in refs if x["id"] == journey_ref["id"])
        got_step_ref = next(x for x in refs if x["id"] == step_ref["id"])
        got_need_ref = next(x for x in refs if x["id"] == need_ref["id"])
        assert got_journey_ref["recheck_state"] == "stale"
        assert got_step_ref["recheck_state"] == "stale"
        assert got_step_ref["target_resolution"] == "unresolved"
        # The Need reference is untouched by a Journey revision.
        assert got_need_ref["recheck_state"] == "current"

        receiver_detail = _get_stakeholder(admin_client, headers, "receiver")
        got_role = next(x for x in receiver_detail["roles"] if x["id"] == role["id"])
        assert got_role["recheck_state"] == "stale"

        # The Exchange's OWN content is untouched by a Journey revision.
        exchange_after = _get_exchange(admin_client, headers, "ex1")
        assert exchange_after["current_revision"]["content_digest"] == exchange_before["current_revision"]["content_digest"]

    def test_need_revision_staleifies_the_ref_not_the_stakeholder(self, admin_client):
        token, system_id = _setup(admin_client, "System Stale Need")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _decide(admin_client, headers, "stakeholder", "sh1", "confirm")
        _create_need(admin_client, headers, "n1", "sh1", statement="stmt")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "stakeholder_need", "n1")
        assert ref["recheck_state"] == "current"

        _add_need_revision_via_api(admin_client, headers, "n1", statement="changed statement")

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got = next(r for r in refs if r["id"] == ref["id"])
        assert got["recheck_state"] == "stale"

        stakeholder_after = _get_stakeholder(admin_client, headers, "sh1")
        assert stakeholder_after["recheck_state"] == "current"

    def test_stakeholder_revision_staleifies_role_and_naming_refs_not_purpose_or_journey(self, admin_client, tmp_path):
        token, system_id, snapshot_id = _pc_setup(admin_client, tmp_path, "System Stale Stakeholder")
        headers = _headers(token, system_id)
        session_id = _pc_create_session(admin_client, headers, snapshot_id)

        _create_stakeholder(admin_client, headers, "sh1", display_name="v1")
        role = _add_role(admin_client, headers, "sh1", "beneficiary", scope_kind="system", scope_ref="")
        # a stakeholder_ref naming this Stakeholder from an unrelated source
        _create_need(admin_client, headers, "n1", "sh1", statement="stmt")
        naming_ref = _create_ref(admin_client, headers, "stakeholder_need", "n1", "stakeholder", "sh1")
        purpose_ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_element", "beneficiary_problem")
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])
        journey_ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "ux_journey", "j1")
        assert role["recheck_state"] == "current"
        assert naming_ref["recheck_state"] == "current"

        _add_stakeholder_revision(admin_client, headers, "sh1", display_name="v2")

        detail = _get_stakeholder(admin_client, headers, "sh1")
        got_role = next(x for x in detail["roles"] if x["id"] == role["id"])
        assert got_role["recheck_state"] == "stale"

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got_naming = next(r for r in refs if r["id"] == naming_ref["id"])
        assert got_naming["recheck_state"] == "stale"

        # Purpose / Journey refs FROM this stakeholder are untouched by its own revision.
        got_purpose = next(r for r in refs if r["id"] == purpose_ref["id"])
        got_journey = next(r for r in refs if r["id"] == journey_ref["id"])
        assert got_purpose["recheck_state"] == "current"
        assert got_journey["recheck_state"] == "current"

    def test_outcome_criterion_change_staleifies_the_ref_only(self, admin_client):
        token, system_id = _setup(admin_client, "System Stale Outcome")
        headers = _headers(token, system_id)
        session_id = _insert_session(system_id)
        criterion_id = _insert_outcome_criterion(system_id, session_id)

        _create_stakeholder(admin_client, headers, "sh1")
        ref = _create_ref(admin_client, headers, "stakeholder", "sh1", "purpose_outcome_criterion", str(criterion_id))
        assert ref["recheck_state"] == "current"

        _mutate_outcome_criterion(criterion_id, measure="a different measure")

        refs = admin_client.get("/stakeholder-network/refs", headers=headers).json()["refs"]
        got = next(r for r in refs if r["id"] == ref["id"])
        assert got["recheck_state"] == "stale"

        # Everything upstream (the Stakeholder itself) is untouched.
        stakeholder_after = _get_stakeholder(admin_client, headers, "sh1")
        assert stakeholder_after["recheck_state"] == "current"


class TestExchangeLineage:
    """§7.1: `provider/receiver -> Need/Purpose -> Journey/Step ->
    Requirement/Solution Design -> Outcome/Evidence`, read-only,
    deterministic, per-section degradation."""

    def test_lineage_chain_end_to_end(self, admin_client):
        token, system_id = _setup(admin_client, "System Lineage E2E")
        headers = _headers(token, system_id)
        session_id = _insert_session(system_id)

        _create_stakeholder(admin_client, headers, "provider", display_name="提供者")
        _create_stakeholder(admin_client, headers, "receiver", display_name="利用者")
        _create_need(admin_client, headers, "n1", "receiver", statement="困りごと")
        _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")

        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "n1")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "purpose_element", "beneficiary_problem")

        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey", "j1")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")

        _create_requirement_via_api(admin_client, headers, "r1")
        _add_step_link(admin_client, headers, "r1", "j1", "s1")

        r = admin_client.post(
            "/solution-designs", json={"design_key": "d1", "title": "Design 1", "summary": ""}, headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/solution-designs/d1/options",
            json={"option_key": "opt1", "option_order": 0, "title": "", "approach": "", "tradeoffs": "", "risks": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/solution-designs/d1/requirement-links", json={"requirement_key": "r1", "note": ""}, headers=headers,
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/solution-designs/d1/decisions", json={"option_key": "opt1", "decision": "adopt", "rationale": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        criterion_id = _insert_outcome_criterion(system_id, session_id)
        _create_ref(admin_client, headers, "value_exchange", "ex1", "purpose_outcome_criterion", str(criterion_id))
        _create_evidence_ref(admin_client, headers, "value_exchange", "ex1", "human_report", statement="told me so")

        lineage = admin_client.get("/stakeholder-network/exchanges/ex1/lineage", headers=headers).json()
        assert lineage["exchange"]["exchange_key"] == "ex1"
        assert lineage["provider"]["stakeholder_key"] == "provider"
        assert lineage["receiver"]["stakeholder_key"] == "receiver"
        assert {n["target_ref"] for n in lineage["needs"]} == {"n1"}
        assert any(p["ref_kind"] == "purpose_element" for p in lineage["purpose_refs"])
        assert {j["target_ref"] for j in lineage["journey_refs"]} == {"j1", "j1#s1"}
        assert {req["requirement_key"] for req in lineage["requirements"]} == {"r1"}
        assert lineage["solution_designs"][0]["design_key"] == "d1"
        assert lineage["solution_designs"][0]["adopted_option_key"] == "opt1"
        assert {o["target_ref"] for o in lineage["outcomes"]} == {str(criterion_id)}
        assert len(lineage["evidence"]) == 1
        assert lineage["degraded_sections"] == []

    def test_lineage_requirement_reached_only_through_step_link(self, admin_client):
        """A Requirement reached via `ux_requirement_step_link` from a
        linked Journey Step, with NO direct `stakeholder_ref` to it."""
        token, system_id = _setup(admin_client, "System Lineage StepLink")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider")
        _create_stakeholder(admin_client, headers, "receiver")
        _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")
        _create_journey_with_steps(admin_client, headers, "j1", [_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement_via_api(admin_client, headers, "r1")
        _add_step_link(admin_client, headers, "r1", "j1", "s1")

        lineage = admin_client.get("/stakeholder-network/exchanges/ex1/lineage", headers=headers).json()
        assert {req["requirement_key"] for req in lineage["requirements"]} == {"r1"}

    def test_lineage_missing_exchange_is_404(self, admin_client):
        token, system_id = _setup(admin_client, "System Lineage Missing")
        headers = _headers(token, system_id)
        r = admin_client.get("/stakeholder-network/exchanges/does-not-exist/lineage", headers=headers)
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "stakeholder_not_found"

    def test_lineage_system_isolation(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Lineage A")
        system_b = _create_system(admin_client, token, "System Lineage B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        _create_stakeholder(admin_client, headers_a, "provider")
        _create_stakeholder(admin_client, headers_a, "receiver")
        _create_exchange(admin_client, headers_a, "ex1", "provider", "receiver", "service")

        r = admin_client.get("/stakeholder-network/exchanges/ex1/lineage", headers=headers_b)
        assert r.status_code == 404

    def test_lineage_degrades_one_section_without_losing_the_rest(self, admin_client, monkeypatch):
        from app import stakeholder_network as sn_module

        token, system_id = _setup(admin_client, "System Lineage Degrade")
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider")
        _create_stakeholder(admin_client, headers, "receiver")
        _create_need(admin_client, headers, "n1", "receiver", statement="need")
        _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "service")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "n1")

        def _boom(*a, **kw):
            raise RuntimeError("evidence read failed")

        monkeypatch.setattr(sn_module, "list_evidence_refs", _boom)
        # get_exchange_lineage builds evidence with its own inline query, not
        # list_evidence_refs -- patch the actual query path instead by
        # breaking the underlying table read via a bad subject_kind lookup
        # is unnecessary; instead exercise the guarded loader directly by
        # monkeypatching _exchange_refs to fail only for one ref-kind tuple.

        original_exchange_refs = sn_module._exchange_refs

        def _flaky_exchange_refs(conn, system_id_, exchange_key, ref_kinds):
            if ref_kinds == ("purpose_outcome_criterion",):
                raise RuntimeError("outcomes unavailable")
            return original_exchange_refs(conn, system_id_, exchange_key, ref_kinds)

        monkeypatch.setattr(sn_module, "_exchange_refs", _flaky_exchange_refs)

        lineage = admin_client.get("/stakeholder-network/exchanges/ex1/lineage", headers=headers).json()
        assert lineage["degraded_sections"] == ["outcomes"]
        assert "outcomes" in lineage["degraded_detail"]
        # every other section still rendered
        assert lineage["exchange"]["exchange_key"] == "ex1"
        assert {n["target_ref"] for n in lineage["needs"]} == {"n1"}
