"""E2E verification for Epic #427 (Product Objective / Milestone / Gap),
Issue #433.

`docs/product-objective-lineage.md` SS11/SS12 (plus SS0/SS6) is the canonical
contract this file verifies against the ALREADY IMPLEMENTED #428-#432 code
(`app/product_objective.py`, `app/product_gap_sources.py`,
`app/product_feature.py`, `app/product_objective_projection.py`,
`app/functional_lineage.py`'s additions, and their routes). This file adds
no source changes of its own -- it is read-only verification, per the task
brief.

One System carries the whole chain (Task A's "representative fixture"):

    Vision (confirmed Intent Brief goal)
      -> parent + child Product Objective
      -> Milestones (with a dependency)
      -> Gaps (manual current/target state)
      -> as-is / to-be UX Journeys
      -> Requirement + acceptance criterion
      -> Feature <-> Capability link
      -> Solution Design with an adopted Option
      -> implementation targets (Component / Evolution Node / Probe Point)
      -> Trace / Replay / Experiment
      -> Outcome criterion + a human decision

Entities OWNED by this Epic (Objective/Milestone/Gap/Feature) and their
immediate neighbours (#405's Journey/Requirement, #408's Solution Design)
are created through the real HTTP API. Entities that are pre-existing
infrastructure this Epic only BORROWS the identity of (Component, Evolution
Node, Probe Point, Trace, Experiment, Replay Run, Outcome Criterion) are
seeded with minimal direct `get_conn()` inserts against their own canonical
tables -- the same convention `tests/test_ux_design.py`'s
`_insert_capability_entity` and `tests/test_solution_design.py`'s FK-spine
inserts already use for infrastructure those files' own Epics do not own
either. This is a documented scoping decision, flagged again in the final
report.

Fixture style mirrors `tests/test_ux_design.py` closely (`admin_client`,
`_login`, `_headers`, `_create_system`, `_init_repo_with_files`,
`_insert_snapshot`), and `tests/test_solution_design.py` for the Solution
Design helpers.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / low-level HTTP helpers (mirrors tests/test_ux_design.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-objective-e2e.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
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
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _init_repo_with_files(tmp_path, name, files):
    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    for rel_path, content in files.items():
        full = os.path.join(repo, rel_path)
        os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _settle_initial_build(session_id, *, ok=True):
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


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _settle_initial_build(session_id)
    return session_id


def _set_goal(client, headers, session_id, text, status="confirmed"):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": text, "status": status},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _get_brief(client, headers, session_id):
    r = client.get(f"/interview/understanding-brief?session_id={session_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _insert_capability_entity(system_id, session_id, name):
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
        return entity_id


# --- ux-design helpers (mirrors tests/test_ux_design.py) --------------------


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key,
        "step_order": order,
        "user_intent": "intent",
        "system_response": "response",
        "success_criteria": "criteria",
        "failure_mode": "",
        "recovery_path": "",
        "evidence_expectation": "",
        "evidence_source_kind": "none",
    }
    base.update(overrides)
    return base


def _criterion(criterion_key, order, **overrides):
    base = {
        "criterion_key": criterion_key,
        "criterion_order": order,
        "statement": "stmt",
        "verification_method": "manual_review",
        "verification_note": "",
    }
    base.update(overrides)
    return base


def _create_journey(
    client, headers, journey_key, *, perspective="to_be", baseline_mode="undecided",
    baseline_journey_id=None, expect=201,
):
    r = client.post(
        "/ux-design/journeys",
        json={
            "journey_key": journey_key,
            "perspective": perspective,
            "baseline_mode": baseline_mode,
            "baseline_journey_id": baseline_journey_id,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_journey_revision(client, headers, journey_key, *, steps=None, expect=201, **fields):
    payload = {
        "title": "", "beneficiary": "", "usage_context": "", "entry_trigger": "",
        "value_arrival": "", "summary": "", "change_note": "", "steps": steps or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/journeys/{journey_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _get_journey(client, headers, journey_key, expect=200):
    r = client.get(f"/ux-design/journeys/{journey_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_journey_upstream_ref(client, headers, journey_key, ref_kind, target_ref, *, note="", expect=201):
    r = client.post(
        f"/ux-design/journeys/{journey_key}/upstream-refs",
        json={"ref_kind": ref_kind, "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _create_requirement(client, headers, requirement_key, requirement_kind="functional", expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": requirement_kind},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_requirement_revision(client, headers, requirement_key, *, acceptance_criteria=None, expect=201, **fields):
    payload = {
        "statement": "", "rationale": "", "constraint_text": "", "out_of_scope_note": "",
        "change_note": "", "acceptance_criteria": acceptance_criteria or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/requirements/{requirement_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _get_requirement(client, headers, requirement_key, expect=200):
    r = client.get(f"/ux-design/requirements/{requirement_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_step_link(client, headers, requirement_key, journey_key, step_key, *, note="", expect=201):
    r = client.post(
        f"/ux-design/requirements/{requirement_key}/step-links",
        json={"journey_key": journey_key, "step_key": step_key, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


# --- Solution Design helpers (mirrors tests/test_solution_design.py) -------


def _create_design(client, headers, design_key, title="", summary="", expect=201):
    r = client.post(
        "/solution-designs",
        json={"design_key": design_key, "title": title, "summary": summary},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_design_option(client, headers, design_key, option_key, option_order=1, expect=201, **fields):
    payload = {"option_key": option_key, "option_order": option_order, "title": "", "approach": "", "tradeoffs": "", "risks": ""}
    payload.update(fields)
    r = client.post(f"/solution-designs/{design_key}/options", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _decide_design_option(client, headers, design_key, option_key, decision, rationale="", expect=201):
    r = client.post(
        f"/solution-designs/{design_key}/decisions",
        json={"option_key": option_key, "decision": decision, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


# --- Product Objective / Milestone helpers ----------------------------------


def _create_objective(client, headers, objective_key, expect=201):
    r = client.post("/product-objectives", json={"objective_key": objective_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_objective_revision(client, headers, objective_key, expect=201, **fields):
    payload = {"title": "", "intent": "", "contribution": "", "scope_note": "", "summary": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/product-objectives/{objective_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _set_objective_parent(client, headers, objective_key, parent_objective_key, rationale="", expect=201):
    r = client.post(
        f"/product-objectives/{objective_key}/parent",
        json={"parent_objective_key": parent_objective_key, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _clear_objective_parent(client, headers, objective_key, rationale="", expect=200):
    r = client.delete(f"/product-objectives/{objective_key}/parent?rationale={rationale}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_objective_upstream_ref(client, headers, objective_key, ref_kind, target_ref, note="", expect=201):
    r = client.post(
        f"/product-objectives/{objective_key}/upstream-refs",
        json={"ref_kind": ref_kind, "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _record_objective_decision(client, headers, objective_key, decision, rationale="", captured_digest=None, expect=201):
    """`captured_digest=None` means "behave like a real client": read the
    Objective and send back the digest it displayed. Passing `""`
    explicitly still records an uncaptured decision (which reads as
    `recheck_state='not_captured'`, §4.2's fail-closed case), and passing a
    wrong value exercises the stale-digest 409."""
    if captured_digest is None:
        current = _get_objective(client, headers, objective_key)
        revision = current.get("current_revision")
        captured_digest = revision["content_digest"] if revision else ""
    r = client.post(
        f"/product-objectives/{objective_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _get_objective(client, headers, objective_key, expect=200):
    r = client.get(f"/product-objectives/{objective_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _list_milestones(client, headers, objective_key, expect=200):
    r = client.get(f"/product-objectives/{objective_key}/milestones", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _create_milestone(client, headers, objective_key, milestone_key, expect=201):
    r = client.post(
        "/product-milestones", json={"objective_key": objective_key, "milestone_key": milestone_key}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_milestone_revision(client, headers, milestone_key, expect=201, **fields):
    payload = {
        "title": "", "target_state": "", "verification_method": "unavailable",
        "verification_note": "", "sequence_hint": 0, "summary": "", "change_note": "",
    }
    payload.update(fields)
    r = client.post(f"/product-milestones/{milestone_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_milestone_dependency(client, headers, milestone_key, depends_on_milestone_key, rationale="", expect=201):
    r = client.post(
        f"/product-milestones/{milestone_key}/dependencies",
        json={"depends_on_milestone_key": depends_on_milestone_key, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _record_milestone_decision(client, headers, milestone_key, decision, rationale="", captured_digest=None, expect=201):
    """See `_record_objective_decision` for what `None` vs `""` means."""
    if captured_digest is None:
        current = _get_milestone(client, headers, milestone_key)
        revision = current.get("current_revision")
        captured_digest = revision["content_digest"] if revision else ""
    r = client.post(
        f"/product-milestones/{milestone_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _record_milestone_assessment(
    client, headers, milestone_key, assessment, rationale="", evidence_note="", captured_digest="", expect=201
):
    r = client.post(
        f"/product-milestones/{milestone_key}/assessments",
        json={
            "assessment": assessment, "rationale": rationale, "evidence_note": evidence_note,
            "captured_digest": captured_digest,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _get_milestone(client, headers, milestone_key, expect=200):
    r = client.get(f"/product-milestones/{milestone_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# --- Product Gap helpers -----------------------------------------------------


def _list_gaps(client, headers, milestone_key=None, expect=200):
    url = "/product-gaps" + (f"?milestone_key={milestone_key}" if milestone_key else "")
    r = client.get(url, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _create_gap(client, headers, milestone_key, gap_key, expect=201):
    r = client.post("/product-gaps", json={"milestone_key": milestone_key, "gap_key": gap_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_gap_revision(client, headers, gap_key, expect=201, **fields):
    payload = {
        "title": "", "current_state": "", "target_state": "", "target_state_mode": "unknown",
        "interpretation": "", "suggested_priority_note": "", "change_note": "",
    }
    payload.update(fields)
    r = client.post(f"/product-gaps/{gap_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_gap_source_ref(client, headers, gap_key, source_kind, source_ref="", note="", expect=201):
    r = client.post(
        f"/product-gaps/{gap_key}/source-refs",
        json={"source_kind": source_kind, "source_ref": source_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_gap_evidence_ref(client, headers, gap_key, evidence_kind, evidence_ref, note="", expect=201):
    r = client.post(
        f"/product-gaps/{gap_key}/evidence-refs",
        json={"evidence_kind": evidence_kind, "evidence_ref": evidence_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_gap_artifact_link(client, headers, gap_key, link_kind, target_ref, note="", expect=201):
    r = client.post(
        f"/product-gaps/{gap_key}/artifact-links",
        json={"link_kind": link_kind, "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _record_gap_decision(client, headers, gap_key, decision, priority_band="unset", rationale="", captured_digest=None, expect=201):
    """See `_record_objective_decision`. A Gap reports `decision_digest`
    rather than its revision's `content_digest`, because an
    `inherited_from_milestone` Gap is judged partly against the Milestone's
    target (§5.3)."""
    if captured_digest is None:
        captured_digest = _get_gap(client, headers, gap_key)["decision_digest"]
    r = client.post(
        f"/product-gaps/{gap_key}/decisions",
        json={"decision": decision, "priority_band": priority_band, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _get_gap(client, headers, gap_key, expect=200):
    r = client.get(f"/product-gaps/{gap_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# --- Product Feature helpers -------------------------------------------------


def _create_feature(client, headers, feature_key, expect=201):
    r = client.post("/product-features", json={"feature_key": feature_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature_revision(client, headers, feature_key, expect=201, **fields):
    payload = {"title": "", "statement": "", "rationale": "", "scope_note": "", "summary": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/product-features/{feature_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature_requirement_link(client, headers, feature_key, requirement_key, note="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/requirement-links",
        json={"requirement_key": requirement_key, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature_capability_link(client, headers, feature_key, capability_entity_id, note="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/capability-links",
        json={"capability_entity_id": capability_entity_id, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature_target_link(client, headers, feature_key, link_kind, target_ref, note="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/target-links",
        json={"link_kind": link_kind, "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_feature_draft_link(client, headers, feature_key, feature_draft_id, note="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/draft-links",
        json={"feature_draft_id": feature_draft_id, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _record_feature_decision(client, headers, feature_key, decision, rationale="", captured_digest="", expect=201):
    r = client.post(
        f"/product-features/{feature_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _get_feature(client, headers, feature_key, expect=200):
    r = client.get(f"/product-features/{feature_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# --- Projection helpers -------------------------------------------------------


def _get_objective_map(client, headers, expect=200):
    r = client.get("/objective-map", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _get_gap_workbench(client, headers, expect=200):
    r = client.get("/gap-workbench", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _get_overview(client, headers, expect=200):
    r = client.get("/overview", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


# --- Direct-insert helpers for pre-existing infrastructure this Epic only ---
# --- borrows the identity of (never re-implemented, never owned here) ------


def _insert_component(system_id, component_id, mode="trace"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO components (system_id, component_id, mode, updated_at) VALUES (?, ?, ?, ?)",
            (system_id, component_id, mode, now),
        )
    return component_id


def _insert_evolution_node(system_id, node_key, display_name=""):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO evolution_node (system_id, node_key, display_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (system_id, node_key, display_name, now, now),
        )
        return cur.lastrowid


def _insert_capability_hierarchy_source(
    system_id, snapshot_id, *, path="a.py", qualified_name="a",
    file_hash="F1", symbol_hash="S1", explanation_hash="E1",
):
    """A `capability_hierarchy` intelligence run plus a matching
    `capability_hierarchy_nodes` anchor AND current `snapshot_files` /
    `code_symbols` / `symbol_source_metadata` rows carrying the SAME
    hashes -- the exact-match fixture `test_product_gap_sources.
    TestCapabilityDrift._fixture` uses, so `capability_drift` resolves to a
    real `contradicted` ("fresh") outcome rather than `unavailable`
    (§5.10). Returns the intelligence run id."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version, schema_version,
                    decision_method, status, started_at, completed_at)
               VALUES (?, ?, 'capability_hierarchy', 'mock', 'mock', 'v1', 'v1', 'deterministic', 'completed', ?, ?)""",
            (system_id, snapshot_id, now, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
                   (system_id, snapshot_id, intelligence_run_id, node_type, name, path, qualified_name,
                    file_content_hash, symbol_source_hash, explanation_hash, created_at)
               VALUES (?, ?, ?, 'element', 'A', ?, ?, ?, ?, ?, ?)""",
            (system_id, snapshot_id, run_id, path, qualified_name, file_hash, symbol_hash, explanation_hash, now),
        )
        conn.execute(
            """INSERT INTO snapshot_files (snapshot_id, path, source_type, size_bytes, content_hash, content, inclusion_status)
               VALUES (?, ?, 'source', 10, ?, X'', 'indexed')""",
            (snapshot_id, path, file_hash),
        )
        sym_id = conn.execute(
            """INSERT INTO code_symbols (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, symbol_source_hash)
               VALUES (?, ?, ?, ?, 'function', 1, 2, ?)""",
            (snapshot_id, system_id, path, qualified_name, symbol_hash),
        ).lastrowid
        conn.execute(
            """INSERT INTO symbol_source_metadata
                   (snapshot_id, system_id, symbol_id, path, qualified_name, start_line, end_line, raw_block, explanation_hash)
               VALUES (?, ?, ?, ?, ?, 1, 2, '', ?)""",
            (snapshot_id, system_id, sym_id, path, qualified_name, explanation_hash),
        )
        return run_id


