"""Tests for Issue #407 -- UX Design Lineage: Journey / Step / Requirement /
Acceptance Criterion / Artifact Reference (Epic #405).

`docs/ux-design-lineage.md` §2.11 is the acceptance list this file is
organized around:

1. revision / supersede / manual decision / `created_by` / source digest
   persist and read back the same after a reload.
2. another System's `journey_key` / `requirement_key` / `baseline_journey_id`
   / `step_key` never leaks or links across Systems.
3. `unknown` (recorded nothing) / `unavailable` (the read failed) /
   `not_applicable` (structurally inapplicable) are three distinct answers.
4. Journey / Step / Requirement are never deleted or overwritten -- past
   revisions and the decisions made against them stay readable.
5. `design_status` is derived from the decision ledger, never a stored
   column.
6. editing content flips `recheck_state` to `stale` while `design_status`
   stays `confirmed` and the decision row survives untouched.
7. an `authored_by_kind="reasoning_model"` revision never reaches
   `confirmed` without an explicit manual decision.
8. `out_of_scope` Requirements can never carry acceptance criteria.
9. Artifact References store no body; only a `repo:<path>` URI the system
   itself resolved via `git show` on a pinned snapshot can reach `verified`.
10. `content_digest` reproduces across a reload.

Plus, per the task brief: the four upstream-reference axes (§2.7) are
independent; `baseline_state`'s `greenfield` vs `undecided` distinction;
downstream-only change propagation (§2.9); exact `step_key`/`criterion_key`
diff matching (§0 invariant 9); a baseline diff with no baseline never
returns an empty diff; guarded per-section degradation; and that this whole
layer writes to no pre-existing canonical table (§5).

Part 1 exercises a few pure/first-match functions directly (mirroring
`test_purpose_chain.py`'s own style); Part 2 drives the real HTTP API via
`TestClient`, reusing that same file's fixture/helper shape closely.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import ux_design


# ---------------------------------------------------------------------------
# Part 1: pure functions / finite vocabularies
# ---------------------------------------------------------------------------


class TestFiniteVocabularies:
    """§0 invariant 9 / §2.5-§2.8: every finite vocabulary this module
    exposes matches the documented contract exactly -- no silently added or
    dropped member."""

    def test_vocabularies_match_the_documented_contract(self):
        assert set(ux_design.PERSPECTIVES) == {"as_is", "to_be"}
        assert set(ux_design.BASELINE_MODES) == {"linked", "greenfield", "undecided"}
        assert set(ux_design.BASELINE_STATES) == {
            "linked", "unresolved", "absent", "not_applicable",
        }
        assert set(ux_design.AUTHORSHIP_KINDS) == {"developer", "reasoning_model"}
        assert set(ux_design.EVIDENCE_SOURCE_KINDS) == {
            "runtime_trace", "human_report", "external_analytics", "none",
        }
        assert set(ux_design.REQUIREMENT_KINDS) == {
            "functional", "non_functional", "constraint", "out_of_scope",
        }
        assert set(ux_design.VERIFICATION_METHODS) == {
            "manual_review", "replay", "experiment", "runtime_observation", "not_verifiable",
        }
        assert set(ux_design.DESIGN_STATUSES) == {"proposed", "confirmed", "rejected", "retired"}
        assert set(ux_design.DESIGN_DECISION_KINDS) == {"confirm", "reject", "retire", "reinstate"}
        assert set(ux_design.DESIGN_RECHECK_STATES) == {"current", "stale"}
        assert set(ux_design.REVISION_STATES) == {"current", "superseded"}
        assert set(ux_design.REF_KINDS) == {
            "purpose_element", "purpose_relation", "capability_entity",
        }
        assert set(ux_design.REF_RELATION_STATUSES) == {"confirmed", "proposed", "derived"}
        assert set(ux_design.REF_TARGET_RESOLUTIONS) == {"resolved", "unresolved", "unavailable"}
        assert set(ux_design.REF_RECHECK_STATES) == {"current", "stale", "not_captured"}
        assert set(ux_design.ARTIFACT_KINDS) == {
            "wireframe", "adr", "spec", "diagram", "research_note", "other",
        }
        assert set(ux_design.ARTIFACT_VERIFICATION_STATES) == {
            "verified", "unverified", "unreachable",
        }
        assert set(ux_design.DIFF_CHANGE_KINDS) == {"added", "removed", "changed", "unchanged"}
        assert set(ux_design.DIFF_STATES) == {"available", "not_applicable", "unavailable"}

    def test_decision_method_to_relation_status_is_a_fixed_table(self):
        assert ux_design._DECISION_METHOD_TO_RELATION_STATUS == {
            "manual": "confirmed", "reasoning_llm": "proposed", "deterministic": "derived",
        }

    def test_decision_to_design_status_is_a_fixed_table(self):
        assert ux_design._DECISION_TO_DESIGN_STATUS == {
            "confirm": "confirmed", "reject": "rejected", "retire": "retired", "reinstate": "proposed",
        }


class TestDigestPureFunctions:
    """§2.6: digests are pure, exclude bookkeeping columns, and are
    reproducible regardless of the caller's own iteration order."""

    def test_step_order_excluded_from_the_steps_own_digest(self):
        base = dict(
            step_key="s1", user_intent="x", system_response="", success_criteria="",
            failure_mode="", recovery_path="", evidence_expectation="", evidence_source_kind="none",
        )
        # step_order is not even a parameter of journey_step_digest -- the
        # same fields always hash the same regardless of where the step
        # later ends up in its Journey.
        assert ux_design.journey_step_digest(**base) == ux_design.journey_step_digest(**base)

    def test_journey_revision_digest_is_independent_of_caller_iteration_order(self):
        step_a_digest = ux_design.journey_step_digest(
            step_key="a", user_intent="x", system_response="", success_criteria="",
            failure_mode="", recovery_path="", evidence_expectation="", evidence_source_kind="none",
        )
        step_b_digest = ux_design.journey_step_digest(
            step_key="b", user_intent="y", system_response="", success_criteria="",
            failure_mode="", recovery_path="", evidence_expectation="", evidence_source_kind="none",
        )
        steps = [
            {"step_key": "a", "step_order": 0, "content_digest": step_a_digest},
            {"step_key": "b", "step_order": 1, "content_digest": step_b_digest},
        ]
        forward = ux_design.journey_revision_digest(
            title="t", beneficiary="", usage_context="", entry_trigger="", value_arrival="",
            summary="", steps=steps,
        )
        backward = ux_design.journey_revision_digest(
            title="t", beneficiary="", usage_context="", entry_trigger="", value_arrival="",
            summary="", steps=list(reversed(steps)),
        )
        assert forward == backward

        # step_order DOES change the journey's own digest -- reordering is a
        # meaningful change at this level, unlike at the step's own level.
        reordered_steps = [
            {"step_key": "a", "step_order": 1, "content_digest": step_a_digest},
            {"step_key": "b", "step_order": 0, "content_digest": step_b_digest},
        ]
        reordered = ux_design.journey_revision_digest(
            title="t", beneficiary="", usage_context="", entry_trigger="", value_arrival="",
            summary="", steps=reordered_steps,
        )
        assert reordered != forward

    def test_requirement_revision_digest_criteria_order_input_is_irrelevant(self):
        c1 = ux_design.acceptance_criterion_digest(
            criterion_key="c1", statement="s1", verification_method="manual_review", verification_note="",
        )
        c2 = ux_design.acceptance_criterion_digest(
            criterion_key="c2", statement="s2", verification_method="manual_review", verification_note="",
        )
        criteria = [
            {"criterion_key": "c1", "content_digest": c1},
            {"criterion_key": "c2", "content_digest": c2},
        ]
        forward = ux_design.requirement_revision_digest(
            requirement_kind="functional", statement="", rationale="", constraint_text="",
            out_of_scope_note="", criteria=criteria,
        )
        backward = ux_design.requirement_revision_digest(
            requirement_kind="functional", statement="", rationale="", constraint_text="",
            out_of_scope_note="", criteria=list(reversed(criteria)),
        )
        assert forward == backward


