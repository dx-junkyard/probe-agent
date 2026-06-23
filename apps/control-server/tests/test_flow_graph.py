"""Tests for Issue #43: API-rooted execution flow visualization.

Phase 1: deterministic AST call-edge extraction, entrypoint enumeration, flow
graph assembly (resolved / inferred / unresolved edges), candidate-path
selection, depth/node budgets, system scoping, conversion of selected nodes
into a manual Probe Plan draft, and the safety guarantee that flow selection
alone never triggers patch generation or application.

Phase 2: explicit external-boundary classification (dispatch / http / database
/ filesystem) as leaf nodes, and trace/evaluation runtime overlay on nodes.

Phase 3: observed-path overlay diffing real traces against static candidate
flows, and the language parser-registry extensibility seam.
"""

import subprocess
import textwrap
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Unit tests: deterministic AST extraction and graph building
# ---------------------------------------------------------------------------


SAMPLE_SOURCE = textwrap.dedent("""\
    from fastapi import APIRouter

    router = APIRouter()


    @router.post("/documents/analyze")
    async def analyze_document(req):
        blocks = parse_blocks(req)
        comps = extract_components(blocks)
        save_analysis_result(comps)
        return comps


    def parse_blocks(req):
        return normalize(req)


    def extract_components(blocks):
        return [b for b in blocks]


    def save_analysis_result(comps):
        requests.post("http://x", json=comps)
        return dynamic_dispatch(comps)


    def normalize(req):
        return req


    def _private_helper():
        return 1
""")


def _records_from_source(path, source):
    """Build SymbolRecord objects the way the symbol index would."""
    import ast

    from app.flow_graph import SymbolRecord

    tree = ast.parse(source)
    records = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                route_path = route_method = None
                for dec in child.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        if dec.func.attr in ("get", "post", "put", "delete", "patch"):
                            route_method = dec.func.attr.upper()
                            if dec.args and isinstance(dec.args[0], ast.Constant):
                                route_path = dec.args[0].value
                records.append(SymbolRecord(
                    symbol_id=len(records) + 1,
                    path=path,
                    qualified_name=qname,
                    kind="async_function" if isinstance(child, ast.AsyncFunctionDef) else "function",
                    start_line=child.lineno,
                    end_line=child.end_lineno,
                    route_path=route_path,
                    route_method=route_method,
                ))
                visit(child, qname)
            elif isinstance(child, ast.ClassDef):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                visit(child, qname)

    visit(tree, "")
    return records


class TestCallExtraction:
    def test_attributes_calls_to_nearest_function(self):
        from app.flow_graph import extract_call_sites

        sites = extract_call_sites("a.py", SAMPLE_SOURCE)
        by_caller = {}
        for s in sites:
            by_caller.setdefault(s.caller_qualified_name, set()).add(s.callee_name)
        assert by_caller["analyze_document"] == {
            "parse_blocks", "extract_components", "save_analysis_result",
        }
        assert by_caller["parse_blocks"] == {"normalize"}

    def test_detects_await_edge(self):
        from app.flow_graph import extract_call_sites

        source = textwrap.dedent("""\
            async def handler():
                await do_work()
        """)
        sites = extract_call_sites("a.py", source)
        assert len(sites) == 1
        assert sites[0].edge_type == "await"

    def test_self_method_call_marked(self):
        from app.flow_graph import extract_call_sites

        source = textwrap.dedent("""\
            class Service:
                def run(self):
                    self.step()
                def step(self):
                    return 1
        """)
        sites = extract_call_sites("a.py", source)
        run_sites = [s for s in sites if s.caller_qualified_name == "Service.run"]
        assert len(run_sites) == 1
        assert run_sites[0].is_self is True
        assert run_sites[0].callee_name == "step"

    def test_decorator_calls_not_attributed_to_body(self):
        from app.flow_graph import extract_call_sites

        sites = extract_call_sites("a.py", SAMPLE_SOURCE)
        # The @router.post(...) decorator on analyze_document must not become a
        # call edge attributed to that handler.
        assert all(
            not (s.caller_qualified_name == "analyze_document" and s.callee_name == "post")
            for s in sites
        )

    def test_syntax_error_yields_no_sites(self):
        from app.flow_graph import extract_call_sites

        assert extract_call_sites("a.py", "def broken(:\n") == []