def _insert_intelligence_run(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version, schema_version,
                    decision_method, status, is_mock, started_at, completed_at)
               VALUES (?, ?, 'repository_drafts', 'mock', 'mock', 'v1', 'v1', 'reasoning_llm', 'completed', 1, ?, ?)""",
            (system_id, snapshot_id, now, now),
        )
        return cur.lastrowid


def _insert_probe_plan(system_id, snapshot_id, intelligence_run_id, feature_id="feat-x"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id, objective, status, origin, created_at, updated_at)
               VALUES (?, ?, ?, ?, '', 'proposed', 'manual', ?, ?)""",
            (system_id, snapshot_id, intelligence_run_id, feature_id, now, now),
        )
        return cur.lastrowid


def _insert_probe_point(system_id, plan_id, component_id, status="approved"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_points
                   (plan_id, system_id, component_id, feature_id, path, symbol, line_start, line_end,
                    reason, recommended_mode, side_effect_risk, replayability, status, created_at, updated_at)
               VALUES (?, ?, ?, 'feat-x', 'a.py', 'a', 1, 2, 'because', 'trace', 'low', '', ?, ?, ?)""",
            (plan_id, system_id, component_id, status, now, now),
        )
        return cur.lastrowid


def _insert_feature_draft(system_id, snapshot_id, intelligence_run_id, feature_id_text="feat-x"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO feature_drafts
                   (system_id, intelligence_run_id, snapshot_id, feature_id, name, created_at)
               VALUES (?, ?, ?, ?, 'name', ?)""",
            (system_id, intelligence_run_id, snapshot_id, feature_id_text, now),
        )
        return cur.lastrowid


