"""Tests for the execution-target gate (Epic #412 / Issue #413, §4.3).

`app/execution_target.py` is the single deterministic mapping from an
execution target (a Component, a Feature) to the Evolution Node whose
execution mode governs it, and the four pre-existing execution entry points
now go through it:

    POST /experiments/{id}/run                 (feature -> Node)
    POST /replay-runs                          (component -> Node)
    POST /replay-variant-runs                  (component -> Node)
    POST /candidate-versions/{id}/replay       (component -> Node)
    POST /candidate-versions/{id}/promote      (component -> Node)

What these tests hold in place:

* the three classifications `governed` / `unmapped` / `ambiguous`, each on its
  own;
* a `governed` target under `fixed` and under `observe` is REFUSED at every
  entry point -- this is #413's acceptance criterion, and before this change
  a `fixed` Node could still have a candidate executed through these
  endpoints, so the mode axis did not actually control candidate execution;
* the SAME target under `shadow` proceeds and really runs;
* an `unmapped` target behaves exactly as it did before the gate existed
  (bulk-migrating existing Components onto Nodes is an explicit Epic
  non-goal, so refusing what cannot be classified would be a worse failure
  than the hole being closed) -- and says so, rather than being silently
  indistinguishable from "permitted";
* `ambiguous` fails closed with its own finite code;
* System isolation: another System's Node never governs (nor is revealed by)
  this System's execution.
"""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import execution_target
from app.experiment_runner import patch_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def unsafe_test_execution(monkeypatch):
    monkeypatch.setenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "true")


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "execution-target-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv(
        "PROBE_REPLAY_WORKSPACE_BASE", str(tmp_path / "replay-workspaces")
    )
    monkeypatch.setenv(
        "PROBE_EXPERIMENT_WORKSPACE_BASE", str(tmp_path / "experiment-workspaces")
    )
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("PROBE_REPLAY_TIMEOUT_SECONDS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client
    get_llm_client.cache_clear()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


SVC_SOURCE = '''\
def probe(component_id=None):
    def deco(fn):
        return fn
    return deco


@probe(component_id="norm")
def normalize(payload):
    return {"kind": "a", "size": len(payload)}
'''


WORKLOAD = (
    "import json\n"
    "from pathlib import Path\n"
    "import calc\n\n"
    "path = Path('.probe-agent/experiment-result.json')\n"
    "path.parent.mkdir(exist_ok=True)\n"
    "path.write_text(json.dumps({\n"
    "  'traces': [{'output': calc.value()}],\n"
    "  'shadow_results': [],\n"
    "  'evaluations': [{'status': 'ok'}],\n"
    "  'safety_warnings': []\n"
    "}))\n"
)

PROBE_YML = (
    "commands:\n"
    "  install: []\n"
    "  test:\n"
    "    - python -m pytest -q\n"
    "  smoke: []\n"
    "  workload:\n"
    "    - python workload.py\n"
    "runtime:\n"
    "  network: false\n"
    "  timeout_seconds: 30\n"
    "  env:\n"
    "    EXPERIMENT_FIXTURE: '1'\n"
    "experiment:\n"
    "  result_artifact_path: .probe-agent/experiment-result.json\n"
    "  artifact_retention_seconds: 0\n"
)


@pytest.fixture
def target_repo(tmp_path):
    """One repo serving BOTH lanes: `svc.py` for the Replay/Candidate
    (component) lane and `calc.py` + a workload for the Experiment (feature)
    lane, so a single System can exercise all entry points."""
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "svc.py").write_text(SVC_SOURCE)
    (repo / "calc.py").write_text("def value():\n    return 1\n")
    (repo / "test_calc.py").write_text(
        "import calc\n\ndef test_value():\n    assert calc.value() in (1, 2, 3)\n"
    )
    (repo / "workload.py").write_text(WORKLOAD)
    (repo / "probe-agent.yml").write_text(PROBE_YML)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


# ---------------------------------------------------------------------------
# Small API helpers
# ---------------------------------------------------------------------------


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _prepare(client, repo, name="execution-target-system"):
    token = _login(client)
    created = client.post(
        "/systems", json={"name": name, "environment": "test"}, headers=_headers(token)
    )
    assert created.status_code == 201, created.text
    system = created.json()
    headers = _headers(token, system["id"])
    assert client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": []},
        headers=headers,
    ).status_code == 200
    snapshot = client.post("/repository/snapshots", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    assert client.post("/repository/symbols/index", headers=headers).status_code == 201
    return token, system, headers, snapshot.json()


def _capture(args=(), kwargs=None):
    from probe_agent.replay_capture import capture_input, compile_spec

    return capture_input(tuple(args), dict(kwargs or {}), compile_spec(True))


def _post_trace(client, headers, trace_id, component_id, *, args=(), output=None):
    capture, replayability, reasons = _capture(args, None)
    event = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [repr(a) for a in args], "kwargs": {}},
        "output": output,
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
        "input_capture": capture,
        "replayability": replayability,
        "replay_reasons": reasons,
    }
    assert client.post("/traces", json=event, headers=headers).status_code == 201


