"""Issue #98: deterministic dogfooding smoke test for the core navigation path.

Drives the documented System Understanding -> Capability Map -> Flow Explorer
flow end-to-end at the API level against a fixture repository:

    snapshot -> symbols/index -> system-understanding/build
    -> capability-hierarchy -> capability-hierarchy/drift -> api-role-cards
    -> flow-entrypoints -> flow-graphs -> probe-plans/from-flow

Runs with LLM_PROVIDER=mock only; every asserted step is deterministic.
This chain is exactly the path that broke when ``intelligence_runs.status``
was written as ``'success'`` instead of the ``pending/completed/failed``
contract, so the hierarchy/drift/role-cards responses are asserted at the
status-contract level, not just for HTTP 200.
"""

import subprocess
import textwrap
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-smoke-test.db"))
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


SMOKE_APP_PY = textwrap.dedent('''\
    from fastapi import APIRouter

    router = APIRouter()


    @router.post("/documents/analyze")
    async def analyze_document(req):
        """Analyze an uploaded document.

        probe-agent:
          role: Analyzes uploaded documents
          capability: document-analysis
          element_type: element
        """
        blocks = parse_blocks(req)
        return extract_components(blocks)


    def parse_blocks(req):
        """Parse a request into blocks.

        probe-agent:
          role: Parses raw requests into blocks
          capability: document-analysis
          element_type: core
        """
        return req


    def extract_components(blocks):
        return [b for b in blocks]
''')

SMOKE_README = (
    "# Smoke Project\n\nAnalyzes documents.\n\n"
    "## Document analysis\n\nDocuments are analyzed via POST /documents/analyze.\n"
)


def _make_repo(tmp_path):
    repo = tmp_path / "smoke-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (repo / "app.py").write_text(SMOKE_APP_PY)
    (repo / "README.md").write_text(SMOKE_README)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


def _wait_job(client, hdrs, job_id, timeout=30.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        r = client.get(
            f"/repository/system-understanding/jobs/{job_id}", headers=hdrs
        )
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("completed", "partial", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    pytest.fail(f"Job {job_id} did not settle within {timeout}s: {job}")


def test_core_navigation_path_smoke(admin_client, tmp_path):
    token = _login(admin_client)
    r = admin_client.post(
        "/systems",
        json={"name": "smoke-sys", "environment": "test", "description": "smoke"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    h = _headers(token, r.json()["id"])
    repo = _make_repo(tmp_path)

    # snapshot -> symbol index
    r = admin_client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["**"]},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    r = admin_client.post("/repository/snapshots", headers=h)
    assert r.status_code == 201, r.text
    r = admin_client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text

    # system understanding build (mock LLM; must settle without failing)
    r = admin_client.post("/repository/system-understanding/build", headers=h)
    assert r.status_code == 202, r.text
    job = _wait_job(admin_client, h, r.json()["id"])
    assert job["status"] in ("completed", "partial"), job
    for step in job["steps"]:
        assert step["status"] in (
            "completed", "partial", "skipped", "blocked", "failed"
        ), step
        # No step may fail outright on the deterministic mock path; reasoning
        # steps that cannot run must be blocked (fail closed), not failed.
        assert step["status"] != "failed", step

    # capability hierarchy + drift + role cards (the endpoints that returned
    # 500 when the intelligence_runs status contract was violated)
    r = admin_client.get("/repository/capability-hierarchy", headers=h)
    assert r.status_code == 200, r.text
    hierarchy = r.json()
    run = hierarchy["intelligence_run"]
    assert run is not None
    assert run["status"] in ("pending", "completed", "failed")
    assert run["status"] == "completed"
    cap_keys = {c["capability_key"] for c in hierarchy["capabilities"]}
    assert "document-analysis" in cap_keys

    r = admin_client.get("/repository/capability-hierarchy/drift", headers=h)
    assert r.status_code == 200, r.text

    r = admin_client.get("/repository/api-role-cards", headers=h)
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    analyze = [c for c in cards if c["label"] == "POST /documents/analyze"]
    assert analyze and analyze[0]["classification"] == "classified"

    # flow explorer path
    r = admin_client.get("/repository/flow-entrypoints", headers=h)
    assert r.status_code == 200, r.text
    ep_ids = {e["entrypoint_id"] for e in r.json()["entrypoints"]}
    assert "POST:/documents/analyze" in ep_ids

    r = admin_client.post(
        "/repository/flow-graphs",
        json={"entrypoint_type": "http_route",
              "entrypoint_id": "POST:/documents/analyze"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    graph = r.json()
    nodes = {n["qualified_name"]: n for n in graph["nodes"]}
    assert "parse_blocks" in nodes

    # manual probe plan from flow selection (deterministic, no LLM)
    r = admin_client.post(
        "/repository/probe-plans/from-flow",
        json={
            "entrypoint_type": "http_route",
            "entrypoint_id": "POST:/documents/analyze",
            "objective": "Observe block parsing.",
            "selections": [
                {"node_id": nodes["parse_blocks"]["node_id"],
                 "observation": "output", "mode_preference": "trace"},
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["status"] == "proposed"
    assert plan["intelligence_run"]["decision_method"] == "manual"
    assert plan["is_mock"] is False