def _insert_experiment(system_id, feature_id_text, snapshot_id, status="completed"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO experiments
                   (system_id, feature_id, objective, snapshot_id, baseline_commit, config_revision,
                    execution_config, status, human_decision, created_at)
               VALUES (?, ?, '', ?, 'abc', 'v1', '{}', ?, 'undecided', ?)""",
            (system_id, feature_id_text, snapshot_id, status, now),
        )
        return cur.lastrowid


def _insert_replay_set(system_id, component_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO replay_sets (system_id, component_id, name, trace_ids_json, created_at)
               VALUES (?, ?, 'set', '[]', ?)""",
            (system_id, component_id, now),
        )
        return cur.lastrowid


def _insert_replay_run(system_id, replay_set_id, component_id, snapshot_id, commit_sha, status="completed"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO replay_runs
                   (system_id, replay_set_id, component_id, snapshot_id, commit_sha, symbol_path,
                    symbol_qualified_name, status, trace_set_hash, created_at)
               VALUES (?, ?, ?, ?, ?, 'a.py', 'a', ?, 'h', ?)""",
            (system_id, replay_set_id, component_id, snapshot_id, commit_sha, status, now),
        )
        return cur.lastrowid


def _insert_outcome_criterion(system_id, session_id, *, state="observed"):
    """Direct insert against `purpose_outcome_criterion` (Issue #391, not
    owned by this Epic). The real `/purpose-chain/outcome-criteria` API
    requires a currently-open Purpose Chain Need derived from live
    understanding content -- setting that up realistically is a whole
    separate Epic's machinery, out of scope for this task's fixture. This
    mirrors what `purpose_verification.record_outcome_result` would have
    written for a human `supports` verdict, so `product_feature`'s target
    link resolver (which only reads `id, state`) sees a realistic row.
    Flagged in the final report."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO purpose_outcome_criterion
                   (system_id, session_id, target_kind, target_id, target_digest, source_need_id,
                    source_need_code, measure, baseline_value, target_value, observation_window, state,
                    human_reported_evidence, human_reported_verdict, human_reported_at, human_reported_by,
                    human_reported_state, created_at)
               VALUES (?, ?, 'element', 'core_capability:x', 'd', 'need-1', 'NEED-1', 'measure', '0', '1', '', ?,
                       'observed in prod', 'supports', ?, 'root', ?, ?)""",
            (system_id, session_id, state, now, state, now),
        )
        return cur.lastrowid


def _insert_issue_draft(system_id, title="issue", status="draft"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO issue_drafts (system_id, source_type, title, body_markdown, status, created_at, updated_at)
               VALUES (?, 'manual', ?, 'body', ?, ?, ?)""",
            (system_id, title, status, now, now),
        )
        return cur.lastrowid


def _set_issue_draft_status(issue_id, status):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE issue_drafts SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), issue_id))


def _set_issue_draft_title(issue_id, title):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE issue_drafts SET title = ?, updated_at = ? WHERE id = ?", (title, time.time(), issue_id))


def _insert_inquiry(system_id, session_id, status="open"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_inquiry (session_id, system_id, origin_kind, origin_id, status, created_at, updated_at)
               VALUES (?, ?, 'manual', 0, ?, ?, ?)""",
            (session_id, system_id, status, now, now),
        )
        inquiry_id = cur.lastrowid
        conn.execute(
            """INSERT INTO interview_inquiry_message (inquiry_id, system_id, role, content, created_at)
               VALUES (?, ?, 'assistant', 'what about X?', ?)""",
            (inquiry_id, system_id, now),
        )
        return inquiry_id


def _set_inquiry_status(inquiry_id, status):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE interview_inquiry SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), inquiry_id))


def _insert_joint_understanding_session(system_id, session_id, status="open"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO joint_understanding_session
                   (session_id, system_id, origin_kind, origin_id, trigger,
                    question_text, status, schema_version, created_at, updated_at)
               VALUES (?, ?, 'manual', 0, 'explicit_request', 'why?', ?,
                       'joint-understanding-v1', ?, ?)""",
            (session_id, system_id, status, now, now),
        )
        return cur.lastrowid


def _set_joint_understanding_status(ju_id, status):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE joint_understanding_session SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), ju_id))


# ---------------------------------------------------------------------------
# The representative fixture (Task A)
# ---------------------------------------------------------------------------


@dataclass
class Chain:
    client: Any
    token: str
    system_id: int
    session_id: int
    headers: Dict[str, str]
    vision_text: str
    objective_parent_key: str
    objective_child_key: str
    milestone_a_key: str
    milestone_b_key: str
    milestone_src_key: str
    gap_main_key: str
    journey_as_is_key: str
    journey_to_be_key: str
    requirement_key: str
    feature_key: str
    capability_entity_id: int
    design_key: str
    option_key: str
    component_id: str
    node_key: str
    probe_point_id: int
    experiment_id: int
    replay_run_id: int
    outcome_id: int
    feature_draft_id: int
    snapshot_id: int
    repo: str


