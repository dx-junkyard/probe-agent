"""Tests for Issue #55: source-hash provenance.

Covers deterministic symbol/file/explanation hashes computed from pinned
snapshots: hash change on implementation edits, stability for unrelated and
comment/docstring-only edits, explanation anchors, committed-snapshot-only
reads, and system/snapshot isolation.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: deterministic hashing in the indexer layer
# ---------------------------------------------------------------------------


class TestSymbolHashing:
    def _hashes(self, source):
        from app.code_indexer import index_python_file_full

        result = index_python_file_full("src/m.py", source)
        return {s.qualified_name: s for s in result.symbols}

    def test_symbols_carry_source_and_body_hashes(self):
        syms = self._hashes(
            "def foo(x):\n    return x + 1\n\nclass C:\n    pass\n"
        )
        foo = syms["foo"]
        assert foo.symbol_source_hash and len(foo.symbol_source_hash) == 64
        assert foo.symbol_body_hash and len(foo.symbol_body_hash) == 64
        assert syms["C"].symbol_source_hash is not None
        assert syms["src.m"].symbol_body_hash is not None

    def test_body_change_changes_both_hashes(self):
        base = self._hashes("def foo(x):\n    return x + 1\n\ndef bar():\n    return 2\n")
        changed = self._hashes("def foo(x):\n    return x + 99\n\ndef bar():\n    return 2\n")
        assert base["foo"].symbol_source_hash != changed["foo"].symbol_source_hash
        assert base["foo"].symbol_body_hash != changed["foo"].symbol_body_hash
        # Unrelated symbol stays stable.
        assert base["bar"].symbol_source_hash == changed["bar"].symbol_source_hash
        assert base["bar"].symbol_body_hash == changed["bar"].symbol_body_hash

    def test_comment_only_change_keeps_body_hash_stable(self):
        base = self._hashes("def foo(x):\n    return x + 1\n")
        commented = self._hashes("def foo(x):\n    # explain\n    return x + 1\n")
        # Body hash ignores comments; source-span hash reflects the edit.
        assert base["foo"].symbol_body_hash == commented["foo"].symbol_body_hash
        assert base["foo"].symbol_source_hash != commented["foo"].symbol_source_hash

    def test_docstring_only_change_keeps_body_hash_stable(self):
        base = self._hashes('def foo():\n    """One."""\n    return 1\n')
        redoc = self._hashes('def foo():\n    """Two, different."""\n    return 1\n')
        assert base["foo"].symbol_body_hash == redoc["foo"].symbol_body_hash
        assert base["foo"].symbol_source_hash != redoc["foo"].symbol_source_hash

    def test_hashing_is_deterministic(self):
        src = "def foo(x):\n    return x + 1\n"
        assert (
            self._hashes(src)["foo"].symbol_source_hash
            == self._hashes(src)["foo"].symbol_source_hash
        )

    def test_decorator_change_changes_source_hash(self):
        a = self._hashes(
            'from fastapi import APIRouter\n'
            'router = APIRouter()\n'
            '\n'
            '@router.get("/a")\n'
            'def foo():\n'
            '    return 1\n'
        )
        b = self._hashes(
            'from fastapi import APIRouter\n'
            'router = APIRouter()\n'
            '\n'
            '@router.get("/b")\n'
            'def foo():\n'
            '    return 1\n'
        )
        # Editing the route decorator is part of the externally observable role
        # and must change the exact source-span hash.
        assert a["foo"].symbol_source_hash != b["foo"].symbol_source_hash
        # The def line is unchanged; start_line stays on the def for display.
        assert a["foo"].start_line == b["foo"].start_line

    def test_comment_around_decorator_changes_source_hash_only(self):
        a = self._hashes(
            '@property\n'
            'def foo(self):\n'
            '    return 1\n'
        )
        commented = self._hashes(
            '@property\n'
            '# explain the decorator\n'
            'def foo(self):\n'
            '    return 1\n'
        )
        assert a["foo"].symbol_source_hash != commented["foo"].symbol_source_hash
        assert a["foo"].symbol_body_hash == commented["foo"].symbol_body_hash

    def test_explanation_hash_present_and_sensitive(self):
        base = self._hashes(
            'def foo():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      capability: alpha\n'
            '    """\n'
            '    return 1\n'
        )
        changed = self._hashes(
            'def foo():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      capability: beta\n'
            '    """\n'
            '    return 1\n'
        )
        assert base["foo"].source_metadata.explanation_hash is not None
        assert (
            base["foo"].source_metadata.explanation_hash
            != changed["foo"].source_metadata.explanation_hash
        )


