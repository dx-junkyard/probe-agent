"""Contract tests for Epic #405 (UX Design Lineage), Issue #408: Solution
Design.

The canonical contract is `docs/ux-design-lineage.md` Section 3, and the
implementation under test is `app/solution_design.py` +
`app/routes/solution_design.py`. Section 3.8 lists 7 acceptance conditions;
each test class below states, in its docstring, which condition(s) it
covers.

Fixture style mirrors `tests/test_node_design.py` / `tests/test_evolution_
node.py`: a local `admin_client` fixture with `PROBE_DB_PATH` under
`tmp_path`, a real `POST /auth/login`, and direct `get_conn()` inserts to
build the FK spine each target kind needs (snapshot -> intelligence run ->
probe plan -> probe point, agent_role_cards -> cell_definitions, etc).
Requirements/Journeys are built through `app.ux_design` (#407's own,
committed writers) rather than raw SQL, so the fixtures under test are
exactly what the real API would produce.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import evolution_node, node_design, solution_design, ux_design
from app.capability_graph import confirm_capability_graph
from app.cell_binding import create_binding
from app.db import get_conn


# ---------------------------------------------------------------------------
# Fixtures / low-level helpers (mirrors tests/test_node_design.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "solution-design-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_headers(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- HTTP helpers for the endpoints under test ------------------------------


def _create_design(client, token, system_id, design_key, title="", summary="", expect_status=201):
    r = client.post(
        "/solution-designs",
        json={"design_key": design_key, "title": title, "summary": summary},
        headers=_headers(token, system_id),
    )
    assert r.status_code == expect_status, r.text
    return r.json()


def _add_option(
    client, token, system_id, design_key, option_key, option_order=1,
    title="", approach="", tradeoffs="", risks="", expect_status=201,
):
    r = client.post(
        f"/solution-designs/{design_key}/options",
        json={
            "option_key": option_key, "option_order": option_order, "title": title,
            "approach": approach, "tradeoffs": tradeoffs, "risks": risks,
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code == expect_status, r.text
    return r.json()


def _add_requirement_link(client, token, system_id, design_key, requirement_key, note="", expect_status=201):
    r = client.post(
        f"/solution-designs/{design_key}/requirement-links",
        json={"requirement_key": requirement_key, "note": note},
        headers=_headers(token, system_id),
    )
    assert r.status_code == expect_status, r.text
    return r.json()


def _add_target_link(
    client, token, system_id, design_key, option_key, target_kind, target_ref,
    captured_snapshot_id=None, note="", expect_status=201,
):
    payload = {
        "option_key": option_key, "target_kind": target_kind, "target_ref": target_ref,
        "note": note,
    }
    if captured_snapshot_id is not None:
        payload["captured_snapshot_id"] = captured_snapshot_id
    r = client.post(
        f"/solution-designs/{design_key}/target-links", json=payload,
        headers=_headers(token, system_id),
    )
    assert r.status_code == expect_status, r.text
    return r.json()


def _decide(client, token, system_id, design_key, option_key, decision, rationale="", expect_status=201):
    r = client.post(
        f"/solution-designs/{design_key}/decisions",
        json={"option_key": option_key, "decision": decision, "rationale": rationale},
        headers=_headers(token, system_id),
    )
    assert r.status_code == expect_status, r.text
    return r.json() if expect_status < 300 else r.json()


def _get_design(client, token, system_id, design_key, expect_status=200):
    r = client.get(f"/solution-designs/{design_key}", headers=_headers(token, system_id))
    assert r.status_code == expect_status, r.text
    return r.json()


def _get_change_origins(client, token, system_id, design_key, expect_status=200):
    r = client.get(f"/solution-designs/{design_key}/change-origins", headers=_headers(token, system_id))
    assert r.status_code == expect_status, r.text
    return r.json()


def _get_handoff(client, token, system_id, design_key, expect_status=200):
    r = client.get(f"/solution-designs/{design_key}/handoff", headers=_headers(token, system_id))
    assert r.status_code == expect_status, r.text
    return r.json()


def _target_link_by_kind(design_detail, target_kind, target_ref=None):
    for link in design_detail["target_links"]:
        if link["target_kind"] == target_kind and (target_ref is None or link["target_ref"] == target_ref):
            return link
    raise AssertionError(f"no target_link of kind {target_kind!r} ref {target_ref!r} in {design_detail}")


def _requirement_link_by_key(design_detail, requirement_key):
    for link in design_detail["requirement_links"]:
        if link["requirement_key"] == requirement_key:
            return link
    raise AssertionError(f"no requirement_link for {requirement_key!r} in {design_detail}")


def _option_by_key(design_detail, option_key):
    for opt in design_detail["options"]:
        if opt["option_key"] == option_key:
            return opt
    raise AssertionError(f"no option {option_key!r} in {design_detail}")


def _decisions_for_option(design_detail, option_key):
    return [d for d in design_detail["decisions"] if d["option_key"] == option_key]


# --- Fixture builders: FK spines for each SolutionTargetKind ---------------


def _insert_ready_snapshot(conn, system_id, commit_sha="abc123"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
        (system_id, commit_sha, now, now),
    )
    return cur.lastrowid


def _insert_entrypoint(conn, system_id, snapshot_id, entrypoint_id, label="Checkout route"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO code_entrypoints
               (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                handler_path, handler_qualified_name, line_start, line_end,
                route_method, route_path, created_at)
           VALUES (?, ?, 'http_route', ?, 'api', ?, 'app/x.py', 'app.x.handler',
                   1, 10, 'POST', '/checkout', ?)""",
        (system_id, snapshot_id, entrypoint_id, label, now),
    )
    return cur.lastrowid


def _insert_component(conn, system_id, component_id, mode="trace"):
    now = time.time()
    conn.execute(
        "INSERT INTO components (system_id, component_id, mode, updated_at) VALUES (?, ?, ?, ?)",
        (system_id, component_id, mode, now),
    )


def _insert_trace_span(conn, system_id, flow_id, trace_id="t-1"):
    conn.execute(
        """INSERT INTO trace_spans (system_id, trace_id, component_id, flow_id, timestamp)
           VALUES (?, ?, 'svc', ?, ?)""",
        (system_id, trace_id, flow_id, time.time()),
    )


def _insert_probe_point_chain(conn, system_id, status="approved", component_id="svc-probe"):
    """Minimal snapshot -> intelligence run -> probe plan -> probe point
    chain (the FK spine `probe_points` requires) -- mirrors
    `tests/test_evolution_node.py`'s `_insert_probe_point_chain`. Returns
    (probe_point_id, probe_plan_id)."""
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
        (system_id, now, now),
    )
    snapshot_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model, prompt_version,
                schema_version, decision_method, status, is_mock, started_at, completed_at)
           VALUES (?, ?, 'probe_plan', 'mock', 'mock-model', 'v1', 'v1',
                   'reasoning_llm', 'completed', 1, ?, ?)""",
        (system_id, snapshot_id, now, now),
    )
    run_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO probe_plans
               (system_id, snapshot_id, intelligence_run_id, feature_id, objective,
                status, origin, created_at, updated_at)
           VALUES (?, ?, ?, 'feat-1', 'objective', 'approved', 'manual', ?, ?)""",
        (system_id, snapshot_id, run_id, now, now),
    )
    plan_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO probe_points
               (plan_id, system_id, component_id, feature_id, path, symbol,
                line_start, line_end, reason, recommended_mode, side_effect_risk,
                replayability, denylist_hit, status, created_at, updated_at)
           VALUES (?, ?, ?, 'feat-1', 'app/x.py', 'do_x', 1, 10,
                   'reason', 'trace', 'low', 'replayable', NULL, ?, ?, ?)""",
        (plan_id, system_id, component_id, status, now, now),
    )
    return cur.lastrowid, plan_id


def _insert_cell(conn, system_id, cell_id):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO agent_role_cards
               (system_id, role_key, version, status, mission, model_alias,
                changelog, schema_version, created_at)
           VALUES (?, 'worker-x', '1.0.0', 'active', 'm', 'worker-default', 'init', '1.0', ?)""",
        (system_id, now),
    )
    role_card_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO cell_definitions
               (system_id, cell_id, role_card_id, status, mission, created_at, updated_at)
           VALUES (?, ?, ?, 'active', '', ?, ?)""",
        (system_id, cell_id, role_card_id, now, now),
    )
    return cur.lastrowid


def _insert_cell_improvement(conn, system_id, cell_definition_id, status="observed"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO cell_improvements
               (system_id, cell_definition_id, status, target_kind, created_at, updated_at)
           VALUES (?, ?, ?, 'role_card', ?, ?)""",
        (system_id, cell_definition_id, status, now, now),
    )
    return cur.lastrowid


