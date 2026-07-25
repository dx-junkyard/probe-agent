"""End-to-end read-only Probe Cell pilot (Issue #314).

The lower-level Cell Binding, Cell state, domain-orchestrator, and Root
Orchestrator suites each prove their own contracts.  This file ties those
contracts together as the operational pilot required by Epic #297:

* three approved Probe Points for one Feature become three worker Cells;
* every Cell is read through ``GET /cell-fabric/cells/{id}/state``;
* the domain and Root orchestrator digests expose the complete roster and
  topology;
* Feature/API refs, component traces, and task evidence remain drillable;
* the read phase cannot call an LLM and cannot mutate the target repository,
  component policies, or Cell-Fabric persistence; and
* identical Cell ids in another System cannot observe the pilot data.

All writes below are fixture setup.  The actual pilot phase uses GET requests
only.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.cell_tasks import resolve_evidence_ref
from app.db import get_conn


FEATURE_ID = "feature-checkout"
CAPABILITY_ID = "capability-checkout"
ORCHESTRATOR_ID = "checkout-orchestrator"
CELL_SPECS = (
    {
        "cell_id": "checkout-api",
        "path": "src/checkout_api.py",
        "symbol": "checkout_api.submit",
        "entrypoint_id": "POST:/checkout",
        "route_method": "POST",
        "route_path": "/checkout",
    },
    {
        "cell_id": "payment-authorizer",
        "path": "src/payment_authorizer.py",
        "symbol": "payment_authorizer.authorize",
        "entrypoint_id": "POST:/payments/authorize",
        "route_method": "POST",
        "route_path": "/payments/authorize",
    },
    {
        "cell_id": "order-recorder",
        "path": "src/order_recorder.py",
        "symbol": "order_recorder.record",
        "entrypoint_id": "POST:/orders",
        "route_method": "POST",
        "route_path": "/orders",
    },
)


@dataclass(frozen=True)
class PilotFixture:
    client: TestClient
    headers: Dict[str, str]
    isolated_headers: Dict[str, str]
    system_id: int
    isolated_system_id: int
    target_repo: Path
    snapshot_id: int
    point_ids: Dict[str, int]
    binding_ids: Dict[str, int]
    trace_ids: Dict[str, str]


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PROBE_DB_PATH", str(tmp_path / "probe-cell-read-only-pilot-test.db")
    )
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("probe_session")


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_system(client: TestClient, token: str, name: str) -> dict:
    response = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(token: str, system_id: int) -> Dict[str, str]:
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_role_card(client: TestClient, headers: Dict[str, str]) -> None:
    response = client.post(
        "/cell-fabric/role-cards",
        json={
            "role_key": "worker-probe",
            "version": "1.0.0",
            "status": "active",
            "mission": "Read and aggregate approved Probe Point facts",
            "scope": ["read-only-pilot"],
            "out_of_scope": ["policy-write", "source-write"],
            "model_alias": "worker-default",
            "tool_policy": {
                "allowed_tools": ["read"],
                "network_allowed": False,
            },
            "acceptance_template": ["state is source-backed"],
            "rubric_ref": None,
            "changelog": "initial",
            "schema_version": "1.0",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _create_cell(
    client: TestClient,
    headers: Dict[str, str],
    cell_id: str,
    *,
    roster: Optional[List[str]] = None,
) -> None:
    response = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id,
            "roster": roster,
            "role_card_ref": {
                "role_key": "worker-probe",
                "version": "1.0.0",
            },
            "status": "active",
            "mission": None,
            "schema_version": "1.0",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _create_cell_roster(client: TestClient, headers: Dict[str, str]) -> None:
    _create_role_card(client, headers)
    for spec in CELL_SPECS:
        _create_cell(client, headers, spec["cell_id"])
    _create_cell(
        client,
        headers,
        ORCHESTRATOR_ID,
        roster=[spec["cell_id"] for spec in CELL_SPECS],
    )


def _make_target_repo(tmp_path: Path) -> Path:
    target_repo = tmp_path / "pilot-target-repo"
    for spec in CELL_SPECS:
        path = target_repo / spec["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"def {spec['symbol'].split('.')[-1]}(payload):\n"
            f"    return {{'component': '{spec['cell_id']}', 'payload': payload}}\n",
            encoding="utf-8",
        )
    return target_repo


def _repo_manifest(target_repo: Path) -> Dict[str, str]:
    return {
        str(path.relative_to(target_repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(target_repo.rglob("*"))
        if path.is_file()
    }


def _insert_pilot_artifacts(system_id: int, target_repo: Path):
    """Insert one approved plan containing the pilot's three Probe Points."""
    now = time.time()
    manifest = _repo_manifest(target_repo)
    commit_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()[:40]
    point_ids: Dict[str, int] = {}

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            snapshot_id = conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status,
                        created_at, completed_at)
                   VALUES (?, ?, ?, 'ready', ?, ?)""",
                (system_id, str(target_repo), commit_sha, now, now),
            ).lastrowid
            run_id = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan', 'mock', 'mock-model',
                           'pilot-v1', 'pilot-v1', 'reasoning_llm',
                           'completed', 1, ?, ?)""",
                (system_id, snapshot_id, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO feature_drafts
                       (system_id, intelligence_run_id, snapshot_id, feature_id,
                        name, summary, user_value, success_criteria, risks,
                        decision_method, is_mock, created_at)
                   VALUES (?, ?, ?, ?, 'Checkout', 'Complete checkout',
                           'A confirmed order', '[]', '[]',
                           'reasoning_llm', 1, ?)""",
                (system_id, run_id, snapshot_id, FEATURE_ID, now),
            )
            plan_id = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'Observe checkout', 'approved',
                           'manual', ?, ?)""",
                (system_id, snapshot_id, run_id, FEATURE_ID, now, now),
            ).lastrowid

            for spec in CELL_SPECS:
                file_path = target_repo / spec["path"]
                content = file_path.read_bytes()
                conn.execute(
                    """INSERT INTO snapshot_files
                           (snapshot_id, path, source_type, size_bytes,
                            content_hash, content, inclusion_status)
                       VALUES (?, ?, 'text', ?, ?, ?, 'indexed')""",
                    (
                        snapshot_id,
                        spec["path"],
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                        content,
                    ),
                )
                symbol_id = conn.execute(
                    """INSERT INTO code_symbols
                           (snapshot_id, system_id, path, qualified_name, kind,
                            start_line, end_line, component_id)
                       VALUES (?, ?, ?, ?, 'function', 1, 2, ?)""",
                    (
                        snapshot_id,
                        system_id,
                        spec["path"],
                        spec["symbol"],
                        spec["cell_id"],
                    ),
                ).lastrowid
                conn.execute(
                    """INSERT INTO code_entrypoints
                           (system_id, snapshot_id, entrypoint_type,
                            entrypoint_id, category, label, operation,
                            framework, handler_symbol_id, handler_path,
                            handler_qualified_name, line_start, line_end,
                            route_method, route_path, confidence, evidence_json,
                            source, created_at)
                       VALUES (?, ?, 'http_route', ?, 'api', ?, ?, 'fastapi',
                               ?, ?, ?, 1, 2, ?, ?, 1.0, '[]',
                               'deterministic', ?)""",
                    (
                        system_id,
                        snapshot_id,
                        spec["entrypoint_id"],
                        f"{spec['route_method']} {spec['route_path']}",
                        f"{spec['route_method']} {spec['route_path']}",
                        symbol_id,
                        spec["path"],
                        spec["symbol"],
                        spec["route_method"],
                        spec["route_path"],
                        now,
                    ),
                )
                point_ids[spec["cell_id"]] = conn.execute(
                    """INSERT INTO probe_points
                           (plan_id, system_id, component_id, feature_id, path,
                            symbol, line_start, line_end, reason,
                            recommended_mode, side_effect_risk, replayability,
                            denylist_hit, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, 2,
                               'read-only pilot', 'trace', 'low',
                               'replayable', NULL, 'approved', ?, ?)""",
                    (
                        plan_id,
                        system_id,
                        spec["cell_id"],
                        FEATURE_ID,
                        spec["path"],
                        spec["symbol"],
                        now,
                        now,
                    ),
                ).lastrowid
                conn.execute(
                    """INSERT INTO components
                           (system_id, component_id, mode, updated_at)
                       VALUES (?, ?, 'trace', ?)""",
                    (system_id, spec["cell_id"], now),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return snapshot_id, point_ids


def _create_bindings(
    client: TestClient,
    headers: Dict[str, str],
    point_ids: Dict[str, int],
) -> Dict[str, int]:
    binding_ids: Dict[str, int] = {}
    for spec in CELL_SPECS:
        response = client.post(
            f"/cell-fabric/cells/{spec['cell_id']}/bindings",
            json={
                "probe_point_id": point_ids[spec["cell_id"]],
                "feature_refs": [FEATURE_ID],
                "capability_refs": [CAPABILITY_ID],
                "entrypoint_refs": [spec["entrypoint_id"]],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        binding_ids[spec["cell_id"]] = response.json()["id"]
    return binding_ids


def _post_traces(
    client: TestClient, headers: Dict[str, str]
) -> Dict[str, str]:
    trace_ids: Dict[str, str] = {}
    for index, spec in enumerate(CELL_SPECS):
        trace_id = f"pilot-trace-{index + 1}"
        response = client.post(
            "/traces",
            json={
                "trace_id": trace_id,
                "component_id": spec["cell_id"],
                "mode": "trace",
                "input": {"args": [], "kwargs": {"feature": FEATURE_ID}},
                "output": "'ok'",
                "error": None,
                "duration_ms": float(index + 1),
                "timestamp": time.time() + index,
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        trace_ids[spec["cell_id"]] = trace_id
    return trace_ids


def _create_evidence_tasks(
    client: TestClient,
    headers: Dict[str, str],
    trace_ids: Dict[str, str],
) -> None:
    goal_response = client.post(
        "/cell-fabric/goals",
        json={
            "title": "Observe checkout",
            "description": "",
            "parent_goal_id": None,
            "owner_cell_id": None,
        },
        headers=headers,
    )
    assert goal_response.status_code == 201, goal_response.text
    goal_id = goal_response.json()["id"]

    for spec in CELL_SPECS:
        task_response = client.post(
            "/cell-fabric/tasks",
            json={
                "goal_id": goal_id,
                "owner_cell_id": spec["cell_id"],
                "title": f"Observe {spec['cell_id']}",
                "acceptance": ["trace is available"],
                "context_refs": [f"trace:{trace_ids[spec['cell_id']]}"],
                "budget": None,
                "deadline": None,
                "priority": "normal",
                "delegated_by_cell_id": ORCHESTRATOR_ID,
                "idempotency_key": f"pilot-{spec['cell_id']}",
            },
            headers=headers,
        )
        assert task_response.status_code == 201, task_response.text
        task_id = task_response.json()["id"]
        for status in ("doing", "review"):
            response = client.post(
                f"/cell-fabric/tasks/{task_id}/transition",
                json={"new_status": status},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        response = client.post(
            f"/cell-fabric/tasks/{task_id}/transition",
            json={
                "new_status": "done",
                "acceptance_met": True,
                "evidence_refs": [f"trace:{trace_ids[spec['cell_id']]}"],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def pilot_fixture(admin_client, tmp_path) -> PilotFixture:
    token = _login(admin_client)
    system = _create_system(admin_client, token, "Checkout Pilot")
    isolated_system = _create_system(admin_client, token, "Isolated Control")
    headers = _headers(token, system["id"])
    isolated_headers = _headers(token, isolated_system["id"])

    _create_cell_roster(admin_client, headers)
    # The control System deliberately uses identical Cell ids.  If any read
    # omits system_id, pilot data will appear here and the isolation test will
    # fail with a positive name collision rather than a merely missing Cell.
    _create_cell_roster(admin_client, isolated_headers)

    target_repo = _make_target_repo(tmp_path)
    snapshot_id, point_ids = _insert_pilot_artifacts(system["id"], target_repo)
    binding_ids = _create_bindings(admin_client, headers, point_ids)
    trace_ids = _post_traces(admin_client, headers)
    _create_evidence_tasks(admin_client, headers, trace_ids)

    return PilotFixture(
        client=admin_client,
        headers=headers,
        isolated_headers=isolated_headers,
        system_id=system["id"],
        isolated_system_id=isolated_system["id"],
        target_repo=target_repo,
        snapshot_id=snapshot_id,
        point_ids=point_ids,
        binding_ids=binding_ids,
        trace_ids=trace_ids,
    )


def _protected_db_snapshot(system_id: int) -> Dict[str, list]:
    """Rows that the read-only pilot must never mutate."""
    queries = {
        "repository_snapshots": (
            "SELECT * FROM repository_snapshots WHERE system_id = ? ORDER BY id"
        ),
        "snapshot_files": (
            """SELECT sf.* FROM snapshot_files sf
               JOIN repository_snapshots rs ON rs.id = sf.snapshot_id
               WHERE rs.system_id = ? ORDER BY sf.id"""
        ),
        "feature_drafts": (
            "SELECT * FROM feature_drafts WHERE system_id = ? ORDER BY id"
        ),
        "probe_plans": "SELECT * FROM probe_plans WHERE system_id = ? ORDER BY id",
        "probe_points": "SELECT * FROM probe_points WHERE system_id = ? ORDER BY id",
        "code_entrypoints": (
            "SELECT * FROM code_entrypoints WHERE system_id = ? ORDER BY id"
        ),
        "components": (
            "SELECT * FROM components WHERE system_id = ? ORDER BY component_id"
        ),
        "traces": "SELECT * FROM traces WHERE system_id = ? ORDER BY trace_id",
        "cell_definitions": (
            "SELECT * FROM cell_definitions WHERE system_id = ? ORDER BY id"
        ),
        "cell_bindings": (
            "SELECT * FROM cell_bindings WHERE system_id = ? ORDER BY id"
        ),
        "cell_activations": (
            "SELECT * FROM cell_activations WHERE system_id = ? ORDER BY id"
        ),
        "cell_goals": "SELECT * FROM cell_goals WHERE system_id = ? ORDER BY id",
        "cell_tasks": "SELECT * FROM cell_tasks WHERE system_id = ? ORDER BY id",
        "intelligence_runs": (
            "SELECT * FROM intelligence_runs WHERE system_id = ? ORDER BY id"
        ),
    }
    with get_conn() as conn:
        return {
            name: [tuple(row) for row in conn.execute(query, (system_id,)).fetchall()]
            for name, query in queries.items()
        }


def test_three_cell_feature_pilot_is_read_only_and_drillable(
    pilot_fixture: PilotFixture, monkeypatch
):
    pilot = pilot_fixture

    import app.cell_orchestrator as orchestrator_module
    import app.llm as llm_module

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("the read-only Cell pilot must not call an LLM")

    monkeypatch.setattr(
        orchestrator_module, "create_llm_client", unexpected_llm_call
    )
    monkeypatch.setattr(llm_module, "create_llm_client", unexpected_llm_call)

    repo_before = _repo_manifest(pilot.target_repo)
    db_before = _protected_db_snapshot(pilot.system_id)

    for spec in CELL_SPECS:
        response = pilot.client.get(
            f"/cell-fabric/cells/{spec['cell_id']}/state",
            headers=pilot.headers,
        )
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["binding"]["id"] == pilot.binding_ids[spec["cell_id"]]
        assert state["binding"]["probe_point_id"] == pilot.point_ids[spec["cell_id"]]
        assert state["binding"]["feature_refs"] == [FEATURE_ID]
        assert state["binding"]["capability_refs"] == [CAPABILITY_ID]
        assert state["binding"]["entrypoint_refs"] == [spec["entrypoint_id"]]
        assert state["binding"]["status"] == "active"
        assert state["state"]["health"]["heartbeat_at"] is not None
        assert state["state"]["health"]["error_rate"] == 0.0
        assert state["recent_activations"] == []

        traces_response = pilot.client.get(
            f"/components/{spec['cell_id']}/traces",
            headers=pilot.headers,
        )
        assert traces_response.status_code == 200, traces_response.text
        assert [row["trace_id"] for row in traces_response.json()] == [
            pilot.trace_ids[spec["cell_id"]]
        ]

    digest_response = pilot.client.get(
        f"/cell-fabric/orchestrators/{ORCHESTRATOR_ID}/digest",
        headers=pilot.headers,
    )
    assert digest_response.status_code == 200, digest_response.text
    digest = digest_response.json()["digest"]
    assert {entry["cell_id"] for entry in digest["roster"]} == {
        spec["cell_id"] for spec in CELL_SPECS
    }
    roster_by_cell = {entry["cell_id"]: entry for entry in digest["roster"]}
    for spec in CELL_SPECS:
        entry = roster_by_cell[spec["cell_id"]]
        assert entry["binding_status"] == "active"
        assert "topology" in entry, entry
        assert entry["topology"] == {
            "feature_refs": [FEATURE_ID],
            "capability_refs": [CAPABILITY_ID],
            "entrypoint_refs": [spec["entrypoint_id"]],
        }
        assert entry["health"]["heartbeat_at"] is not None

    root_response = pilot.client.get(
        "/cell-fabric/root-digest", headers=pilot.headers
    )
    assert root_response.status_code == 200, root_response.text
    root_digest = root_response.json()["digest"]
    orchestrator_point = next(
        point
        for point in root_digest["key_points"]
        if point.get("type") == "orchestrator"
        and point["cell_id"] == ORCHESTRATOR_ID
    )
    topology_by_cell = {
        entry["cell_id"]: entry for entry in orchestrator_point["topology"]
    }
    for spec in CELL_SPECS:
        assert topology_by_cell[spec["cell_id"]] == {
            "cell_id": spec["cell_id"],
            "feature_refs": [FEATURE_ID],
            "capability_refs": [CAPABILITY_ID],
            "entrypoint_refs": [spec["entrypoint_id"]],
        }

    evidence_by_cell = {
        entry["cell_id"]: entry["evidence_refs"]
        for entry in root_digest["evidence"]["tasks"]
    }
    assert evidence_by_cell == {
        spec["cell_id"]: [f"trace:{pilot.trace_ids[spec['cell_id']]}"]
        for spec in CELL_SPECS
    }

    # The topology refs exposed by Cell state/digests resolve to real Feature
    # and API rows, and every trace evidence ref resolves in this System.
    with get_conn() as conn:
        feature_row = conn.execute(
            """SELECT feature_id FROM feature_drafts
               WHERE system_id = ? AND snapshot_id = ? AND feature_id = ?""",
            (pilot.system_id, pilot.snapshot_id, FEATURE_ID),
        ).fetchone()
        assert feature_row is not None
        entrypoint_ids = {
            row["entrypoint_id"]
            for row in conn.execute(
                """SELECT entrypoint_id FROM code_entrypoints
                   WHERE system_id = ? AND snapshot_id = ?""",
                (pilot.system_id, pilot.snapshot_id),
            ).fetchall()
        }
        assert entrypoint_ids == {spec["entrypoint_id"] for spec in CELL_SPECS}
        for refs in evidence_by_cell.values():
            for ref in refs:
                assert resolve_evidence_ref(conn, pilot.system_id, ref) is True

    # No GET above may change source, component policy, bindings, audit runs,
    # tasks, traces, or any other pilot fixture row.
    assert _repo_manifest(pilot.target_repo) == repo_before
    assert _protected_db_snapshot(pilot.system_id) == db_before


def test_three_cell_feature_pilot_is_system_scoped(
    pilot_fixture: PilotFixture,
):
    pilot = pilot_fixture

    # The other System contains the exact same business Cell ids and roster,
    # but none of the pilot's bindings/traces/tasks.  Every read must therefore
    # return the control System's own empty facts rather than pilot data.
    for spec in CELL_SPECS:
        response = pilot.client.get(
            f"/cell-fabric/cells/{spec['cell_id']}/state",
            headers=pilot.isolated_headers,
        )
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["binding"] is None
        assert state["state"]["health"]["heartbeat_at"] is None
        assert state["state"]["health"]["error_rate"] is None
        assert state["recent_activations"] == []

        traces_response = pilot.client.get(
            f"/components/{spec['cell_id']}/traces",
            headers=pilot.isolated_headers,
        )
        assert traces_response.status_code == 200, traces_response.text
        assert traces_response.json() == []

    digest_response = pilot.client.get(
        f"/cell-fabric/orchestrators/{ORCHESTRATOR_ID}/digest",
        headers=pilot.isolated_headers,
    )
    assert digest_response.status_code == 200, digest_response.text
    digest = digest_response.json()["digest"]
    assert {entry["cell_id"] for entry in digest["roster"]} == {
        spec["cell_id"] for spec in CELL_SPECS
    }
    for entry in digest["roster"]:
        assert entry["binding_status"] is None
        assert "topology" in entry, entry
        assert entry["topology"] == {
            "feature_refs": [],
            "capability_refs": [],
            "entrypoint_refs": [],
        }
        assert entry["health"]["heartbeat_at"] is None
    assert all(count == 0 for count in digest["tasks"]["total"].values())

    root_response = pilot.client.get(
        "/cell-fabric/root-digest", headers=pilot.isolated_headers
    )
    assert root_response.status_code == 200, root_response.text
    root_digest = root_response.json()["digest"]
    assert root_digest["evidence"]["tasks"] == []
    control_point = next(
        point
        for point in root_digest["key_points"]
        if point.get("type") == "orchestrator"
        and point["cell_id"] == ORCHESTRATOR_ID
    )
    assert all(
        entry["feature_refs"] == []
        and entry["capability_refs"] == []
        and entry["entrypoint_refs"] == []
        for entry in control_point["topology"]
    )

    # Positive control: the same Cell id in the pilot System does have the
    # approved binding, so the empty control response is genuine isolation.
    pilot_state = pilot.client.get(
        f"/cell-fabric/cells/{CELL_SPECS[0]['cell_id']}/state",
        headers=pilot.headers,
    )
    assert pilot_state.status_code == 200, pilot_state.text
    assert pilot_state.json()["binding"]["feature_refs"] == [FEATURE_ID]