# ---------------------------------------------------------------------------
# API + persistence + isolation tests
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-hash-test.db"))
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
    '      role: API endpoint for flow graph construction\n'
    '      capability: execution-flow-understanding\n'
    '      element_type: core\n'
    '    """\n'
    '    return {"nodes": 1}\n'
)
UTIL_PY = 'def helper():\n    return 42\n'


def _git_init(repo):
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "T"],
        check=True, capture_output=True,
    )


def _git_commit(repo, msg):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", msg], check=True, capture_output=True
    )


def _make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    _git_init(repo)
    (repo / "src").mkdir()
    (repo / "src" / "flow.py").write_text(FLOW_PY)
    (repo / "src" / "util.py").write_text(UTIL_PY)
    _git_commit(repo, "init")
    return repo


def _configure_and_index(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["src/**"]},
        headers=h,
    )
    client.post("/repository/snapshots", headers=h)
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _by_name(body, name):
    return next(s for s in body["symbols"] if s["qualified_name"] == name)


class TestSymbolHashAPI:
    def test_index_exposes_all_hash_types(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "HashSys")
        repo = _make_repo(tmp_path)
        body = _configure_and_index(admin_client, token, system["id"], repo)

        flow = _by_name(body, "build_flow_graph")
        assert flow["file_content_hash"] and len(flow["file_content_hash"]) == 64
        assert flow["symbol_source_hash"] and len(flow["symbol_source_hash"]) == 64
        assert flow["symbol_body_hash"] and len(flow["symbol_body_hash"]) == 64
        assert flow["source_metadata"]["explanation_hash"] is not None

        # Two symbols in the same file share the file hash but differ in symbol hash.
        flow_file = flow["file_content_hash"]
        helper = _by_name(body, "helper")
        assert helper["file_content_hash"] != flow_file  # different files
        assert helper["symbol_source_hash"] != flow["symbol_source_hash"]

    def test_hash_changes_on_impl_change_stable_for_unrelated(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "HashChange")
        repo = _make_repo(tmp_path)
        h = _headers(token, system["id"])
        first = _configure_and_index(admin_client, token, system["id"], repo)
        flow1 = _by_name(first, "build_flow_graph")
        helper1 = _by_name(first, "helper")

        # Change only build_flow_graph's implementation, commit, re-snapshot.
        (repo / "src" / "flow.py").write_text(FLOW_PY.replace('{"nodes": 1}', '{"nodes": 2}'))
        _git_commit(repo, "change flow impl")
        admin_client.post("/repository/snapshots", headers=h)
        second = admin_client.post("/repository/symbols/index", headers=h).json()
        flow2 = _by_name(second, "build_flow_graph")
        helper2 = _by_name(second, "helper")

        assert flow2["symbol_source_hash"] != flow1["symbol_source_hash"]
        assert flow2["symbol_body_hash"] != flow1["symbol_body_hash"]
        assert flow2["file_content_hash"] != flow1["file_content_hash"]
        # The untouched file/symbol keeps identical hashes.
        assert helper2["symbol_source_hash"] == helper1["symbol_source_hash"]
        assert helper2["symbol_body_hash"] == helper1["symbol_body_hash"]
        assert helper2["file_content_hash"] == helper1["file_content_hash"]

    def test_hashes_read_committed_snapshot_only(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "HashCommitted")
        repo = _make_repo(tmp_path)
        h = _headers(token, system["id"])
        first = _configure_and_index(admin_client, token, system["id"], repo)
        flow1 = _by_name(first, "build_flow_graph")

        # Uncommitted working-tree edit must not influence a new snapshot.
        (repo / "src" / "flow.py").write_text(
            FLOW_PY.replace('{"nodes": 1}', '{"nodes": 999}')
        )
        admin_client.post("/repository/snapshots", headers=h)
        second = admin_client.post("/repository/symbols/index", headers=h).json()
        flow2 = _by_name(second, "build_flow_graph")
        assert flow2["symbol_source_hash"] == flow1["symbol_source_hash"]
        assert flow2["file_content_hash"] == flow1["file_content_hash"]

    def test_hashes_are_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "HashA")
        sys_b = _create_system(admin_client, token, "HashB")
        repo_a = _make_repo(tmp_path, name="repo-a")
        repo_b = _make_repo(tmp_path, name="repo-b")
        # Make B's implementation differ so hashes must differ.
        (repo_b / "src" / "flow.py").write_text(
            FLOW_PY.replace('{"nodes": 1}', '{"nodes": 7}')
        )
        _git_commit(repo_b, "vary b")

        body_a = _configure_and_index(admin_client, token, sys_a["id"], repo_a)
        body_b = _configure_and_index(admin_client, token, sys_b["id"], repo_b)
        flow_a = _by_name(body_a, "build_flow_graph")
        flow_b = _by_name(body_b, "build_flow_graph")
        assert flow_a["symbol_source_hash"] != flow_b["symbol_source_hash"]


