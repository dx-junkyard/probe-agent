"""Tests for Issue #58: API role-card endpoint (Flow Explorer context).

Covers classified/unclassified cards, provenance, drift reflection,
LLM-scan-without-handler review flagging, empty states, and system/snapshot
isolation. The endpoint consumes #56 hierarchy and #57 drift; it invents no new
semantics.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-card-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
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
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


FLOW = (
    'from fastapi import APIRouter\n'
    'router = APIRouter()\n'
    '\n'
    '\n'
    'def build_flow_graph():\n'
    '    """Build.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Builds a deterministic candidate execution graph\n'
    '      capability: execution-flow-understanding\n'
    '      element_type: core\n'
    '      operation_kind: analysis\n'
    '      consumers: [dashboard]\n'
    '      state_effects: [database-read]\n'
    '      probe_value: validate graph shape\n'
    '    """\n'
    '    return {}\n'
    '\n'
    '\n'
    '@router.get("/flow")\n'
    'def get_flow():\n'
    '    """List.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Lists flows\n'
    '      capability: execution-flow-understanding\n'
    '      element_type: element\n'
    '      state_effects: [database-read]\n'
    '    """\n'
    '    return build_flow_graph()\n'
    '\n'
    '\n'
    '@router.get("/unrelated")\n'
    'def get_unrelated():\n'
    '    return 1\n'
)


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _make_repo(tmp_path, name="repo", flow=FLOW):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "src").mkdir()
    (repo / "src" / "flow.py").write_text(flow)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v1")
    return repo


def _setup(client, token, system_id, repo, generate=True):
    h = _headers(token, system_id)
    client.put("/repository",
               json={"repo_path": str(repo), "include_patterns": ["src/**"]}, headers=h)
    client.post("/repository/snapshots", headers=h)
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text
    if generate:
        client.post("/repository/capability-hierarchy/generate", headers=h)
    return h


def _card(body, label):
    return next(c for c in body["cards"] if c["label"] == label)


class TestApiRoleCards:
    def test_classified_card_has_full_context(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardClassified")
        repo = _make_repo(tmp_path)
        h = _setup(admin_client, token, system["id"], repo)

        body = admin_client.get("/repository/api-role-cards", headers=h).json()
        flow = _card(body, "GET /flow")
        assert flow["classification"] == "classified"
        assert flow["capability_key"] == "execution-flow-understanding"
        assert flow["element_type"] == "element"
        assert flow["role"] == "Lists flows"
        assert "source_authored" in flow["provenance_kinds"]
        assert "structural" in flow["provenance_kinds"]
        assert flow["handler_resolved"] is True
        assert "database" in flow["boundaries"]
        # flows_through lists sibling capability elements.
        assert "build_flow_graph" in flow["flows_through"]
        # Drift available and fresh against the same indexed snapshot.
        assert body["drift_available"] is True
        assert flow["drift_status"] == "fresh"

    def test_unclassified_card_empty_state(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardUnclass")
        repo = _make_repo(tmp_path)
        h = _setup(admin_client, token, system["id"], repo)

        body = admin_client.get("/repository/api-role-cards", headers=h).json()
        unrel = _card(body, "GET /unrelated")
        assert unrel["classification"] == "unclassified"
        assert unrel["capability_key"] is None
        assert unrel["role"] is None
        assert unrel["provenance_kinds"] == ["structural"]
        # Graph behavior is still supported (handler resolved).
        assert unrel["handler_resolved"] is True
        assert unrel["review_needed"] is False

    def test_drift_reflected_after_source_change(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardDrift")
        repo = _make_repo(tmp_path)
        h = _setup(admin_client, token, system["id"], repo)

        # Change build_flow_graph implementation; new snapshot + index.
        (repo / "src" / "flow.py").write_text(FLOW.replace("return {}", "return {'n': 1}"))
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/symbols/index", headers=h)

        body = admin_client.get("/repository/api-role-cards", headers=h).json()
        flow = _card(body, "GET /flow")
        # The capability's source changed -> capability-level drift is not fresh.
        assert flow["drift_status"] in ("stale", "partially_stale")
        assert flow["drift_changed_anchors"] >= 1
        assert flow["drift_total_anchors"] >= flow["drift_changed_anchors"]

    def test_llm_scan_without_handler_is_review_needed(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardLLM")
        repo = _make_repo(tmp_path)
        h = _setup(admin_client, token, system["id"], repo)

        body = admin_client.get("/repository/api-role-cards", headers=h).json()
        snapshot_id = body["snapshot_id"]
        # Insert an LLM-scan-derived entrypoint with no resolved handler.
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO code_entrypoints
                    (system_id, snapshot_id, entrypoint_type, entrypoint_id,
                     category, label, operation, framework, handler_symbol_id,
                     handler_path, handler_qualified_name, line_start, line_end,
                     route_method, route_path, confidence, evidence_json, source,
                     created_at)
                VALUES (?, ?, 'http_route', 'GET:/scanned', 'api', 'GET /scanned',
                        'GET /scanned', 'django', NULL, 'urls.py', '', 1, 1,
                        'GET', '/scanned', 0.6, '[]', 'reasoning_llm', 1.0)
                """,
                (system["id"], snapshot_id),
            )

        body = admin_client.get("/repository/api-role-cards", headers=h).json()
        scanned = _card(body, "GET /scanned")
        assert scanned["source"] == "reasoning_llm"
        assert scanned["handler_resolved"] is False
        assert scanned["review_needed"] is True
        assert "handler" in (scanned["review_reason"] or "").lower()

    def test_empty_without_snapshot(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "CardEmpty")
        body = admin_client.get(
            "/repository/api-role-cards", headers=_headers(token, system["id"])
        ).json()
        assert body["cards"] == []
        assert body["drift_available"] is False

    def test_cards_are_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "CardA")
        sys_b = _create_system(admin_client, token, "CardB")
        repo_a = _make_repo(tmp_path, name="a")
        repo_b = _make_repo(
            tmp_path, name="b",
            flow="def plain():\n    return 1\n",
        )
        _setup(admin_client, token, sys_a["id"], repo_a)
        _setup(admin_client, token, sys_b["id"], repo_b)

        body_b = admin_client.get(
            "/repository/api-role-cards", headers=_headers(token, sys_b["id"])
        ).json()
        labels = {c["label"] for c in body_b["cards"]}
        assert "GET /flow" not in labels