def _snapshot_and_session(conn, system_id):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
        (system_id, now, now),
    )
    snapshot_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO interview_session
               (system_id, snapshot_id, title, focus, status, stage, created_at, updated_at)
           VALUES (?, ?, '', '', 'open', 'purpose_confirmation', ?, ?)""",
        (system_id, snapshot_id, now, now),
    )
    return snapshot_id, cur.lastrowid


def _confirm_capability_graph(conn, system_id, names=("Checkout",)):
    """Confirm a Capability composition through #312's own writer (mirrors
    `tests/test_node_design.py`), so entity ids under test are real stable
    identities."""
    snapshot_id, session_id = _snapshot_and_session(conn, system_id)
    understanding = {
        "core_capabilities": [
            {"name": name, "summary": f"{name} summary", "children": []} for name in names
        ],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
    }
    now = time.time()
    cur = conn.execute(
        """INSERT INTO understanding_revision
               (session_id, system_id, snapshot_id, intelligence_run_id,
                current_understanding, gap_analysis, created_at)
           VALUES (?, ?, ?, NULL, ?, NULL, ?)""",
        (session_id, system_id, snapshot_id, json.dumps(understanding), now),
    )
    revision_id = cur.lastrowid
    graph = confirm_capability_graph(
        conn, system_id=system_id, session_id=session_id, revision_id=revision_id,
        revision_created_at=now, current_understanding=understanding, actor="alice",
        now=now + 0.1,
    )
    return graph, snapshot_id, session_id


def _make_requirement(conn, system_id, key, kind="functional", statement="stmt v1", criteria=None):
    ux_design.create_requirement(
        conn, system_id=system_id, requirement_key=key, requirement_kind=kind, created_by="alice",
    )
    ux_design.add_requirement_revision(
        conn, system_id=system_id, requirement_key=key, statement=statement,
        acceptance_criteria=criteria, created_by="alice",
    )


def _revise_requirement(conn, system_id, key, statement):
    ux_design.add_requirement_revision(
        conn, system_id=system_id, requirement_key=key, statement=statement, created_by="alice",
    )


def _make_journey_with_step(conn, system_id, journey_key, step_key="s1", user_intent="do thing"):
    ux_design.create_journey(
        conn, system_id=system_id, journey_key=journey_key, perspective="to_be", created_by="alice",
    )
    ux_design.add_journey_revision(
        conn, system_id=system_id, journey_key=journey_key,
        steps=[{"step_key": step_key, "user_intent": user_intent}], created_by="alice",
    )


def _revise_journey_step(conn, system_id, journey_key, step_key="s1", user_intent="changed"):
    ux_design.add_journey_revision(
        conn, system_id=system_id, journey_key=journey_key,
        steps=[{"step_key": step_key, "user_intent": user_intent}], created_by="alice",
    )


CANONICAL_TABLES = [
    "evolution_node", "evolution_node_version", "evolution_node_link", "evolution_node_event",
    "components", "cell_definitions", "cell_bindings", "cell_improvements",
    "probe_points", "probe_plans", "probe_patches", "publish_jobs",
    "purpose_relation_decision", "interview_session", "interview_qa",
    "understanding_revision", "understanding_capability_entity",
    "understanding_capability_confirmation",
]


def _snapshot_canonical_tables(conn, system_id):
    snap = {}
    for table in CANONICAL_TABLES:
        # `components`' primary key is (system_id, component_id) -- it has
        # no `id` column, unlike every other table in this list.
        order_col = "component_id" if table == "components" else "id"
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE system_id = ? ORDER BY {order_col}", (system_id,)
        ).fetchall()
        snap[table] = [dict(r) for r in rows]
    return snap


# ---------------------------------------------------------------------------
# 1. Many-to-many, both directions (#408 acceptance condition 1)
# ---------------------------------------------------------------------------


class TestManyToMany:
    """One Requirement can be satisfied by several targets (Nodes/Flows/...),
    and one target (Evolution Node) can support several Solution Designs --
    and the reverse bridge (one Requirement <-> several Designs) holds too."""

    def test_one_requirement_linked_to_two_designs(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-Req2Designs")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-shared")

        _create_design(admin_client, token, system_id, "design-a")
        _create_design(admin_client, token, system_id, "design-b")
        _add_requirement_link(admin_client, token, system_id, "design-a", "req-shared")
        _add_requirement_link(admin_client, token, system_id, "design-b", "req-shared")

        a = _get_design(admin_client, token, system_id, "design-a")
        b = _get_design(admin_client, token, system_id, "design-b")
        assert _requirement_link_by_key(a, "req-shared")["link_state"] == "current"
        assert _requirement_link_by_key(b, "req-shared")["link_state"] == "current"

    def test_one_design_linked_to_two_requirements(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-Design2Reqs")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-1")
            _make_requirement(conn, system_id, "req-2")

        _create_design(admin_client, token, system_id, "design-multi")
        _add_requirement_link(admin_client, token, system_id, "design-multi", "req-1")
        _add_requirement_link(admin_client, token, system_id, "design-multi", "req-2")

        detail = _get_design(admin_client, token, system_id, "design-multi")
        assert {l["requirement_key"] for l in detail["requirement_links"]} == {"req-1", "req-2"}

    def test_one_evolution_node_target_supports_multiple_designs(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NodeMultiDesign")
        with get_conn() as conn:
            node = evolution_node.create_node(
                conn, system_id=system_id, node_key="shared-node", display_name="Shared Node",
            )

        _create_design(admin_client, token, system_id, "design-x")
        _add_option(admin_client, token, system_id, "design-x", "opt-x")
        _add_target_link(
            admin_client, token, system_id, "design-x", "opt-x", "evolution_node", "shared-node",
        )
        _create_design(admin_client, token, system_id, "design-y")
        _add_option(admin_client, token, system_id, "design-y", "opt-y")
        _add_target_link(
            admin_client, token, system_id, "design-y", "opt-y", "evolution_node", "shared-node",
        )

        x = _get_design(admin_client, token, system_id, "design-x")
        y = _get_design(admin_client, token, system_id, "design-y")
        link_x = _target_link_by_kind(x, "evolution_node")
        link_y = _target_link_by_kind(y, "evolution_node")
        assert link_x["target_name"] == "Shared Node" == link_y["target_name"]
        assert link_x["target_row_id"] == node["id"] == link_y["target_row_id"]
        assert link_x["link_state"] == "current"
        assert link_y["link_state"] == "current"

    def test_one_requirement_satisfied_by_multiple_target_kinds(self, admin_client):
        """The Requirement is linked to a Design whose Option carries target
        links of two DIFFERENT kinds -- the requirement is satisfied by
        several targets at once."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqMultiTarget")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-multi-target")
            evolution_node.create_node(conn, system_id=system_id, node_key="norm-node")
            _insert_component(conn, system_id, "svc-a")

        _create_design(admin_client, token, system_id, "design-mt")
        _add_requirement_link(admin_client, token, system_id, "design-mt", "req-multi-target")
        _add_option(admin_client, token, system_id, "design-mt", "opt-mt")
        _add_target_link(
            admin_client, token, system_id, "design-mt", "opt-mt", "evolution_node", "norm-node",
        )
        _add_target_link(
            admin_client, token, system_id, "design-mt", "opt-mt", "component", "svc-a",
        )

        detail = _get_design(admin_client, token, system_id, "design-mt")
        assert len(detail["target_links"]) == 2
        assert {l["target_kind"] for l in detail["target_links"]} == {"evolution_node", "component"}


