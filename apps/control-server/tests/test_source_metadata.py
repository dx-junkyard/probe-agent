"""Tests for Issue #54: source-anchored explanation metadata.

Covers deterministic extraction of the ``probe-agent:`` docstring block from
module / class / function docstrings, malformed/unknown/missing handling,
source-line provenance, typed API exposure, persistence, and system/snapshot
isolation.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: deterministic extraction in the indexer layer
# ---------------------------------------------------------------------------


VALID_FUNCTION_SOURCE = (
    'def build_flow_graph():\n'
    '    """\n'
    '    Build a candidate execution flow from a backend entrypoint.\n'
    '\n'
    '    probe-agent:\n'
    '      role: API endpoint for deterministic flow graph construction\n'
    '      capability: execution-flow-understanding\n'
    '      element_type: core\n'
    '      consumers: [dashboard]\n'
    '      operation_kind: analysis\n'
    '      state_effects: [database-read]\n'
    '      probe_value: Validate graph shape and unresolved edges.\n'
    '    """\n'
    '    return None\n'
)


class TestSourceMetadataExtraction:
    def test_extracts_valid_function_metadata(self):
        from app.code_indexer import index_python_file_full

        result = index_python_file_full("src/flow.py", VALID_FUNCTION_SOURCE)
        assert result.warnings == []
        sym = next(s for s in result.symbols if s.qualified_name == "build_flow_graph")
        meta = sym.source_metadata
        assert meta is not None
        assert meta.role == "API endpoint for deterministic flow graph construction"
        assert meta.capability == "execution-flow-understanding"
        assert meta.element_type == "core"
        assert meta.operation_kind == "analysis"
        assert meta.consumers == ["dashboard"]
        assert meta.state_effects == ["database-read"]
        assert meta.probe_value == "Validate graph shape and unresolved edges."
        assert meta.origin == "source_authored"

    def test_source_line_range_points_at_block(self):
        from app.code_indexer import index_python_file_full

        result = index_python_file_full("src/flow.py", VALID_FUNCTION_SOURCE)
        sym = next(s for s in result.symbols if s.qualified_name == "build_flow_graph")
        meta = sym.source_metadata
        # The ``probe-agent:`` marker is on source line 5 in VALID_FUNCTION_SOURCE.
        assert meta.start_line == 5
        # Block ends on the probe_value line (line 12).
        assert meta.end_line == 12
        assert "probe-agent:" in VALID_FUNCTION_SOURCE.split("\n")[meta.start_line - 1]

    def test_extracts_module_and_class_metadata(self):
        from app.code_indexer import index_python_file_full

        source = (
            '"""Top module.\n'
            '\n'
            'probe-agent:\n'
            '  system_purpose: Coordinate flow analysis across the system.\n'
            '  element_type: system\n'
            '"""\n'
            '\n'
            '\n'
            'class FlowService:\n'
            '    """A service.\n'
            '\n'
            '    probe-agent:\n'
            '      capability: execution-flow-understanding\n'
            '      element_type: supporting\n'
            '    """\n'
            '    pass\n'
        )
        result = index_python_file_full("src/svc.py", source)
        assert result.warnings == []
        module_sym = next(s for s in result.symbols if s.kind == "module")
        assert module_sym.source_metadata.system_purpose == (
            "Coordinate flow analysis across the system."
        )
        assert module_sym.source_metadata.element_type == "system"
        class_sym = next(s for s in result.symbols if s.qualified_name == "FlowService")
        assert class_sym.source_metadata.capability == "execution-flow-understanding"
        assert class_sym.source_metadata.element_type == "supporting"

    def test_missing_metadata_yields_none_without_warning(self):
        from app.code_indexer import index_python_file_full

        source = (
            'def plain():\n'
            '    """Just a normal docstring without any block."""\n'
            '    return 1\n'
            '\n'
            'def nodoc():\n'
            '    return 2\n'
        )
        result = index_python_file_full("src/plain.py", source)
        assert result.warnings == []
        for sym in result.symbols:
            assert sym.source_metadata is None

    def test_malformed_yaml_produces_warning_and_no_metadata(self):
        from app.code_indexer import index_python_file_full

        source = (
            'def broken_meta():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      role: "unterminated\n'
            '      capability: x\n'
            '    """\n'
            '    return None\n'
            '\n'
            'def healthy():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      capability: ok\n'
            '    """\n'
            '    return None\n'
        )
        result = index_python_file_full("src/m.py", source)
        broken = next(s for s in result.symbols if s.qualified_name == "broken_meta")
        assert broken.source_metadata is None
        healthy = next(s for s in result.symbols if s.qualified_name == "healthy")
        assert healthy.source_metadata is not None
        assert healthy.source_metadata.capability == "ok"
        # A deterministic warning was emitted for the malformed block only.
        assert any("broken_meta" in w.message for w in result.warnings)
        assert all("healthy" not in w.message for w in result.warnings)

    def test_unknown_key_and_bad_enum_warn_but_keep_valid_fields(self):
        from app.code_indexer import index_python_file_full

        source = (
            'def partial():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      role: valid role text\n'
            '      element_type: not-a-real-type\n'
            '      mystery_key: something\n'
            '      state_effects: [database-read, teleport]\n'
            '    """\n'
            '    return None\n'
        )
        result = index_python_file_full("src/p.py", source)
        sym = next(s for s in result.symbols if s.qualified_name == "partial")
        meta = sym.source_metadata
        assert meta is not None
        assert meta.role == "valid role text"
        # Invalid enum value dropped.
        assert meta.element_type is None
        # Invalid enum-list value dropped (whole field rejected).
        assert meta.state_effects == []
        messages = " ".join(w.message for w in result.warnings)
        assert "element_type" in messages
        assert "mystery_key" in messages
        assert "state_effects" in messages

    def test_no_recognized_fields_warns_and_drops_metadata(self):
        from app.code_indexer import index_python_file_full

        source = (
            'def junk():\n'
            '    """Doc.\n'
            '\n'
            '    probe-agent:\n'
            '      totally_unknown: value\n'
            '    """\n'
            '    return None\n'
        )
        result = index_python_file_full("src/j.py", source)
        sym = next(s for s in result.symbols if s.qualified_name == "junk")
        assert sym.source_metadata is None
        assert any("no recognized fields" in w.message for w in result.warnings)

    def test_legacy_wrapper_still_returns_symbols(self):
        from app.code_indexer import index_python_file

        symbols, imports, warn = index_python_file("src/flow.py", VALID_FUNCTION_SOURCE)
        assert warn is None
        sym = next(s for s in symbols if s.qualified_name == "build_flow_graph")
        assert sym.source_metadata is not None