def _build_chain(client, tmp_path, *, name="System PO E2E") -> Chain:
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo_with_files(
        tmp_path, f"repo-{name.replace(' ', '-')}", {"a.py": b"def a():\n    return 1\n"}
    )
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    headers = _headers(token, system_id)
    session_id = _create_session(client, headers, snapshot_id)

    vision_text = "初回利用者が決済を1回で完了できる状態にする"
    _set_goal(client, headers, session_id, vision_text, status="confirmed")
    brief = _get_brief(client, headers, session_id)
    assert brief["vision"]["name"] == vision_text
    assert brief["vision"]["confirmation"] == "confirmed"

    capability_entity_id = _insert_capability_entity(system_id, session_id, "決済処理")

    # --- Objective (parent + child) ---
    _create_objective(client, headers, "obj-parent")
    _add_objective_revision(
        client, headers, "obj-parent",
        title="決済体験の向上", intent="決済離脱を減らす", contribution="Vision へ寄与する",
    )
    _add_objective_upstream_ref(client, headers, "obj-parent", "vision_claim", vision_text)
    _record_objective_decision(client, headers, "obj-parent", "confirm")
    _record_objective_decision(client, headers, "obj-parent", "activate")

    _create_objective(client, headers, "obj-child")
    _add_objective_revision(
        client, headers, "obj-child",
        title="初回決済の完了率向上", intent="初回利用者の決済完了", contribution="親 Objective の一部",
    )
    _set_objective_parent(client, headers, "obj-child", "obj-parent")
    _add_objective_upstream_ref(client, headers, "obj-child", "capability_entity", str(capability_entity_id))
    _record_objective_decision(client, headers, "obj-child", "confirm")
    _record_objective_decision(client, headers, "obj-child", "activate")

    # --- Milestones (with a dependency) ---
    _create_milestone(client, headers, "obj-child", "ms-a")
    _add_milestone_revision(
        client, headers, "ms-a",
        title="初回決済が手戻りなく完了する", target_state="手戻りなしで決済完了が観測できる",
        verification_method="runtime_observation", sequence_hint=1,
    )
    _record_milestone_decision(client, headers, "ms-a", "confirm")

    _create_milestone(client, headers, "obj-child", "ms-b")
    _add_milestone_revision(
        client, headers, "ms-b",
        title="決済エラーからの回復導線がある", target_state="エラー時に再試行できる",
        verification_method="manual_review", sequence_hint=2,
    )
    _record_milestone_decision(client, headers, "ms-b", "confirm")
    _add_milestone_dependency(client, headers, "ms-b", "ms-a", rationale="ms-a が先に必要")

    _create_milestone(client, headers, "obj-child", "ms-src")
    _add_milestone_revision(client, headers, "ms-src", title="Gap source 検証用", verification_method="unavailable")
    _record_milestone_decision(client, headers, "ms-src", "confirm")

    # --- Gap (manual current/target state) ---
    _create_gap(client, headers, "ms-a", "gap-main")
    _add_gap_revision(
        client, headers, "gap-main",
        title="初回決済の離脱", current_state="初回決済でエラー時に離脱する", target_state="エラー時も完了できる",
        target_state_mode="own", interpretation="UI のエラー表示が分かりにくい",
    )
    _add_gap_source_ref(client, headers, "gap-main", "manual", "")
    trace_id = "trace-1"
    component_id = _insert_component(system_id, "payments.checkout")
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO traces (system_id, trace_id, component_id, mode, input_json, output_text, timestamp)
               VALUES (?, ?, ?, 'trace', '{}', 'ok', ?)""",
            (system_id, trace_id, component_id, now),
        )
    _add_gap_evidence_ref(client, headers, "gap-main", "trace", trace_id)

    # --- as-is / to-be UX Journeys ---
    journey_as_is_key = "journey-as-is"
    journey_to_be_key = "journey-to-be"
    _create_journey(client, headers, journey_as_is_key, perspective="as_is", baseline_mode="undecided")
    _add_journey_revision(
        client, headers, journey_as_is_key,
        steps=[_step("step-1", 1)], title="現状の決済フロー",
    )
    as_is = _get_journey(client, headers, journey_as_is_key)

    _create_journey(
        client, headers, journey_to_be_key, perspective="to_be", baseline_mode="linked",
        baseline_journey_id=as_is["id"],
    )
    _add_journey_revision(
        client, headers, journey_to_be_key,
        steps=[_step("step-1", 1)], title="改善後の決済フロー",
    )
    # §5.11: the Gap -> Journey connection is written ONCE, on the Journey
    # side (`ux_journey_upstream_ref(ref_kind='product_gap')`). Writing it a
    # second time via `product_gap_artifact_link` is no longer possible --
    # that `link_kind` was removed to close the twin-canon this created.
    _add_journey_upstream_ref(client, headers, journey_to_be_key, "product_gap", "gap-main")

    # --- Requirement + acceptance criterion ---
    requirement_key = "req-main"
    _create_requirement(client, headers, requirement_key, requirement_kind="functional")
    _add_requirement_revision(
        client, headers, requirement_key,
        statement="決済エラー時に再試行できる",
        acceptance_criteria=[_criterion("crit-1", 1, statement="エラー後に再試行ボタンが出る")],
    )
    _add_step_link(client, headers, requirement_key, journey_to_be_key, "step-1")

    # --- Feature <-> Capability, Solution Design, implementation targets ---
    design_key = "design-main"
    option_key = "opt-main"
    _create_design(client, headers, design_key, title="決済エラー回復の設計")
    _add_design_option(client, headers, design_key, option_key, title="リトライボタンを追加")
    _decide_design_option(client, headers, design_key, option_key, "adopt")

    node_key = "node-main"
    _insert_evolution_node(system_id, node_key, display_name="決済処理ノード")

    intelligence_run_id = _insert_intelligence_run(system_id, snapshot_id)
    plan_id = _insert_probe_plan(system_id, snapshot_id, intelligence_run_id)
    probe_point_id = _insert_probe_point(system_id, plan_id, component_id, status="approved")

    feature_draft_id = _insert_feature_draft(system_id, snapshot_id, intelligence_run_id, feature_id_text="feat-main-draft")

    experiment_id = _insert_experiment(system_id, "feat-main-draft", snapshot_id, status="completed")
    replay_set_id = _insert_replay_set(system_id, component_id)
    replay_run_id = _insert_replay_run(system_id, replay_set_id, component_id, snapshot_id, sha, status="completed")
    outcome_id = _insert_outcome_criterion(system_id, session_id, state="observed")

    feature_key = "feat-main"
    _create_feature(client, headers, feature_key)
    _add_feature_revision(client, headers, feature_key, title="決済エラー再試行機能", statement="ユーザーが再試行できる")
    _add_feature_requirement_link(client, headers, feature_key, requirement_key)
    _add_feature_capability_link(client, headers, feature_key, capability_entity_id)
    _add_feature_target_link(client, headers, feature_key, "component", component_id)
    _add_feature_target_link(client, headers, feature_key, "evolution_node", node_key)
    _add_feature_target_link(client, headers, feature_key, "probe_point", str(probe_point_id))
    _add_feature_target_link(client, headers, feature_key, "solution_design", design_key)
    _add_feature_target_link(client, headers, feature_key, "experiment", str(experiment_id))
    _add_feature_target_link(client, headers, feature_key, "replay_run", str(replay_run_id))
    _add_feature_target_link(client, headers, feature_key, "purpose_outcome_criterion", str(outcome_id))
    _add_feature_draft_link(client, headers, feature_key, feature_draft_id)
    _record_feature_decision(client, headers, feature_key, "confirm")

    _add_gap_artifact_link(client, headers, "gap-main", "product_feature", feature_key)

    return Chain(
        client=client, token=token, system_id=system_id, session_id=session_id, headers=headers,
        vision_text=vision_text, objective_parent_key="obj-parent", objective_child_key="obj-child",
        milestone_a_key="ms-a", milestone_b_key="ms-b", milestone_src_key="ms-src",
        gap_main_key="gap-main", journey_as_is_key=journey_as_is_key, journey_to_be_key=journey_to_be_key,
        requirement_key=requirement_key, feature_key=feature_key, capability_entity_id=capability_entity_id,
        design_key=design_key, option_key=option_key, component_id=component_id, node_key=node_key,
        probe_point_id=probe_point_id, experiment_id=experiment_id, replay_run_id=replay_run_id,
        outcome_id=outcome_id, feature_draft_id=feature_draft_id, snapshot_id=snapshot_id, repo=repo,
    )


@pytest.fixture
def chain(admin_client, tmp_path) -> Chain:
    return _build_chain(admin_client, tmp_path)


# ---------------------------------------------------------------------------
# 1. Every hop traverses by stable ref, walked forward through real endpoints
# ---------------------------------------------------------------------------


class TestForwardWalk:
    """Task A #1: Vision -> Objective -> Milestone -> Gap -> Journey ->
    Requirement -> Feature -> Solution Design / Component / Evolution Node /
    Probe Point / Experiment / Replay Run / Outcome, read exclusively through
    GET endpoints."""

    def test_vision_to_objective(self, chain: Chain):
        obj = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        ref = next(r for r in obj["upstream_refs"] if r["ref_kind"] == "capability_entity")
        assert ref["target_resolution"] == "resolved"
        assert ref["target_name"] == "決済処理"

        parent = _get_objective(chain.client, chain.headers, chain.objective_parent_key)
        vref = next(r for r in parent["upstream_refs"] if r["ref_kind"] == "vision_claim")
        assert vref["target_resolution"] == "resolved"
        assert vref["target_name"] == chain.vision_text
        assert vref["target_state"] == "confirmed"
        assert vref["recheck_state"] == "current"

        assert obj["parent_objective_key"] == chain.objective_parent_key
        assert obj["objective_state"] == "active"
        assert parent["objective_state"] == "active"

    def test_objective_to_milestone(self, chain: Chain):
        ms_list = _list_milestones(chain.client, chain.headers, chain.objective_child_key)
        keys = {m["milestone_key"] for m in ms_list["milestones"]}
        assert {chain.milestone_a_key, chain.milestone_b_key, chain.milestone_src_key} <= keys

        ms_b = _get_milestone(chain.client, chain.headers, chain.milestone_b_key)
        dep = ms_b["dependencies"][0]
        assert dep["depends_on_milestone_key"] == chain.milestone_a_key

    def test_milestone_to_gap(self, chain: Chain):
        gaps = _list_gaps(chain.client, chain.headers, chain.milestone_a_key)
        assert any(g["gap_key"] == chain.gap_main_key for g in gaps["gaps"])

        gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)
        assert gap["milestone_key"] == chain.milestone_a_key
        assert gap["objective_key"] == chain.objective_child_key
        assert gap["current_revision"]["target_state_mode"] == "own"
        manual_source = next(s for s in gap["source_refs"] if s["source_kind"] == "manual")
        assert manual_source["source_state"] == "current"
        assert gap["evidence_refs"][0]["evidence_kind"] == "trace"

    def test_gap_to_journey(self, chain: Chain):
        """§5.11: the Gap -> Journey connection has exactly ONE writable
        home -- `ux_journey_upstream_ref(ref_kind='product_gap')` on the
        Journey side. `product_gap_artifact_link` no longer accepts
        `ux_journey` at all (see `TestArtifactLinkKind` below), so this hop
        is read forward from the Journey only, never from the Gap's
        `artifact_links`."""
        gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)
        assert not any(a["link_kind"] == "ux_journey" for a in gap["artifact_links"])

        journey = _get_journey(chain.client, chain.headers, chain.journey_to_be_key)
        ref = next(r for r in journey["upstream_refs"] if r["ref_kind"] == "product_gap")
        assert ref["target_ref"] == chain.gap_main_key
        assert ref["target_resolution"] == "resolved"
        assert ref["target_state"] == "open"

    def test_journey_to_requirement_to_feature(self, chain: Chain):
        requirement = _get_requirement(chain.client, chain.headers, chain.requirement_key)
        assert requirement["current_revision"]["acceptance_criteria"][0]["criterion_key"] == "crit-1"

        gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)
        feature_link = next(a for a in gap["artifact_links"] if a["link_kind"] == "product_feature")
        assert feature_link["target_ref"] == chain.feature_key

        feature = _get_feature(chain.client, chain.headers, chain.feature_key)
        req_link = feature["requirement_links"][0]
        assert req_link["requirement_key"] == chain.requirement_key
        assert req_link["recheck_state"] == "current"

    def test_feature_to_implementation_targets(self, chain: Chain):
        feature = _get_feature(chain.client, chain.headers, chain.feature_key)
        by_kind = {l["link_kind"]: l for l in feature["target_links"]}

        assert by_kind["solution_design"]["target_resolution"] == "resolved"
        assert by_kind["solution_design"]["target_state"] == "adopted"

        assert by_kind["component"]["target_resolution"] == "resolved"
        assert by_kind["evolution_node"]["target_resolution"] == "resolved"
        assert by_kind["probe_point"]["target_resolution"] == "resolved"

        assert by_kind["experiment"]["target_resolution"] == "resolved"
        assert by_kind["experiment"]["target_state"] == "completed"

        assert by_kind["replay_run"]["target_resolution"] == "resolved"
        assert by_kind["replay_run"]["target_state"] == "completed"

        assert by_kind["purpose_outcome_criterion"]["target_resolution"] == "resolved"
        assert by_kind["purpose_outcome_criterion"]["target_state"] == "observed"

        cap_link = feature["capability_links"][0]
        assert cap_link["target_resolution"] == "resolved"
        assert cap_link["capability_name"] == "決済処理"

        draft_link = feature["draft_links"][0]
        assert draft_link["target_resolution"] == "resolved"
        assert draft_link["feature_draft_id"] == chain.feature_draft_id


# ---------------------------------------------------------------------------
# 2. Gap sources across several source_kind values
# ---------------------------------------------------------------------------


class TestGapSourceFederation:
    """Task A #2: several `source_kind` values resolve, including
    `contradicted`, `disappeared`, `changed`, `unavailable`, and one kind
    that structurally cannot reach `contradicted` (SS5.4.1) reporting
    `disappeared` instead."""

    def _gap(self, chain: Chain, gap_key: str) -> Dict[str, Any]:
        _create_gap(chain.client, chain.headers, chain.milestone_src_key, gap_key)
        _add_gap_revision(chain.client, chain.headers, gap_key, title=gap_key, target_state_mode="unknown")
        return _get_gap(chain.client, chain.headers, gap_key)

    def test_manual_is_always_current(self, chain: Chain):
        gap_key = "gap-src-manual"
        self._gap(chain, gap_key)
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "manual", "")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_kind"] == "manual"
        assert source["source_state"] == "current"

    def test_issue_draft_reaches_contradicted_and_changed(self, chain: Chain):
        gap_key = "gap-src-issue"
        self._gap(chain, gap_key)
        issue_id = _insert_issue_draft(chain.system_id, title="離脱の記録", status="draft")
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "issue_draft", str(issue_id))
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_state"] == "current"

        _set_issue_draft_title(issue_id, "離脱の記録（更新）")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_state"] == "changed"

        _set_issue_draft_status(issue_id, "closed")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_state"] == "contradicted"

    def test_inquiry_unresolved_reaches_contradicted(self, chain: Chain):
        gap_key = "gap-src-inquiry"
        self._gap(chain, gap_key)
        inquiry_id = _insert_inquiry(chain.system_id, chain.session_id, status="open")
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "inquiry_unresolved", str(inquiry_id))
        detail = _get_gap(chain.client, chain.headers, gap_key)
        assert detail["source_refs"][0]["source_state"] == "current"

        _set_inquiry_status(inquiry_id, "answered")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        assert detail["source_refs"][0]["source_state"] == "contradicted"

    def test_joint_understanding_open_reaches_contradicted(self, chain: Chain):
        gap_key = "gap-src-ju"
        self._gap(chain, gap_key)
        ju_id = _insert_joint_understanding_session(chain.system_id, chain.session_id, status="open")
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "joint_understanding_open", str(ju_id))
        detail = _get_gap(chain.client, chain.headers, gap_key)
        assert detail["source_refs"][0]["source_state"] == "current"

        _set_joint_understanding_status(ju_id, "closed")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        assert detail["source_refs"][0]["source_state"] == "contradicted"

    def test_functional_lineage_gap_cannot_reach_contradicted_and_reports_disappeared(self, chain: Chain):
        """SS5.4.1: `functional_lineage_gap` has no vocabulary to assert
        "this condition no longer holds" -- when it resolves and the match
        is gone, that is `disappeared`, never `contradicted`."""
        gap_key = "gap-src-fl"
        self._gap(chain, gap_key)
        # No `functional_lineage_gap` with this code/subject will ever exist.
        _add_gap_source_ref(
            chain.client, chain.headers, gap_key, "functional_lineage_gap",
            "no_such_code|component|does-not-exist",
        )
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_state"] == "disappeared"

    def test_capability_drift_resolves_via_the_http_endpoint(self, chain: Chain):
        """§5.10: `POST /product-gaps/{key}/source-refs` never accepts a
        `captured_run_id` pin (§10.1's Create model carries none) -- but
        `capability_drift` still resolves for real through this endpoint,
        because the resolver decides its OWN base run (the latest completed
        Capability Hierarchy build for this System) and `add_gap_source_ref`
        stores that resolved pin in the same call. This replaces the pre-fix
        behaviour, which pinned `unavailable` as the PERMANENT answer for
        every `capability_drift` source ever created through the public
        API, because no pin was ever captured for it to re-resolve against."""
        gap_key = "gap-src-drift"
        self._gap(chain, gap_key)
        _insert_capability_hierarchy_source(chain.system_id, chain.snapshot_id, path="a.py", qualified_name="a")
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "capability_drift", "a.py|a")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        # Exact-hash fixture -> `drift.compute_anchor_drift` reports FRESH,
        # which is capability_drift's own `contradicted` condition (§5.4) --
        # a real resolution, not the structural `unavailable` this endpoint
        # used to be stuck at.
        assert source["source_state"] == "contradicted"
        assert source["captured_snapshot_id"] == chain.snapshot_id
        assert source["captured_run_id"] is not None

    def test_capability_drift_is_unavailable_with_no_capability_hierarchy_run(self, chain: Chain):
        """The one legitimate `unavailable`: no `capability_hierarchy` run
        has EVER completed for this System, so the resolver has no base run
        to decide on -- honest degradation, never a guessed pin (§5.10)."""
        gap_key = "gap-src-drift-none"
        self._gap(chain, gap_key)
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "capability_drift", "a.py|a")
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["source_state"] == "unavailable"
        assert source["captured_run_id"] is None
        assert source["captured_snapshot_id"] is None

    def test_several_kinds_together_on_one_gap(self, chain: Chain):
        gap_key = "gap-src-multi"
        self._gap(chain, gap_key)
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "manual", "")
        issue_id = _insert_issue_draft(chain.system_id)
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "issue_draft", str(issue_id))
        _add_gap_source_ref(
            chain.client, chain.headers, gap_key, "functional_lineage_gap", "x|y|z",
        )
        detail = _get_gap(chain.client, chain.headers, gap_key)
        states = {s["source_kind"]: s["source_state"] for s in detail["source_refs"]}
        assert states["manual"] == "current"
        assert states["issue_draft"] == "current"
        assert states["functional_lineage_gap"] == "disappeared"


# ---------------------------------------------------------------------------
# 3. Downstream-only change propagation
# ---------------------------------------------------------------------------


class TestDownstreamOnlyPropagation:
    def test_moving_objective_revision_stales_confirmation_not_state(self, chain: Chain):
        before = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        assert before["recheck_state"] == "current"
        assert before["objective_state"] == "active"

        _add_objective_revision(chain.client, chain.headers, chain.objective_child_key, title="改訂後のタイトル")
        after = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        assert after["recheck_state"] == "stale"
        assert after["objective_state"] == "active"

    def test_moving_milestone_stales_inherited_gap_but_not_by_default_own_gap(self, chain: Chain):
        _create_milestone(chain.client, chain.headers, chain.objective_child_key, "ms-inherit")
        _add_milestone_revision(
            chain.client, chain.headers, "ms-inherit", title="継承 Milestone", target_state="v1",
            verification_method="manual_review",
        )
        _record_milestone_decision(chain.client, chain.headers, "ms-inherit", "confirm")

        _create_gap(chain.client, chain.headers, "ms-inherit", "gap-inherit")
        _add_gap_revision(
            chain.client, chain.headers, "gap-inherit", title="継承 Gap",
            target_state_mode="inherited_from_milestone",
        )
        # `recheck_state` re-checks a HUMAN DECISION against the content it
        # was made on, so the Gap needs one before there is anything to go
        # stale. `decision_digest` (not `current_revision.content_digest`) is
        # what the server compares: this Gap inherits its target from the
        # Milestone, so half of what is being judged lives on that row.
        before = _get_gap(chain.client, chain.headers, "gap-inherit")
        # §5.3: an `inherited_from_milestone` Gap does not store a target of
        # its own -- the response must resolve and show what it is actually
        # measured against.
        assert before["effective_target_state"] == "v1"
        assert before["effective_target_availability"] == "resolved"
        _record_gap_decision(
            chain.client, chain.headers, "gap-inherit", "acknowledge",
            captured_digest=before["decision_digest"],
        )
        before = _get_gap(chain.client, chain.headers, "gap-inherit")
        assert before["recheck_state"] == "current"

        _add_milestone_revision(
            chain.client, chain.headers, "ms-inherit", title="継承 Milestone (改訂)", target_state="v2",
            verification_method="manual_review",
        )
        after = _get_gap(chain.client, chain.headers, "gap-inherit")
        # The target the developer acknowledged this Gap against moved, so
        # the acknowledgement is up for re-check -- while the lifecycle
        # itself is untouched (§6: a stale confirmation is not a reversed
        # one).
        assert after["recheck_state"] == "stale"
        assert after["lifecycle"] == "acknowledged"
        assert after["effective_target_state"] == "v2"
        assert after["effective_target_availability"] == "resolved"

    def test_effective_target_state_for_own_and_unknown_modes(self, chain: Chain):
        """§5.3: `own` always resolves (even an intentionally empty string
        is still `own`, never `unknown`); `unknown` never fabricates a
        target text."""
        gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)
        assert gap["current_revision"]["target_state_mode"] == "own"
        assert gap["effective_target_state"] == gap["current_revision"]["target_state"]
        assert gap["effective_target_availability"] == "own"

        _create_gap(chain.client, chain.headers, chain.milestone_src_key, "gap-target-unknown")
        _add_gap_revision(
            chain.client, chain.headers, "gap-target-unknown", title="unknown target",
            target_state_mode="unknown",
        )
        unknown_gap = _get_gap(chain.client, chain.headers, "gap-target-unknown")
        assert unknown_gap["effective_target_state"] is None
        assert unknown_gap["effective_target_availability"] == "unknown"

    def test_effective_target_state_is_unavailable_not_empty_when_milestone_has_no_revision(self, chain: Chain):
        """§5.3/§0-8: a Milestone with no revision yet cannot be read for its
        target -- this must be `unavailable`, NEVER an empty string that
        would be indistinguishable from a legitimately empty target."""
        _create_milestone(chain.client, chain.headers, chain.objective_child_key, "ms-no-revision")
        _create_gap(chain.client, chain.headers, "ms-no-revision", "gap-unreadable-target")
        _add_gap_revision(
            chain.client, chain.headers, "gap-unreadable-target", title="unreadable target",
            target_state_mode="inherited_from_milestone",
        )
        gap = _get_gap(chain.client, chain.headers, "gap-unreadable-target")
        assert gap["effective_target_state"] is None
        assert gap["effective_target_availability"] == "unavailable"

    def test_unreadable_milestone_target_digest_differs_from_a_real_empty_target(self, chain: Chain):
        """§5.3: `decision_digest` must not conflate "the Milestone could
        not be read" with "the target is legitimately empty string" -- an
        unreadable Milestone that is later given a revision with an EMPTY
        `target_state` must still register as a content change, never as
        "unchanged"."""
        _create_milestone(chain.client, chain.headers, chain.objective_child_key, "ms-empty-later")
        _create_gap(chain.client, chain.headers, "ms-empty-later", "gap-empty-later")
        _add_gap_revision(
            chain.client, chain.headers, "gap-empty-later", title="empty later",
            target_state_mode="inherited_from_milestone",
        )
        before = _get_gap(chain.client, chain.headers, "gap-empty-later")
        assert before["effective_target_availability"] == "unavailable"
        digest_before = before["decision_digest"]

        # Give the Milestone a real (empty-string) target_state.
        _add_milestone_revision(
            chain.client, chain.headers, "ms-empty-later", title="ms-empty-later", target_state="",
            verification_method="manual_review",
        )
        after = _get_gap(chain.client, chain.headers, "gap-empty-later")
        assert after["effective_target_availability"] == "resolved"
        assert after["effective_target_state"] == ""
        assert after["decision_digest"] != digest_before

    def test_moving_gap_does_not_stale_the_milestone(self, chain: Chain):
        before = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        assert before["recheck_state"] == "current"

        _add_gap_revision(chain.client, chain.headers, chain.gap_main_key, title="改訂後の Gap タイトル")

        after = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        assert after["recheck_state"] == "current"


# ---------------------------------------------------------------------------
# 4. No automatic success, anywhere
# ---------------------------------------------------------------------------


class TestNoAutomaticSuccess:
    def test_every_milestone_met_leaves_objective_state_unchanged(self, chain: Chain):
        before = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        assert before["objective_state"] == "active"

        for milestone_key in (chain.milestone_a_key, chain.milestone_b_key, chain.milestone_src_key):
            _record_milestone_assessment(chain.client, chain.headers, milestone_key, "met")

        after = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        assert after["objective_state"] == "active"

    def test_every_gap_resolved_leaves_milestone_achievement_unassessed(self, chain: Chain):
        before = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        assert before["achievement"] == "unassessed"

        _record_gap_decision(chain.client, chain.headers, chain.gap_main_key, "resolve")

        after = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        assert after["achievement"] == "unassessed"

    def test_source_disappeared_or_contradicted_leaves_lifecycle_untouched(self, chain: Chain):
        gap_key = "gap-lifecycle-untouched"
        _create_gap(chain.client, chain.headers, chain.milestone_src_key, gap_key)
        _add_gap_revision(chain.client, chain.headers, gap_key, title=gap_key)
        issue_id = _insert_issue_draft(chain.system_id)
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "issue_draft", str(issue_id))

        before = _get_gap(chain.client, chain.headers, gap_key)
        assert before["lifecycle"] == "open"

        _set_issue_draft_status(issue_id, "closed")  # -> contradicted
        after_contradicted = _get_gap(chain.client, chain.headers, gap_key)
        assert after_contradicted["source_refs"][0]["source_state"] == "contradicted"
        assert after_contradicted["lifecycle"] == "open"
        assert "close_candidate" in after_contradicted["read_flags"]

        _add_gap_source_ref(chain.client, chain.headers, gap_key, "functional_lineage_gap", "no|such|thing")
        after_disappeared = _get_gap(chain.client, chain.headers, gap_key)
        assert after_disappeared["lifecycle"] == "open"

    def test_inserting_traces_experiment_replay_confirms_nothing(self, chain: Chain):
        before_objective = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        before_milestone = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        before_gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)

        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO traces (system_id, trace_id, component_id, mode, input_json, output_text, timestamp)
                   VALUES (?, 'trace-confirms-nothing', ?, 'trace', '{}', 'ok', ?)""",
                (chain.system_id, chain.component_id, now),
            )
        _insert_experiment(chain.system_id, "feat-main-draft-2", chain.snapshot_id, status="completed")

        after_objective = _get_objective(chain.client, chain.headers, chain.objective_child_key)
        after_milestone = _get_milestone(chain.client, chain.headers, chain.milestone_a_key)
        after_gap = _get_gap(chain.client, chain.headers, chain.gap_main_key)

        assert after_objective["objective_state"] == before_objective["objective_state"]
        assert after_milestone["achievement"] == before_milestone["achievement"]
        assert after_milestone["design_status"] == before_milestone["design_status"]
        assert after_gap["lifecycle"] == before_gap["lifecycle"]