# ---------------------------------------------------------------------------
# 2. link_state / stale_reason (#408 acceptance conditions 2 and 5) plus
#    change-origins classification (§3.5)
# ---------------------------------------------------------------------------


class TestLinkStalenessAndChangeOrigins:
    """§3.4's 6-row first-match table, exercised for both link tables, all
    four `SolutionLinkState` values and all five `SolutionLinkStaleReason`
    values -- and each stale link's `/change-origins` classification."""

    def test_requirement_link_current_when_nothing_changed(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqCurrent")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-c")
        _create_design(admin_client, token, system_id, "design-c")
        _add_requirement_link(admin_client, token, system_id, "design-c", "req-c")

        detail = _get_design(admin_client, token, system_id, "design-c")
        link = _requirement_link_by_key(detail, "req-c")
        assert link["link_state"] == "current"
        assert link["stale_reason"] is None

    def test_requirement_link_stale_requirement_changed(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqChanged")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-rc", statement="v1")
        _create_design(admin_client, token, system_id, "design-rc")
        _add_requirement_link(admin_client, token, system_id, "design-rc", "req-rc")
        with get_conn() as conn:
            _revise_requirement(conn, system_id, "req-rc", "v2")

        detail = _get_design(admin_client, token, system_id, "design-rc")
        link = _requirement_link_by_key(detail, "req-rc")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "requirement_changed"

        origins = _get_change_origins(admin_client, token, system_id, "design-rc")["origins"]
        assert any(o["stale_reason"] == "requirement_changed" and o["origin"] == "requirement" for o in origins)

    def test_requirement_link_stale_upstream_changed_via_step(self, admin_client):
        """A linked Requirement's own Journey Step link degrades -- the
        Requirement's own text is untouched, but the upstream chain moved."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqUpstream")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-up")
            _make_journey_with_step(conn, system_id, "journey-up")
            ux_design.add_requirement_step_link(
                conn, system_id=system_id, requirement_key="req-up",
                journey_key="journey-up", step_key="s1", created_by="alice",
            )
        _create_design(admin_client, token, system_id, "design-up")
        _add_requirement_link(admin_client, token, system_id, "design-up", "req-up")

        # Still current before the Journey Step content moves.
        before = _requirement_link_by_key(
            _get_design(admin_client, token, system_id, "design-up"), "req-up"
        )
        assert before["link_state"] == "current"

        with get_conn() as conn:
            _revise_journey_step(conn, system_id, "journey-up", user_intent="now different")

        detail = _get_design(admin_client, token, system_id, "design-up")
        link = _requirement_link_by_key(detail, "req-up")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "upstream_changed"

        origins = _get_change_origins(admin_client, token, system_id, "design-up")["origins"]
        assert any(o["stale_reason"] == "upstream_changed" and o["origin"] == "journey" for o in origins)

    def test_requirement_link_unresolved_when_requirement_row_disappears(self, admin_client):
        """A defensive branch: the linked Requirement row itself is gone.
        There is no delete endpoint for a Requirement, so this is forced
        directly at the DB layer with FK enforcement relaxed, to exercise
        the code path `_requirement_link_state` explicitly handles."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqGone")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-gone")
        _create_design(admin_client, token, system_id, "design-gone")
        _add_requirement_link(admin_client, token, system_id, "design-gone", "req-gone")

        with get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "DELETE FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
                (system_id, "req-gone"),
            )
            design_row = conn.execute(
                "SELECT id FROM solution_design WHERE system_id = ? AND design_key = 'design-gone'",
                (system_id,),
            ).fetchone()
            links = solution_design.list_requirement_links(conn, system_id, design_row["id"])

        assert links[0]["link_state"] == "unresolved"

    def test_requirement_link_section_degrades_when_source_table_unreadable(self, admin_client):
        """The per-link `_requirement_link_state` computation has its own
        `unavailable` branch (§3.4 step 1), but the public read path is
        `get_design_detail`, whose `_guarded()` wrapper catches an
        unreadable `ux_requirement` at the WHOLE-SECTION level (the same
        "a failed section degrades alone" rule Epic #380 documents) rather
        than reporting a per-link `unavailable` value -- so this is what a
        caller of the real API actually observes: `degraded_sections`
        containing `requirement_links`, never a request-crashing 500."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReqSectionDegraded")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-degrade")
        _create_design(admin_client, token, system_id, "design-degrade")
        _add_requirement_link(admin_client, token, system_id, "design-degrade", "req-degrade")

        with get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE ux_requirement")

        detail = _get_design(admin_client, token, system_id, "design-degrade")
        assert "requirement_links" in detail["degraded_sections"]
        assert detail["requirement_links"] == []

    def test_target_link_current_for_a_resolved_component(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetCurrent")
        with get_conn() as conn:
            _insert_component(conn, system_id, "svc-cur")
        _create_design(admin_client, token, system_id, "design-tc")
        _add_option(admin_client, token, system_id, "design-tc", "opt-tc")
        _add_target_link(admin_client, token, system_id, "design-tc", "opt-tc", "component", "svc-cur")

        detail = _get_design(admin_client, token, system_id, "design-tc")
        link = _target_link_by_kind(detail, "component")
        assert link["link_state"] == "current"
        assert link["review_required"] is False

    def test_target_link_unresolved_for_a_nonexistent_evolution_node(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetUnresolved")
        _create_design(admin_client, token, system_id, "design-tu")
        _add_option(admin_client, token, system_id, "design-tu", "opt-tu")
        _add_target_link(
            admin_client, token, system_id, "design-tu", "opt-tu", "evolution_node", "never-made",
        )

        detail = _get_design(admin_client, token, system_id, "design-tu")
        link = _target_link_by_kind(detail, "evolution_node")
        assert link["link_state"] == "unresolved"
        assert link["review_required"] is True

        origins = _get_change_origins(admin_client, token, system_id, "design-tu")["origins"]
        assert any(o["target_kind"] == "evolution_node" for o in origins)
        assert next(o for o in origins if o["target_kind"] == "evolution_node")["origin"] == "implementation_target"

    def test_target_link_unavailable_source_table_unreadable(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetUnavailable")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
        _create_design(admin_client, token, system_id, "design-tunavail")
        _add_option(admin_client, token, system_id, "design-tunavail", "opt-tunavail")
        _add_target_link(
            admin_client, token, system_id, "design-tunavail", "opt-tunavail",
            "capability", str(entity_id),
        )
        before = _target_link_by_kind(
            _get_design(admin_client, token, system_id, "design-tunavail"), "capability"
        )
        assert before["link_state"] == "current"

        with get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE understanding_capability_entity")
            design_row = conn.execute(
                "SELECT id FROM solution_design WHERE system_id = ? AND design_key = 'design-tunavail'",
                (system_id,),
            ).fetchone()
            links = solution_design.list_target_links(conn, system_id, design_row["id"])

        assert links[0]["link_state"] == "unavailable"

    def test_target_link_stale_snapshot_changed(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetSnapChanged")
        with get_conn() as conn:
            snap1 = _insert_ready_snapshot(conn, system_id, commit_sha="c1")
            _insert_entrypoint(conn, system_id, snap1, "ep-checkout")
        _create_design(admin_client, token, system_id, "design-snap")
        _add_option(admin_client, token, system_id, "design-snap", "opt-snap")
        _add_target_link(
            admin_client, token, system_id, "design-snap", "opt-snap", "static_flow",
            "ep-checkout", captured_snapshot_id=snap1,
        )
        before = _target_link_by_kind(
            _get_design(admin_client, token, system_id, "design-snap"), "static_flow"
        )
        assert before["link_state"] == "current"

        with get_conn() as conn:
            _insert_ready_snapshot(conn, system_id, commit_sha="c2")

        detail = _get_design(admin_client, token, system_id, "design-snap")
        link = _target_link_by_kind(detail, "static_flow")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "snapshot_changed"

        origins = _get_change_origins(admin_client, token, system_id, "design-snap")["origins"]
        assert any(o["stale_reason"] == "snapshot_changed" and o["origin"] == "snapshot" for o in origins)

    def test_target_link_stale_target_changed(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetChanged")
        with get_conn() as conn:
            snap = _insert_ready_snapshot(conn, system_id)
            ep_id = _insert_entrypoint(conn, system_id, snap, "ep-target", label="original label")
        _create_design(admin_client, token, system_id, "design-target")
        _add_option(admin_client, token, system_id, "design-target", "opt-target")
        _add_target_link(
            admin_client, token, system_id, "design-target", "opt-target", "static_flow",
            "ep-target", captured_snapshot_id=snap,
        )
        with get_conn() as conn:
            conn.execute("UPDATE code_entrypoints SET label = 'new label' WHERE id = ?", (ep_id,))

        detail = _get_design(admin_client, token, system_id, "design-target")
        link = _target_link_by_kind(detail, "static_flow")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "target_changed"

        origins = _get_change_origins(admin_client, token, system_id, "design-target")["origins"]
        assert any(o["stale_reason"] == "target_changed" for o in origins)

    def test_target_link_stale_design_changed_when_its_option_is_corrected(
        self, admin_client
    ):
        """Correcting the option a link hangs off makes the link `stale`.

        `design_changed` is only reachable once a `solution_design_option`
        row has actually been superseded, so it was dead code for as long as
        the option table carried an unqualified UNIQUE on option_key -- see
        `TestOptionCorrection`.
        """
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-DesignChanged")
        with get_conn() as conn:
            _insert_component(conn, system_id, "svc-dc")
        _create_design(admin_client, token, system_id, "design-dc")
        _add_option(admin_client, token, system_id, "design-dc", "opt-a", title="v1")
        _add_target_link(
            admin_client, token, system_id, "design-dc", "opt-a",
            target_kind="component", target_ref="svc-dc",
        )

        before = _get_design(admin_client, token, system_id, "design-dc")
        assert _target_link_by_kind(before, "component", "svc-dc")["link_state"] == "current"

        _add_option(admin_client, token, system_id, "design-dc", "opt-a", title="v2")

        after = _get_design(admin_client, token, system_id, "design-dc")
        link = _target_link_by_kind(after, "component", "svc-dc")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "design_changed"

    def test_target_link_stale_upstream_changed_via_requirement(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-TargetUpstream")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-tu", statement="v1")
            _insert_component(conn, system_id, "svc-tu")
        _create_design(admin_client, token, system_id, "design-tup")
        _add_requirement_link(admin_client, token, system_id, "design-tup", "req-tu")
        _add_option(admin_client, token, system_id, "design-tup", "opt-tup")
        _add_target_link(
            admin_client, token, system_id, "design-tup", "opt-tup", "component", "svc-tu",
        )
        before = _target_link_by_kind(
            _get_design(admin_client, token, system_id, "design-tup"), "component"
        )
        assert before["link_state"] == "current"

        with get_conn() as conn:
            _revise_requirement(conn, system_id, "req-tu", "v2")

        detail = _get_design(admin_client, token, system_id, "design-tup")
        link = _target_link_by_kind(detail, "component")
        assert link["link_state"] == "stale"
        assert link["stale_reason"] == "upstream_changed"

        origins = _get_change_origins(admin_client, token, system_id, "design-tup")["origins"]
        assert any(
            o["stale_reason"] == "upstream_changed" and o["origin"] == "requirement"
            and o["link_kind"] == "target_link"
            for o in origins
        )

    def test_review_required_is_the_derived_negation_of_current_never_a_stored_column(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ReviewRequired")
        with get_conn() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(solution_design_target_link)")}
        assert "review_required" not in columns

        with get_conn() as conn:
            _insert_component(conn, system_id, "svc-rr")
        _create_design(admin_client, token, system_id, "design-rr")
        _add_option(admin_client, token, system_id, "design-rr", "opt-rr")
        _add_target_link(admin_client, token, system_id, "design-rr", "opt-rr", "component", "svc-rr")
        _add_target_link(
            admin_client, token, system_id, "design-rr", "opt-rr", "evolution_node", "missing-node",
        )
        detail = _get_design(admin_client, token, system_id, "design-rr")
        current_link = _target_link_by_kind(detail, "component")
        unresolved_link = _target_link_by_kind(detail, "evolution_node")
        assert current_link["review_required"] is False
        assert unresolved_link["review_required"] is True


# ---------------------------------------------------------------------------
# 3. static_flow vs runtime_flow (#408 acceptance condition 5)
# ---------------------------------------------------------------------------


class TestStaticFlowVsRuntimeFlow:
    def test_static_flow_requires_captured_snapshot_id(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-StaticNeedsSnap")
        _create_design(admin_client, token, system_id, "design-noshot")
        _add_option(admin_client, token, system_id, "design-noshot", "opt-noshot")
        r = admin_client.post(
            "/solution-designs/design-noshot/target-links",
            json={"option_key": "opt-noshot", "target_kind": "static_flow", "target_ref": "ep-1"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "flow_target_requires_snapshot"

    def test_static_flow_and_runtime_flow_are_independent_facts(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-StaticVsRuntime")
        with get_conn() as conn:
            snap = _insert_ready_snapshot(conn, system_id)
            _insert_entrypoint(conn, system_id, snap, "checkout-flow")
        _create_design(admin_client, token, system_id, "design-both")
        _add_option(admin_client, token, system_id, "design-both", "opt-both")
        _add_target_link(
            admin_client, token, system_id, "design-both", "opt-both", "static_flow",
            "checkout-flow", captured_snapshot_id=snap,
        )
        _add_target_link(
            admin_client, token, system_id, "design-both", "opt-both", "runtime_flow", "checkout-flow",
        )

        detail = _get_design(admin_client, token, system_id, "design-both")
        static_link = _target_link_by_kind(detail, "static_flow")
        runtime_link = _target_link_by_kind(detail, "runtime_flow")
        # The identical string ref resolves against two entirely different
        # canonical sources: static_flow is `resolved` (a real entrypoint),
        # runtime_flow is `unresolved` (no trace span has used it yet).
        assert static_link["link_state"] == "current"
        assert runtime_link["link_state"] == "unresolved"

        with get_conn() as conn:
            _insert_trace_span(conn, system_id, "checkout-flow")

        after = _get_design(admin_client, token, system_id, "design-both")
        # runtime_flow now resolves; static_flow's own state is untouched by
        # a completely unrelated table (trace_spans) gaining a row.
        assert _target_link_by_kind(after, "runtime_flow")["link_state"] == "current"
        assert _target_link_by_kind(after, "static_flow")["link_state"] == "current"

    def test_a_flow_graph_derived_id_is_not_accepted_as_a_stable_static_flow_target(self, admin_client):
        """`flow-{i}` is only stable within one `flow_graph` derivation and
        is never minted as a permanent identity -- a static_flow link naming
        one resolves `unresolved`, never silently treated as valid."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoFakeFlowId")
        with get_conn() as conn:
            snap = _insert_ready_snapshot(conn, system_id)
        _create_design(admin_client, token, system_id, "design-fakeflow")
        _add_option(admin_client, token, system_id, "design-fakeflow", "opt-fakeflow")
        _add_target_link(
            admin_client, token, system_id, "design-fakeflow", "opt-fakeflow", "static_flow",
            "flow-1", captured_snapshot_id=snap,
        )
        detail = _get_design(admin_client, token, system_id, "design-fakeflow")
        link = _target_link_by_kind(detail, "static_flow")
        assert link["link_state"] == "unresolved"