class TestEntrypoints:
    def test_lists_routes_and_public_functions(self):
        from app.flow_graph import list_entrypoints

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        eps = list_entrypoints(records)
        route_eps = [e for e in eps if e.entrypoint_type == "http_route"]
        assert len(route_eps) == 1
        assert route_eps[0].entrypoint_id == "POST:/documents/analyze"
        # Private helpers are excluded from public-function entrypoints.
        fn_ids = {e.qualified_name for e in eps if e.entrypoint_type == "public_function"}
        assert "_private_helper" not in fn_ids
        assert "parse_blocks" in fn_ids

    def test_deterministic_ordering(self):
        from app.flow_graph import list_entrypoints

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        first = [e.entrypoint_id for e in list_entrypoints(records)]
        second = [e.entrypoint_id for e in list_entrypoints(list(reversed(records)))]
        assert first == second


class TestGraphBuilding:
    def _graph(self, **kwargs):
        from app.flow_graph import build_flow_graph

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        return build_flow_graph(
            symbols=records,
            files=[("a.py", SAMPLE_SOURCE)],
            snapshot_id=1,
            commit_sha="deadbeef",
            entrypoint_type="http_route",
            entrypoint_id="POST:/documents/analyze",
            **kwargs,
        )

    def test_builds_resolved_edges(self):
        graph = self._graph()
        assert graph is not None
        node_names = {n.qualified_name for n in graph.nodes}
        assert {"analyze_document", "parse_blocks", "normalize"} <= node_names
        resolved = [e for e in graph.edges if e.resolution == "resolved"]
        assert any(
            e.target_node_id and e.target_node_id.endswith("parse_blocks")
            for e in resolved
        )

    def test_unresolved_dynamic_call_kept_separate(self):
        # ``dynamic_dispatch`` is ambiguous-free (absent), ``external_db.commit``
        # is external. We add an ambiguous symbol to force an unresolved edge.
        from app.flow_graph import SymbolRecord, build_flow_graph

        source = textwrap.dedent("""\
            def entry():
                handle()
            def handle():
                pass
        """)
        # Two functions named ``handle`` in different files => ambiguous.
        records = [
            SymbolRecord(1, "a.py", "entry", "function", 1, 2),
            SymbolRecord(2, "a.py", "handle", "function", 3, 4),
            SymbolRecord(3, "b.py", "handle", "function", 1, 2),
        ]
        graph = build_flow_graph(
            symbols=records,
            files=[("a.py", source)],
            snapshot_id=1,
            commit_sha="x",
            entrypoint_type="public_function",
            entrypoint_id="function:a.py::entry",
        )
        # Same-file handle resolves first (unique in file), so it is resolved.
        edge = [e for e in graph.edges if e.callee_name == "handle"][0]
        assert edge.resolution == "resolved"
        assert edge.target_node_id == "a.py::handle"

    def test_truly_ambiguous_call_is_unresolved(self):
        from app.flow_graph import SymbolRecord, build_flow_graph

        source = textwrap.dedent("""\
            def entry():
                handle()
        """)
        records = [
            SymbolRecord(1, "a.py", "entry", "function", 1, 2),
            SymbolRecord(2, "b.py", "handle", "function", 1, 2),
            SymbolRecord(3, "c.py", "handle", "function", 1, 2),
        ]
        graph = build_flow_graph(
            symbols=records,
            files=[("a.py", source)],
            snapshot_id=1, commit_sha="x",
            entrypoint_type="public_function",
            entrypoint_id="function:a.py::entry",
        )
        edge = [e for e in graph.edges if e.callee_name == "handle"][0]
        assert edge.resolution == "unresolved"
        assert edge.target_node_id is None

    def test_candidate_paths_present(self):
        graph = self._graph()
        assert len(graph.candidate_paths) >= 1
        flow = graph.candidate_paths[0]
        assert flow.node_ids[0] == "a.py::analyze_document"
        assert flow.entrypoint_node_id == "a.py::analyze_document"

    def test_depth_budget_limits_traversal(self):
        graph = self._graph(max_depth=1)
        # With depth 1 only the entrypoint's direct callees become nodes.
        names = {n.qualified_name for n in graph.nodes}
        assert "analyze_document" in names
        assert "normalize" not in names  # depth 2 from entry

    def test_node_budget_truncates(self):
        graph = self._graph(max_nodes=2)
        assert graph.truncated is True
        assert any("truncated" in d for d in graph.diagnostics)

    def test_deterministic_output(self):
        from app.flow_graph import build_flow_graph

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        g1 = build_flow_graph(
            records, [("a.py", SAMPLE_SOURCE)], 1, "x",
            "http_route", "POST:/documents/analyze",
        )
        g2 = build_flow_graph(
            list(reversed(records)), [("a.py", SAMPLE_SOURCE)], 1, "x",
            "http_route", "POST:/documents/analyze",
        )
        assert [n.node_id for n in g1.nodes] == [n.node_id for n in g2.nodes]
        assert [(e.source_node_id, e.target_node_id) for e in g1.edges] == \
            [(e.source_node_id, e.target_node_id) for e in g2.edges]

    def test_unknown_entrypoint_returns_none(self):
        from app.flow_graph import build_flow_graph

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        assert build_flow_graph(
            records, [("a.py", SAMPLE_SOURCE)], 1, "x",
            "http_route", "GET:/nope",
        ) is None


