"""Tests for Issue #175: capability context aggregation (gaps / probe plans /
experiments) surfaced through GET /repository/capabilities/{capability_key}/context.

Every item returned must be joined by an exact key match (capability_key or
feature_id) — never a guess (CLAUDE.md Principle 6).
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-context-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


FLOW_PY = (
    'def build_flow_graph():\n'
    '    """Build a flow graph.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Builds the deterministic flow graph\n'
    '      capability: execution-flow-understanding\n'
    '      element_type: core\n'
    '    """\n'
    '    return {}\n'
)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "flow.py").write_text(FLOW_PY)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


def _index(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put("/repository",
               json={"repo_path": str(repo), "include_patterns": ["src/**"]}, headers=h)
    client.post("/repository/snapshots", headers=h)
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text


def _wire_feature_and_hierarchy(admin_client, token, system, repo, feature_id):
    """Index the repo, accept a Feature-to-Code link for build_flow_graph, and
    generate the capability hierarchy so capability_hierarchy_nodes carries both
    capability_key and feature_id on the same row (Issue #24 + #56 wiring, the
    exact-key join #175 relies on)."""
    from app.db import get_conn

    h = _headers(token, system["id"])
    _index(admin_client, token, system["id"], repo)
    symbols = admin_client.get("/repository/symbols", headers=h).json()
    snapshot_id = symbols["snapshot_id"]
    flow_sym = next(s for s in symbols["symbols"] if s["qualified_name"] == "build_flow_graph")

    now = 1.0
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at, completed_at)
            VALUES (?, ?, 'feature_code_mapping', 'mock', 'mock', 'v1', 'v1',
                    'reasoning_llm', 'completed', 1, ?, ?)
            """,
            (system["id"], snapshot_id, now, now),
        )
        run_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO feature_code_links
                (system_id, snapshot_id, intelligence_run_id, feature_id,
                 symbol_id, relation_reason, confidence, source,
                 review_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'implements flow', 0.9,
                    'reasoning_llm', 'accepted', ?, ?)
            """,
            (system["id"], snapshot_id, run_id, feature_id, flow_sym["id"], now, now),
        )

    body = admin_client.post(
        "/repository/capability-hierarchy/generate", headers=h
    ).json()
    assert body["capabilities"], body
    return body["capabilities"][0]["capability_key"]


def _latest_snapshot_id(system_id):
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM repository_snapshots WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        return row["id"]


def _insert_snapshot(system_id, commit_sha="newer-unready", status="indexing"):
    from app.db import get_conn

    now = 9.0
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at)
            VALUES (?, '/tmp/repo', ?, ?, ?)
            """,
            (system_id, commit_sha, status, now),
        )
        return cur.lastrowid


def _insert_probe_plan(system_id, feature_id, status="proposed"):
    from app.db import get_conn

    now = 2.0
    snapshot_id = _latest_snapshot_id(system_id)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at, completed_at)
            VALUES (?, ?, 'probe_plan', 'mock', 'mock', 'v1', 'v1',
                    'reasoning_llm', 'completed', 1, ?, ?)
            """,
            (system_id, snapshot_id, now, now),
        )
        run_id = cur.lastrowid
        cur = conn.execute(
            """
            INSERT INTO probe_plans
                (system_id, snapshot_id, intelligence_run_id, feature_id,
                 objective, status, origin, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'observe latency', ?, 'manual', ?, ?)
            """,
            (system_id, snapshot_id, run_id, feature_id, status, now, now),
        )
        return cur.lastrowid


