"""Tests for Issue #59: reasoning-model explanation refresh proposals.

A drifted (#57) source-backed explanation can request a reviewable refresh
proposal. The proposal is a SUGGESTION only: probe-agent never edits the target
repository. Covers the structured-output contract, fail-closed behavior for
mock/non-reasoning models, stale and missing-source anchors, audit persistence,
target-repository immutability, and system/snapshot isolation.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: structured-output contract + fail-closed
# ---------------------------------------------------------------------------


class TestRefreshContract:
    def test_valid_response_parses(self):
        from app.refresh_proposal import parse_refresh_response

        out = parse_refresh_response(json.dumps({
            "proposed_explanation": "Builds and caches the flow graph",
            "proposed_metadata": {"role": "Builds the flow graph",
                                  "capability": "flow", "element_type": "core"},
            "summary_of_changes": "Now caches results; clarify wording.",
            "confidence": 0.8,
        }))
        assert out["proposed_explanation"].startswith("Builds")
        assert out["proposed_metadata"]["element_type"] == "core"
        assert out["confidence"] == 0.8

    def test_unknown_enum_value_is_rejected(self):
        from app.refresh_proposal import (
            RefreshValidationError, parse_refresh_response,
        )

        with pytest.raises(RefreshValidationError):
            parse_refresh_response(json.dumps({
                "proposed_metadata": {"element_type": "totally-made-up"},
                "summary_of_changes": "x",
            }))

    def test_unknown_metadata_key_is_rejected(self):
        from app.refresh_proposal import (
            RefreshValidationError, parse_refresh_response,
        )

        with pytest.raises(RefreshValidationError):
            parse_refresh_response(json.dumps({
                "proposed_metadata": {"made_up_key": "value"},
                "summary_of_changes": "x",
            }))

    def test_empty_proposal_is_rejected(self):
        from app.refresh_proposal import (
            RefreshValidationError, parse_refresh_response,
        )

        with pytest.raises(RefreshValidationError):
            parse_refresh_response(json.dumps({"summary_of_changes": "x"}))

    def test_missing_summary_is_rejected(self):
        from app.refresh_proposal import (
            RefreshValidationError, parse_refresh_response,
        )

        with pytest.raises(RefreshValidationError):
            parse_refresh_response(json.dumps({
                "proposed_explanation": "something",
            }))

    def test_mock_provider_fails_closed(self):
        from app.llm import LLMConfig, MockLLMClient
        from app.refresh_proposal import RefreshContext, propose_refresh

        cfg = LLMConfig(provider="mock", api_key=None, model="mock",
                        base_url=None, timeout=5.0)
        ctx = RefreshContext(
            node_id=1, node_type="element", name="foo", path="m.py",
            qualified_name="foo", drift_status="stale",
            changed_hashes=["symbol"], old_explanation="probe-agent:\n  role: x",
        )
        proposal = propose_refresh(MockLLMClient(), cfg, ctx)
        assert proposal.is_mock is True
        assert proposal.error is not None
        assert proposal.proposed_explanation is None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-refresh-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


class _RefreshClient:
    """A reasoning client that returns a valid refresh proposal."""

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "proposed_explanation": "Builds the flow graph and returns 42 nodes",
            "proposed_metadata": {"role": "Builds the flow graph",
                                  "capability": "flow", "element_type": "core"},
            "summary_of_changes": "The node count changed; clarify the wording.",
            "confidence": 0.77,
        })


class _MalformedClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return "not json at all"


def _enable_reasoning(monkeypatch, client_cls=_RefreshClient):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.project_intelligence.create_llm_client",
        lambda config: client_cls(),
    )


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


FLOW_V1 = (
    'from fastapi import APIRouter\n'
    'router = APIRouter()\n'
    '\n'
    '\n'
    'def build_flow_graph():\n'
    '    """Build the flow graph.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Builds the flow graph\n'
    '      capability: flow\n'
    '      element_type: core\n'
    '      state_effects: [database-read]\n'
    '    """\n'
    '    return {"nodes": 1}\n'
    '\n'
    '\n'
    '@router.get("/flow")\n'
    'def get_flow():\n'
    '    """List flows.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Lists flows\n'
    '      capability: flow\n'
    '      element_type: element\n'
    '    """\n'
    '    return build_flow_graph()\n'
)


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _make_repo(tmp_path, name="repo", flow=FLOW_V1):
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


def _configure(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put("/repository",
               json={"repo_path": str(repo), "include_patterns": ["src/**"]}, headers=h)


def _snapshot_and_index(client, h):
    client.post("/repository/snapshots", headers=h)
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text


def _new_commit_snapshot(client, h, repo, path, content):
    (repo / path).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "change")
    _snapshot_and_index(client, h)


def _setup_hierarchy(client, token, system_id, repo):
    h = _headers(token, system_id)
    _configure(client, token, system_id, repo)
    _snapshot_and_index(client, h)
    r = client.post("/repository/capability-hierarchy/generate", headers=h)
    assert r.status_code == 201, r.text
    return h


def _stale_node_id(client, h, name="build_flow_graph"):
    body = client.get("/repository/capability-hierarchy/drift", headers=h).json()
    for cap in body["capabilities"]:
        for el in cap["elements"]:
            if el["name"] == name and el["status"] in ("stale", "missing_source"):
                return el["node_id"], el["status"]
    raise AssertionError(f"no stale node named {name}: {body}")


