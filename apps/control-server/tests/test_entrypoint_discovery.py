"""Tests for Issue #51: backend-entrypoint-first Flow Explorer.

Covers framework-aware API route discovery (FastAPI/Starlette router-prefix
composition, including across modules, and Flask blueprint url_prefix), the
public-function Advanced fallback, deterministic diagnostics when no backend
entrypoint is detected, handler-symbol resolution, building a call tree from a
composed route, and System-scoped persistence of ``code_entrypoints`` that
leaves the target repository unchanged.
"""

import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient


def _records(files):
    """Index ``{path: source}`` into flow_graph.SymbolRecord objects."""
    from app.code_indexer import index_snapshot_files
    from app.flow_graph import SymbolRecord

    indexed = index_snapshot_files(
        [(p, src.encode("utf-8")) for p, src in files.items()]
    )
    records = []
    for i, s in enumerate(indexed.symbols):
        records.append(SymbolRecord(
            symbol_id=i + 1,
            path=s.path,
            qualified_name=s.qualified_name,
            kind=s.kind,
            start_line=s.start_line,
            end_line=s.end_line,
            decorators=s.decorators,
            component_id=s.component_id,
            route_path=s.route_path,
            route_method=s.route_method,
            docstring=s.docstring,
            is_test=s.is_test,
        ))
    return records, list(files.items())