# ---------------------------------------------------------------------------
# Part 2: HTTP API, fixtures/helpers (closely mirrors test_purpose_chain.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ux-design-test.db"))
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


def _remove_file_and_commit(repo, rel_path):
    os.remove(os.path.join(repo, rel_path))
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "remove"], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


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


def _setup(client, tmp_path, name="System UX", files=None):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo_with_files(
        tmp_path, f"repo-{name.replace(' ', '-')}", files or {"a.py": b"def a():\n    return 1\n"}
    )
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id, repo


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
    return r.json() if expect < 300 else r


def _add_upstream_ref(client, headers, journey_key, ref_kind, target_ref, *, note="", expect=201):
    r = client.post(
        f"/ux-design/journeys/{journey_key}/upstream-refs",
        json={"ref_kind": ref_kind, "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_requirement(client, headers, requirement_key, requirement_kind="functional", expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": requirement_kind},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_revision(client, headers, requirement_key, *, acceptance_criteria=None, expect=201, **fields):
    payload = {
        "statement": "", "rationale": "", "constraint_text": "", "out_of_scope_note": "",
        "change_note": "", "acceptance_criteria": acceptance_criteria or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/requirements/{requirement_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_requirement(client, headers, requirement_key, expect=200):
    r = client.get(f"/ux-design/requirements/{requirement_key}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_revision_domain(system_id, requirement_key, *, acceptance_criteria=None, created_by="root", **fields):
    """Domain-level equivalent of `_add_requirement_revision`, used as a
    workaround where noted: `POST /ux-design/requirements/{key}/revisions`
    always 500s once it actually returns a revision (see
    `TestRequirementKindOnRevisionProjection` below) because
    `UxRequirementRevisionOut.requirement_kind` is a required response field
    with no corresponding column. `ux_design.add_requirement_revision`
    itself already returns the same `get_requirement_detail(...)` shape the
    route would have serialized, so calling it directly exercises the real
    domain behaviour without going through the broken response model."""
    from app.db import get_conn

    payload = {
        "statement": "", "rationale": "", "constraint_text": "", "out_of_scope_note": "",
        "change_note": "", "acceptance_criteria": acceptance_criteria or [],
    }
    payload.update(fields)
    with get_conn() as conn:
        return ux_design.add_requirement_revision(
            conn, system_id=system_id, requirement_key=requirement_key,
            statement=payload["statement"], rationale=payload["rationale"],
            constraint_text=payload["constraint_text"], out_of_scope_note=payload["out_of_scope_note"],
            change_note=payload["change_note"], acceptance_criteria=payload["acceptance_criteria"],
            created_by=created_by,
        )


def _get_requirement_domain(system_id, requirement_key):
    from app.db import get_conn

    with get_conn() as conn:
        return ux_design.get_requirement_detail(conn, system_id, requirement_key)


def _add_step_link(client, headers, requirement_key, journey_key, step_key, *, note="", expect=201):
    r = client.post(
        f"/ux-design/requirements/{requirement_key}/step-links",
        json={"journey_key": journey_key, "step_key": step_key, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_artifact_reference(
    client, headers, *, subject_kind, subject_key, uri, content_hash,
    artifact_kind="wireframe", title="", media_type="", byte_size=None, expect=201,
):
    r = client.post(
        "/ux-design/artifact-references",
        json={
            "subject_kind": subject_kind, "subject_key": subject_key, "artifact_kind": artifact_kind,
            "title": title, "uri": uri, "media_type": media_type, "content_hash": content_hash,
            "byte_size": byte_size,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide(client, headers, subject_kind, subject_key, decision, *, rationale="", captured_digest="", expect=201):
    r = client.post(
        "/ux-design/decisions",
        json={
            "subject_kind": subject_kind, "subject_key": subject_key, "decision": decision,
            "rationale": rationale, "captured_digest": captured_digest,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


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


def _supersede_capability_head(system_id, session_id):
    """Insert a new head confirmation carrying NO entity_version rows, so
    every existing entity's latest version now belongs to an older,
    non-head confirmation (`superseded`)."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, 'd2', 'root', 'manual', ?)""",
            (system_id, session_id, now),
        )


# --- §2.11 item 3 / §2.3: baseline_state ------------------------------------


class TestJourneyBaselineState:
    """§2.3's 5-row first-match `baseline_state` derivation. All four
    documented values are reachable, and `greenfield` (an explicit developer
    declaration) is kept distinct from `undecided` (nobody has decided) even
    though both currently have no linked baseline."""

    def test_as_is_journey_baseline_state_is_not_applicable_regardless_of_mode(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline AsIs")
        headers = _headers(token, system_id)
        row = _create_journey(admin_client, headers, "as-is", perspective="as_is", baseline_mode="linked")
        assert row["baseline_state"] == "not_applicable"

    def test_greenfield_declared_baseline_state_is_not_applicable(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Greenfield")
        headers = _headers(token, system_id)
        row = _create_journey(
            admin_client, headers, "to-be", perspective="to_be", baseline_mode="greenfield",
        )
        assert row["baseline_state"] == "not_applicable"

    def test_undecided_baseline_state_is_absent_not_not_applicable(self, admin_client, tmp_path):
        """The distinction the contract exists to preserve: `undecided`
        (nobody has decided) must never read the same as `greenfield` (the
        developer explicitly declared there is nothing to compare)."""
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Undecided")
        headers = _headers(token, system_id)
        row = _create_journey(admin_client, headers, "to-be", perspective="to_be")
        assert row["baseline_mode"] == "undecided"
        assert row["baseline_state"] == "absent"
        assert row["baseline_state"] != "not_applicable"

    def test_linked_baseline_state_when_target_resolves(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Linked")
        headers = _headers(token, system_id)
        as_is = _create_journey(admin_client, headers, "as-is", perspective="as_is")
        to_be = _create_journey(
            admin_client, headers, "to-be", perspective="to_be",
            baseline_mode="linked", baseline_journey_id=as_is["id"],
        )
        assert to_be["baseline_state"] == "linked"
        assert to_be["baseline_journey_key"] == "as-is"

    def test_unresolved_baseline_state_when_target_no_longer_resolves(self, admin_client, tmp_path):
        """Reached via `journey_baseline_state` (a pure first-match helper)
        directly -- the API's own create-time validation never lets a
        `linked` mode persist a dangling id, so this row 5 is exercised the
        same way the domain module documents it."""
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Unresolved")
        headers = _headers(token, system_id)
        as_is = _create_journey(admin_client, headers, "as-is", perspective="as_is")
        _create_journey(
            admin_client, headers, "to-be", perspective="to_be",
            baseline_mode="linked", baseline_journey_id=as_is["id"],
        )

        # a real row that exists (so the FK is satisfied) but is NOT an
        # as_is Journey -- the API's own create-time check would refuse
        # this, so it is simulated with a direct UPDATE to exercise row 5
        # of the first-match table on its own.
        other_to_be = _create_journey(admin_client, headers, "other-to-be", perspective="to_be")

        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE ux_journey SET baseline_journey_id = ? WHERE journey_key = ?",
                (other_to_be["id"], "to-be"),
            )
            row = dict(
                conn.execute("SELECT * FROM ux_journey WHERE journey_key = ?", ("to-be",)).fetchone()
            )
            state = ux_design.journey_baseline_state(conn, system_id, row)
        assert state == "unresolved"

    def test_baseline_journey_id_must_point_at_an_as_is_journey(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline NotAsIs")
        headers = _headers(token, system_id)
        other_to_be = _create_journey(admin_client, headers, "to-be-1", perspective="to_be")
        r = admin_client.post(
            "/ux-design/journeys",
            json={
                "journey_key": "to-be-2", "perspective": "to_be", "baseline_mode": "linked",
                "baseline_journey_id": other_to_be["id"],
            },
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "journey_baseline_not_as_is"


# --- §2.7: upstream reference resolution, four independent axes -------------


class TestUpstreamRefFourAxes:
    """§2.7: `relation_status` (who asserted the reference), `target_state`
    (the target's OWN verbatim value), `target_resolution` (could the source
    even be read), and `recheck_state` (has the target's content moved) are
    four separate axes -- never folded into one."""

    def test_relation_status_confirmed_and_target_state_unknown_are_independent(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Ref Axes")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)  # no pain/goal ever set
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        ref = _add_upstream_ref(
            admin_client, headers, "j1", "purpose_relation",
            "problem_to_change:beneficiary_problem->desired_change",
        )
        # decision_method is forced 'manual' by the route -> WHO asserted
        # this reference is 'confirmed'...
        assert ref["relation_status"] == "confirmed"
        # ...which says nothing about the relation's OWN status: neither
        # pain nor goal was ever recorded, so the target itself is 'unknown'.
        assert ref["target_state"] == "unknown"
        assert ref["target_resolution"] == "resolved"

    def test_empty_captured_digest_reads_not_captured_never_current(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Ref NotCaptured")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        ref = _add_upstream_ref(
            admin_client, headers, "j1", "purpose_element", "core_capability:doesnotexist",
        )
        assert ref["target_resolution"] == "unresolved"
        assert ref["captured_digest"] == ""
        assert ref["recheck_state"] == "not_captured"
        assert ref["recheck_state"] != "current"

    def test_target_resolution_unavailable_when_the_canonical_source_cannot_be_read(
        self, admin_client, tmp_path, monkeypatch
    ):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Ref Unavailable")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        ref = _add_upstream_ref(admin_client, headers, "j1", "purpose_element", "beneficiary_problem")
        assert ref["target_resolution"] == "resolved"

        def _boom(*a, **kw):
            raise RuntimeError("purpose chain unreadable")

        monkeypatch.setattr(ux_design.purpose_chain, "derive_purpose_chain", _boom)

        detail = _get_journey(admin_client, headers, "j1")
        got = next(r for r in detail["upstream_refs"] if r["id"] == ref["id"])
        assert got["target_resolution"] == "unavailable"

    def test_capability_entity_ref_resolves_confirmed_against_the_head(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Cap Confirmed")
        headers = _headers(token, system_id)
        session_id = _create_session(admin_client, headers, snapshot_id)
        entity_id = _insert_capability_entity(system_id, session_id, "Billing")
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        ref = _add_upstream_ref(admin_client, headers, "j1", "capability_entity", str(entity_id))
        assert ref["target_resolution"] == "resolved"
        assert ref["target_state"] == "confirmed"
        assert ref["target_name"] == "Billing"

    def test_capability_entity_target_state_moves_to_superseded_without_translation(
        self, admin_client, tmp_path
    ):
        """§2.9: a Capability falling out of the current head moves
        `target_state` to `superseded` while `target_resolution` STAYS
        `resolved` -- the entity still exists, it just is not current."""
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Cap Superseded")
        headers = _headers(token, system_id)
        session_id = _create_session(admin_client, headers, snapshot_id)
        entity_id = _insert_capability_entity(system_id, session_id, "Billing")
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        ref = _add_upstream_ref(admin_client, headers, "j1", "capability_entity", str(entity_id))
        assert ref["target_state"] == "confirmed"

        _supersede_capability_head(system_id, session_id)

        detail = _get_journey(admin_client, headers, "j1")
        got = next(r for r in detail["upstream_refs"] if r["id"] == ref["id"])
        assert got["target_resolution"] == "resolved"
        assert got["target_state"] == "superseded"
        assert got["recheck_state"] == "stale"


class TestThreeEmptyAnswersAreDistinct:
    """§0 invariant 8: `unknown` (read fine, nothing recorded), `unavailable`
    (the read itself failed), and `not_applicable` (structurally
    inapplicable) are three separate answers, never rounded into the same
    value or displayed with the same message."""

    def test_unknown_unavailable_and_not_applicable_never_share_a_value(
        self, admin_client, tmp_path
    ):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Empty Answers")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)

        # unknown -- read fine, nothing recorded there yet.
        _create_journey(admin_client, headers, "j-unknown", perspective="to_be")
        ref = _add_upstream_ref(admin_client, headers, "j-unknown", "purpose_element", "beneficiary_problem")
        unknown_value = ref["target_state"]
        assert unknown_value == "unknown"

        # unavailable -- the read of the canonical source itself failed.
        # Scoped to its own MonkeyPatch instance (never `.undo()` on the
        # shared `monkeypatch` fixture here -- that would also revert the
        # `admin_client` fixture's own PROBE_DB_PATH env patch).
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                ux_design.purpose_chain, "derive_purpose_chain",
                lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            detail = _get_journey(admin_client, headers, "j-unknown")
        got = next(r for r in detail["upstream_refs"] if r["id"] == ref["id"])
        unavailable_value = got["target_resolution"]
        assert unavailable_value == "unavailable"

        # not_applicable -- the concept structurally does not apply: a
        # greenfield to_be Journey has explicitly declared there is no
        # as_is baseline to compare against.
        greenfield = _create_journey(
            admin_client, headers, "j-greenfield", perspective="to_be", baseline_mode="greenfield",
        )
        not_applicable_value = greenfield["baseline_state"]
        assert not_applicable_value == "not_applicable"

        values = {unknown_value, unavailable_value, not_applicable_value}
        assert len(values) == 3, "unknown / unavailable / not_applicable must never collapse together"


# --- §2.5: design_status derived, decision ledger, recheck_state ------------


class TestDesignStatusDerivedNotStored:
    """§2.11 items 5-7: `design_status` is folded from the decision ledger
    at read time; it is never a stored column, a content edit never revokes
    a decision (only staleness moves), and an AI-authored revision never
    becomes `confirmed` on its own."""

    def test_no_status_column_exists_on_the_identity_tables(self, admin_client, tmp_path):
        from app.db import get_conn

        _setup(admin_client, tmp_path, "System Schema Check")
        with get_conn() as conn:
            journey_cols = {r["name"] for r in conn.execute("PRAGMA table_info(ux_journey)").fetchall()}
            requirement_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(ux_requirement)").fetchall()
            }
        for cols in (journey_cols, requirement_cols):
            assert "status" not in cols
            assert "design_status" not in cols

    def test_decision_ledger_walk_is_append_only_and_derives_the_status(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Ledger Walk")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1")

        assert _get_journey(admin_client, headers, "j1")["design_status"] == "proposed"

        for decision, expected in [
            ("confirm", "confirmed"),
            ("reject", "rejected"),
            ("reinstate", "proposed"),
            ("confirm", "confirmed"),
            ("retire", "retired"),
            ("reinstate", "proposed"),
        ]:
            _decide(admin_client, headers, "journey", "j1", decision)
            assert _get_journey(admin_client, headers, "j1")["design_status"] == expected

        decisions = _get_journey(admin_client, headers, "j1")["decisions"]
        assert len(decisions) == 6
        assert [d["decision"] for d in decisions] == list(
            reversed(["confirm", "reject", "reinstate", "confirm", "retire", "reinstate"])
        )
        assert all(d["decision_method"] == "manual" for d in decisions)
        assert all(d["decided_by"] == "root" for d in decisions)
        # append-only: every row but the current one is superseded, none deleted
        assert decisions[0]["superseded_by_id"] is None
        assert all(d["superseded_by_id"] is not None for d in decisions[1:])

    def test_confirm_then_content_edit_leaves_confirmed_but_stale(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Stale Confirm")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1")
        _decide(admin_client, headers, "journey", "j1", "confirm")

        before = _get_journey(admin_client, headers, "j1")
        assert before["design_status"] == "confirmed"
        assert before["recheck_state"] == "current"

        _add_journey_revision(admin_client, headers, "j1", title="v2 (edited)")
        after = _get_journey(admin_client, headers, "j1")

        assert after["design_status"] == "confirmed"  # the decision is NOT revoked
        assert after["recheck_state"] == "stale"       # but content moved past it
        assert after["decisions"] == before["decisions"]  # the decision row is untouched

    def test_reasoning_model_authored_revision_never_auto_confirms(self, admin_client, tmp_path):
        """The public write endpoint never accepts `authored_by_kind` /
        `decision_method` from the body (see next class); the only way to
        even construct this case is the domain function directly, which is
        exactly what its own docstring says this item is tested through."""
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System AI Authored")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        from app.db import get_conn

        with get_conn() as conn:
            ux_design.add_journey_revision(
                conn, system_id=system_id, journey_key="j1", title="AI proposed journey", steps=[],
                authored_by_kind="reasoning_model", decision_method="reasoning_llm",
                intelligence_run_id=None, created_by="ai-agent",
            )

        detail = _get_journey(admin_client, headers, "j1")
        assert detail["current_revision"]["authored_by_kind"] == "reasoning_model"
        assert detail["current_revision"]["decision_method"] == "reasoning_llm"
        assert detail["design_status"] == "proposed"

        _decide(admin_client, headers, "journey", "j1", "confirm")
        confirmed = _get_journey(admin_client, headers, "j1")
        assert confirmed["design_status"] == "confirmed"
        assert confirmed["decisions"][0]["decision_method"] == "manual"
        assert confirmed["decisions"][0]["decided_by"] == "root"

    def test_confirm_on_retired_without_reinstate_first_is_rejected(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System NotDecidable A")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _decide(admin_client, headers, "journey", "j1", "confirm")
        _decide(admin_client, headers, "journey", "j1", "retire")

        r = admin_client.post(
            "/ux-design/decisions",
            json={"subject_kind": "journey", "subject_key": "j1", "decision": "confirm"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "ux_design_not_decidable"

    def test_reinstate_on_a_never_decided_subject_is_rejected(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System NotDecidable B")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        r = admin_client.post(
            "/ux-design/decisions",
            json={"subject_kind": "journey", "subject_key": "j1", "decision": "reinstate"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "ux_design_not_decidable"

    def test_stale_captured_digest_is_a_409_conflict(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Stale Digest")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1")

        r = admin_client.post(
            "/ux-design/decisions",
            json={
                "subject_kind": "journey", "subject_key": "j1", "decision": "confirm",
                "captured_digest": "not-the-real-digest",
            },
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "ux_design_decision_stale_digest"

    def test_get_never_writes_a_decision(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System GET Read Only")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _get_journey(admin_client, headers, "j1")
        _get_journey(admin_client, headers, "j1")
        detail = _get_journey(admin_client, headers, "j1")
        assert detail["design_status"] == "proposed"
        assert detail["decisions"] == []


class TestPrincipalDerivedAuditFields:
    """§2.10: `created_by` / `decided_by` / `decision_method` /
    `authored_by_kind` are never accepted from a request body -- every write
    model is `extra=\"forbid\"` and the value always comes from the
    authenticated Principal."""

    def test_created_by_in_the_body_is_rejected(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Forbid CreatedBy")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "j1", "perspective": "to_be", "created_by": "someone-else"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_authored_by_kind_in_the_body_is_rejected(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Forbid AuthoredBy")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        r = admin_client.post(
            "/ux-design/journeys/j1/revisions",
            json={"title": "x", "steps": [], "authored_by_kind": "reasoning_model"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_decision_method_in_the_body_is_rejected(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Forbid DecisionMethod")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        r = admin_client.post(
            "/ux-design/decisions",
            json={
                "subject_kind": "journey", "subject_key": "j1", "decision": "confirm",
                "decision_method": "reasoning_llm",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_created_by_is_the_authenticated_principal(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Actual CreatedBy")
        headers = _headers(token, system_id)
        row = _create_journey(admin_client, headers, "j1", perspective="to_be")
        assert row["created_by"] == "root"


# --- §2.11 items 1 & 4: append-only revisions, persistence across reload ----


class TestRevisionsAreAppendOnly:
    def test_revision_chain_supersedes_without_mutating_old_content(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Revision Chain")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        r1 = _add_journey_revision(admin_client, headers, "j1", title="v1", change_note="first")
        rev1_id = r1["current_revision"]["id"]
        r2 = _add_journey_revision(admin_client, headers, "j1", title="v2", change_note="second")
        rev2_id = r2["current_revision"]["id"]

        revs = admin_client.get("/ux-design/journeys/j1/revisions", headers=headers).json()["revisions"]
        by_id = {rv["id"]: rv for rv in revs}

        assert by_id[rev1_id]["revision_state"] == "superseded"
        assert by_id[rev1_id]["superseded_by_id"] == rev2_id
        assert by_id[rev1_id]["title"] == "v1"  # old content survives unmutated
        assert by_id[rev1_id]["change_note"] == "first"
        assert by_id[rev2_id]["revision_state"] == "current"
        assert by_id[rev2_id]["title"] == "v2"
        assert by_id[rev1_id]["created_by"] == "root"
        assert by_id[rev2_id]["created_by"] == "root"

        # reload reproduces exactly the same persisted values
        revs_again = admin_client.get(
            "/ux-design/journeys/j1/revisions", headers=headers
        ).json()["revisions"]
        assert revs_again == revs

    def test_requirement_revision_chain_and_criteria_persist_across_reload(self, admin_client, tmp_path):
        # NOTE: reads/writes of a Requirement's OWN revision content go
        # through the domain layer directly here, not the HTTP route --
        # see `TestRequirementKindOnRevisionProjection` for why (the route's
        # response model 500s on any real revision content).
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Requirement Chain")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1")
        _add_requirement_revision_domain(
            system_id, "r1", statement="v1",
            acceptance_criteria=[_criterion("c1", 0, statement="first criterion")],
        )
        detail_before = _get_requirement_domain(system_id, "r1")
        detail_after = _get_requirement_domain(system_id, "r1")
        assert detail_before == detail_after
        assert detail_after["current_revision"]["acceptance_criteria"][0]["statement"] == "first criterion"
        assert detail_after["current_revision"]["created_by"] == "root"


# --- §2.11 item 2: System isolation ------------------------------------------


class TestSystemIsolation:
    def test_journey_key_is_isolated_across_systems(self, admin_client, tmp_path):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Journey A")
        system_b = _create_system(admin_client, token, "System Iso Journey B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _create_journey(admin_client, headers_a, "shared-key", perspective="to_be")
        # the same key is free to reuse in a different System
        _create_journey(admin_client, headers_b, "shared-key", perspective="as_is")

        got_b = _get_journey(admin_client, headers_b, "shared-key")
        assert got_b["perspective"] == "as_is"  # System B's own row, not A's

        listed_b = admin_client.get("/ux-design/journeys", headers=headers_b).json()["journeys"]
        assert len(listed_b) == 1

    def test_journey_not_visible_from_a_foreign_system(self, admin_client, tmp_path):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Journey Foreign A")
        system_b = _create_system(admin_client, token, "System Iso Journey Foreign B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        _create_journey(admin_client, headers_a, "only-in-a", perspective="to_be")

        r = admin_client.get("/ux-design/journeys/only-in-a", headers=headers_b)
        assert r.status_code == 404

    def test_requirement_key_is_isolated_across_systems(self, admin_client, tmp_path):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Iso Req A")
        system_b = _create_system(admin_client, token, "System Iso Req B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _create_requirement(admin_client, headers_a, "shared-req", requirement_kind="functional")
        _create_requirement(admin_client, headers_b, "shared-req", requirement_kind="out_of_scope")

        got_b = _get_requirement(admin_client, headers_b, "shared-req")
        assert got_b["requirement_kind"] == "out_of_scope"

        r = admin_client.get("/ux-design/requirements/does-not-exist-anywhere", headers=headers_a)
        assert r.status_code == 404

    def test_baseline_journey_id_cannot_point_at_another_system(self, admin_client, tmp_path):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Baseline Iso A")
        system_b = _create_system(admin_client, token, "System Baseline Iso B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        as_is_a = _create_journey(admin_client, headers_a, "as-is", perspective="as_is")

        r = admin_client.post(
            "/ux-design/journeys",
            json={
                "journey_key": "to-be", "perspective": "to_be", "baseline_mode": "linked",
                "baseline_journey_id": as_is_a["id"],
            },
            headers=headers_b,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "journey_baseline_foreign_system"

    def test_step_link_cannot_join_a_requirement_and_journey_across_systems(self, admin_client, tmp_path):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System Step Iso A")
        system_b = _create_system(admin_client, token, "System Step Iso B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _create_journey(admin_client, headers_b, "j-b", perspective="to_be")
        _add_journey_revision(admin_client, headers_b, "j-b", steps=[_step("s1", 0)])
        _create_requirement(admin_client, headers_a, "req-a")

        # the Requirement lives in A; A cannot reach B's Journey/step
        r = admin_client.post(
            "/ux-design/requirements/req-a/step-links",
            json={"journey_key": "j-b", "step_key": "s1"},
            headers=headers_a,
        )
        assert r.status_code == 404


# --- §2.9: downstream-only propagation ---------------------------------------


class TestDownstreamOnlyPropagation:
    """§2.9: change propagation flows downstream only -- editing a
    Requirement never makes its Journey stale, and a vanished Journey Step
    (rather than an edited one) reads `unresolved` instead of `stale`."""

    def test_editing_a_requirement_does_not_stale_its_linked_journey(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Downstream Only")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _decide(admin_client, headers, "journey", "j1", "confirm")
        journey_before = _get_journey(admin_client, headers, "j1")
        assert journey_before["design_status"] == "confirmed"
        assert journey_before["recheck_state"] == "current"

        _create_requirement(admin_client, headers, "r1")
        _add_step_link(admin_client, headers, "r1", "j1", "s1")
        # requirement-revision content added via the domain layer -- see
        # TestRequirementKindOnRevisionProjection; unrelated to what this test
        # actually checks (that the JOURNEY never staled).
        _add_requirement_revision_domain(system_id, "r1", statement="v1")
        _add_requirement_revision_domain(system_id, "r1", statement="v2 edited")

        journey_after = _get_journey(admin_client, headers, "j1")
        assert journey_after["design_status"] == "confirmed"
        assert journey_after["recheck_state"] == "current"
        assert (
            journey_after["current_revision"]["content_digest"]
            == journey_before["current_revision"]["content_digest"]
        )

    def test_editing_the_journey_stales_the_requirement_step_link(self, admin_client, tmp_path):
        """The opposite (documented) direction: a Journey revision change
        DOES stale the downstream link that points at it."""
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Downstream Direction")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0, user_intent="original")])
        _create_requirement(admin_client, headers, "r1")
        link = _add_step_link(admin_client, headers, "r1", "j1", "s1")
        assert link["recheck_state"] == "current"

        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0, user_intent="changed")])
        detail = _get_requirement(admin_client, headers, "r1")
        got = next(l for l in detail["step_links"] if l["id"] == link["id"])
        assert got["target_resolution"] == "resolved"
        assert got["recheck_state"] == "stale"

    def test_step_link_reads_unresolved_when_its_step_disappears(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Step Vanish")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_requirement(admin_client, headers, "r1")
        link = _add_step_link(admin_client, headers, "r1", "j1", "s1")
        assert link["target_resolution"] == "resolved"

        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s2", 0)])  # s1 is gone
        detail = _get_requirement(admin_client, headers, "r1")
        got = next(l for l in detail["step_links"] if l["id"] == link["id"])
        assert got["target_resolution"] == "unresolved"


# --- §2.11 item 8: out_of_scope requirements --------------------------------


class TestOutOfScopeRequirements:
    def test_out_of_scope_cannot_carry_acceptance_criteria(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System OOS Reject")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1", requirement_kind="out_of_scope")

        r = admin_client.post(
            "/ux-design/requirements/r1/revisions",
            json={
                "statement": "", "out_of_scope_note": "won't build this",
                "acceptance_criteria": [_criterion("c1", 0)],
            },
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "out_of_scope_requirement_not_verifiable"

    def test_out_of_scope_can_be_revised_without_any_criteria(self, admin_client, tmp_path):
        # via the domain layer -- see TestRequirementKindOnRevisionProjection;
        # the successful-revision HTTP response is what is broken, not the
        # domain behaviour this test actually checks.
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System OOS OK")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1", requirement_kind="out_of_scope")
        detail = _add_requirement_revision_domain(
            system_id, "r1", out_of_scope_note="deliberately not building this",
        )
        assert detail["current_revision"]["acceptance_criteria"] == []
        assert detail["current_revision"]["out_of_scope_note"] == "deliberately not building this"


class TestRequirementKindOnRevisionProjection:
    """`requirement_kind` reaches the revision projection by JOIN, not by a
    duplicated column.

    The kind lives on the `ux_requirement` IDENTITY row only (contract
    §2.2/§2.4): it is part of what the Requirement IS, so an `out_of_scope`
    declaration must not be able to become a `functional` requirement
    through an ordinary content edit. A reader judging a revision still
    needs it and `UxRequirementRevisionOut` declares it required, so
    `_requirement_revision_out_dict` joins it back in at read time.

    This regressed once: the projection was built from a plain `dict(row)`
    of the revision table, so the required field was simply absent and every
    SUCCESSFUL POST to the revisions endpoint returned 500 from a Pydantic
    ValidationError instead of 201 -- as did every later GET carrying a
    current revision. The reject paths (422/404/409) and the endpoints whose
    response model does not embed the revision stayed green throughout,
    which is why this needs its own test.
    """

    def test_adding_a_requirement_revision_returns_201_not_500(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Bug RequirementKind")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1", requirement_kind="functional")

        r = admin_client.post(
            "/ux-design/requirements/r1/revisions",
            json={
                "statement": "a real statement", "rationale": "", "constraint_text": "",
                "out_of_scope_note": "", "change_note": "", "acceptance_criteria": [],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["current_revision"]["requirement_kind"] == "functional"


# --- §2.11 item 9: Artifact References --------------------------------------


class TestArtifactReferences:
    """§2.8: no body column anywhere, and `verified` is reachable ONLY
    through a `repo:<path>` URI the system itself resolved via `git show`
    on a pinned snapshot -- probe-agent fetches no other URI (SSRF /
    Principle 5)."""

    def test_no_body_column_exists_on_the_artifact_table(self, admin_client, tmp_path):
        from app.db import get_conn

        _setup(admin_client, tmp_path, "System Artifact Schema")
        with get_conn() as conn:
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(ux_design_artifact_reference)").fetchall()
            }
        assert cols.isdisjoint({"body", "content", "text", "payload"})

    def test_subject_must_exist_in_the_current_system(self, admin_client, tmp_path):
        token, system_a, _, _ = _setup(
            admin_client, tmp_path, "System Artifact Subject A"
        )
        system_b = _create_system(admin_client, token, "System Artifact Subject B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        _create_journey(admin_client, headers_b, "foreign-journey", perspective="to_be")

        for subject_kind, subject_key in (
            ("journey", "missing-journey"),
            ("journey", "foreign-journey"),
            ("requirement", "missing-requirement"),
            ("solution_design", "missing-design"),
            ("journey_step", "999999"),
            ("design_option", "999999"),
        ):
            r = admin_client.post(
                "/ux-design/artifact-references",
                json={
                    "subject_kind": subject_kind,
                    "subject_key": subject_key,
                    "artifact_kind": "spec",
                    "title": "",
                    "uri": "https://example.com/design",
                    "media_type": "",
                    "content_hash": "a" * 64,
                    "byte_size": None,
                },
                headers=headers_a,
            )
            assert r.status_code == 404, (subject_kind, subject_key, r.text)
            assert r.json()["detail"]["code"] == "ux_design_subject_not_found"

    def test_https_uri_never_reaches_verified_and_attempts_no_network_call(
        self, admin_client, tmp_path, monkeypatch
    ):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Artifact HTTPS")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        import socket

        def _boom(*a, **kw):
            raise AssertionError("no outbound network call is ever allowed for artifact verification")

        monkeypatch.setattr(socket, "create_connection", _boom)
        monkeypatch.setattr(socket.socket, "connect", _boom)

        row = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="https://example.com/design.png", content_hash="a" * 64,
        )
        assert row["verification_state"] == "unverified"
        assert row["verified_snapshot_id"] is None
        assert row["verified_commit_sha"] is None

        # structural corroboration: the domain module never even imports an
        # HTTP client.
        import inspect

        source = inspect.getsource(ux_design)
        for banned in ("import requests", "import httpx", "import urllib.request", "urlopen("):
            assert banned not in source

    def test_repo_uri_resolves_and_verifies_against_the_pinned_snapshot(self, admin_client, tmp_path):
        content = b"# design note\n"
        expected_hash = hashlib.sha256(content).hexdigest()
        token, system_id, snapshot_id, repo = _setup(
            admin_client, tmp_path, "System Artifact Verify",
            files={"a.py": b"x = 1\n", "notes/design.md": content},
        )
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        row = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="repo:notes/design.md", content_hash=expected_hash,
        )
        assert row["verification_state"] == "verified"
        assert row["verified_snapshot_id"] == snapshot_id
        assert row["verified_commit_sha"]

    def test_a_wrong_hash_on_a_resolving_repo_path_stays_unverified(self, admin_client, tmp_path):
        content = b"# design note\n"
        token, system_id, snapshot_id, repo = _setup(
            admin_client, tmp_path, "System Artifact Hash Mismatch",
            files={"a.py": b"x = 1\n", "notes/design.md": content},
        )
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        row = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="repo:notes/design.md", content_hash="0" * 64,
        )
        assert row["verification_state"] == "unverified"

    def test_a_previously_verified_repo_path_becomes_unreachable_not_unverified(self, admin_client, tmp_path):
        content = b"# design note\n"
        expected_hash = hashlib.sha256(content).hexdigest()
        token, system_id, snapshot_id, repo = _setup(
            admin_client, tmp_path, "System Artifact Unreachable",
            files={"a.py": b"x = 1\n", "notes/design.md": content},
        )
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        first = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="repo:notes/design.md", content_hash=expected_hash,
        )
        assert first["verification_state"] == "verified"

        new_sha = _remove_file_and_commit(repo, "notes/design.md")
        _insert_snapshot(system_id, repo, new_sha)

        second = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="repo:notes/design.md", content_hash=expected_hash,
        )
        assert second["verification_state"] == "unreachable"
        assert second["verification_state"] != "unverified"

        detail = _get_journey(admin_client, headers, "j1")
        current_ids = {a["id"] for a in detail["artifact_references"]}
        assert second["id"] in current_ids
        assert first["id"] not in current_ids  # superseded, not deleted

    def test_a_repo_path_never_verified_before_stays_unverified_not_unreachable(self, admin_client, tmp_path):
        token, system_id, snapshot_id, repo = _setup(
            admin_client, tmp_path, "System Artifact Never Verified", files={"a.py": b"x = 1\n"},
        )
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        row = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="repo:does/not/exist.md", content_hash="b" * 64,
        )
        assert row["verification_state"] == "unverified"

    def test_path_traversal_is_refused(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Artifact Traversal")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        r = admin_client.post(
            "/ux-design/artifact-references",
            json={
                "subject_kind": "journey", "subject_key": "j1", "artifact_kind": "spec",
                "title": "", "uri": "repo:../../etc/passwd", "media_type": "",
                "content_hash": "c" * 64, "byte_size": None,
            },
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "artifact_uri_invalid"

    def test_empty_uri_is_refused(self, admin_client, tmp_path):
        token, system_id, _, _ = _setup(admin_client, tmp_path, "System Artifact Empty URI")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        r = admin_client.post(
            "/ux-design/artifact-references",
            json={
                "subject_kind": "journey", "subject_key": "j1", "artifact_kind": "spec",
                "title": "", "uri": "  ", "media_type": "",
                "content_hash": "c" * 64, "byte_size": None,
            },
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "artifact_uri_invalid"

    def test_content_hash_is_required(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Artifact NoHash")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        r = admin_client.post(
            "/ux-design/artifact-references",
            json={
                "subject_kind": "journey", "subject_key": "j1", "artifact_kind": "spec",
                "title": "", "uri": "https://example.com/x", "media_type": "",
                "content_hash": "", "byte_size": None,
            },
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "artifact_hash_required"

    def test_content_hash_must_be_a_canonical_sha256_digest(self, admin_client, tmp_path):
        token, system_id, _, _ = _setup(
            admin_client, tmp_path, "System Artifact Invalid Hash"
        )
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")

        for invalid_hash in ("short", "g" * 64):
            r = admin_client.post(
                "/ux-design/artifact-references",
                json={
                    "subject_kind": "journey", "subject_key": "j1", "artifact_kind": "spec",
                    "title": "", "uri": "https://example.com/x", "media_type": "",
                    "content_hash": invalid_hash, "byte_size": None,
                },
                headers=headers,
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["code"] == "artifact_hash_invalid"

        canonical = _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="https://example.com/uppercase", content_hash="A" * 64,
        )
        assert canonical["content_hash"] == "a" * 64


# --- §0 invariant 9 / §2.10: diffs are exact-key matches only ----------------


class TestDiffs:
    def test_similar_text_under_different_keys_is_not_paired(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Diff Exact Match")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(
            admin_client, headers, "j1", steps=[_step("step-a", 0, user_intent="ユーザーがログインする")],
        )
        _add_journey_revision(
            admin_client, headers, "j1",
            steps=[_step("step-b", 0, user_intent="ユーザーがログインする")],  # same text, different key
        )

        diff = admin_client.get("/ux-design/journeys/j1/diff", headers=headers).json()
        kinds = {e["step_key"]: e["change_kind"] for e in diff["steps"]}
        assert kinds == {"step-a": "removed", "step-b": "added"}
        assert "changed" not in kinds.values()

    def test_diff_reports_added_removed_changed_and_unchanged(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Diff Kinds")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", steps=[
            _step("stays", 0, user_intent="unchanged text"),
            _step("will-change", 1, user_intent="before"),
            _step("will-vanish", 2, user_intent="gone soon"),
        ])
        _add_journey_revision(admin_client, headers, "j1", steps=[
            _step("stays", 0, user_intent="unchanged text"),
            _step("will-change", 1, user_intent="after"),
            _step("brand-new", 2, user_intent="new"),
        ])

        diff = admin_client.get("/ux-design/journeys/j1/diff", headers=headers).json()
        assert diff["diff_state"] == "available"
        kinds = {e["step_key"]: e["change_kind"] for e in diff["steps"]}
        assert kinds == {
            "stays": "unchanged", "will-change": "changed",
            "will-vanish": "removed", "brand-new": "added",
        }

    def test_criterion_diff_also_matches_by_exact_key(self, admin_client, tmp_path):
        # revisions are set up via the domain layer (see
        # TestRequirementKindOnRevisionProjection); the diff endpoint itself is
        # unaffected by that bug and is exercised for real over HTTP below.
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Criterion Diff")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1")
        _add_requirement_revision_domain(
            system_id, "r1", acceptance_criteria=[_criterion("c1", 0, statement="同じ文章")],
        )
        _add_requirement_revision_domain(
            system_id, "r1", acceptance_criteria=[_criterion("c2", 0, statement="同じ文章")],
        )
        diff = admin_client.get("/ux-design/requirements/r1/diff", headers=headers).json()
        kinds = {e["criterion_key"]: e["change_kind"] for e in diff["criteria"]}
        assert kinds == {"c1": "removed", "c2": "added"}

    def test_baseline_diff_is_not_applicable_without_a_baseline(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Diff NA")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j-green", perspective="to_be", baseline_mode="greenfield")
        r1 = admin_client.get("/ux-design/journeys/j-green/baseline-diff", headers=headers).json()
        assert r1["diff_state"] == "not_applicable"
        assert r1["steps"] == []

        _create_journey(admin_client, headers, "j-undecided", perspective="to_be")
        r2 = admin_client.get("/ux-design/journeys/j-undecided/baseline-diff", headers=headers).json()
        assert r2["diff_state"] == "not_applicable"
        assert r2["steps"] == []

    def test_baseline_diff_is_available_when_linked(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Baseline Diff OK")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "as-is", perspective="as_is")
        _add_journey_revision(admin_client, headers, "as-is", steps=[_step("current-flow", 0, user_intent="今のやり方")])
        as_is = _get_journey(admin_client, headers, "as-is")
        _create_journey(
            admin_client, headers, "to-be", perspective="to_be",
            baseline_mode="linked", baseline_journey_id=as_is["id"],
        )
        _add_journey_revision(admin_client, headers, "to-be", steps=[_step("new-flow", 0, user_intent="新しいやり方")])

        diff = admin_client.get("/ux-design/journeys/to-be/baseline-diff", headers=headers).json()
        assert diff["diff_state"] == "available"
        kinds = {e["step_key"]: e["change_kind"] for e in diff["steps"]}
        assert kinds == {"current-flow": "removed", "new-flow": "added"}


# --- §0 invariant 6: guarded, per-section degradation ------------------------


class TestGuardedDegradation:
    def test_upstream_ref_resolution_failure_degrades_only_that_section(
        self, admin_client, tmp_path, monkeypatch
    ):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Degrade")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1", steps=[_step("s1", 0)])
        _add_upstream_ref(admin_client, headers, "j1", "purpose_element", "beneficiary_problem")

        monkeypatch.setattr(
            ux_design, "_resolve_upstream_target",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        r = admin_client.get("/ux-design/journeys/j1", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert "upstream_refs" in body["degraded_sections"]
        assert "upstream_refs" in body["degraded_detail"]
        assert body["upstream_refs"] == []
        # everything else the same request needed still returns intact
        assert body["current_revision"]["title"] == "v1"
        assert body["design_status"] == "proposed"
        assert body["artifact_references"] == []
        assert body["decisions"] == []


# --- §2.11 item 10: digest reproducibility -----------------------------------


class TestDigestReproducibility:
    def test_journey_and_requirement_digests_reproduce_across_reload(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Digest Reload")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1", steps=[_step("s1", 0)])
        _create_requirement(admin_client, headers, "r1")
        # via the domain layer -- see TestRequirementKindOnRevisionProjection.
        _add_requirement_revision_domain(
            system_id, "r1", statement="stmt", acceptance_criteria=[_criterion("c1", 0)],
        )

        first_j = _get_journey(admin_client, headers, "j1")
        second_j = _get_journey(admin_client, headers, "j1")
        assert first_j["current_revision"]["content_digest"] == second_j["current_revision"]["content_digest"]

        first_r = _get_requirement_domain(system_id, "r1")
        second_r = _get_requirement_domain(system_id, "r1")
        assert first_r["current_revision"]["content_digest"] == second_r["current_revision"]["content_digest"]

        # and it matches a fresh, independent recomputation from the same fields
        recomputed = ux_design.journey_step_digest(
            step_key="s1", user_intent="intent", system_response="response", success_criteria="criteria",
            failure_mode="", recovery_path="", evidence_expectation="", evidence_source_kind="none",
        )
        assert first_j["current_revision"]["steps"][0]["content_digest"] == recomputed


# --- §5: this Epic writes to no pre-existing canonical table -----------------


_CANONICAL_PREFIXES = ("interview_", "purpose_", "understanding_", "evolution_node", "cell_")
_CANONICAL_EXTRA = ("components", "probe_points")


def _canonical_table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return sorted(
        r["name"] for r in rows
        if r["name"].startswith(_CANONICAL_PREFIXES) or r["name"] in _CANONICAL_EXTRA
    )


def _dump_tables(conn, names):
    return {
        name: [dict(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()]
        for name in names
    }


class TestNoWritesToExistingCanonicalTables:
    """§5: every table this Epic owns is brand new. The domain module must
    never INSERT/UPDATE a pre-existing `interview_*` / `purpose_*` /
    `understanding_*` / `evolution_node*` / `cell_*` / `components` /
    `probe_points` row (#329's origin-row discipline, applied here)."""

    def test_full_create_revise_link_confirm_flow_touches_no_canonical_row(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System No Canonical Writes")
        headers = _headers(token, system_id)
        _create_session(admin_client, headers, snapshot_id)

        from app.db import get_conn

        with get_conn() as conn:
            names = _canonical_table_names(conn)
            assert names  # sanity: the prefixes actually matched real tables
            before = _dump_tables(conn, names)

        # the whole flow this issue owns: create -> revise -> link -> confirm
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", title="v1", steps=[_step("s1", 0)])
        _add_upstream_ref(
            admin_client, headers, "j1", "purpose_relation",
            "problem_to_change:beneficiary_problem->desired_change",
        )
        _create_requirement(admin_client, headers, "r1")
        # via the domain layer -- see TestRequirementKindOnRevisionProjection.
        _add_requirement_revision_domain(
            system_id, "r1", statement="stmt", acceptance_criteria=[_criterion("c1", 0)],
        )
        _add_step_link(admin_client, headers, "r1", "j1", "s1")
        _create_artifact_reference(
            admin_client, headers, subject_kind="journey", subject_key="j1",
            uri="https://example.com/x", content_hash="d" * 64,
        )
        _decide(admin_client, headers, "journey", "j1", "confirm")
        _decide(admin_client, headers, "requirement", "r1", "confirm")

        with get_conn() as conn:
            after = _dump_tables(conn, names)

        assert before == after


# --- §2.10: finite reject-code coverage --------------------------------------


class TestFiniteRejectCodes:
    """§2.10's finite reject-code table -- each reachable with its
    documented HTTP status, in addition to the codes already exercised by
    the classes above (`journey_baseline_not_as_is`,
    `journey_baseline_foreign_system`, `out_of_scope_requirement_not_verifiable`,
    `artifact_uri_invalid`, `artifact_hash_required`,
    `ux_design_decision_stale_digest`, `ux_design_not_decidable`)."""

    def test_key_required(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject KeyRequired")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "", "perspective": "to_be"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "ux_design_key_required"

    def test_key_conflict(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject KeyConflict")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "dup", perspective="to_be")
        r = admin_client.post(
            "/ux-design/journeys",
            json={"journey_key": "dup", "perspective": "to_be"},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "ux_design_key_conflict"

    def test_journey_step_key_duplicated(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject DupStep")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        r = admin_client.post(
            "/ux-design/journeys/j1/revisions",
            json={"steps": [_step("dup", 0), _step("dup", 1)]},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "journey_step_key_duplicated"

    def test_journey_step_not_found(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject StepNotFound")
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", perspective="to_be")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_requirement(admin_client, headers, "r1")
        r = admin_client.post(
            "/ux-design/requirements/r1/step-links",
            json={"journey_key": "j1", "step_key": "does-not-exist"},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "journey_step_not_found"

    def test_criterion_key_duplicated(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject DupCriterion")
        headers = _headers(token, system_id)
        _create_requirement(admin_client, headers, "r1")
        r = admin_client.post(
            "/ux-design/requirements/r1/revisions",
            json={"acceptance_criteria": [_criterion("dup", 0), _criterion("dup", 1)]},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "ux_requirement_criterion_key_duplicated"

    def test_subject_not_found(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject SubjectNotFound")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/ux-design/decisions",
            json={"subject_kind": "requirement_step_link", "subject_key": "999999", "decision": "confirm"},
            headers=headers,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "ux_design_subject_not_found"

    def test_journey_not_found_is_a_plain_404(self, admin_client, tmp_path):
        token, system_id, snapshot_id, _ = _setup(admin_client, tmp_path, "System Reject PlainNotFound")
        headers = _headers(token, system_id)
        r = admin_client.get("/ux-design/journeys/does-not-exist", headers=headers)
        assert r.status_code == 404