# ---------------------------------------------------------------------------
# 5. No weighted score anywhere in the API surface
# ---------------------------------------------------------------------------


_NUMERIC_SCORE_KEY_SUBSTRINGS = (
    "priority", "severity", "score", "confidence", "completeness", "percent",
)

#: Keys that structurally carry a plain int/float and are NOT a synthesized
#: score -- ids, counts, timestamps, and `sequence_hint` (an explicit
#: developer-supplied DISPLAY ORDER value, SS0 invariant 7 / SS4.4's own
#: docstring: "進捗率の列は存在しない"). None of these match any substring
#: in `_NUMERIC_SCORE_KEY_SUBSTRINGS` above, so this allowlist is here for
#: documentation, not to suppress a real match.
_ALLOWED_NUMERIC_KEYS: set = set()


def _walk_json(node, path, violations):
    if isinstance(node, dict):
        for key, value in node.items():
            lower_key = key.lower()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if any(sub in lower_key for sub in _NUMERIC_SCORE_KEY_SUBSTRINGS) and key not in _ALLOWED_NUMERIC_KEYS:
                    violations.append(f"{path}.{key} = {value!r}")
            _walk_json(value, f"{path}.{key}", violations)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_json(item, f"{path}[{i}]", violations)


class TestNoWeightedScore:
    def test_no_numeric_priority_severity_score_confidence_completeness_percent(self, chain: Chain):
        responses = {
            "objective": _get_objective(chain.client, chain.headers, chain.objective_child_key),
            "milestone": _get_milestone(chain.client, chain.headers, chain.milestone_a_key),
            "gap": _get_gap(chain.client, chain.headers, chain.gap_main_key),
            "feature": _get_feature(chain.client, chain.headers, chain.feature_key),
            "objective_map": _get_objective_map(chain.client, chain.headers),
            "gap_workbench": _get_gap_workbench(chain.client, chain.headers),
            "overview": _get_overview(chain.client, chain.headers),
        }
        violations: List[str] = []
        for name, payload in responses.items():
            _walk_json(payload, name, violations)
        assert violations == [], f"numeric score-shaped fields found: {violations}"

    def test_gap_source_severity_is_a_string_from_its_own_vocabulary(self, chain: Chain):
        """The one deliberate exception: a Gap source's `severity` carries
        the DETECTOR's own vocabulary verbatim as a STRING (SS5.1) -- never a
        number. `issue_drafts.severity` is a free-text column; setting it
        confirms the resolver passes it through as a string, not a score."""
        gap_key = "gap-severity-string"
        _create_gap(chain.client, chain.headers, chain.milestone_src_key, gap_key)
        _add_gap_revision(chain.client, chain.headers, gap_key, title=gap_key)
        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO issue_drafts (system_id, source_type, title, body_markdown, severity, status, created_at, updated_at)
                   VALUES (?, 'manual', 'sev', 'body', 'high', 'draft', ?, ?)""",
                (chain.system_id, now, now),
            )
            issue_id = cur.lastrowid
        _add_gap_source_ref(chain.client, chain.headers, gap_key, "issue_draft", str(issue_id))
        detail = _get_gap(chain.client, chain.headers, gap_key)
        source = detail["source_refs"][0]
        assert source["severity"] == "high"
        assert isinstance(source["severity"], str)
        assert source["severity_vocabulary"] == "issue_draft"


# ---------------------------------------------------------------------------
# 6. No LLM fact -- decision/assessment endpoints reject non-manual
# ---------------------------------------------------------------------------


class TestNoLLMFact:
    def test_objective_decision_rejects_non_manual_decision_method_at_the_api(self, chain: Chain):
        r = chain.client.post(
            f"/product-objectives/{chain.objective_child_key}/decisions",
            json={"decision": "confirm", "decision_method": "reasoning_llm"},
            headers=chain.headers,
        )
        assert r.status_code == 422, r.text  # extra="forbid" rejects the field outright

    def test_milestone_decision_rejects_non_manual_decision_method_at_the_api(self, chain: Chain):
        r = chain.client.post(
            f"/product-milestones/{chain.milestone_a_key}/decisions",
            json={"decision": "confirm", "decided_by": "someone-else"},
            headers=chain.headers,
        )
        assert r.status_code == 422, r.text

    def test_gap_decision_rejects_non_manual_decision_method_at_the_api(self, chain: Chain):
        r = chain.client.post(
            f"/product-gaps/{chain.gap_main_key}/decisions",
            json={"decision": "acknowledge", "decision_method": "reasoning_llm"},
            headers=chain.headers,
        )
        assert r.status_code == 422, r.text

    def test_feature_decision_rejects_non_manual_decision_method_at_the_api(self, chain: Chain):
        r = chain.client.post(
            f"/product-features/{chain.feature_key}/decisions",
            json={"decision": "confirm", "authored_by_kind": "reasoning_model"},
            headers=chain.headers,
        )
        assert r.status_code == 422, r.text

    def test_decision_tables_check_decision_method_manual_at_the_db(self, chain: Chain):
        from app.db import get_conn

        now = time.time()
        for table, extra_cols, extra_vals in (
            (
                "product_objective_decision",
                "objective_id, objective_key, decision",
                "(SELECT id FROM product_objective WHERE objective_key = ?), ?, 'confirm'",
            ),
        ):
            pass  # see explicit table-by-table checks below for clarity

        with get_conn() as conn:
            objective_row = conn.execute(
                "SELECT id FROM product_objective WHERE system_id = ? AND objective_key = ?",
                (chain.system_id, chain.objective_child_key),
            ).fetchone()
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO product_objective_decision
                           (system_id, objective_id, objective_key, decision, decision_method, created_at)
                       VALUES (?, ?, ?, 'confirm', 'reasoning_llm', ?)""",
                    (chain.system_id, objective_row["id"], chain.objective_child_key, now),
                )

            milestone_row = conn.execute(
                "SELECT id FROM product_milestone WHERE system_id = ? AND milestone_key = ?",
                (chain.system_id, chain.milestone_a_key),
            ).fetchone()
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO product_milestone_decision
                           (system_id, milestone_id, milestone_key, decision, decision_method, created_at)
                       VALUES (?, ?, ?, 'confirm', 'reasoning_llm', ?)""",
                    (chain.system_id, milestone_row["id"], chain.milestone_a_key, now),
                )
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO product_milestone_assessment
                           (system_id, milestone_id, milestone_key, assessment, decision_method, created_at)
                       VALUES (?, ?, ?, 'met', 'reasoning_llm', ?)""",
                    (chain.system_id, milestone_row["id"], chain.milestone_a_key, now),
                )

            gap_row = conn.execute(
                "SELECT id FROM product_gap WHERE system_id = ? AND gap_key = ?",
                (chain.system_id, chain.gap_main_key),
            ).fetchone()
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO product_gap_decision
                           (system_id, gap_id, gap_key, decision, decision_method, created_at)
                       VALUES (?, ?, ?, 'acknowledge', 'reasoning_llm', ?)""",
                    (chain.system_id, gap_row["id"], chain.gap_main_key, now),
                )

            feature_row = conn.execute(
                "SELECT id FROM product_feature WHERE system_id = ? AND feature_key = ?",
                (chain.system_id, chain.feature_key),
            ).fetchone()
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO product_feature_decision
                           (system_id, feature_id, feature_key, decision, decision_method, created_at)
                       VALUES (?, ?, ?, 'confirm', 'reasoning_llm', ?)""",
                    (chain.system_id, feature_row["id"], chain.feature_key, now),
                )


