"""Tests for Issue #24: Feature-to-Code Mapping MVP.

Covers: Python AST symbol extraction, syntax error handling, symbol
idempotency, Feature-to-Code link generation, review status, system
scoping, repository boundary enforcement, and audit persistence.
"""

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-fcm-test.db"))
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


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


class _ReasoningMappingClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps({
            "links": [{
                "feature_id": "source-implementation",
                "symbol_qualified_name": "list_users",
                "symbol_path": "src/main.py",
                "relation_reason": "The route implements source-backed user listing.",
                "confidence": 0.9,
            }]
        })


def _enable_reasoning_mapping(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.project_intelligence.create_llm_client",
        lambda config: _ReasoningMappingClient(),
    )


@pytest.fixture
def git_repo(tmp_path):
    """Create a git repo with Python files for AST testing."""
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    (repo / "README.md").write_text("# Test Project\n\nA test project.\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text(
        'from pydantic import BaseModel\n'
        'from fastapi import APIRouter\n'
        '\n'
        'router = APIRouter()\n'
        '\n'
        '\n'
        'class UserRequest(BaseModel):\n'
        '    """User request model."""\n'
        '    name: str\n'
        '    email: str\n'
        '\n'
        '\n'
        '@router.get("/users")\n'
        'def list_users():\n'
        '    """List all users."""\n'
        '    return []\n'
        '\n'
        '\n'
        '@router.post("/users")\n'
        'async def create_user(req: UserRequest):\n'
        '    """Create a new user."""\n'
        '    return {"name": req.name}\n'
        '\n'
        '\n'
        'def helper():\n'
        '    pass\n'
    )
    (repo / "src" / "probed.py").write_text(
        'from probe_agent import probe\n'
        '\n'
        '\n'
        '@probe(component_id="summarizer")\n'
        'def summarize(text: str) -> str:\n'
        '    return text[:80]\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text(
        'def test_list_users():\n'
        '    assert True\n'
        '\n'
        '\n'
        'def test_create_user():\n'
        '    assert True\n'
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("# Design\n\nFeature overview.\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


@pytest.fixture
def git_repo_with_syntax_error(git_repo):
    """A repo that also has a file with a syntax error."""
    (git_repo / "src" / "broken.py").write_text(
        "def broken(\n    return None\n"
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "add broken file"],
        check=True, capture_output=True,
    )
    return git_repo


def _setup_snapshot_and_drafts(client, token, system_id, repo_path):
    """Helper: configure repo, create snapshot, generate drafts."""
    h = _headers(token, system_id)
    client.put(
        "/repository",
        json={
            "repo_path": str(repo_path),
            "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
        },
        headers=h,
    )
    client.post("/repository/snapshots", headers=h)
    client.post("/repository/drafts/generate", headers=h)
    return h


# ---------------------------------------------------------------------------
# AST unit tests
# ---------------------------------------------------------------------------


class TestCodeIndexer:
    def test_extracts_functions_classes_and_async(self):
        from app.code_indexer import index_python_file

        source = (
            'class MyClass:\n'
            '    """A class."""\n'
            '    def method(self):\n'
            '        pass\n'
            '\n'
            'def regular():\n'
            '    pass\n'
            '\n'
            'async def async_func():\n'
            '    pass\n'
        )
        symbols, imports, warn = index_python_file("src/example.py", source)
        assert warn is None
        names = {s.qualified_name for s in symbols}
        assert "src.example" in names
        assert "MyClass" in names
        assert "MyClass.method" in names
        assert "regular" in names
        assert "async_func" in names
        async_sym = next(s for s in symbols if s.qualified_name == "async_func")
        assert async_sym.kind == "async_function"

    def test_extracts_decorators(self):
        from app.code_indexer import index_python_file

        source = (
            'from probe_agent import probe\n'
            '\n'
            '@probe(component_id="test")\n'
            'def probed_func():\n'
            '    pass\n'
        )
        symbols, imports, warn = index_python_file("src/probed.py", source)
        assert warn is None
        func = next(s for s in symbols if s.qualified_name == "probed_func")
        assert any("probe" in d for d in func.decorators)
        assert func.component_id == "test"

    def test_detects_pydantic_models(self):
        from app.code_indexer import index_python_file

        source = (
            'from pydantic import BaseModel\n'
            '\n'
            'class UserModel(BaseModel):\n'
            '    name: str\n'
        )
        symbols, _, _ = index_python_file("src/models.py", source)
        user_model = next(s for s in symbols if s.qualified_name == "UserModel")
        assert user_model.is_pydantic_model is True
        assert user_model.kind == "class"

    def test_detects_route_paths(self):
        from app.code_indexer import index_python_file

        source = (
            'from fastapi import APIRouter\n'
            'router = APIRouter()\n'
            '\n'
            '@router.get("/items")\n'
            'def list_items():\n'
            '    return []\n'
            '\n'
            '@router.post("/items")\n'
            'async def create_item():\n'
            '    return {}\n'
        )
        symbols, _, _ = index_python_file("src/routes.py", source)
        get_func = next(s for s in symbols if s.qualified_name == "list_items")
        assert get_func.route_path == "/items"
        assert get_func.route_method == "GET"
        post_func = next(s for s in symbols if s.qualified_name == "create_item")
        assert post_func.route_path == "/items"
        assert post_func.route_method == "POST"

    def test_detects_test_functions(self):
        from app.code_indexer import index_python_file

        source = (
            'def test_something():\n'
            '    assert True\n'
            '\n'
            'def helper_util():\n'
            '    pass\n'
            '\n'
            'def integration_test():\n'
            '    pass\n'
        )
        symbols, _, _ = index_python_file("tests/test_example.py", source)
        test_func = next(s for s in symbols if s.qualified_name == "test_something")
        assert test_func.is_test is True
        helper = next(s for s in symbols if s.qualified_name == "helper_util")
        assert helper.is_test is False
        suffix_test = next(s for s in symbols if s.qualified_name == "integration_test")
        assert suffix_test.is_test is True

    def test_extracts_docstrings(self):
        from app.code_indexer import index_python_file

        source = (
            'def documented():\n'
            '    """This function has a docstring."""\n'
            '    pass\n'
            '\n'
            'def undocumented():\n'
            '    pass\n'
        )
        symbols, _, _ = index_python_file("src/mod.py", source)
        doc_func = next(s for s in symbols if s.qualified_name == "documented")
        assert doc_func.docstring == "This function has a docstring."
        undoc_func = next(s for s in symbols if s.qualified_name == "undocumented")
        assert undoc_func.docstring is None

    def test_syntax_error_returns_warning(self):
        from app.code_indexer import index_python_file

        source = "def broken(\n    return None\n"
        symbols, imports, warn = index_python_file("src/broken.py", source)
        assert symbols == []
        assert warn is not None
        assert "SyntaxError" in warn.message
        assert warn.path == "src/broken.py"

    def test_line_ranges(self):
        from app.code_indexer import index_python_file

        source = (
            'def first():\n'
            '    pass\n'
            '\n'
            'def second():\n'
            '    x = 1\n'
            '    return x\n'
        )
        symbols, _, _ = index_python_file("src/lines.py", source)
        first = next(s for s in symbols if s.qualified_name == "first")
        assert first.start_line == 1
        assert first.end_line == 2
        second = next(s for s in symbols if s.qualified_name == "second")
        assert second.start_line == 4
        assert second.end_line == 6

    def test_imports_extracted(self):
        from app.code_indexer import index_python_file

        source = (
            'import os\n'
            'from pathlib import Path\n'
            'from typing import List, Optional\n'
        )
        _, imports, _ = index_python_file("src/imports.py", source)
        modules = [i.module for i in imports]
        assert "os" in modules
        assert "pathlib" in modules
        assert "typing" in modules

    def test_skips_non_python_files(self):
        from app.code_indexer import index_snapshot_files

        files = [
            ("README.md", b"# Hello\n"),
            ("src/main.py", b"def hello():\n    pass\n"),
            ("config.yaml", b"key: value\n"),
        ]
        result = index_snapshot_files(files)
        assert len(result.symbols) == 2
        assert {s.qualified_name for s in result.symbols} == {"src.main", "hello"}
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Symbol index API tests
# ---------------------------------------------------------------------------


class TestSymbolIndexAPI:
    def test_index_requires_snapshot(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoSnap")
        r = admin_client.post(
            "/repository/symbols/index",
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 400
        assert "not ready" in r.json()["detail"].lower()

    def test_index_extracts_symbols(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SymbolIndex")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["src/**", "tests/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        r = admin_client.post("/repository/symbols/index", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["symbol_count"] > 0
        assert body["warning_count"] == 0

        names = {s["qualified_name"] for s in body["symbols"]}
        assert "src.main" in names
        assert "list_users" in names
        assert "create_user" in names
        assert "UserRequest" in names
        assert "helper" in names
        assert "summarize" in names
        assert "test_list_users" in names

        user_req = next(s for s in body["symbols"] if s["qualified_name"] == "UserRequest")
        assert user_req["is_pydantic_model"] is True
        assert user_req["kind"] == "class"

        list_users = next(s for s in body["symbols"] if s["qualified_name"] == "list_users")
        assert list_users["route_path"] == "/users"
        assert list_users["route_method"] == "GET"

        create_user = next(s for s in body["symbols"] if s["qualified_name"] == "create_user")
        assert create_user["kind"] == "async_function"
        assert create_user["route_path"] == "/users"
        assert create_user["route_method"] == "POST"

        test_func = next(s for s in body["symbols"] if s["qualified_name"] == "test_list_users")
        assert test_func["is_test"] is True
        summarize = next(s for s in body["symbols"] if s["qualified_name"] == "summarize")
        assert summarize["component_id"] == "summarizer"
        main_module = next(s for s in body["symbols"] if s["qualified_name"] == "src.main")
        assert any(item.startswith("pydantic:") for item in main_module["imports"])

        assert body["intelligence_run"] is not None
        assert body["intelligence_run"]["decision_method"] == "deterministic"
        assert body["intelligence_run"]["run_type"] == "symbol_index"

    def test_reindex_same_commit_is_idempotent(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Idempotent")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        r1 = admin_client.post("/repository/symbols/index", headers=h)
        assert r1.status_code == 201
        count1 = r1.json()["symbol_count"]

        r2 = admin_client.post("/repository/symbols/index", headers=h)
        assert r2.status_code == 201
        assert r2.json()["symbol_count"] == count1

    def test_syntax_error_produces_warning(self, admin_client, git_repo_with_syntax_error):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SyntaxWarn")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo_with_syntax_error),
                "include_patterns": ["src/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        r = admin_client.post("/repository/symbols/index", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["symbol_count"] > 0
        assert body["warning_count"] > 0
        warning_paths = [w["path"] for w in body["warnings"]]
        assert "src/broken.py" in warning_paths

    def test_get_symbols(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GetSymbols")
        h = _headers(token, system["id"])

        r = admin_client.get("/repository/symbols", headers=h)
        assert r.status_code == 200
        assert r.json()["symbol_count"] == 0

        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/symbols/index", headers=h)

        r = admin_client.get("/repository/symbols", headers=h)
        assert r.status_code == 200
        assert r.json()["symbol_count"] > 0

    def test_symbols_are_system_scoped(self, admin_client, git_repo):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "SymScopeA")
        sys_b = _create_system(admin_client, token, "SymScopeB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])

        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=headers_a,
        )
        admin_client.post("/repository/snapshots", headers=headers_a)
        admin_client.post("/repository/symbols/index", headers=headers_a)

        r_a = admin_client.get("/repository/symbols", headers=headers_a)
        assert r_a.json()["symbol_count"] > 0

        r_b = admin_client.get("/repository/symbols", headers=headers_b)
        assert r_b.json()["symbol_count"] == 0

    def test_does_not_index_outside_repository(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Boundary")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.post("/repository/symbols/index", headers=h)
        body = r.json()
        for sym in body["symbols"]:
            assert not sym["path"].startswith("/")
            assert ".." not in sym["path"]


# ---------------------------------------------------------------------------
# Feature-to-Code mapping API tests
# ---------------------------------------------------------------------------


class TestFeatureCodeMappingAPI:
    def test_generate_requires_symbols(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoSymbols")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/drafts/generate", headers=h)

        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.status_code == 400
        assert "symbol" in r.json()["detail"].lower()

    def test_generate_requires_drafts(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoDrafts")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/symbols/index", headers=h)

        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.status_code == 400
        assert "draft" in r.json()["detail"].lower()

    def test_mock_provider_fails_without_fabricating_links(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MockMapping")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/drafts/generate", headers=h)
        admin_client.post("/repository/symbols/index", headers=h)

        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.status_code == 201
        body = r.json()

        run = body["intelligence_run"]
        assert run["status"] == "failed"
        assert run["provider"] == "mock"
        assert run["is_mock"] is True
        assert run["decision_method"] == "reasoning_llm"
        assert run["run_type"] == "feature_code_mapping"

        assert "prohibited" in run["error_details"]
        assert body["links"] == []

    def test_get_code_links(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GetLinks")
        h = _headers(token, system["id"])

        r = admin_client.get("/repository/code-links", headers=h)
        assert r.status_code == 200
        assert r.json()["links"] == []

        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        _enable_reasoning_mapping(monkeypatch)
        admin_client.post("/repository/code-links/generate", headers=h)

        r = admin_client.get("/repository/code-links", headers=h)
        assert r.status_code == 200
        assert len(r.json()["links"]) > 0
        link = r.json()["links"][0]
        assert link["source"] == "reasoning_llm"
        assert link["provider"] == "openai"
        assert link["model"] == "gpt-5"

    def test_non_reasoning_model_rejected(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NonReasoningMap")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/drafts/generate", headers=h)
        admin_client.post("/repository/symbols/index", headers=h)

        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("LLM_API_KEY", "unused")

        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["intelligence_run"]["status"] == "failed"
        assert "reasoning model" in body["intelligence_run"]["error_details"]
        assert body["links"] == []

    def test_invalid_structured_output_fails_without_links(
        self, admin_client, git_repo, monkeypatch
    ):
        class InvalidSymbolClient:
            def generate_text(self, messages, *, temperature=None, max_tokens=None):
                return json.dumps({
                    "links": [{
                        "feature_id": "source-implementation",
                        "symbol_qualified_name": "invented",
                        "symbol_path": "src/missing.py",
                        "relation_reason": "Invented by model",
                        "confidence": 0.9,
                    }]
                })

        token = _login(admin_client)
        system = _create_system(admin_client, token, "InvalidMapping")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-5")
        monkeypatch.setenv("LLM_API_KEY", "unused")
        monkeypatch.setattr(
            "app.routes.project_intelligence.create_llm_client",
            lambda config: InvalidSymbolClient(),
        )

        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.status_code == 201
        assert r.json()["intelligence_run"]["status"] == "failed"
        assert "unknown symbol" in r.json()["intelligence_run"]["error_details"]
        assert r.json()["links"] == []

    def test_code_links_are_system_scoped(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "LinkScopeA")
        sys_b = _create_system(admin_client, token, "LinkScopeB")

        _setup_snapshot_and_drafts(admin_client, token, sys_a["id"], git_repo)
        admin_client.post(
            "/repository/symbols/index",
            headers=_headers(token, sys_a["id"]),
        )
        _enable_reasoning_mapping(monkeypatch)
        admin_client.post(
            "/repository/code-links/generate",
            headers=_headers(token, sys_a["id"]),
        )

        r_a = admin_client.get(
            "/repository/code-links",
            headers=_headers(token, sys_a["id"]),
        )
        assert len(r_a.json()["links"]) > 0

        r_b = admin_client.get(
            "/repository/code-links",
            headers=_headers(token, sys_b["id"]),
        )
        assert r_b.json()["links"] == []


# ---------------------------------------------------------------------------
# Link review tests
# ---------------------------------------------------------------------------


class TestLinkReview:
    def test_accept_link(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "AcceptLink")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        _enable_reasoning_mapping(monkeypatch)
        gen = admin_client.post("/repository/code-links/generate", headers=h)
        links = gen.json()["links"]
        assert len(links) > 0
        link_id = links[0]["id"]

        r = admin_client.put(
            f"/repository/code-links/{link_id}/review",
            json={"review_status": "accepted"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["review_status"] == "accepted"

    def test_reject_link(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "RejectLink")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        _enable_reasoning_mapping(monkeypatch)
        gen = admin_client.post("/repository/code-links/generate", headers=h)
        links = gen.json()["links"]
        link_id = links[0]["id"]

        r = admin_client.put(
            f"/repository/code-links/{link_id}/review",
            json={"review_status": "rejected"},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["review_status"] == "rejected"

    def test_review_persists(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReviewPersist")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        _enable_reasoning_mapping(monkeypatch)
        gen = admin_client.post("/repository/code-links/generate", headers=h)
        link_id = gen.json()["links"][0]["id"]

        admin_client.put(
            f"/repository/code-links/{link_id}/review",
            json={"review_status": "accepted"},
            headers=h,
        )

        r = admin_client.get("/repository/code-links", headers=h)
        link = next(l for l in r.json()["links"] if l["id"] == link_id)
        assert link["review_status"] == "accepted"

    def test_review_not_found(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NotFound")
        r = admin_client.put(
            "/repository/code-links/99999/review",
            json={"review_status": "accepted"},
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 404

    def test_review_cross_system_denied(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ReviewScopeA")
        sys_b = _create_system(admin_client, token, "ReviewScopeB")

        _setup_snapshot_and_drafts(admin_client, token, sys_a["id"], git_repo)
        admin_client.post(
            "/repository/symbols/index",
            headers=_headers(token, sys_a["id"]),
        )
        _enable_reasoning_mapping(monkeypatch)
        gen = admin_client.post(
            "/repository/code-links/generate",
            headers=_headers(token, sys_a["id"]),
        )
        link_id = gen.json()["links"][0]["id"]

        r = admin_client.put(
            f"/repository/code-links/{link_id}/review",
            json={"review_status": "accepted"},
            headers=_headers(token, sys_b["id"]),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_symbol_index_run_metadata(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SymAudit")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.post("/repository/symbols/index", headers=h)
        run = r.json()["intelligence_run"]
        assert run["provider"] == "deterministic"
        assert run["model"] == "ast"
        assert run["decision_method"] == "deterministic"
        assert run["status"] == "completed"
        assert run["is_mock"] is False
        assert run["started_at"] > 0
        assert run["completed_at"] >= run["started_at"]

    def test_mapping_run_metadata(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MapAudit")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        r = admin_client.post("/repository/code-links/generate", headers=h)
        run = r.json()["intelligence_run"]
        assert run["provider"] == "mock"
        assert run["model"] == "mock"
        assert run["decision_method"] == "reasoning_llm"
        assert run["is_mock"] is True
        assert run["prompt_version"] == "v1"
        assert run["schema_version"] == "v1"

    def test_mock_links_visibly_marked(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MockMark")
        h = _headers(token, system["id"])
        _setup_snapshot_and_drafts(admin_client, token, system["id"], git_repo)
        admin_client.post("/repository/symbols/index", headers=h)
        r = admin_client.post("/repository/code-links/generate", headers=h)
        assert r.json()["is_mock"] is True
        assert r.json()["intelligence_run"]["is_mock"] is True