# ---------------------------------------------------------------------------
# 4. out_of_scope requirements refuse a target link (#408 acceptance
#    condition 6)
# ---------------------------------------------------------------------------


class TestOutOfScopeRefusesTargetLink:
    def test_out_of_scope_only_design_refuses_target_link(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OOSOnly")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-oos", kind="out_of_scope", statement="not doing this")
            _insert_component(conn, system_id, "svc-oos")
        _create_design(admin_client, token, system_id, "design-oos")
        _add_requirement_link(admin_client, token, system_id, "design-oos", "req-oos")
        _add_option(admin_client, token, system_id, "design-oos", "opt-oos")

        r = admin_client.post(
            "/solution-designs/design-oos/target-links",
            json={"option_key": "opt-oos", "target_kind": "component", "target_ref": "svc-oos"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "out_of_scope_requirement_not_implementable"

    def test_mixed_out_of_scope_and_functional_allows_target_link(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OOSMixed")
        with get_conn() as conn:
            _make_requirement(conn, system_id, "req-oos-2", kind="out_of_scope")
            _make_requirement(conn, system_id, "req-func-2", kind="functional")
            _insert_component(conn, system_id, "svc-mixed")
        _create_design(admin_client, token, system_id, "design-mixed")
        _add_requirement_link(admin_client, token, system_id, "design-mixed", "req-oos-2")
        _add_requirement_link(admin_client, token, system_id, "design-mixed", "req-func-2")
        _add_option(admin_client, token, system_id, "design-mixed", "opt-mixed")
        _add_target_link(
            admin_client, token, system_id, "design-mixed", "opt-mixed", "component", "svc-mixed",
        )
        detail = _get_design(admin_client, token, system_id, "design-mixed")
        assert len(detail["target_links"]) == 1

    def test_zero_requirement_links_allows_target_link(self, admin_client):
        """Nothing has been declared out of scope yet -- a design with no
        Requirement links at all is not the same state as "everything
        linked is out_of_scope"."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OOSZeroLinks")
        with get_conn() as conn:
            _insert_component(conn, system_id, "svc-zero")
        _create_design(admin_client, token, system_id, "design-zero")
        _add_option(admin_client, token, system_id, "design-zero", "opt-zero")
        _add_target_link(
            admin_client, token, system_id, "design-zero", "opt-zero", "component", "svc-zero",
        )
        detail = _get_design(admin_client, token, system_id, "design-zero")
        assert len(detail["target_links"]) == 1


# ---------------------------------------------------------------------------
# 5. Probe Point gate (write-time, reused from evolution_node)
# ---------------------------------------------------------------------------


class TestProbePointGate:
    def test_approved_probe_point_link_succeeds(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-PPApproved")
        with get_conn() as conn:
            point_id, _ = _insert_probe_point_chain(conn, system_id, status="approved")
        _create_design(admin_client, token, system_id, "design-pp-ok")
        _add_option(admin_client, token, system_id, "design-pp-ok", "opt-pp-ok")
        _add_target_link(
            admin_client, token, system_id, "design-pp-ok", "opt-pp-ok", "probe_point", str(point_id),
        )
        detail = _get_design(admin_client, token, system_id, "design-pp-ok")
        link = _target_link_by_kind(detail, "probe_point")
        assert link["link_state"] == "current"

    def test_unapproved_probe_point_is_refused_409(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-PPUnapproved")
        with get_conn() as conn:
            point_id, _ = _insert_probe_point_chain(conn, system_id, status="proposed")
        _create_design(admin_client, token, system_id, "design-pp-bad")
        _add_option(admin_client, token, system_id, "design-pp-bad", "opt-pp-bad")
        r = admin_client.post(
            "/solution-designs/design-pp-bad/target-links",
            json={"option_key": "opt-pp-bad", "target_kind": "probe_point", "target_ref": str(point_id)},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "probe_point_not_approved"

    def test_missing_probe_point_is_refused_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-PPMissing")
        _create_design(admin_client, token, system_id, "design-pp-missing")
        _add_option(admin_client, token, system_id, "design-pp-missing", "opt-pp-missing")
        r = admin_client.post(
            "/solution-designs/design-pp-missing/target-links",
            json={"option_key": "opt-pp-missing", "target_kind": "probe_point", "target_ref": "999999"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "probe_point_not_found"

    def test_another_systems_probe_point_is_indistinguishable_from_missing(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-PPCrossA")
        sys_b = _create_system(admin_client, token, "SD-PPCrossB")
        with get_conn() as conn:
            point_id, _ = _insert_probe_point_chain(conn, sys_a, status="approved")
        _create_design(admin_client, token, sys_b, "design-pp-cross")
        _add_option(admin_client, token, sys_b, "design-pp-cross", "opt-pp-cross")
        r = admin_client.post(
            "/solution-designs/design-pp-cross/target-links",
            json={"option_key": "opt-pp-cross", "target_kind": "probe_point", "target_ref": str(point_id)},
            headers=_headers(token, sys_b),
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "probe_point_not_found"


# ---------------------------------------------------------------------------
# 6. option_status: derived, never stored; follows the ledger
# ---------------------------------------------------------------------------


class TestOptionStatusDerivation:
    def test_option_status_is_not_a_stored_column(self, admin_client):
        token = _login(admin_client)
        with get_conn() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(solution_design_option)")}
        assert "option_status" not in columns
        assert "status" not in columns

    def test_status_follows_the_decision_ledger_through_every_value(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-StatusLedger")
        _create_design(admin_client, token, system_id, "design-ledger")
        _add_option(admin_client, token, system_id, "design-ledger", "opt-ledger")

        detail = _get_design(admin_client, token, system_id, "design-ledger")
        assert _option_by_key(detail, "opt-ledger")["option_status"] == "draft"

        _decide(admin_client, token, system_id, "design-ledger", "opt-ledger", "hold")
        detail = _get_design(admin_client, token, system_id, "design-ledger")
        assert _option_by_key(detail, "opt-ledger")["option_status"] == "held"

        _decide(admin_client, token, system_id, "design-ledger", "opt-ledger", "reject")
        detail = _get_design(admin_client, token, system_id, "design-ledger")
        assert _option_by_key(detail, "opt-ledger")["option_status"] == "rejected"

        _decide(admin_client, token, system_id, "design-ledger", "opt-ledger", "adopt")
        detail = _get_design(admin_client, token, system_id, "design-ledger")
        assert _option_by_key(detail, "opt-ledger")["option_status"] == "adopted"

        _decide(admin_client, token, system_id, "design-ledger", "opt-ledger", "withdraw")
        detail = _get_design(admin_client, token, system_id, "design-ledger")
        assert _option_by_key(detail, "opt-ledger")["option_status"] == "withdrawn"


# ---------------------------------------------------------------------------
# 6b. KNOWN BUG: correcting an existing Option is unreachable.
#
# `solution_design_option` is documented (this module's own docstring,
# `add_option`'s docstring, the schema comment in `app/db.py`, and
# `docs/ux-design-lineage.md` §3.2: "append-only(訂正は superseded_by_id)")
# as append-only: posting an Option with an `option_key` that already has a
# CURRENT row is supposed to supersede the old row and insert a new current
# one. The table's schema instead declares a plain
# `UNIQUE (solution_design_id, option_key)` constraint with NO
# `WHERE superseded_by_id IS NULL` qualifier, so `add_option`'s own INSERT
# for the correction always raises `sqlite3.IntegrityError` before it can
# reach the `UPDATE ... SET superseded_by_id = ...` that would make the
# prior row correctly read as superseded. Options can be created once and
# never corrected. This also makes `SolutionLinkStaleReason.design_changed`
# permanently unreachable (see the note in `TestLinkStalenessAndChangeOrigins`
# above), since a superseded Option row can never exist.
# ---------------------------------------------------------------------------


class TestOptionCorrection:
    """An option is append-only: correcting it supersedes the old row and
    appends a new current one (contract §3.2).

    The uniqueness of an option_key therefore holds over the CURRENT row
    only -- `ux_solution_design_option_current` is a partial index over
    `superseded_by_id IS NULL`. An unqualified table-level UNIQUE regressed
    this once and made corrections impossible outright, which also took
    `SolutionLinkStaleReason.design_changed` with it: that reason is only
    reachable once an option row HAS been superseded, so the constraint
    turned a documented finite value into dead code rather than merely
    unused. `add_option` retires the prior row before inserting its
    replacement for the same reason -- inserting first would put two current
    rows with one key in the table for the length of the statement.
    """

    def test_correcting_an_existing_option_appends_a_new_current_row(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OptionCorrectionBug")
        _create_design(admin_client, token, system_id, "design-correct")
        _add_option(admin_client, token, system_id, "design-correct", "opt-a", title="v1")

        second = _add_option(admin_client, token, system_id, "design-correct", "opt-a", title="v2")

        assert second["title"] == "v2"
        detail = _get_design(admin_client, token, system_id, "design-correct")
        assert _option_by_key(detail, "opt-a")["title"] == "v2"

    def test_the_superseded_row_survives_as_history(self, admin_client):
        """A correction never destroys what the option previously said."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OptionCorrectionHistory")
        _create_design(admin_client, token, system_id, "design-hist")
        _add_option(admin_client, token, system_id, "design-hist", "opt-a", title="v1")
        _add_option(admin_client, token, system_id, "design-hist", "opt-a", title="v2")

        with get_conn() as conn:
            rows = conn.execute(
                """SELECT id, title, superseded_by_id FROM solution_design_option
                   WHERE system_id = ? AND option_key = 'opt-a' ORDER BY id""",
                (system_id,),
            ).fetchall()
        assert [r["title"] for r in rows] == ["v1", "v2"]
        # The retired row points at its real successor. `add_option` parks it
        # on its own id to free the key mid-transaction; that placeholder must
        # never survive the commit.
        assert rows[0]["superseded_by_id"] == rows[1]["id"]
        assert rows[0]["superseded_by_id"] != rows[0]["id"]
        assert rows[1]["superseded_by_id"] is None

    def test_two_current_rows_for_one_option_key_remain_impossible(self, admin_client):
        """Scoping uniqueness to the current row must not weaken it."""
        import sqlite3

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-OptionCorrectionUnique")
        _create_design(admin_client, token, system_id, "design-uniq")
        _add_option(admin_client, token, system_id, "design-uniq", "opt-a")

        with get_conn() as conn:
            design_id = conn.execute(
                "SELECT id FROM solution_design WHERE system_id = ? AND design_key = 'design-uniq'",
                (system_id,),
            ).fetchone()["id"]
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO solution_design_option
                           (solution_design_id, system_id, option_key, option_order,
                            content_digest, created_at)
                       VALUES (?, ?, 'opt-a', 1, 'd', 1.0)""",
                    (design_id, system_id),
                )


# ---------------------------------------------------------------------------
# 7. Adoption exclusivity is enforced by REFUSAL, never auto-withdraw
#    (#408 acceptance condition 7)
# ---------------------------------------------------------------------------


class TestAdoptionExclusivity:
    def test_second_adopt_is_refused_and_the_incumbent_is_untouched(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ExclusiveRefuse")
        _create_design(admin_client, token, system_id, "design-excl")
        _add_option(admin_client, token, system_id, "design-excl", "opt-a")
        _add_option(admin_client, token, system_id, "design-excl", "opt-b")
        _decide(admin_client, token, system_id, "design-excl", "opt-a", "adopt")

        before = _get_design(admin_client, token, system_id, "design-excl")
        assert _option_by_key(before, "opt-a")["option_status"] == "adopted"
        decisions_before = len(_decisions_for_option(before, "opt-a"))

        r = admin_client.post(
            "/solution-designs/design-excl/decisions",
            json={"option_key": "opt-b", "decision": "adopt", "rationale": ""},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "solution_design_option_already_adopted"

        after = _get_design(admin_client, token, system_id, "design-excl")
        # The incumbent's own status and its decision-row count are BOTH
        # unchanged -- no system-authored `withdraw` row was fabricated.
        assert _option_by_key(after, "opt-a")["option_status"] == "adopted"
        assert len(_decisions_for_option(after, "opt-a")) == decisions_before
        # And option-b never became adopted either.
        assert _option_by_key(after, "opt-b")["option_status"] == "draft"

    def test_explicit_withdraw_then_adopt_succeeds(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-ExclusiveWithdraw")
        _create_design(admin_client, token, system_id, "design-wd")
        _add_option(admin_client, token, system_id, "design-wd", "opt-a")
        _add_option(admin_client, token, system_id, "design-wd", "opt-b")
        _decide(admin_client, token, system_id, "design-wd", "opt-a", "adopt")

        _decide(admin_client, token, system_id, "design-wd", "opt-a", "withdraw")
        _decide(admin_client, token, system_id, "design-wd", "opt-b", "adopt")

        detail = _get_design(admin_client, token, system_id, "design-wd")
        assert _option_by_key(detail, "opt-a")["option_status"] == "withdrawn"
        assert _option_by_key(detail, "opt-b")["option_status"] == "adopted"


# ---------------------------------------------------------------------------
# 8. Adoption is not implementation: §3.6's 5 items (#408 acceptance
#    condition 3)
# ---------------------------------------------------------------------------


class TestAdoptionDoesNotChangeExistingCanonicalState:
    """§3.6: adopting a Solution Design Option must not move `evolution_
    node.maturity`, Cell Improvement state, `components.mode`, create any
    patch/worktree/publish job row, or change Probe Plan/Probe Point
    approval -- each asserted by snapshotting before/after a full
    create -> option -> target-link -> adopt flow."""

    def test_evolution_node_maturity_is_unchanged(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoTouchMaturity")
        with get_conn() as conn:
            evolution_node.create_node(conn, system_id=system_id, node_key="untouched-node")
            before_maturity = conn.execute(
                "SELECT maturity FROM evolution_node WHERE system_id = ? AND node_key = ?",
                (system_id, "untouched-node"),
            ).fetchone()["maturity"]
        assert before_maturity == "exploring"

        _create_design(admin_client, token, system_id, "design-maturity")
        _add_option(admin_client, token, system_id, "design-maturity", "opt-maturity")
        _add_target_link(
            admin_client, token, system_id, "design-maturity", "opt-maturity",
            "evolution_node", "untouched-node",
        )
        _decide(admin_client, token, system_id, "design-maturity", "opt-maturity", "adopt")

        with get_conn() as conn:
            after_maturity = conn.execute(
                "SELECT maturity FROM evolution_node WHERE system_id = ? AND node_key = ?",
                (system_id, "untouched-node"),
            ).fetchone()["maturity"]
        assert after_maturity == before_maturity == "exploring"

    def test_cell_improvement_status_is_unchanged(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoTouchCellImprovement")
        with get_conn() as conn:
            cell_definition_id = _insert_cell(conn, system_id, "cell-ci")
            improvement_id = _insert_cell_improvement(conn, system_id, cell_definition_id, status="proposed")
            point_id, _ = _insert_probe_point_chain(conn, system_id, status="approved", component_id="cell-ci")

        _create_design(admin_client, token, system_id, "design-ci")
        _add_option(admin_client, token, system_id, "design-ci", "opt-ci")
        _add_target_link(
            admin_client, token, system_id, "design-ci", "opt-ci", "cell_definition", "cell-ci",
        )
        _decide(admin_client, token, system_id, "design-ci", "opt-ci", "adopt")

        with get_conn() as conn:
            status = conn.execute(
                "SELECT status FROM cell_improvements WHERE id = ?", (improvement_id,)
            ).fetchone()["status"]
        assert status == "proposed"

    def test_components_mode_is_unchanged(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoTouchComponentMode")
        with get_conn() as conn:
            _insert_component(conn, system_id, "svc-mode", mode="trace")

        _create_design(admin_client, token, system_id, "design-mode")
        _add_option(admin_client, token, system_id, "design-mode", "opt-mode")
        _add_target_link(
            admin_client, token, system_id, "design-mode", "opt-mode", "component", "svc-mode",
        )
        _decide(admin_client, token, system_id, "design-mode", "opt-mode", "adopt")

        with get_conn() as conn:
            mode = conn.execute(
                "SELECT mode FROM components WHERE system_id = ? AND component_id = ?",
                (system_id, "svc-mode"),
            ).fetchone()["mode"]
        assert mode == "trace"

    def test_no_patch_worktree_or_publish_job_row_is_created(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoPatchRows")
        with get_conn() as conn:
            before_patches = conn.execute(
                "SELECT COUNT(*) AS n FROM probe_patches WHERE system_id = ?", (system_id,)
            ).fetchone()["n"]
            before_jobs = conn.execute(
                "SELECT COUNT(*) AS n FROM publish_jobs WHERE system_id = ?", (system_id,)
            ).fetchone()["n"]
            _insert_component(conn, system_id, "svc-patch")
        assert before_patches == 0
        assert before_jobs == 0

        _create_design(admin_client, token, system_id, "design-patch")
        _add_option(admin_client, token, system_id, "design-patch", "opt-patch")
        _add_target_link(
            admin_client, token, system_id, "design-patch", "opt-patch", "component", "svc-patch",
        )
        _decide(admin_client, token, system_id, "design-patch", "opt-patch", "adopt")

        with get_conn() as conn:
            after_patches = conn.execute(
                "SELECT COUNT(*) AS n FROM probe_patches WHERE system_id = ?", (system_id,)
            ).fetchone()["n"]
            after_jobs = conn.execute(
                "SELECT COUNT(*) AS n FROM publish_jobs WHERE system_id = ?", (system_id,)
            ).fetchone()["n"]
        assert after_patches == 0
        assert after_jobs == 0

    def test_probe_plan_and_probe_point_approval_are_unchanged(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-NoTouchApproval")
        with get_conn() as conn:
            point_id, plan_id = _insert_probe_point_chain(conn, system_id, status="approved")

        _create_design(admin_client, token, system_id, "design-approval")
        _add_option(admin_client, token, system_id, "design-approval", "opt-approval")
        _add_target_link(
            admin_client, token, system_id, "design-approval", "opt-approval", "probe_point", str(point_id),
        )
        _decide(admin_client, token, system_id, "design-approval", "opt-approval", "adopt")

        with get_conn() as conn:
            point_status = conn.execute(
                "SELECT status FROM probe_points WHERE id = ?", (point_id,)
            ).fetchone()["status"]
            plan_status = conn.execute(
                "SELECT status FROM probe_plans WHERE id = ?", (plan_id,)
            ).fetchone()["status"]
        assert point_status == "approved"
        assert plan_status == "approved"

    def test_full_flow_writes_to_no_other_canonical_table(self, admin_client):
        """A broad snapshot of every existing-Epic canonical table this
        module might touch (evolution_node*, components, cell_*,
        probe_points/plans/patches, publish_jobs, purpose_*, interview_*,
        understanding_*), taken immediately before the Solution Design flow
        and again immediately after adoption. Every table's rows must be
        byte-for-byte identical."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-FullSnapshot")
        with get_conn() as conn:
            evolution_node.create_node(conn, system_id=system_id, node_key="snap-node")
            _insert_component(conn, system_id, "snap-svc")
            cell_id = _insert_cell(conn, system_id, "snap-cell")
            _insert_cell_improvement(conn, system_id, cell_id)
            point_id, _ = _insert_probe_point_chain(conn, system_id, status="approved", component_id="snap-pp")
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            _make_requirement(conn, system_id, "req-snap")

            before = _snapshot_canonical_tables(conn, system_id)

        _create_design(admin_client, token, system_id, "design-snapshot")
        _add_requirement_link(admin_client, token, system_id, "design-snapshot", "req-snap")
        _add_option(admin_client, token, system_id, "design-snapshot", "opt-snapshot")
        _add_target_link(
            admin_client, token, system_id, "design-snapshot", "opt-snapshot",
            "evolution_node", "snap-node",
        )
        _add_target_link(
            admin_client, token, system_id, "design-snapshot", "opt-snapshot",
            "component", "snap-svc",
        )
        _add_target_link(
            admin_client, token, system_id, "design-snapshot", "opt-snapshot",
            "capability", str(entity_id),
        )
        _add_target_link(
            admin_client, token, system_id, "design-snapshot", "opt-snapshot",
            "probe_point", str(point_id),
        )
        _decide(admin_client, token, system_id, "design-snapshot", "opt-snapshot", "adopt")

        with get_conn() as conn:
            after = _snapshot_canonical_tables(conn, system_id)

        assert after == before


# ---------------------------------------------------------------------------
# 9. Handoff (§3.7, #408 acceptance condition 4)
# ---------------------------------------------------------------------------


class TestHandoff:
    def test_no_adopted_option_is_incomplete_and_names_it(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-HandoffNoAdopt")
        _create_design(admin_client, token, system_id, "design-h-noadopt")
        _add_option(admin_client, token, system_id, "design-h-noadopt", "opt-h")

        handoff = _get_handoff(admin_client, token, system_id, "design-h-noadopt")
        assert handoff["handoff_state"] == "incomplete"
        assert handoff["adopted_option"] is None
        assert any(u["kind"] == "adopted_option" for u in handoff["unresolved_references"])

    def test_unresolvable_target_reference_is_named_not_silently_dropped(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-HandoffUnresolved")
        _create_design(admin_client, token, system_id, "design-h-unresolved")
        _add_option(admin_client, token, system_id, "design-h-unresolved", "opt-h2")
        _add_target_link(
            admin_client, token, system_id, "design-h-unresolved", "opt-h2",
            "evolution_node", "ghost-node",
        )
        _decide(admin_client, token, system_id, "design-h-unresolved", "opt-h2", "adopt")

        handoff = _get_handoff(admin_client, token, system_id, "design-h-unresolved")
        assert handoff["handoff_state"] == "incomplete"
        named = [u for u in handoff["unresolved_references"] if u["ref"] == "ghost-node"]
        assert len(named) == 1
        assert named[0]["kind"] == "target_link:evolution_node"

    def test_complete_handoff_when_everything_resolves(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-HandoffComplete")
        with get_conn() as conn:
            evolution_node.create_node(conn, system_id=system_id, node_key="resolvable-node")
            _make_requirement(conn, system_id, "req-h-complete")
        _create_design(admin_client, token, system_id, "design-h-complete")
        _add_requirement_link(admin_client, token, system_id, "design-h-complete", "req-h-complete")
        _add_option(admin_client, token, system_id, "design-h-complete", "opt-h3")
        _add_target_link(
            admin_client, token, system_id, "design-h-complete", "opt-h3",
            "evolution_node", "resolvable-node",
        )
        _decide(admin_client, token, system_id, "design-h-complete", "opt-h3", "adopt")

        handoff = _get_handoff(admin_client, token, system_id, "design-h-complete")
        assert handoff["handoff_state"] == "complete"
        assert handoff["unresolved_references"] == []
        assert handoff["adopted_option"]["option_key"] == "opt-h3"
        assert len(handoff["requirements"]) == 1
        assert handoff["requirements"][0]["requirement_key"] == "req-h-complete"

    def test_evaluation_policy_refs_are_grouped_by_level_never_flattened(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-HandoffLevels")
        with get_conn() as conn:
            for level in node_design.EVALUATION_LEVELS:
                node_design.create_evaluation_policy(
                    conn, system_id=system_id, policy_key=f"policy-{level}", level=level,
                    criteria=[{"name": "c", "measure": "m"}],
                    floors=[{"name": "f", "measure": "m"}],
                )
        _create_design(admin_client, token, system_id, "design-h-levels")
        _add_option(admin_client, token, system_id, "design-h-levels", "opt-h4")

        handoff = _get_handoff(admin_client, token, system_id, "design-h-levels")
        refs = handoff["evaluation_policy_refs"]
        assert set(refs) == set(node_design.EVALUATION_LEVELS)
        for level in node_design.EVALUATION_LEVELS:
            assert len(refs[level]) == 1
            assert refs[level][0]["level"] == level

    def test_no_composite_score_field_anywhere_in_the_handoff(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "SD-HandoffNoScore")
        with get_conn() as conn:
            evolution_node.create_node(conn, system_id=system_id, node_key="score-node")
            _make_requirement(conn, system_id, "req-score")
            for level in node_design.EVALUATION_LEVELS:
                node_design.create_evaluation_policy(
                    conn, system_id=system_id, policy_key=f"p-{level}", level=level,
                )
        _create_design(admin_client, token, system_id, "design-h-score")
        _add_requirement_link(admin_client, token, system_id, "design-h-score", "req-score")
        _add_option(admin_client, token, system_id, "design-h-score", "opt-h5")
        _add_target_link(
            admin_client, token, system_id, "design-h-score", "opt-h5", "evolution_node", "score-node",
        )
        _decide(admin_client, token, system_id, "design-h-score", "opt-h5", "adopt")

        handoff = _get_handoff(admin_client, token, system_id, "design-h-score")

        def _walk_forbidden(value):
            forbidden = {"score", "weight", "total", "overall", "confidence", "completeness", "percent"}
            if isinstance(value, dict):
                for key, sub in value.items():
                    assert not any(f in key.lower() for f in forbidden), key
                    _walk_forbidden(sub)
            elif isinstance(value, list):
                for item in value:
                    _walk_forbidden(item)

        _walk_forbidden(handoff)


# ---------------------------------------------------------------------------
# 10. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_same_design_key_is_allowed_in_two_systems(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-IsoKeyA")
        sys_b = _create_system(admin_client, token, "SD-IsoKeyB")
        _create_design(admin_client, token, sys_a, "shared-key")
        _create_design(admin_client, token, sys_b, "shared-key")

        a = _get_design(admin_client, token, sys_a, "shared-key")
        b = _get_design(admin_client, token, sys_b, "shared-key")
        assert a["id"] != b["id"]

    def test_a_design_is_invisible_from_another_system(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-IsoInvisibleA")
        sys_b = _create_system(admin_client, token, "SD-IsoInvisibleB")
        _create_design(admin_client, token, sys_a, "only-in-a")

        r = admin_client.get("/solution-designs/only-in-a", headers=_headers(token, sys_b))
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "solution_design_not_found"

    def test_requirement_link_refuses_a_foreign_systems_requirement(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-IsoReqA")
        sys_b = _create_system(admin_client, token, "SD-IsoReqB")
        with get_conn() as conn:
            _make_requirement(conn, sys_a, "req-only-a")
        _create_design(admin_client, token, sys_b, "design-cross-req")

        r = admin_client.post(
            "/solution-designs/design-cross-req/requirement-links",
            json={"requirement_key": "req-only-a", "note": ""},
            headers=_headers(token, sys_b),
        )
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["code"] == "requirement_not_found"

    def test_target_resolution_never_crosses_systems(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-IsoTargetA")
        sys_b = _create_system(admin_client, token, "SD-IsoTargetB")
        with get_conn() as conn:
            evolution_node.create_node(conn, system_id=sys_a, node_key="a-only-node")
            _insert_component(conn, sys_a, "a-only-component")

        _create_design(admin_client, token, sys_b, "design-cross-target")
        _add_option(admin_client, token, sys_b, "design-cross-target", "opt-cross")
        _add_target_link(
            admin_client, token, sys_b, "design-cross-target", "opt-cross",
            "evolution_node", "a-only-node",
        )
        _add_target_link(
            admin_client, token, sys_b, "design-cross-target", "opt-cross",
            "component", "a-only-component",
        )

        detail = _get_design(admin_client, token, sys_b, "design-cross-target")
        assert _target_link_by_kind(detail, "evolution_node")["link_state"] == "unresolved"
        assert _target_link_by_kind(detail, "evolution_node")["target_name"] is None
        assert _target_link_by_kind(detail, "component")["link_state"] == "unresolved"

    def test_decision_ledger_isolated_across_systems(self, admin_client):
        """Adopting an option in System A never leaks an "already adopted"
        refusal into System B, even with an identical design_key/option_key
        pair."""
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SD-IsoDecisionA")
        sys_b = _create_system(admin_client, token, "SD-IsoDecisionB")
        _create_design(admin_client, token, sys_a, "twin-design")
        _add_option(admin_client, token, sys_a, "twin-design", "opt-1")
        _add_option(admin_client, token, sys_a, "twin-design", "opt-2")
        _decide(admin_client, token, sys_a, "twin-design", "opt-1", "adopt")

        _create_design(admin_client, token, sys_b, "twin-design")
        _add_option(admin_client, token, sys_b, "twin-design", "opt-1")
        _add_option(admin_client, token, sys_b, "twin-design", "opt-2")
        # The SAME option_key that is refused in System A adopts cleanly in
        # System B -- the ledger's exclusivity check is System-scoped.
        _decide(admin_client, token, sys_b, "twin-design", "opt-2", "adopt")

        b_detail = _get_design(admin_client, token, sys_b, "twin-design")
        assert _option_by_key(b_detail, "opt-2")["option_status"] == "adopted"
        a_detail = _get_design(admin_client, token, sys_a, "twin-design")
        assert _option_by_key(a_detail, "opt-1")["option_status"] == "adopted"