# ---------------------------------------------------------------------------
# API + persistence + isolation tests
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-meta-test.db"))
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


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
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


def _git_repo(tmp_path, name="repo"):
    repo = tmp_path / name
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
    (repo / "src").mkdir()
    (repo / "src" / "flow.py").write_text(VALID_FUNCTION_SOURCE)
    (repo / "src" / "bad.py").write_text(
        'def bad_meta():\n'
        '    """Doc.\n'
        '\n'
        '    probe-agent:\n'
        '      element_type: nonsense\n'
        '    """\n'
        '    return None\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


def _index(client, token, system_id, repo_path):
    h = _headers(token, system_id)
    client.put(
        "/repository",
        json={"repo_path": str(repo_path), "include_patterns": ["src/**"]},
        headers=h,
    )
    client.post("/repository/snapshots", headers=h)
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text
    return r.json()


class TestSourceMetadataAPI:
    def test_index_exposes_typed_source_metadata(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MetaSys")
        repo = _git_repo(tmp_path)
        body = _index(admin_client, token, system["id"], repo)

        sym = next(s for s in body["symbols"] if s["qualified_name"] == "build_flow_graph")
        meta = sym["source_metadata"]
        assert meta is not None
        assert meta["role"] == "API endpoint for deterministic flow graph construction"
        assert meta["element_type"] == "core"
        assert meta["consumers"] == ["dashboard"]
        assert meta["state_effects"] == ["database-read"]
        assert meta["origin"] == "source_authored"
        assert meta["start_line"] >= 1 and meta["end_line"] >= meta["start_line"]
        # The intelligence run stays deterministic.
        assert body["intelligence_run"]["decision_method"] == "deterministic"

    def test_malformed_metadata_warns_without_failing_index(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MetaWarn")
        repo = _git_repo(tmp_path)
        body = _index(admin_client, token, system["id"], repo)

        assert body["symbol_count"] > 0
        assert body["warning_count"] > 0
        warn_paths = [w["path"] for w in body["warnings"]]
        assert "src/bad.py" in warn_paths
        bad_sym = next(s for s in body["symbols"] if s["qualified_name"] == "bad_meta")
        assert bad_sym["source_metadata"] is None

    def test_get_symbols_returns_persisted_metadata(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MetaGet")
        repo = _git_repo(tmp_path)
        _index(admin_client, token, system["id"], repo)

        r = admin_client.get(
            "/repository/symbols", headers=_headers(token, system["id"])
        )
        assert r.status_code == 200
        sym = next(
            s for s in r.json()["symbols"] if s["qualified_name"] == "build_flow_graph"
        )
        assert sym["source_metadata"]["capability"] == "execution-flow-understanding"

    def test_backfill_upgrades_pre_54_index(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token, "MetaBackfill")
        repo = _git_repo(tmp_path)
        body = _index(admin_client, token, system["id"], repo)
        snapshot_id = body["snapshot_id"]
        flow_sym = next(
            s for s in body["symbols"] if s["qualified_name"] == "build_flow_graph"
        )
        original_symbol_id = flow_sym["id"]

        # Simulate a snapshot indexed before #54: symbols exist, but no source
        # metadata and the run predates the metadata schema version.
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM symbol_source_metadata WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            conn.execute(
                "DELETE FROM symbol_index_warnings WHERE snapshot_id = ? "
                "AND message LIKE '%probe-agent metadata:%'",
                (snapshot_id,),
            )
            conn.execute(
                """
                UPDATE intelligence_runs SET schema_version = 'legacy'
                WHERE snapshot_id = ? AND run_type = 'symbol_index'
                """,
                (snapshot_id,),
            )

        h = _headers(token, system["id"])
        # Re-running index triggers a deterministic, additive backfill.
        r2 = admin_client.post("/repository/symbols/index", headers=h)
        assert r2.status_code == 201
        body2 = r2.json()
        flow2 = next(
            s for s in body2["symbols"] if s["qualified_name"] == "build_flow_graph"
        )
        # Symbols are preserved (same ids), metadata is now populated.
        assert flow2["id"] == original_symbol_id
        assert flow2["source_metadata"] is not None
        assert flow2["source_metadata"]["element_type"] == "core"
        assert body2["intelligence_run"]["schema_version"] == "provenance-v1"
        warn_paths = [w["path"] for w in body2["warnings"]]
        assert "src/bad.py" in warn_paths

        # Idempotent: a third call does not duplicate metadata or warnings.
        with get_conn() as conn:
            meta_count = conn.execute(
                "SELECT COUNT(*) AS c FROM symbol_source_metadata WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()["c"]
            warn_count = conn.execute(
                "SELECT COUNT(*) AS c FROM symbol_index_warnings WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()["c"]
        r3 = admin_client.post("/repository/symbols/index", headers=h)
        assert r3.status_code == 201
        with get_conn() as conn:
            assert meta_count == conn.execute(
                "SELECT COUNT(*) AS c FROM symbol_source_metadata WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()["c"]
            assert warn_count == conn.execute(
                "SELECT COUNT(*) AS c FROM symbol_index_warnings WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()["c"]

    def test_metadata_is_system_scoped(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "MetaA")
        sys_b = _create_system(admin_client, token, "MetaB")
        repo_a = _git_repo(tmp_path, name="repo-a")
        repo_b = tmp_path / "repo-b"
        repo_b.mkdir()
        subprocess.run(["git", "init", str(repo_b)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_b), "config", "user.email", "t@t.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_b), "config", "user.name", "T"],
            check=True, capture_output=True,
        )
        (repo_b / "src").mkdir()
        (repo_b / "src" / "other.py").write_text(
            'def other():\n    """No metadata here."""\n    return 1\n'
        )
        subprocess.run(["git", "-C", str(repo_b), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_b), "commit", "-m", "init"],
            check=True, capture_output=True,
        )

        _index(admin_client, token, sys_a["id"], repo_a)
        _index(admin_client, token, sys_b["id"], repo_b)

        r_b = admin_client.get(
            "/repository/symbols", headers=_headers(token, sys_b["id"])
        )
        qnames = {s["qualified_name"] for s in r_b.json()["symbols"]}
        assert "build_flow_graph" not in qnames
        for s in r_b.json()["symbols"]:
            assert s["source_metadata"] is None
