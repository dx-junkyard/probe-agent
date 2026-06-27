"""Tests for Issue #57: deterministic explanation-drift detection.

Covers anchor-level and aggregate drift computation, and the API across two
snapshots: unchanged source, changed function body, changed docstring metadata
only, deleted/renamed symbols, unrelated changes, and system/snapshot isolation.
Hash drift is a review trigger, not a correctness verdict.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: deterministic drift service
# ---------------------------------------------------------------------------


def _facts(files=None, symbols=None):
    from app.drift import SnapshotFacts

    return SnapshotFacts(
        file_hash_by_path=files or {},
        symbol_by_key=symbols or {},
    )


def _anchor(**kw):
    from app.drift import NodeAnchor

    base = dict(node_id=1, node_type="element", name="n", path="src/m.py",
                qualified_name="foo")
    base.update(kw)
    return NodeAnchor(**base)


class TestDriftService:
    def test_fresh_when_all_hashes_match(self):
        from app.drift import compute_anchor_drift, FRESH

        facts = _facts({"src/m.py": "F1"}, {("src/m.py", "foo"): ("S1", "E1")})
        a = _anchor(file_content_hash="F1", symbol_source_hash="S1",
                    explanation_hash="E1")
        d = compute_anchor_drift(a, facts)
        assert d.status == FRESH
        assert d.changed_hashes == []

    def test_stale_on_symbol_change(self):
        from app.drift import compute_anchor_drift, STALE

        facts = _facts({"src/m.py": "F1"}, {("src/m.py", "foo"): ("S2", "E1")})
        a = _anchor(file_content_hash="F1", symbol_source_hash="S1",
                    explanation_hash="E1")
        d = compute_anchor_drift(a, facts)
        assert d.status == STALE
        assert d.changed_hashes == ["symbol"]
        assert d.current_symbol_source_hash == "S2"

    def test_missing_source_when_file_gone(self):
        from app.drift import compute_anchor_drift, MISSING_SOURCE

        facts = _facts({}, {})
        a = _anchor(file_content_hash="F1", symbol_source_hash="S1")
        d = compute_anchor_drift(a, facts)
        assert d.status == MISSING_SOURCE
        assert "file" in d.changed_hashes and "symbol" in d.changed_hashes

    def test_missing_source_when_symbol_renamed(self):
        from app.drift import compute_anchor_drift, MISSING_SOURCE

        # File present, but the symbol qualified name is gone.
        facts = _facts({"src/m.py": "F1"}, {("src/m.py", "other"): ("S9", None)})
        a = _anchor(file_content_hash="F1", symbol_source_hash="S1")
        d = compute_anchor_drift(a, facts)
        assert d.status == MISSING_SOURCE
        assert "symbol" in d.changed_hashes

    def test_unknown_without_captured_hashes(self):
        from app.drift import compute_anchor_drift, UNKNOWN

        a = _anchor(file_content_hash=None, symbol_source_hash=None,
                    explanation_hash=None)
        d = compute_anchor_drift(a, _facts())
        assert d.status == UNKNOWN

    def test_aggregate_partially_stale_and_ratios(self):
        from app.drift import (
            AnchorDrift, aggregate_drift, PARTIALLY_STALE, FRESH, STALE,
        )

        fresh = AnchorDrift(1, "element", "a", "p1", "a", None, FRESH, [],
                            captured_symbol_source_hash="x")
        stale = AnchorDrift(2, "element", "b", "p2", "b", None, STALE,
                            ["symbol"], captured_symbol_source_hash="y")
        status, counts = aggregate_drift([fresh, stale])
        assert status == PARTIALLY_STALE
        assert counts.fresh == 1 and counts.stale == 1
        assert counts.symbol_deps_total == 2 and counts.symbol_deps_changed == 1
        assert counts.mismatch_ratio == 0.5

    def test_aggregate_all_unknown_is_unknown(self):
        from app.drift import AnchorDrift, aggregate_drift, UNKNOWN

        nodes = [AnchorDrift(1, "purpose", "p", None, None, None, UNKNOWN, [])]
        status, counts = aggregate_drift(nodes)
        assert status == UNKNOWN
        assert counts.mismatch_ratio == 0.0

    def test_file_dependencies_counted_distinct(self):
        from app.drift import AnchorDrift, aggregate_drift

        # Two anchors in the same changed file count as one file dependency.
        a = AnchorDrift(1, "element", "a", "src/m.py", "a", None, "stale",
                        ["file"], captured_file_content_hash="f")
        b = AnchorDrift(2, "element", "b", "src/m.py", "b", None, "stale",
                        ["file"], captured_file_content_hash="f")
        _, counts = aggregate_drift([a, b])
        assert counts.file_deps_total == 1
        assert counts.file_deps_changed == 1


# ---------------------------------------------------------------------------
# API tests across two snapshots
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-drift-test.db"))
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


def _make_repo(tmp_path, name="repo", flow=FLOW_V1, other="def util():\n    return 0\n"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "src").mkdir()
    (repo / "src" / "flow.py").write_text(flow)
    (repo / "src" / "other.py").write_text(other)
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


class TestDriftAPI:
    def _setup_hierarchy(self, client, token, system_id, repo):
        h = _headers(token, system_id)
        _configure(client, token, system_id, repo)
        _snapshot_and_index(client, h)
        r = client.post("/repository/capability-hierarchy/generate", headers=h)
        assert r.status_code == 201, r.text
        return h

    def test_no_change_is_fresh(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftFresh")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        assert body["status"] == "fresh"
        assert body["base_snapshot_id"] == body["target_snapshot_id"]
        assert body["counts"]["mismatch_ratio"] == 0.0
        assert body["is_review_recommended"] is False
        assert body["review_note"] is None

    def test_changed_function_body_is_stale(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftBody")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        _new_commit_snapshot(admin_client, h, repo, "src/flow.py",
                             FLOW_V1.replace('{"nodes": 1}', '{"nodes": 42}'))
        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        assert body["status"] == "stale"
        assert body["base_snapshot_id"] != body["target_snapshot_id"]
        assert body["is_review_recommended"] is True
        assert "review trigger" in body["review_note"]
        cap = body["capabilities"][0]
        build = next(e for e in cap["elements"] if e["name"] == "build_flow_graph")
        assert build["status"] == "stale"
        assert "symbol" in build["changed_hashes"]
        assert cap["counts"]["symbol_deps_changed"] >= 1

    def test_changed_docstring_metadata_only_is_stale(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftDoc")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        # Only the docstring metadata changes: role text differs.
        changed = FLOW_V1.replace("role: Builds the flow graph",
                                  "role: Builds and caches the flow graph")
        _new_commit_snapshot(admin_client, h, repo, "src/flow.py", changed)
        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        assert body["status"] in ("stale", "partially_stale")
        cap = body["capabilities"][0]
        build = next(e for e in cap["elements"] if e["name"] == "build_flow_graph")
        # The explanation block hash changed -> explanation review needed.
        assert "explanation" in build["changed_hashes"]
        assert cap["counts"]["explanation_blocks_changed"] >= 1

    def test_deleted_or_renamed_symbol_is_missing_source(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftRename")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        renamed = FLOW_V1.replace("build_flow_graph", "build_graph_v2")
        _new_commit_snapshot(admin_client, h, repo, "src/flow.py", renamed)
        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        cap = body["capabilities"][0]
        build = next(
            e for e in cap["elements"] if e["qualified_name"] == "build_flow_graph"
        )
        assert build["status"] == "missing_source"
        assert cap["counts"]["missing_anchors"] >= 1
        assert body["is_review_recommended"] is True

    def test_unrelated_change_keeps_capability_fresh(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftUnrelated")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        # Change only an unrelated file the flow capability does not depend on.
        _new_commit_snapshot(admin_client, h, repo, "src/other.py",
                             "def util():\n    return 999\n")
        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        cap = next(c for c in body["capabilities"] if c["capability_key"] == "flow")
        assert cap["status"] == "fresh"

    def test_default_target_skips_unindexed_snapshot(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftUnindexed")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        # Create a newer snapshot but do NOT symbol-index it.
        (repo / "src" / "flow.py").write_text(
            FLOW_V1.replace('{"nodes": 1}', '{"nodes": 9}')
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2 unindexed")
        snap = admin_client.post("/repository/snapshots", headers=h).json()
        assert snap["status"] == "ready"

        body = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=h
        ).json()
        # The default target must be the indexed base, not the un-indexed newer
        # snapshot, so there is no false-positive missing_source.
        assert body["target_snapshot_id"] == body["base_snapshot_id"]
        assert body["target_indexed"] is True
        assert body["status"] == "fresh"
        assert body["is_review_recommended"] is False
        assert body["counts"]["missing"] == 0

    def test_explicit_unindexed_target_returns_409(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftUnindexed409")
        repo = _make_repo(tmp_path)
        h = self._setup_hierarchy(admin_client, token, system["id"], repo)

        (repo / "src" / "flow.py").write_text(
            FLOW_V1.replace('{"nodes": 1}', '{"nodes": 9}')
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2 unindexed")
        snap = admin_client.post("/repository/snapshots", headers=h).json()

        r = admin_client.get(
            f"/repository/capability-hierarchy/drift?target_snapshot_id={snap['id']}",
            headers=h,
        )
        assert r.status_code == 409
        assert "symbol index" in r.json()["detail"].lower()

    def test_no_hierarchy_returns_400(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftNone")
        r = admin_client.get(
            "/repository/capability-hierarchy/drift",
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 400

    def test_drift_is_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "DriftA")
        sys_b = _create_system(admin_client, token, "DriftB")
        repo_a = _make_repo(tmp_path, name="a")
        repo_b = _make_repo(tmp_path, name="b")
        ha = self._setup_hierarchy(admin_client, token, sys_a["id"], repo_a)
        hb = self._setup_hierarchy(admin_client, token, sys_b["id"], repo_b)

        # Change A's source only; B must remain fresh and not see A's snapshots.
        _new_commit_snapshot(admin_client, ha, repo_a, "src/flow.py",
                             FLOW_V1.replace('{"nodes": 1}', '{"nodes": 5}'))
        drift_a = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=ha
        ).json()
        drift_b = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=hb
        ).json()
        assert drift_a["status"] == "stale"
        assert drift_b["status"] == "fresh"
        assert drift_a["target_snapshot_id"] != drift_b["target_snapshot_id"]