def _approve(client, headers, component_id):
    response = client.post(
        f"/components/{component_id}/replay-approval",
        json={"reason": "test"},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _create_set(client, headers, trace_ids, component_id, name="set"):
    response = client.post(
        "/replay-sets",
        json={"component_id": component_id, "name": name, "trace_ids": trace_ids},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_node(client, headers, node_key):
    response = client.post(
        "/evolution-nodes",
        json={"node_key": node_key, "display_name": node_key},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _link(client, headers, node_id, link_kind, target_ref):
    response = client.post(
        f"/evolution-nodes/{node_id}/links",
        json={"link_kind": link_kind, "target_ref": target_ref},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assign(client, headers, *, scope_kind, scope_ref, mode, **extra):
    payload = {
        "scope_kind": scope_kind,
        "scope_ref": scope_ref,
        "mode": mode,
        "reason": "test",
    }
    payload.update(extra)
    response = client.post(
        "/execution-modes/assignments", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _govern_component(client, headers, component_id, node_key, mode):
    """Link a Node to the Component and put it in `mode`."""
    node = _create_node(client, headers, node_key)
    _link(client, headers, node["id"], "component", component_id)
    _assign(client, headers, scope_kind="node", scope_ref=node_key, mode=mode)
    return node


def _approved_flow_proposal(
    system_id, node_key, candidate_refs, *, captured_snapshot_id=None
):
    """Create the canonical approval fact used by real execution boundaries.

    This fixture intentionally seeds the ledger directly: proposal validation
    has its own suite; these tests exercise the integration from an already
    approved proposal into Replay / Experiment.
    """
    from app.db import get_conn

    now = time.time()
    flow_ref = f"execution-auth:{node_key}"
    with get_conn() as conn:
        node = conn.execute(
            "SELECT id FROM evolution_node WHERE system_id = ? AND node_key = ?",
            (system_id, node_key),
        ).fetchone()
        conn.execute(
            """INSERT INTO evolution_node_link
                   (system_id, node_id, link_kind, target_ref, created_by, created_at)
               VALUES (?, ?, 'flow', ?, 'test', ?)""",
            (system_id, node["id"], flow_ref, now),
        )
        cur = conn.execute(
            """INSERT INTO flow_experiment_proposal
                   (system_id, proposal_key, flow_subject_kind, flow_subject_ref,
                    captured_snapshot_id,
                    comparison_scope, title, purpose, hypothesis, baseline_ref,
                    candidate_refs_json, evaluation_axes_json, quality_floor_json,
                    isolation_strategy, isolation_detail, cost_cap_json,
                    stop_conditions_json, rollback_plan, evidence_refs_json,
                    decision_method, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, 'single_node', 't', 'p', 'h',
                       'baseline:test', ?, '[{"level":"node","name":"match"}]',
                       '{"match":1}', 'isolated_workspace', 'network-off',
                       '{"max_runs":5}', '["stop"]', 'discard', '["test:evidence"]',
                       'manual', 'root', ?)""",
            (
                system_id,
                f"auth-{node_key}-{time.time_ns()}",
                "static_flow" if captured_snapshot_id is not None else "runtime_flow",
                flow_ref,
                captured_snapshot_id,
                json.dumps(list(candidate_refs)),
                now,
            ),
        )
        proposal_id = cur.lastrowid
        conn.execute(
            """INSERT INTO flow_experiment_target
                   (system_id, proposal_id, target_node_key, target_role, position, created_at)
               VALUES (?, ?, ?, 'candidate_target', 0, ?)""",
            (system_id, proposal_id, node_key, now),
        )
        for kind in ("proposed", "approved"):
            conn.execute(
                """INSERT INTO flow_experiment_event
                       (system_id, proposal_id, event_kind, actor_kind, actor,
                        reason, decision_method, payload_json, created_at)
                   VALUES (?, ?, ?, 'user', 'root', 'test', 'manual', '{}', ?)""",
                (system_id, proposal_id, kind, now),
            )
    return proposal_id


def _recorded_execution_refs(system_id, proposal_id):
    from app.db import get_conn

    with get_conn() as conn:
        return [
            (row["execution_kind"], row["execution_ref"])
            for row in conn.execute(
                """SELECT execution_kind, execution_ref
                     FROM flow_experiment_execution_ref
                    WHERE system_id = ? AND proposal_id = ? ORDER BY id""",
                (system_id, proposal_id),
            ).fetchall()
        ]


def _make_patch(repo, rel, transform):
    path = repo / rel
    original = path.read_text()
    path.write_text(transform(original))
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--", rel],
        check=True, capture_output=True, text=True,
    ).stdout
    _git(repo, "checkout", "--", rel)
    assert diff.strip(), "transform produced no diff"
    return diff


def _experiment_payload(snapshot_id, feature_id="calculator"):
    return {
        "feature_id": feature_id,
        "objective": "compare calculator outputs",
        "snapshot_id": snapshot_id,
        "variants": [
            {
                "label": "return two",
                "patch_text": (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    " def value():\n-    return 1\n+    return 2\n"
                ),
                "source": "test",
                "risk_note": "changes numeric output",
            },
            {
                "label": "return three",
                "patch_text": (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    " def value():\n-    return 1\n+    return 3\n"
                ),
                "source": "test",
                "risk_note": "changes numeric output",
            },
        ],
    }


def _denial(response):
    """The finite denial body every gated refusal shares (§4.1)."""
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail


# ---------------------------------------------------------------------------
# 1. The mapping itself: the three classifications
# ---------------------------------------------------------------------------


def test_classification_unmapped_when_no_node_links_the_target(
    admin_client, target_repo
):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    response = admin_client.get(
        "/execution-modes/target-governance",
        params={"target_kind": "component", "target_ref": "norm"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["governance"] == "unmapped"
    assert body["gated"] is False
    assert body["node_key"] is None
    assert body["node_keys"] == []
    # No mode was resolved, so none is reported. Substituting the fail-closed
    # default would assert a reading that was never taken (#380).
    assert body["mode"] is None
    assert body["capability_permitted"] is None


def test_classification_governed_by_exactly_one_node(admin_client, target_repo):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _govern_component(admin_client, headers, "norm", "norm-node", "propose")

    response = admin_client.get(
        "/execution-modes/target-governance",
        params={
            "target_kind": "component",
            "target_ref": "norm",
            "capability": "candidate_execution",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["governance"] == "governed"
    assert body["gated"] is True
    assert body["node_key"] == "norm-node"
    assert body["mode"] == "propose"
    assert body["mode_source"] == "node"
    assert body["mode_reason"] == "node_assignment"
    # `propose` deliberately does NOT carry candidate_execution (§1.3).
    assert body["capability_permitted"] is False
    assert body["denial_code"] == "capability_not_permitted"


def test_classification_ambiguous_when_two_nodes_link_one_target(
    admin_client, target_repo
):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    first = _create_node(admin_client, headers, "node-a")
    second = _create_node(admin_client, headers, "node-b")
    _link(admin_client, headers, first["id"], "component", "norm")
    _link(admin_client, headers, second["id"], "component", "norm")

    response = admin_client.get(
        "/execution-modes/target-governance",
        params={"target_kind": "component", "target_ref": "norm"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["governance"] == "ambiguous"
    assert body["gated"] is False
    assert body["node_key"] is None
    assert body["node_keys"] == ["node-a", "node-b"]
    assert body["denial_code"] == "ambiguous_target_mapping"


def test_mapping_matches_exactly_never_by_prefix(admin_client, target_repo):
    """Principle 6: a near-miss is not a match. Guessing which Node
    "probably" owns a target is the caller's-claim defect EM-ADR-4 removed,
    re-introduced one layer up."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _govern_component(admin_client, headers, "norm", "norm-node", "fixed")

    for near_miss in ("nor", "norm2", "NORM", " norm"):
        response = admin_client.get(
            "/execution-modes/target-governance",
            params={"target_kind": "component", "target_ref": near_miss},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["governance"] == "unmapped", near_miss


def test_superseded_link_no_longer_governs(admin_client, target_repo):
    """Currency is the same `superseded_by_id IS NULL` rule Flow membership
    uses; a Node that gave up its link stops governing."""
    from app import db as db_module
    from app import execution_target as target_module

    _, system, headers, _ = _prepare(admin_client, target_repo)
    node = _govern_component(admin_client, headers, "norm", "norm-node", "fixed")
    link = _link(admin_client, headers, node["id"], "component", "other-component")

    with db_module.get_conn() as conn:
        conn.execute(
            "UPDATE evolution_node_link SET superseded_by_id = ? WHERE id = ?",
            (link["id"], link["id"]),
        )
        mapping = target_module.resolve_execution_target(
            conn,
            system_id=system["id"],
            target_kind="component",
            target_ref="other-component",
        )
    assert mapping.governance == "unmapped"


def test_unknown_target_kind_is_refused(admin_client, target_repo):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    response = admin_client.get(
        "/execution-modes/target-governance",
        params={"target_kind": "capability", "target_ref": "x"},
        headers=headers,
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 2. `governed` + non-permitting mode is REFUSED at every entry point
#    (#413's acceptance criterion / #412's completion criterion)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_replay_run_refused_for_governed_node(admin_client, target_repo, mode):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    _govern_component(admin_client, headers, "norm", "norm-node", mode)

    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    assert detail["source_scope"] == "node"
    # An approved Replay is still refused: approval and mode are two
    # independent facts (§7.4).
    runs = admin_client.get("/replay-runs", headers=headers).json()
    assert runs == []


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_replay_variant_run_refused_for_governed_node(
    admin_client, target_repo, mode
):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(target_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    _govern_component(admin_client, headers, "norm", "norm-node", mode)

    response = admin_client.post(
        "/replay-variant-runs",
        json={
            "replay_set_id": replay_set["id"],
            "variants": [
                {"label": "c", "patch_text": patch, "source": "manual"}
            ],
        },
        headers=headers,
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    assert admin_client.get("/replay-variant-runs", headers=headers).json() == []


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_candidate_replay_refused_for_governed_node(admin_client, target_repo, mode):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = admin_client.post(
        "/candidate-sessions", json={"component_id": "norm", "trace_id": "t1"},
        headers=headers,
    )
    assert session.status_code == 201, session.text
    version = admin_client.post(
        f"/candidate-sessions/{session.json()['id']}/generate",
        json={"instruction": "improve it"}, headers=headers,
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    _approve(admin_client, headers, "norm")
    _govern_component(admin_client, headers, "norm", "norm-node", mode)

    response = admin_client.post(
        f"/candidate-versions/{version_id}/replay", json={}, headers=headers
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    # The refusal must not strand the immutable candidate in 'running'.
    reread = admin_client.get(
        f"/candidate-versions/{version_id}", headers=headers
    ).json()
    assert reread["replay_status"] == "not_run"


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_candidate_generation_refuses_before_llm_client_or_credentials(
    admin_client, target_repo, monkeypatch, mode
):
    """The real legacy Candidate Studio entry point must share the mode gate;
    testing only the adapter would leave its direct client construction open."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",), output="ok")
    session = admin_client.post(
        "/candidate-sessions",
        json={"component_id": "norm", "trace_id": "t1"},
        headers=headers,
    )
    assert session.status_code == 201, session.text
    _govern_component(admin_client, headers, "norm", "norm-node", mode)

    reached = False

    def _credential_trap(_config):
        nonlocal reached
        reached = True
        raise AssertionError("LLM credential/client path must be unreachable")

    monkeypatch.setattr(
        "app.routes.candidate_studio.create_llm_client", _credential_trap
    )
    response = admin_client.post(
        f"/candidate-sessions/{session.json()['id']}/generate",
        json={"instruction": "improve it"},
        headers=headers,
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    assert reached is False


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_candidate_promote_refused_for_governed_node(admin_client, target_repo, mode):
    """The whole candidate flow runs while the Component is still `unmapped`;
    the Node is linked and clamped afterwards. Promotion must then refuse --
    otherwise a Node whose mode fell back to `fixed` could keep exporting
    candidates into the Experiment lane and the hole would simply move one
    step downstream."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    session = admin_client.post(
        "/candidate-sessions", json={"component_id": "norm", "trace_id": "t1"},
        headers=headers,
    ).json()
    version = admin_client.post(
        f"/candidate-sessions/{session['id']}/generate",
        json={"instruction": "improve it"}, headers=headers,
    ).json()
    _approve(admin_client, headers, "norm")
    replayed = admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replay_status"] == "completed"

    _govern_component(admin_client, headers, "norm", "norm-node", mode)

    response = admin_client.post(
        f"/candidate-versions/{version['id']}/promote", headers=headers
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    reread = admin_client.get(
        f"/candidate-versions/{version['id']}", headers=headers
    ).json()
    assert reread["promoted_at"] is None


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_experiment_run_refused_for_governed_feature(admin_client, target_repo, mode):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    created = admin_client.post(
        "/experiments", json=_experiment_payload(snapshot["id"]), headers=headers
    )
    assert created.status_code == 201, created.text
    experiment = created.json()

    node = _create_node(admin_client, headers, "calc-node")
    _link(admin_client, headers, node["id"], "feature", "calculator")
    _assign(admin_client, headers, scope_kind="node", scope_ref="calc-node", mode=mode)

    response = admin_client.post(
        f"/experiments/{experiment['id']}/run", headers=headers
    )
    detail = _denial(response)
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == mode
    # The refusal happens BEFORE the status/variant reset, so the Experiment
    # is left untouched rather than half-reset.
    reread = admin_client.get(
        f"/experiments/{experiment['id']}", headers=headers
    ).json()
    assert reread["status"] == "draft"
    assert reread["started_at"] is None


# ---------------------------------------------------------------------------
# 3. The same target under `shadow` proceeds
# ---------------------------------------------------------------------------


def test_shadow_requires_approved_proposal_then_permits_governed_replay_variant_run(
    admin_client, target_repo
):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(target_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    _govern_component(admin_client, headers, "norm", "norm-node", "fixed")

    refused = admin_client.post(
        "/replay-variant-runs",
        json={"replay_set_id": replay_set["id"],
              "variants": [{"label": "c", "patch_text": patch, "source": "manual"}]},
        headers=headers,
    )
    assert refused.status_code == 409, refused.text

    # A new append-only assignment promotes the SAME Node to `shadow`.
    _assign(admin_client, headers, scope_kind="node", scope_ref="norm-node",
            mode="shadow")

    missing = admin_client.post(
        "/replay-variant-runs",
        json={"replay_set_id": replay_set["id"],
              "variants": [{"label": "c", "patch_text": patch, "source": "manual"}]},
        headers=headers,
    )
    assert _denial(missing)["code"] == "execution_proposal_required"
    wrong_proposal_id = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]),
        "norm-node",
        ["patch_sha256:" + ("0" * 64)],
        captured_snapshot_id=snapshot["id"],
    )
    wrong_candidate = admin_client.post(
        "/replay-variant-runs",
        json={
            "replay_set_id": replay_set["id"],
            "variants": [{"label": "c", "patch_text": patch, "source": "manual"}],
            "flow_experiment_proposal_id": wrong_proposal_id,
        },
        headers=headers,
    )
    assert _denial(wrong_candidate)["code"] == "execution_candidate_not_authorized"
    proposal_id = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]),
        "norm-node",
        [f"patch_sha256:{patch_hash(patch)}"],
        captured_snapshot_id=snapshot["id"],
    )
    allowed = admin_client.post(
        "/replay-variant-runs",
        json={
            "replay_set_id": replay_set["id"],
            "variants": [{"label": "c", "patch_text": patch, "source": "manual"}],
            "flow_experiment_proposal_id": proposal_id,
        },
        headers=headers,
    )
    assert allowed.status_code == 201, allowed.text
    run = allowed.json()
    assert run["status"] == "completed"
    assert allowed.headers[execution_target.GOVERNANCE_HEADER] == "governed"
    assert allowed.headers[execution_target.MODE_HEADER] == "shadow"
    assert allowed.headers[execution_target.NODE_HEADER] == "norm-node"
    candidate = next(v for v in run["variants"] if not v["is_baseline"])
    assert candidate["apply_status"] == "applied"
    assert _recorded_execution_refs(
        int(headers["X-Probe-System-Id"]), proposal_id
    ) == [("replay_variant_run", str(candidate["id"]))]
    shadow = admin_client.post(
        "/components/norm/shadow-results",
        json={
            "trace_id": "shadow-canonical",
            "component_id": "norm",
            "current_output": "baseline",
            "candidate_output": "candidate",
            "candidate_duration_ms": 1.0,
            "timestamp": time.time(),
            "flow_experiment_proposal_id": proposal_id,
            "flow_experiment_candidate_kind": "replay_variant",
            "flow_experiment_candidate_id": candidate["id"],
        },
        headers=headers,
    )
    assert shadow.status_code == 201, shadow.text
    assert _recorded_execution_refs(
        int(headers["X-Probe-System-Id"]), proposal_id
    ) == [
        ("replay_variant_run", str(candidate["id"])),
        ("shadow_result", str(shadow.json()["id"])),
    ]


def test_shadow_permits_the_governed_experiment_run(admin_client, target_repo):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    created = admin_client.post(
        "/experiments", json=_experiment_payload(snapshot["id"]), headers=headers
    )
    assert created.status_code == 201, created.text
    experiment = created.json()
    node = _create_node(admin_client, headers, "calc-node")
    _link(admin_client, headers, node["id"], "feature", "calculator")
    _assign(admin_client, headers, scope_kind="node", scope_ref="calc-node",
            mode="shadow")

    missing = admin_client.post(f"/experiments/{experiment['id']}/run", headers=headers)
    assert _denial(missing)["code"] == "execution_proposal_required"
    other_snapshot = admin_client.post("/repository/snapshots", headers=headers)
    assert other_snapshot.status_code == 201, other_snapshot.text
    wrong_snapshot_proposal = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]),
        "calc-node",
        [
            f"patch_sha256:{variant['patch_hash']}"
            for variant in experiment["variants"]
            if not variant["is_baseline"]
        ],
        captured_snapshot_id=other_snapshot.json()["id"],
    )
    wrong_snapshot = admin_client.post(
        f"/experiments/{experiment['id']}/run",
        params={"flow_experiment_proposal_id": wrong_snapshot_proposal},
        headers=headers,
    )
    assert _denial(wrong_snapshot)["code"] == "execution_snapshot_mismatch"
    proposal_id = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]),
        "calc-node",
        [
            f"patch_sha256:{variant['patch_hash']}"
            for variant in experiment["variants"]
            if not variant["is_baseline"]
        ],
        captured_snapshot_id=snapshot["id"],
    )
    response = admin_client.post(
        f"/experiments/{experiment['id']}/run",
        params={"flow_experiment_proposal_id": proposal_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.headers[execution_target.GOVERNANCE_HEADER] == "governed"
    assert response.headers[execution_target.MODE_HEADER] == "shadow"
    assert _recorded_execution_refs(
        int(headers["X-Probe-System-Id"]), proposal_id
    ) == [("experiment", str(experiment["id"]))]
    divergence = admin_client.get(
        "/execution-modes/divergence", headers=headers
    ).json()["nodes"]
    calc = next(item for item in divergence if item["node_key"] == "calc-node")
    assert calc["divergence"] == "match"
    assert calc["run_ref_state"] == "corroborated"
    rerun = admin_client.post(
        f"/experiments/{experiment['id']}/run",
        params={"flow_experiment_proposal_id": proposal_id},
        headers=headers,
    )
    assert rerun.status_code == 409, rerun.text
    assert rerun.json()["detail"]["code"] == "experiment_execution_immutable"
    assert _recorded_execution_refs(
        int(headers["X-Probe-System-Id"]), proposal_id
    ) == [("experiment", str(experiment["id"]))]


def test_system_scope_shadow_reaches_a_governed_node(admin_client, target_repo):
    """The Node itself has no assignment; the System's `shadow` is inherited
    through the resolver's ordinary scope chain (§3.3 row 10)."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    node = _create_node(admin_client, headers, "norm-node")
    _link(admin_client, headers, node["id"], "component", "norm")
    _assign(admin_client, headers, scope_kind="system", scope_ref="", mode="shadow")
    proposal_id = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]), "norm-node", ["candidate:baseline-replay"]
    )

    response = admin_client.post(
        "/replay-runs",
        json={
            "replay_set_id": replay_set["id"],
            "flow_experiment_proposal_id": proposal_id,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.headers[execution_target.MODE_HEADER] == "shadow"


def test_governed_shadow_rejects_free_text_candidate_and_snapshot_claims(
    admin_client, target_repo
):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    _govern_component(admin_client, headers, "norm", "norm-node", "shadow")
    proposal_id = _approved_flow_proposal(
        int(headers["X-Probe-System-Id"]),
        "norm-node",
        ["patch_sha256:" + ("a" * 64)],
        captured_snapshot_id=snapshot["id"],
    )
    response = admin_client.post(
        "/components/norm/shadow-results",
        json={
            "trace_id": "shadow-claim",
            "component_id": "norm",
            "current_output": "baseline",
            "candidate_output": "unverified candidate",
            "candidate_duration_ms": 1.0,
            "timestamp": time.time(),
            "flow_experiment_proposal_id": proposal_id,
            "flow_experiment_candidate_ref": "patch_sha256:" + ("a" * 64),
            "flow_experiment_snapshot_id": snapshot["id"],
        },
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert (
        response.json()["detail"]["code"]
        == "execution_candidate_attestation_required"
    )


def test_expired_node_assignment_refuses_rather_than_inheriting(
    admin_client, target_repo
):
    """EM-ADR-2, reached through an execution entry point: an elapsed
    experiment window falls to `fixed` instead of letting the System's
    `shadow` take over -- otherwise the deadline would stop nothing."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    node = _create_node(admin_client, headers, "norm-node")
    _link(admin_client, headers, node["id"], "component", "norm")
    _assign(admin_client, headers, scope_kind="system", scope_ref="", mode="shadow")
    past = time.time() - 3600
    _assign(admin_client, headers, scope_kind="node", scope_ref="norm-node",
            mode="shadow", effective_from=past - 60, effective_until=past)

    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    detail = _denial(response)
    assert detail["denial_code"] == "node_expired_assignment"
    assert detail["mode"] == "fixed"


# ---------------------------------------------------------------------------
# 4. `unmapped` behaves exactly as before -- and says so
# ---------------------------------------------------------------------------


def test_unmapped_component_runs_replay_and_variants_unchanged(
    admin_client, target_repo
):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(target_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    # A System-wide `fixed` assignment exists; it must not touch an unmapped
    # target, because no Node maps it and the gate therefore never applies.
    _assign(admin_client, headers, scope_kind="system", scope_ref="", mode="fixed")

    baseline = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert baseline.status_code == 201, baseline.text
    assert baseline.json()["status"] == "completed"
    assert baseline.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"
    # "Not governed" is not "permitted": no mode and no Node are asserted.
    assert execution_target.MODE_HEADER not in baseline.headers
    assert execution_target.NODE_HEADER not in baseline.headers

    variants = admin_client.post(
        "/replay-variant-runs",
        json={"replay_set_id": replay_set["id"],
              "variants": [{"label": "c", "patch_text": patch, "source": "manual"}]},
        headers=headers,
    )
    assert variants.status_code == 201, variants.text
    assert variants.json()["status"] == "completed"
    assert variants.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"


def test_unmapped_candidate_replay_and_promote_unchanged(admin_client, target_repo):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    _assign(admin_client, headers, scope_kind="system", scope_ref="", mode="fixed")
    session = admin_client.post(
        "/candidate-sessions", json={"component_id": "norm", "trace_id": "t1"},
        headers=headers,
    ).json()
    version = admin_client.post(
        f"/candidate-sessions/{session['id']}/generate",
        json={"instruction": "improve it"}, headers=headers,
    ).json()
    _approve(admin_client, headers, "norm")

    replayed = admin_client.post(
        f"/candidate-versions/{version['id']}/replay", json={}, headers=headers
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replay_status"] == "completed"
    assert replayed.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"

    promoted = admin_client.post(
        f"/candidate-versions/{version['id']}/promote", headers=headers
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"


def test_unmapped_experiment_runs_unchanged(admin_client, target_repo):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    _assign(admin_client, headers, scope_kind="system", scope_ref="", mode="fixed")
    created = admin_client.post(
        "/experiments", json=_experiment_payload(snapshot["id"]), headers=headers
    )
    assert created.status_code == 201, created.text
    experiment = created.json()

    response = admin_client.post(
        f"/experiments/{experiment['id']}/run", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"
    assert execution_target.MODE_HEADER not in response.headers


def test_a_node_linked_to_a_different_component_does_not_govern(
    admin_client, target_repo
):
    """A Node exists and is `fixed`, but links some OTHER Component. The
    target stays on the legacy path -- mapping is by the persisted link, not
    by the existence of a Node somewhere in the System."""
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    _govern_component(admin_client, headers, "some-other-component",
                      "other-node", "fixed")

    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"


# ---------------------------------------------------------------------------
# 5. `ambiguous` fails closed at the entry points
# ---------------------------------------------------------------------------


def test_ambiguous_component_refuses_replay_and_variant_runs(
    admin_client, target_repo
):
    _, _, headers, _ = _prepare(admin_client, target_repo)
    _post_trace(admin_client, headers, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers, ["t1"], "norm")
    _approve(admin_client, headers, "norm")
    patch = _make_patch(target_repo, "svc.py",
                        lambda s: s.replace('"kind": "a"', '"kind": "b"'))
    first = _create_node(admin_client, headers, "node-a")
    second = _create_node(admin_client, headers, "node-b")
    _link(admin_client, headers, first["id"], "component", "norm")
    _link(admin_client, headers, second["id"], "component", "norm")
    # Both Nodes are `shadow`: ambiguity refuses regardless of the modes,
    # because the system must not decide WHOSE permission applies.
    _assign(admin_client, headers, scope_kind="node", scope_ref="node-a",
            mode="shadow")
    _assign(admin_client, headers, scope_kind="node", scope_ref="node-b",
            mode="shadow")

    baseline = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers
    )
    detail = _denial(baseline)
    assert detail["denial_code"] == "ambiguous_target_mapping"
    assert "node-a" in detail["message"] and "node-b" in detail["message"]
    # The competing Nodes are NAMED, so the developer is told which links to
    # resolve rather than only that something was ambiguous.
    assert detail["node_keys"] == ["node-a", "node-b"]
    # No mode reading was taken, so none is reported.
    assert detail["mode"] is None
    assert detail["decision"] is None

    variants = admin_client.post(
        "/replay-variant-runs",
        json={"replay_set_id": replay_set["id"],
              "variants": [{"label": "c", "patch_text": patch, "source": "manual"}]},
        headers=headers,
    )
    assert _denial(variants)["denial_code"] == "ambiguous_target_mapping"


def test_ambiguous_feature_refuses_experiment_run(admin_client, target_repo):
    _, _, headers, snapshot = _prepare(admin_client, target_repo)
    created = admin_client.post(
        "/experiments", json=_experiment_payload(snapshot["id"]), headers=headers
    )
    assert created.status_code == 201, created.text
    experiment = created.json()
    for key in ("calc-a", "calc-b"):
        node = _create_node(admin_client, headers, key)
        _link(admin_client, headers, node["id"], "feature", "calculator")

    response = admin_client.post(
        f"/experiments/{experiment['id']}/run", headers=headers
    )
    assert _denial(response)["denial_code"] == "ambiguous_target_mapping"
    reread = admin_client.get(
        f"/experiments/{experiment['id']}", headers=headers
    ).json()
    assert reread["status"] == "draft"


# ---------------------------------------------------------------------------
# 6. System isolation
# ---------------------------------------------------------------------------


def test_another_systems_node_never_governs_this_systems_execution(
    admin_client, target_repo
):
    token, _, headers_a, _ = _prepare(admin_client, target_repo, name="system-a")

    created_b = admin_client.post(
        "/systems", json={"name": "system-b", "environment": "test"},
        headers=_headers(token),
    )
    assert created_b.status_code == 201, created_b.text
    headers_b = _headers(token, created_b.json()["id"])

    # System B links the SAME component_id and clamps it to `fixed`.
    _govern_component(admin_client, headers_b, "norm", "norm-node", "fixed")
    _assign(admin_client, headers_b, scope_kind="system", scope_ref="", mode="fixed")

    # System A's target is untouched by it.
    governance = admin_client.get(
        "/execution-modes/target-governance",
        params={"target_kind": "component", "target_ref": "norm"},
        headers=headers_a,
    ).json()
    assert governance["governance"] == "unmapped"
    assert governance["node_keys"] == []

    _post_trace(admin_client, headers_a, "t1", "norm", args=("hello",),
                output="{'kind': 'a', 'size': 5}")
    replay_set = _create_set(admin_client, headers_a, ["t1"], "norm")
    _approve(admin_client, headers_a, "norm")
    response = admin_client.post(
        "/replay-runs", json={"replay_set_id": replay_set["id"]}, headers=headers_a
    )
    assert response.status_code == 201, response.text
    assert response.headers[execution_target.GOVERNANCE_HEADER] == "unmapped"

    # And System B still sees its own Node.
    governance_b = admin_client.get(
        "/execution-modes/target-governance",
        params={"target_kind": "component", "target_ref": "norm"},
        headers=headers_b,
    ).json()
    assert governance_b["governance"] == "governed"
    assert governance_b["node_key"] == "norm-node"


# ---------------------------------------------------------------------------
# 7. Vocabulary shape
# ---------------------------------------------------------------------------


def test_finite_vocabularies_are_exactly_these():
    assert execution_target.EXECUTION_TARGET_GOVERNANCE_VALUES == (
        "governed", "unmapped", "ambiguous",
    )
    assert execution_target.EXECUTION_TARGET_KINDS == ("component", "feature")
    assert execution_target.EXECUTION_TARGET_DENIAL_CODES == (
        "ambiguous_target_mapping",
    )
    # Every mapped target kind names a real `evolution_node_link.link_kind`.
    from app.evolution_node import LINK_KINDS

    for link_kind in execution_target.TARGET_KIND_LINK_KIND.values():
        assert link_kind in LINK_KINDS