class TestRefreshAPI:
    def test_successful_proposal_for_stale_node(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshOK")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        changed = FLOW_V1.replace('{"nodes": 1}', '{"nodes": 42}')
        _new_commit_snapshot(admin_client, h, repo, "src/flow.py", changed)
        node_id, status = _stale_node_id(admin_client, h)
        assert status == "stale"

        _enable_reasoning(monkeypatch)
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=h
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "proposed"
        assert data["review_required"] is True
        assert "source of truth" in data["review_note"]
        p = data["proposal"]
        assert p["drift_status"] == "stale"
        assert "symbol" in p["changed_hashes"]
        assert "role: Builds the flow graph" in p["old_explanation"]
        assert p["proposed_explanation"].startswith("Builds")
        assert p["proposed_metadata"]["element_type"] == "core"
        assert p["summary_of_changes"]
        assert data["intelligence_run"]["decision_method"] == "reasoning_llm"
        assert data["intelligence_run"]["status"] == "completed"

        # The original source-authored explanation is unchanged in the repo.
        assert (repo / "src" / "flow.py").read_text() == changed
        assert "Builds the flow graph and returns 42 nodes" not in (
            repo / "src" / "flow.py"
        ).read_text()

        # The proposal is persisted and listable.
        listing = admin_client.get("/repository/explanation-refresh", headers=h).json()
        assert len(listing["proposals"]) == 1
        assert listing["proposals"][0]["status"] == "proposed"

    def test_refresh_by_entrypoint_logical_key(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshEp")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        # Change get_flow's body so the API entrypoint's handler hash drifts.
        changed = FLOW_V1.replace("return build_flow_graph()",
                                  "return build_flow_graph()  # changed")
        _new_commit_snapshot(admin_client, h, repo, "src/flow.py", changed)

        cards = admin_client.get("/repository/api-role-cards", headers=h).json()["cards"]
        card = next(c for c in cards if c["route_path"] == "/flow")

        _enable_reasoning(monkeypatch)
        r = admin_client.post(
            "/repository/explanation-refresh",
            json={"entrypoint_type": card["entrypoint_type"],
                  "entrypoint_id": card["entrypoint_id"]},
            headers=h,
        )
        assert r.status_code == 201, r.text
        p = r.json()["proposal"]
        assert p["entrypoint_type"] == card["entrypoint_type"]
        assert p["entrypoint_id"] == card["entrypoint_id"]
        assert p["drift_status"] in ("stale", "missing_source")

    def test_missing_source_node_can_be_refreshed(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshMissing")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        renamed = FLOW_V1.replace("build_flow_graph", "build_graph_v2")
        _new_commit_snapshot(admin_client, h, repo, "src/flow.py", renamed)
        node_id, status = _stale_node_id(admin_client, h)
        assert status == "missing_source"

        _enable_reasoning(monkeypatch)
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=h
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "proposed"
        assert data["proposal"]["drift_status"] == "missing_source"
        assert "gone" in data["proposal"]["drift_reason"].lower()

    def test_reasoning_failure_is_visible(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshFail")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        _new_commit_snapshot(admin_client, h, repo, "src/flow.py",
                             FLOW_V1.replace('{"nodes": 1}', '{"nodes": 7}'))
        node_id, _ = _stale_node_id(admin_client, h)

        _enable_reasoning(monkeypatch, client_cls=_MalformedClient)
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=h
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert data["error"]
        assert data["intelligence_run"]["status"] == "failed"
        assert data["proposal"]["proposed_explanation"] is None

    def test_mock_provider_fails_closed_via_api(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshMock")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        _new_commit_snapshot(admin_client, h, repo, "src/flow.py",
                             FLOW_V1.replace('{"nodes": 1}', '{"nodes": 7}'))
        node_id, _ = _stale_node_id(admin_client, h)

        # LLM_PROVIDER stays mock -> reasoning required, fails closed.
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=h
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert data["proposal"]["is_mock"] is True
        assert "reasoning model" in (data["error"] or "").lower()

    def test_fresh_node_returns_409(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshFresh")
        repo = _make_repo(tmp_path)
        h = _setup_hierarchy(admin_client, token, system["id"], repo)

        node_id, _status = None, None
        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        node_id = body["capabilities"][0]["elements"][0]["node_id"]

        _enable_reasoning(monkeypatch)
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=h
        )
        assert r.status_code == 409
        assert "not stale" in r.json()["detail"].lower()

    def test_no_hierarchy_returns_400(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RefreshNoHier")
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": 1},
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 400

    def test_refresh_is_system_scoped(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "RefreshA")
        sys_b = _create_system(admin_client, token, "RefreshB")
        repo_a = _make_repo(tmp_path, name="a")
        repo_b = _make_repo(tmp_path, name="b")
        ha = _setup_hierarchy(admin_client, token, sys_a["id"], repo_a)
        hb = _setup_hierarchy(admin_client, token, sys_b["id"], repo_b)

        _new_commit_snapshot(admin_client, ha, repo_a, "src/flow.py",
                             FLOW_V1.replace('{"nodes": 1}', '{"nodes": 5}'))
        node_id, _ = _stale_node_id(admin_client, ha)

        _enable_reasoning(monkeypatch)
        # System B must not be able to refresh System A's node.
        r = admin_client.post(
            "/repository/explanation-refresh", json={"node_id": node_id}, headers=hb
        )
        assert r.status_code == 404

        # System B has its own (fresh) hierarchy and no proposals leak across.
        listing_b = admin_client.get(
            "/repository/explanation-refresh", headers=hb
        ).json()
        assert listing_b["proposals"] == []