class TestApiRouteComposition:
    def test_fastapi_router_prefix_same_file(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "api.py": textwrap.dedent("""\
                from fastapi import FastAPI, APIRouter

                app = FastAPI()
                router = APIRouter(prefix="/documents")


                @router.post("/analyze")
                async def analyze_document(req):
                    return req


                app.include_router(router, prefix="/api")
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        ids = {e.entrypoint_id for e in disc.entrypoints}
        assert "POST:/api/documents/analyze" in ids
        ep = next(e for e in disc.entrypoints if e.entrypoint_id == "POST:/api/documents/analyze")
        assert ep.category == "api"
        assert ep.qualified_name == "analyze_document"
        assert ep.route_path == "/api/documents/analyze"

    def test_fastapi_router_prefix_cross_module(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "routes/users.py": textwrap.dedent("""\
                from fastapi import APIRouter

                router = APIRouter(prefix="/users")


                @router.get("/{uid}")
                def get_user(uid):
                    return uid
            """),
            "main.py": textwrap.dedent("""\
                from fastapi import FastAPI
                from routes import users

                app = FastAPI()
                app.include_router(users.router, prefix="/api")
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        ids = {e.entrypoint_id for e in disc.entrypoints}
        assert "GET:/api/users/{uid}" in ids
        # Composition diagnostic is surfaced.
        assert any("composed" in d.lower() for d in disc.diagnostics)

    def test_flask_blueprint_url_prefix(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "web.py": textwrap.dedent("""\
                from flask import Flask, Blueprint

                app = Flask(__name__)
                bp = Blueprint("bp", __name__, url_prefix="/admin")


                @bp.route("/users", methods=["GET", "POST"])
                def list_users():
                    return []


                app.register_blueprint(bp)
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        ids = {e.entrypoint_id for e in disc.entrypoints}
        assert "GET:/admin/users" in ids
        assert "POST:/admin/users" in ids

    def test_route_without_router_prefix_unchanged(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "a.py": textwrap.dedent("""\
                from fastapi import APIRouter

                router = APIRouter()


                @router.post("/documents/analyze")
                async def analyze_document(req):
                    return req
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        ids = {e.entrypoint_id for e in disc.entrypoints}
        assert "POST:/documents/analyze" in ids


class TestFallbackAndDiagnostics:
    def test_functions_are_separated_fallback(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "a.py": textwrap.dedent("""\
                from fastapi import APIRouter

                router = APIRouter()


                @router.get("/x")
                def handler():
                    return helper()


                def helper():
                    return 1
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        # The route is a backend entrypoint; the public helper is fallback-only.
        assert {e.category for e in disc.entrypoints} == {"api"}
        assert "helper" in {f.qualified_name for f in disc.functions}
        assert all(f.category == "function" for f in disc.functions)

    def test_no_backend_entrypoints_diagnostic(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "lib.py": textwrap.dedent("""\
                def normalize(t):
                    return t


                def classify(t):
                    return t
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        assert disc.entrypoints == []
        assert disc.backend_total == 0
        assert disc.counts["function"] == 2
        assert any("No backend entrypoints detected" in d for d in disc.diagnostics)
        assert any("Python indexer only" in d for d in disc.diagnostics)

    def test_message_queue_still_detected_as_backend(self):
        from app.entrypoint_discovery import discover_entrypoints

        files = {
            "tasks.py": textwrap.dedent("""\
                from celery import shared_task


                @shared_task
                def analyze_document_task(doc):
                    return doc
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        cats = {e.category for e in disc.entrypoints}
        assert "message_queue" in cats
        assert disc.counts["message_queue"] == 1


class TestComposedGraphBuild:
    def test_build_flow_graph_from_composed_route(self):
        from app.entrypoint_discovery import discover_entrypoints
        from app.flow_graph import build_flow_graph

        files = {
            "routes/users.py": textwrap.dedent("""\
                from fastapi import APIRouter

                router = APIRouter(prefix="/users")


                @router.get("/{uid}")
                def get_user(uid):
                    return load_user(uid)


                def load_user(uid):
                    return uid
            """),
            "main.py": textwrap.dedent("""\
                from fastapi import FastAPI
                from routes import users

                app = FastAPI()
                app.include_router(users.router, prefix="/api")
            """),
        }
        records, fl = _records(files)
        disc = discover_entrypoints(records, fl)
        graph = build_flow_graph(
            symbols=records, files=fl, snapshot_id=1, commit_sha="x",
            entrypoint_type="http_route", entrypoint_id="GET:/api/users/{uid}",
            entrypoints=disc.entrypoints + disc.functions,
        )
        assert graph is not None
        assert graph.entrypoint.qualified_name == "get_user"
        names = {n.qualified_name for n in graph.nodes}
        assert "load_user" in names


# ---------------------------------------------------------------------------
# API integration: persistence, isolation, target-repo safety
# ---------------------------------------------------------------------------


SAMPLE = textwrap.dedent("""\
    from fastapi import APIRouter

    router = APIRouter(prefix="/documents")


    @router.post("/analyze")
    async def analyze_document(req):
        return parse(req)


    def parse(req):
        return req
""")


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "ep-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
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
        json={"name": name, "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (path / "app.py").write_text(SAMPLE)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return path


def _setup_snapshot(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put("/repository", json={"repo_path": str(repo),
               "include_patterns": ["*.py"]}, headers=h)
    assert client.post("/repository/snapshots", headers=h).status_code == 201
    assert client.post("/repository/symbols/index", headers=h).status_code == 201
    return h


def _repo_head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class TestEntrypointPersistence:
    def test_persists_code_entrypoints_and_serves_composed_route(
        self, admin_client, tmp_path, monkeypatch,
    ):
        repo = _git_repo(tmp_path / "repo")
        before = _repo_head(repo)
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ep")
        h = _setup_snapshot(admin_client, token, system["id"], repo)

        data = admin_client.get("/repository/flow-entrypoints", headers=h).json()
        ids = {e["entrypoint_id"] for e in data["entrypoints"]}
        assert "POST:/documents/analyze" in ids

        # Function fallback is Advanced-only: excluded unless explicitly requested.
        assert data["functions"] == []
        assert data["counts"]["api"] == 1
        assert data["counts"]["function"] == 1
        assert data["indexed_function_count"] == 1
        assert data["has_backend_entrypoints"] is True
        assert "fastapi" in data["frameworks"]

        adv = admin_client.get(
            "/repository/flow-entrypoints?include_functions=true", headers=h,
        ).json()
        assert "parse" in {f["qualified_name"] for f in adv["functions"]}

        # A persistent code_entrypoints row + audit run must exist.
        from app.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM code_entrypoints WHERE system_id = ?",
                (system["id"],),
            ).fetchall()
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE system_id = ? "
                "AND run_type = 'entrypoint_index'",
                (system["id"],),
            ).fetchone()
        assert any(r["entrypoint_id"] == "POST:/documents/analyze" for r in rows)
        assert run is not None
        assert run["decision_method"] == "deterministic"

        # Idempotent: a second GET does not duplicate rows or runs.
        admin_client.get("/repository/flow-entrypoints", headers=h)
        with get_conn() as conn:
            rows2 = conn.execute(
                "SELECT COUNT(*) AS c FROM code_entrypoints WHERE system_id = ?",
                (system["id"],),
            ).fetchone()
            runs2 = conn.execute(
                "SELECT COUNT(*) AS c FROM intelligence_runs WHERE system_id = ? "
                "AND run_type = 'entrypoint_index'",
                (system["id"],),
            ).fetchone()
        assert rows2["c"] == len(rows)
        assert runs2["c"] == 1

        # Target repository is unchanged by discovery/persistence.
        assert _repo_head(repo) == before

    def test_entrypoints_are_system_scoped(
        self, admin_client, tmp_path, monkeypatch,
    ):
        repo = _git_repo(tmp_path / "repo")
        token = _login(admin_client)
        s1 = _create_system(admin_client, token, "one")
        _setup_snapshot(admin_client, token, s1["id"], repo)
        s2 = _create_system(admin_client, token, "two")
        h2 = _headers(token, s2["id"])

        # System two has no snapshot: empty, never leaks system one's rows.
        data = admin_client.get("/repository/flow-entrypoints", headers=h2).json()
        assert data["entrypoints"] == []
        assert data["snapshot_id"] is None

        from app.db import get_conn

        with get_conn() as conn:
            other = conn.execute(
                "SELECT COUNT(*) AS c FROM code_entrypoints WHERE system_id = ?",
                (s2["id"],),
            ).fetchone()
        assert other["c"] == 0