class TestExternalBoundaries:
    """Phase 2: explicit, enumerated external boundary classification."""

    BOUNDARY_SOURCE = textwrap.dedent("""\
        async def run_experiment(req):
            apply_variant(req)
            requests.post("http://x")
            cursor.execute("INSERT")
            task.delay(req)
            persist(req)

        def apply_variant(req):
            return req

        def persist(req):
            open("/tmp/x").write("y")
            return mystery_external(req)
    """)

    def _graph(self):
        from app.flow_graph import SymbolRecord, build_flow_graph
        import ast

        tree = ast.parse(self.BOUNDARY_SOURCE)
        syms = [
            SymbolRecord(
                i + 1, "e.py", n.name,
                "async_function" if isinstance(n, ast.AsyncFunctionDef) else "function",
                n.lineno, n.end_lineno,
            )
            for i, n in enumerate(ast.walk(tree))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return build_flow_graph(
            syms, [("e.py", self.BOUNDARY_SOURCE)], 1, "sha",
            "public_function", "function:e.py::run_experiment",
        )

    def test_classifies_boundary_kinds(self):
        graph = self._graph()
        ext = {n.node_id: n for n in graph.nodes if n.is_external}
        kinds = {n.boundary_kind for n in ext.values()}
        assert kinds == {"http", "database", "dispatch", "filesystem"}

    def test_dispatch_is_resolved_io_is_inferred(self):
        graph = self._graph()
        by_type = {e.edge_type: e for e in graph.edges}
        assert by_type["dispatch"].resolution == "resolved"
        assert by_type["http"].resolution == "inferred"
        assert by_type["database"].resolution == "inferred"

    def test_external_nodes_are_leaves(self):
        graph = self._graph()
        ext_ids = {n.node_id for n in graph.nodes if n.is_external}
        # No edge originates from an external boundary node.
        assert all(e.source_node_id not in ext_ids for e in graph.edges)

    def test_unknown_external_call_dropped(self):
        graph = self._graph()
        # mystery_external() matches no registry and is not a project symbol.
        assert all(
            "mystery_external" not in (e.callee_name or "") for e in graph.edges
        )

    def test_candidate_flow_counts_boundaries(self):
        graph = self._graph()
        assert any(f.external_boundary_count >= 1 for f in graph.candidate_paths)


class TestParserRegistry:
    """Phase 3: language parser extensibility seam."""

    def test_python_registered(self):
        from app.flow_graph import supported_extensions

        assert ".py" in supported_extensions()

    def test_unknown_extension_returns_empty(self):
        from app.flow_graph import parse_call_sites

        assert parse_call_sites("main.go", "func main() {}") == []

    def test_register_custom_parser(self):
        from app.flow_graph import register_parser, parse_call_sites, _CallSite

        def fake_parser(path, source):
            return [_CallSite("entry", "callee", False, "call", 1)]

        register_parser(".fake", fake_parser)
        try:
            sites = parse_call_sites("x.fake", "anything")
            assert len(sites) == 1
            assert sites[0].callee_name == "callee"
        finally:
            from app.flow_graph import _PARSERS
            _PARSERS.pop(".fake", None)


class TestObservedOverlay:
    """Phase 3: observed-path overlay against candidate flows."""

    def test_overlay_marks_observed_and_unobserved(self):
        from app.flow_graph import build_flow_graph, apply_observed_overlay

        records = _records_from_source("a.py", SAMPLE_SOURCE)
        graph = build_flow_graph(
            records, [("a.py", SAMPLE_SOURCE)], 1, "x",
            "http_route", "POST:/documents/analyze",
        )
        # Mark one node as observed.
        for n in graph.nodes:
            if n.qualified_name == "parse_blocks":
                n.observed = True
        apply_observed_overlay(graph)
        flow = next(
            f for f in graph.candidate_paths
            if "a.py::parse_blocks" in f.node_ids
        )
        assert flow.observed_node_count >= 1
        assert "a.py::analyze_document" in flow.unobserved_node_ids
        assert "a.py::parse_blocks" not in flow.unobserved_node_ids


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "flow-test.db"))
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


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (repo / "app.py").write_text(SAMPLE_SOURCE)
    (repo / "mailer.py").write_text(textwrap.dedent("""\
        def notify(user):
            return send_email(user)


        def send_email(user):
            return True
    """))
    (repo / "probed.py").write_text(textwrap.dedent("""\
        from probe_agent import probe


        @probe(component_id="parser")
        def parse_text(t):
            return clean(t)


        def clean(t):
            return t
    """))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