def _insert_experiment(system_id, feature_id, human_decision="undecided"):
    from app.db import get_conn

    now = 3.0
    snapshot_id = _latest_snapshot_id(system_id)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO experiments
                (system_id, feature_id, objective, snapshot_id, baseline_commit,
                 config_revision, execution_config, status, human_decision,
                 created_at)
            VALUES (?, ?, 'compare candidate', ?, 'deadbeef', 'rev-1', '{}',
                    'completed', ?, ?)
            """,
            (system_id, feature_id, snapshot_id, human_decision, now),
        )
        return cur.lastrowid


class TestCapabilityContextAPI:
    def test_empty_when_no_hierarchy(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CtxEmpty")
        h = _headers(token, system["id"])

        r = admin_client.get(
            "/repository/capabilities/nonexistent-capability/context", headers=h
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["capability_key"] == "nonexistent-capability"
        assert body["gaps"] == []
        assert body["probe_plans"] == []
        assert body["experiments"] == []

    def test_returns_plan_and_experiment_by_exact_feature_id_join(
        self, admin_client, tmp_path
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CtxJoin")
        repo = _make_repo(tmp_path)
        capability_key = _wire_feature_and_hierarchy(
            admin_client, token, system, repo, "flow-feature"
        )

        plan_id = _insert_probe_plan(system["id"], "flow-feature", status="approved")
        exp_id = _insert_experiment(system["id"], "flow-feature", human_decision="adopted")

        # An unrelated feature/plan/experiment must never leak in.
        _insert_probe_plan(system["id"], "unrelated-feature")
        _insert_experiment(system["id"], "unrelated-feature")

        h = _headers(token, system["id"])
        body = admin_client.get(
            f"/repository/capabilities/{capability_key}/context", headers=h
        ).json()

        assert [p["id"] for p in body["probe_plans"]] == [plan_id]
        assert body["probe_plans"][0]["feature_id"] == "flow-feature"
        assert body["probe_plans"][0]["status"] == "approved"

        assert [e["id"] for e in body["experiments"]] == [exp_id]
        assert body["experiments"][0]["feature_id"] == "flow-feature"
        assert body["experiments"][0]["human_decision"] == "adopted"

    def test_uses_latest_ready_snapshot_when_newer_snapshot_is_not_ready(
        self, admin_client, tmp_path
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CtxReadyRule")
        repo = _make_repo(tmp_path)
        capability_key = _wire_feature_and_hierarchy(
            admin_client, token, system, repo, "flow-feature"
        )
        ready_snapshot_id = _latest_snapshot_id(system["id"])
        plan_id = _insert_probe_plan(system["id"], "flow-feature", status="approved")
        exp_id = _insert_experiment(system["id"], "flow-feature", human_decision="adopted")

        newer_snapshot_id = _insert_snapshot(
            system["id"], commit_sha="newer-indexing", status="indexing"
        )
        assert newer_snapshot_id > ready_snapshot_id

        h = _headers(token, system["id"])
        body = admin_client.get(
            f"/repository/capabilities/{capability_key}/context", headers=h
        ).json()

        assert [p["id"] for p in body["probe_plans"]] == [plan_id]
        assert [e["id"] for e in body["experiments"]] == [exp_id]

    def test_no_probe_plan_without_matching_feature_id(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CtxNoMatch")
        repo = _make_repo(tmp_path)
        capability_key = _wire_feature_and_hierarchy(
            admin_client, token, system, repo, "flow-feature"
        )

        _insert_probe_plan(system["id"], "some-other-feature")
        _insert_experiment(system["id"], "some-other-feature")

        h = _headers(token, system["id"])
        body = admin_client.get(
            f"/repository/capabilities/{capability_key}/context", headers=h
        ).json()
        assert body["probe_plans"] == []
        assert body["experiments"] == []

    def test_gaps_filtered_by_capability_key_reuse_system_understanding(
        self, admin_client, monkeypatch
    ):
        # Gaps reuse the existing System Understanding gap representation
        # (CLAUDE.md: "no new gap representation"); monkeypatch the service to
        # avoid re-deriving a full docs-code reconciler graph in this test.
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CtxGaps")

        from app.system_understanding_service import SystemUnderstandingSummary

        def _fake_get_system_understanding(system_id):
            return SystemUnderstandingSummary(
                system_id=system_id,
                gaps=[
                    {"gap_type": "undocumented", "capability_key": "cap-a",
                     "title": "gap in cap-a"},
                    {"gap_type": "undocumented", "capability_key": "cap-b",
                     "title": "gap in cap-b"},
                    {"gap_type": "undocumented", "capability_key": None,
                     "title": "gap with no capability"},
                ],
            )

        monkeypatch.setattr(
            "app.system_understanding_service.get_system_understanding",
            _fake_get_system_understanding,
        )

        h = _headers(token, system["id"])
        body = admin_client.get(
            "/repository/capabilities/cap-a/context", headers=h
        ).json()
        assert [g["title"] for g in body["gaps"]] == ["gap in cap-a"]

    def test_context_is_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "CtxSysA")
        sys_b = _create_system(admin_client, token, "CtxSysB")
        repo_a = _make_repo(tmp_path)
        capability_key = _wire_feature_and_hierarchy(
            admin_client, token, sys_a, repo_a, "flow-feature"
        )
        _insert_probe_plan(sys_a["id"], "flow-feature")

        h_b = _headers(token, sys_b["id"])
        body = admin_client.get(
            f"/repository/capabilities/{capability_key}/context", headers=h_b
        ).json()
        assert body["probe_plans"] == []