class TestExplanationAnchorsAPI:
    def test_anchors_bundle_all_hashes(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "AnchorSys")
        repo = _make_repo(tmp_path)
        h = _headers(token, system["id"])
        _configure_and_index(admin_client, token, system["id"], repo)

        r = admin_client.get("/repository/explanation-anchors", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["anchor_count"] == 1
        anchor = body["anchors"][0]
        assert anchor["qualified_name"] == "build_flow_graph"
        assert anchor["path"] == "src/flow.py"
        assert anchor["file_content_hash"] is not None
        assert anchor["symbol_source_hash"] is not None
        assert anchor["symbol_body_hash"] is not None
        assert anchor["explanation_hash"] is not None

    def test_anchors_empty_without_snapshot(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "AnchorEmpty")
        r = admin_client.get(
            "/repository/explanation-anchors", headers=_headers(token, system["id"])
        )
        assert r.status_code == 200
        assert r.json()["anchor_count"] == 0

    def test_anchors_are_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "AnchorA")
        sys_b = _create_system(admin_client, token, "AnchorB")
        repo_a = _make_repo(tmp_path, name="a")
        repo_b = tmp_path / "b"
        _git_init(repo_b)
        (repo_b / "src").mkdir()
        (repo_b / "src" / "plain.py").write_text("def x():\n    return 1\n")
        _git_commit(repo_b, "init")

        _configure_and_index(admin_client, token, sys_a["id"], repo_a)
        _configure_and_index(admin_client, token, sys_b["id"], repo_b)

        r_b = admin_client.get(
            "/repository/explanation-anchors", headers=_headers(token, sys_b["id"])
        )
        assert r_b.json()["anchor_count"] == 0


def _degrade_to_pre_55_index(snapshot_id):
    """Simulate a snapshot indexed before #55: clear hashes/metadata/anchors
    and reset the run schema version so the upgrade gate fires."""
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "UPDATE code_symbols SET symbol_source_hash = NULL, "
            "symbol_body_hash = NULL WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        conn.execute(
            "DELETE FROM symbol_source_metadata WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        conn.execute(
            "DELETE FROM explanation_source_anchors WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        conn.execute(
            "DELETE FROM symbol_index_warnings WHERE snapshot_id = ? "
            "AND message LIKE '%probe-agent metadata:%'",
            (snapshot_id,),
        )
        conn.execute(
            "UPDATE intelligence_runs SET schema_version = 'legacy' "
            "WHERE snapshot_id = ? AND run_type = 'symbol_index'",
            (snapshot_id,),
        )


class TestReadPathUpgrade:
    def test_get_symbols_upgrades_stale_index(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReadUpgradeSym")
        repo = _make_repo(tmp_path)
        h = _headers(token, system["id"])
        body = _configure_and_index(admin_client, token, system["id"], repo)
        _degrade_to_pre_55_index(body["snapshot_id"])

        # A plain GET (what the Dashboard uses) must surface hashes without an
        # explicit re-index.
        r = admin_client.get("/repository/symbols", headers=h)
        assert r.status_code == 200
        out = r.json()
        flow = _by_name(out, "build_flow_graph")
        assert flow["symbol_source_hash"] is not None
        assert flow["symbol_body_hash"] is not None
        assert flow["file_content_hash"] is not None
        assert flow["source_metadata"] is not None
        assert flow["source_metadata"]["explanation_hash"] is not None
        assert out["intelligence_run"]["schema_version"] == "provenance-v1"

    def test_get_anchors_upgrades_stale_index(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReadUpgradeAnc")
        repo = _make_repo(tmp_path)
        h = _headers(token, system["id"])
        body = _configure_and_index(admin_client, token, system["id"], repo)
        _degrade_to_pre_55_index(body["snapshot_id"])

        r = admin_client.get("/repository/explanation-anchors", headers=h)
        assert r.status_code == 200
        out = r.json()
        assert out["anchor_count"] == 1
        anchor = out["anchors"][0]
        assert anchor["qualified_name"] == "build_flow_graph"
        assert anchor["symbol_source_hash"] is not None
        assert anchor["explanation_hash"] is not None