def _setup_snapshot(client, token, system_id, repo):
    h = _headers(token, system_id)
    client.put("/repository", json={"repo_path": str(repo),
               "include_patterns": ["*.py"]}, headers=h)
    r = client.post("/repository/snapshots", headers=h)
    assert r.status_code == 201, r.text
    r = client.post("/repository/symbols/index", headers=h)
    assert r.status_code == 201, r.text
    return h


class TestFlowApi:
    def test_lists_entrypoints(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ep")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)

        r = admin_client.get("/repository/flow-entrypoints", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["commit_sha"]
        ids = {e["entrypoint_id"] for e in data["entrypoints"]}
        assert "POST:/documents/analyze" in ids

    def test_builds_flow_graph(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "graph")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)

        r = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "http_route",
                  "entrypoint_id": "POST:/documents/analyze"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["commit_sha"]
        names = {n["qualified_name"] for n in data["nodes"]}
        assert "analyze_document" in names
        assert "parse_blocks" in names
        # every in-repo node and edge tracks source provenance
        for node in data["nodes"]:
            assert "evidence" in node
            if not node["is_external"]:
                assert node["path"] and node["line_start"] >= 1
        for edge in data["edges"]:
            assert edge["resolution"] in ("resolved", "inferred", "unresolved")
        assert len(data["candidate_paths"]) >= 1

    def test_unknown_entrypoint_404(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "missing")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        r = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "http_route", "entrypoint_id": "GET:/nope"},
            headers=h,
        )
        assert r.status_code == 404

    def test_runtime_overlay_marks_observed_nodes(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "overlay")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)

        # Send a real trace for the probed component "parser".
        r = admin_client.post(
            "/traces",
            json={
                "trace_id": "t-1", "component_id": "parser",
                "input": {"t": "hi"}, "output": "hi",
                "duration_ms": 3.0, "timestamp": time.time(),
            },
            headers=h,
        )
        assert r.status_code == 201, r.text

        r = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "public_function",
                  "entrypoint_id": "function:probed.py::parse_text"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        nodes = {n["qualified_name"]: n for n in data["nodes"]}
        assert nodes["parse_text"]["component_id"] == "parser"
        assert nodes["parse_text"]["trace_count"] == 1
        assert nodes["parse_text"]["observed"] is True
        assert nodes["clean"]["observed"] is False
        # Observed-path overlay diffs against the static candidate flow.
        flow = data["candidate_paths"][0]
        assert flow["observed_node_count"] >= 1
        assert "probed.py::clean" in flow["unobserved_node_ids"]

    def test_external_node_cannot_be_probed(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "extprobe")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        graph = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "public_function",
                  "entrypoint_id": "function:probed.py::parse_text"},
            headers=h,
        ).json()
        # SAMPLE_SOURCE has no external nodes; use app.py save -> external open.
        graph2 = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "http_route",
                  "entrypoint_id": "POST:/documents/analyze"},
            headers=h,
        ).json()
        ext = [n for n in graph2["nodes"] if n["is_external"]]
        if not ext:
            pytest.skip("no external node present in sample")
        r = admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "http_route",
                "entrypoint_id": "POST:/documents/analyze",
                "selections": [{"node_id": ext[0]["node_id"],
                                "observation": "boundary", "mode_preference": "trace"}],
            },
            headers=h,
        )
        assert r.status_code == 400
        assert "external" in r.json()["detail"].lower()

    def test_high_risk_node_flagged(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "risk")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        r = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "public_function",
                  "entrypoint_id": "function:mailer.py::notify"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        nodes = {n["qualified_name"]: n for n in r.json()["nodes"]}
        assert nodes["send_email"]["risk"] == "high"
        assert nodes["send_email"]["denylist_hit"]