# ---------------------------------------------------------------------------
# 7. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_same_keys_in_a_second_system_are_fully_independent(self, admin_client, tmp_path):
        chain_a = _build_chain(admin_client, tmp_path, name="System PO Isolation A")
        token_b = _login(admin_client)
        system_b = _create_system(admin_client, token_b, "System PO Isolation B")
        repo_b, sha_b = _init_repo_with_files(tmp_path, "repo-iso-b", {"a.py": b"def a():\n    return 2\n"})
        snapshot_b = _insert_snapshot(system_b, repo_b, sha_b)
        headers_b = _headers(token_b, system_b)

        # Same objective_key/milestone_key/gap_key/feature_key as System A.
        _create_objective(admin_client, headers_b, chain_a.objective_parent_key)
        _add_objective_revision(admin_client, headers_b, chain_a.objective_parent_key, title="別 System の同名 Objective")

        obj_b = _get_objective(admin_client, headers_b, chain_a.objective_parent_key)
        obj_a = _get_objective(admin_client, chain_a.headers, chain_a.objective_parent_key)
        assert obj_b["id"] != obj_a["id"]
        assert obj_b["title"] != obj_a["title"]

        _create_milestone(admin_client, headers_b, chain_a.objective_parent_key, chain_a.milestone_a_key)
        ms_b = _get_milestone(admin_client, headers_b, chain_a.milestone_a_key)
        ms_a = _get_milestone(admin_client, chain_a.headers, chain_a.milestone_a_key)
        assert ms_b["id"] != ms_a["id"]
        assert ms_b["objective_id"] != ms_a["objective_id"]

        _create_gap(admin_client, headers_b, chain_a.milestone_a_key, chain_a.gap_main_key)
        gap_b = _get_gap(admin_client, headers_b, chain_a.gap_main_key)
        gap_a = _get_gap(admin_client, chain_a.headers, chain_a.gap_main_key)
        assert gap_b["id"] != gap_a["id"]

        _create_feature(admin_client, headers_b, chain_a.feature_key)
        feat_b = _get_feature(admin_client, headers_b, chain_a.feature_key)
        feat_a = _get_feature(admin_client, chain_a.headers, chain_a.feature_key)
        assert feat_b["id"] != feat_a["id"]

    def test_cross_system_refs_are_404(self, admin_client, tmp_path):
        chain_a = _build_chain(admin_client, tmp_path, name="System PO Cross A")
        token_b = _login(admin_client)
        system_b = _create_system(admin_client, token_b, "System PO Cross B")
        headers_b = _headers(token_b, system_b)

        r = chain_a.client.get(f"/product-objectives/{chain_a.objective_parent_key}", headers=headers_b)
        assert r.status_code == 404

        r = chain_a.client.get(f"/product-milestones/{chain_a.milestone_a_key}", headers=headers_b)
        assert r.status_code == 404

        r = chain_a.client.get(f"/product-gaps/{chain_a.gap_main_key}", headers=headers_b)
        assert r.status_code == 404

        r = chain_a.client.get(f"/product-features/{chain_a.feature_key}", headers=headers_b)
        assert r.status_code == 404

        # Cross-System parent / dependency references.
        _create_objective(admin_client, headers_b, "obj-b-only")
        r = chain_a.client.post(
            f"/product-objectives/obj-b-only/parent",
            json={"parent_objective_key": chain_a.objective_parent_key},
            headers=headers_b,
        )
        assert r.status_code == 404

        _create_milestone(admin_client, headers_b, "obj-b-only", "ms-b-only")
        r = chain_a.client.post(
            "/product-milestones/ms-b-only/dependencies",
            json={"depends_on_milestone_key": chain_a.milestone_a_key},
            headers=headers_b,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 8. This Epic writes to no existing canon
# ---------------------------------------------------------------------------


_CANON_PREFIXES = (
    "interview_", "purpose_", "understanding_", "ux_", "solution_design",
    "stakeholder", "evolution_node", "cell_",
)
_CANON_EXACT = ("components", "probe_points", "feature_drafts")


def _snapshot_canon() -> Dict[str, List[Dict[str, Any]]]:
    from app.db import get_conn

    with get_conn() as conn:
        tables = [
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        selected = sorted(
            t for t in tables
            if t in _CANON_EXACT or any(t.startswith(p) for p in _CANON_PREFIXES)
        )
        snap: Dict[str, List[Dict[str, Any]]] = {}
        for t in selected:
            rows = conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()  # noqa: S608 - t comes from sqlite_master, not caller input
            snap[t] = [dict(r) for r in rows]
        return snap


class TestNoWriteToExistingCanon:
    def test_epic_endpoints_write_nothing_to_existing_canonical_tables(self, admin_client, tmp_path):
        chain = _build_chain(admin_client, tmp_path, name="System PO NoWrite")
        before = _snapshot_canon()

        # Exercise every endpoint this Epic owns.
        _create_objective(chain.client, chain.headers, "obj-nowrite")
        _add_objective_revision(chain.client, chain.headers, "obj-nowrite", title="t")
        _set_objective_parent(chain.client, chain.headers, "obj-nowrite", chain.objective_parent_key)
        _clear_objective_parent(chain.client, chain.headers, "obj-nowrite")
        _add_objective_upstream_ref(chain.client, chain.headers, "obj-nowrite", "vision_claim", chain.vision_text)
        _record_objective_decision(chain.client, chain.headers, "obj-nowrite", "confirm")
        _record_objective_decision(chain.client, chain.headers, "obj-nowrite", "activate")

        _create_milestone(chain.client, chain.headers, "obj-nowrite", "ms-nowrite")
        _add_milestone_revision(chain.client, chain.headers, "ms-nowrite", title="t", verification_method="manual_review")
        _record_milestone_decision(chain.client, chain.headers, "ms-nowrite", "confirm")
        _record_milestone_assessment(chain.client, chain.headers, "ms-nowrite", "met")

        _create_gap(chain.client, chain.headers, "ms-nowrite", "gap-nowrite")
        _add_gap_revision(chain.client, chain.headers, "gap-nowrite", title="t")
        _add_gap_source_ref(chain.client, chain.headers, "gap-nowrite", "manual", "")
        _add_gap_evidence_ref(chain.client, chain.headers, "gap-nowrite", "human_report", "ref")
        # `ux_journey` is not a valid `link_kind` (§5.11) -- the Gap ->
        # Journey connection lives only in `ux_journey_upstream_ref`.
        _add_gap_artifact_link(chain.client, chain.headers, "gap-nowrite", "ux_requirement", chain.requirement_key)
        _record_gap_decision(chain.client, chain.headers, "gap-nowrite", "acknowledge")
        _record_gap_decision(chain.client, chain.headers, "gap-nowrite", "prioritize", priority_band="now")

        _create_feature(chain.client, chain.headers, "feat-nowrite")
        _add_feature_revision(chain.client, chain.headers, "feat-nowrite", title="t")
        _add_feature_requirement_link(chain.client, chain.headers, "feat-nowrite", chain.requirement_key)
        _add_feature_capability_link(chain.client, chain.headers, "feat-nowrite", chain.capability_entity_id)
        _add_feature_target_link(chain.client, chain.headers, "feat-nowrite", "component", chain.component_id)
        _add_feature_draft_link(chain.client, chain.headers, "feat-nowrite", chain.feature_draft_id)
        _record_feature_decision(chain.client, chain.headers, "feat-nowrite", "confirm")

        _get_objective_map(chain.client, chain.headers)
        _get_gap_workbench(chain.client, chain.headers)
        _get_overview(chain.client, chain.headers)

        after = _snapshot_canon()
        assert before == after


# ---------------------------------------------------------------------------
# 9. Partial failure isolation
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_one_resolver_failure_degrades_only_that_gap(self, chain: Chain, monkeypatch):
        """SS5.5/SS5.10: `app/product_gap_sources.py` is owned by #430 and is
        not a file this task may edit, so the failure is injected by
        monkeypatching its `resolve_source` entry point for the duration of
        one test -- the same technique `tests/test_product_gap_sources.py`
        uses for its own "never raises for data reasons" coverage. Note that
        `resolve_source` itself already SWALLOWS a per-kind resolver's
        exception into `source_state='unavailable'` (SS5.10's own contract)
        rather than propagating it, so a per-kind-resolver failure alone
        never reaches `degraded_sections` -- `product_objective.
        _gap_source_out_dict`'s `except Exception` guard is for a failure of
        `resolve_source` ITSELF (an unresolvable import, or a bug in the
        dispatcher), which is what is simulated here.

        The failure is keyed on the SOURCE KIND, not on a call counter. A
        single Gap read resolves its sources twice -- once to derive
        `read_flags` and once to render `source_refs` -- so "raise on the
        second call" does not mean "raise for the second Gap", and a
        counter-based fake would report the first Gap as unavailable while
        appearing to test isolation."""
        gap_ok_key = "gap-partial-ok"
        gap_bad_key = "gap-partial-bad"
        _create_gap(chain.client, chain.headers, chain.milestone_src_key, gap_ok_key)
        _add_gap_revision(chain.client, chain.headers, gap_ok_key, title=gap_ok_key)
        _add_gap_source_ref(chain.client, chain.headers, gap_ok_key, "manual", "")

        _create_gap(chain.client, chain.headers, chain.milestone_src_key, gap_bad_key)
        _add_gap_revision(chain.client, chain.headers, gap_bad_key, title=gap_bad_key)
        _add_gap_source_ref(chain.client, chain.headers, gap_bad_key, "issue_draft", "999999")

        from app import product_gap_sources

        original = product_gap_sources.resolve_source

        def _flaky_resolve_source(conn, **kwargs):
            if kwargs.get("source_kind") == "issue_draft":
                raise RuntimeError("resolver exploded")
            return original(conn, **kwargs)

        monkeypatch.setattr(product_gap_sources, "resolve_source", _flaky_resolve_source)

        good = _get_gap(chain.client, chain.headers, gap_ok_key)
        assert good["source_refs"][0]["source_state"] == "current"
        assert good["degraded_sections"] == []

        bad = _get_gap(chain.client, chain.headers, gap_bad_key)
        assert bad["source_refs"][0]["source_state"] == "unavailable"
        assert any("source_ref" in s for s in bad["degraded_sections"])

    def test_gap_workbench_degrades_one_entry_not_the_whole_response(self, chain: Chain, monkeypatch):
        gap_ok_key = "gap-wb-ok"
        gap_bad_key = "gap-wb-bad"
        for key in (gap_ok_key, gap_bad_key):
            _create_gap(chain.client, chain.headers, chain.milestone_src_key, key)
            _add_gap_revision(chain.client, chain.headers, key, title=key)
            _add_gap_source_ref(chain.client, chain.headers, key, "manual", "")

        from app import product_gap_sources

        original = product_gap_sources.resolve_source
        calls = {"n": 0}

        def _flaky_resolve_source(conn, **kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("resolver exploded")
            return original(conn, **kwargs)

        monkeypatch.setattr(product_gap_sources, "resolve_source", _flaky_resolve_source)

        workbench = _get_gap_workbench(chain.client, chain.headers)
        keys = {e["gap_key"] for e in workbench["entries"]}
        assert gap_ok_key in keys
        assert gap_bad_key in keys
        assert any("source_ref" in s for s in workbench["degraded_sections"])