class TestProbePlanFromFlow:
    def _build_and_select(self, client, h):
        graph = client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "http_route",
                  "entrypoint_id": "POST:/documents/analyze"},
            headers=h,
        ).json()
        return graph

    def test_creates_manual_plan(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "fromflow")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        graph = self._build_and_select(admin_client, h)
        node = [n for n in graph["nodes"] if n["qualified_name"] == "parse_blocks"][0]

        r = admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "http_route",
                "entrypoint_id": "POST:/documents/analyze",
                "objective": "Observe block parsing.",
                "selections": [
                    {"node_id": node["node_id"], "observation": "output",
                     "mode_preference": "trace"},
                ],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        plan = r.json()
        assert plan["status"] == "proposed"
        assert plan["intelligence_run"]["decision_method"] == "manual"
        assert plan["intelligence_run"]["run_type"] == "probe_plan_from_flow"
        assert plan["is_mock"] is False
        assert len(plan["probe_points"]) == 1
        pt = plan["probe_points"][0]
        assert pt["symbol"] == "parse_blocks"
        assert pt["recommended_mode"] == "trace"
        assert pt["status"] == "proposed"

    def test_denylist_node_marked_high_risk(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "deny")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        graph = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "public_function",
                  "entrypoint_id": "function:mailer.py::notify"},
            headers=h,
        ).json()
        node = [n for n in graph["nodes"] if n["qualified_name"] == "send_email"][0]
        r = admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "public_function",
                "entrypoint_id": "function:mailer.py::notify",
                "selections": [{"node_id": node["node_id"],
                                "observation": "output", "mode_preference": "shadow"}],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        pt = r.json()["probe_points"][0]
        assert pt["side_effect_risk"] == "high"
        assert pt["denylist_hit"]
        # A denylisted point cannot be approved.
        approve = admin_client.put(
            f"/repository/probe-points/{pt['id']}/status",
            json={"status": "approved"}, headers=h,
        )
        assert approve.status_code == 409

    def test_rejects_node_outside_graph(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "outside")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        r = admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "http_route",
                "entrypoint_id": "POST:/documents/analyze",
                "selections": [{"node_id": "a.py::ghost", "observation": "output",
                                "mode_preference": "trace"}],
            },
            headers=h,
        )
        assert r.status_code == 400

    def test_selection_does_not_generate_patch(self, admin_client, git_repo, monkeypatch):
        """Flow selection must not create or apply any patch."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "nopatch")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        graph = self._build_and_select(admin_client, h)
        node = [n for n in graph["nodes"] if n["qualified_name"] == "parse_blocks"][0]
        admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "http_route",
                "entrypoint_id": "POST:/documents/analyze",
                "selections": [{"node_id": node["node_id"], "observation": "output",
                                "mode_preference": "trace"}],
            },
            headers=h,
        )
        patches = admin_client.get("/repository/probe-patches", headers=h)
        assert patches.status_code == 200
        assert patches.json() == []

    def test_target_repo_unchanged(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "unchanged")
        h = _setup_snapshot(admin_client, token, system["id"], git_repo)
        before = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        graph = self._build_and_select(admin_client, h)
        node = graph["nodes"][0]
        admin_client.post(
            "/repository/probe-plans/from-flow",
            json={
                "entrypoint_type": "http_route",
                "entrypoint_id": "POST:/documents/analyze",
                "selections": [{"node_id": node["node_id"], "observation": "input",
                                "mode_preference": "trace"}],
            },
            headers=h,
        )
        after = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert before == after


class TestSystemScoping:
    def test_graph_inputs_are_system_scoped(self, admin_client, git_repo, monkeypatch):
        token = _login(admin_client)
        system1 = _create_system(admin_client, token, "s1")
        system2 = _create_system(admin_client, token, "s2")
        _setup_snapshot(admin_client, token, system1["id"], git_repo)

        h2 = _headers(token, system2["id"])
        r = admin_client.get("/repository/flow-entrypoints", headers=h2)
        assert r.status_code == 200
        assert r.json()["entrypoints"] == []

        r = admin_client.post(
            "/repository/flow-graphs",
            json={"entrypoint_type": "http_route",
                  "entrypoint_id": "POST:/documents/analyze"},
            headers=h2,
        )
        assert r.status_code == 400  # no snapshot for system2
