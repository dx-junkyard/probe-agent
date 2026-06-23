"""Deterministic execution-flow graph construction (Issue #43, Phase 1).

Builds candidate execution flows starting from an entrypoint (FastAPI HTTP
route or a public function) using only committed-snapshot symbols and a minimal
Python AST call-edge extraction.

Design constraints (see CLAUDE.md / docs/project-intelligence.md):

- Only committed-snapshot symbols and source are used. No working-tree reads.
- Edges that cannot be resolved deterministically are kept as ``unresolved``
  with ``target_node_id=None``; they are never presented as confirmed paths.
- Node/edge ordering and identifiers are stable regardless of input order so
  the same snapshot always yields the same graph.
- No LLM inference happens here. Summaries/titles are deterministic.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .probe_planner import check_denylist

# Edge resolution levels and confidence used for them.
_RESOLVED = "resolved"
_INFERRED = "inferred"
_UNRESOLVED = "unresolved"

_CONFIDENCE = {
    _RESOLVED: 1.0,
    _INFERRED: 0.5,
    _UNRESOLVED: 0.0,
}

# Function-like symbol kinds that can host call sites and become flow nodes.
_FUNCTION_KINDS = {"function", "async_function"}

# Maximum number of candidate flows to enumerate for a single entrypoint.
_MAX_CANDIDATE_FLOWS = 5


@dataclass
class SymbolRecord:
    """A snapshot symbol with the metadata the flow builder needs."""

    symbol_id: Optional[int]
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    decorators: List[str] = field(default_factory=list)
    component_id: Optional[str] = None
    route_path: Optional[str] = None
    route_method: Optional[str] = None
    docstring: Optional[str] = None
    is_test: bool = False


@dataclass
class EvidenceRef:
    path: str
    start_line: int
    end_line: int
    summary: str


@dataclass
class FlowEntrypoint:
    entrypoint_type: str  # http_route | public_function
    entrypoint_id: str
    label: str
    path: str
    qualified_name: str
    line_start: int
    line_end: int
    component_id: Optional[str] = None
    route_method: Optional[str] = None
    route_path: Optional[str] = None


@dataclass
class FlowNode:
    node_id: str
    # http_route | function | async_function | external_io | async_dispatch
    node_type: str
    symbol_id: Optional[int]
    qualified_name: str
    path: str
    line_start: int
    line_end: int
    component_id: Optional[str]
    probe_capabilities: List[str]
    risk: str  # low | medium | high
    denylist_hit: Optional[str]
    evidence: List[EvidenceRef]
    # External boundary classification (Phase 2); None for in-repo symbols.
    boundary_kind: Optional[str] = None  # http | database | filesystem | dispatch
    is_external: bool = False
    # Runtime overlay (Phase 2/3); filled by the API layer from traces.
    trace_count: int = 0
    error_count: int = 0
    evaluation_pass: int = 0
    evaluation_fail: int = 0
    observed: bool = False


@dataclass
class FlowEdge:
    source_node_id: str
    target_node_id: Optional[str]
    # call | await | dispatch | http | database | filesystem
    edge_type: str
    confidence: float
    resolution: str  # resolved | inferred | unresolved
    callee_name: str
    line: int
    evidence: List[EvidenceRef]


@dataclass
class CandidateFlow:
    flow_id: str
    title: str
    summary: str
    entrypoint_node_id: str
    node_ids: List[str]
    node_count: int
    max_depth: int
    confidence: float
    unresolved_edge_count: int
    # Phase 2: how many nodes cross an external boundary.
    external_boundary_count: int = 0
    # Phase 3: observed-path overlay against real traces.
    observed_node_count: int = 0
    unobserved_node_ids: List[str] = field(default_factory=list)


@dataclass
class FlowGraph:
    snapshot_id: int
    commit_sha: str
    entrypoint: FlowEntrypoint
    nodes: List[FlowNode]
    edges: List[FlowEdge]
    candidate_paths: List[CandidateFlow]
    diagnostics: List[str]
    truncated: bool = False


# ---------------------------------------------------------------------------
# AST call-edge extraction
# ---------------------------------------------------------------------------


@dataclass
class _CallSite:
    caller_qualified_name: str
    callee_name: str  # last attribute / name component
    is_self: bool
    edge_type: str  # call | await
    line: int
    base: Optional[str] = None  # immediate attribute base, e.g. "requests"
    dotted: str = ""  # "requests.get", "task.delay", "open"


def _node_id(path: str, qualified_name: str) -> str:
    return f"{path}::{qualified_name}"


def _callee_name(func: ast.expr) -> Tuple[Optional[str], bool, Optional[str], str]:
    """Return (callee_simple_name, is_self, base_name, dotted_name).

    Only ``name()``, ``self.method()`` and ``obj.method()`` shapes are handled;
    anything else returns ``(None, ...)`` and is treated as external.
    """
    if isinstance(func, ast.Name):
        return func.id, False, None, func.id
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            base_name = base.id
            return func.attr, base_name == "self", base_name, f"{base_name}.{func.attr}"
        return func.attr, False, None, func.attr
    return None, False, None, ""


def extract_call_sites(path: str, source: str) -> List[_CallSite]:
    """Extract intra-file call sites grouped by their enclosing function.

    Calls nested inside a closure are attributed to the nearest enclosing
    function. Qualified names mirror ``code_indexer`` (dotted by class/function
    nesting). Syntax errors yield an empty list (the snapshot already records
    indexing warnings separately).
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    sites: List[_CallSite] = []
    # Pre-compute the set of Call nodes that are directly awaited.
    awaited: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))

    def record_calls(node: ast.AST, enclosing_func: str) -> None:
        """Record call sites within an expression/statement subtree.

        Stops at nested function/class definitions, which are handled by the
        structural walk so their bodies are attributed to themselves.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                callee, is_self, base, dotted = _callee_name(child.func)
                if callee:
                    sites.append(_CallSite(
                        caller_qualified_name=enclosing_func,
                        callee_name=callee,
                        is_self=is_self,
                        edge_type="await" if id(child) in awaited else "call",
                        line=getattr(child, "lineno", 0),
                        base=base,
                        dotted=dotted,
                    ))
            record_calls(child, enclosing_func)

    def walk(body: List[ast.stmt], prefix: str, enclosing_func: Optional[str]) -> None:
        """Structural walk that attributes each statement to one enclosing def."""
        for child in body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                walk(child.body, qname, qname)
            elif isinstance(child, ast.ClassDef):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                walk(child.body, qname, enclosing_func)
            elif enclosing_func:
                record_calls(child, enclosing_func)

    walk(tree.body, "", None)
    return sites


# ---------------------------------------------------------------------------
# Language parser registry (Phase 3 extensibility seam)
# ---------------------------------------------------------------------------

# A parser maps (path, source) -> list of intra-file call sites. Additional
# languages register here without touching the graph-assembly code. Symbol and
# entrypoint extraction remain Python-specific until per-language indexers are
# added; that is intentionally out of scope for this phase.
CallSiteParser = Callable[[str, str], List[_CallSite]]

_PARSERS: Dict[str, CallSiteParser] = {}


def register_parser(extension: str, parser: CallSiteParser) -> None:
    _PARSERS[extension.lower()] = parser


def supported_extensions() -> List[str]:
    return sorted(_PARSERS)


def parse_call_sites(path: str, source: str) -> List[_CallSite]:
    """Dispatch call-site extraction to the parser registered for the file."""
    ext = os.path.splitext(path)[1].lower()
    parser = _PARSERS.get(ext)
    if parser is None:
        return []
    return parser(path, source)


register_parser(".py", extract_call_sites)


# ---------------------------------------------------------------------------
# External boundary classification (Phase 2)
#
# Deterministic and explicit: a boundary is recognised only when it matches one
# of these enumerated registries, mirroring the route-decorator and safety
# denylist approach. Anything else stays an in-repo call or is dropped as an
# unknown external/builtin. Unknown side-effect analysis is never inferred here.
# ---------------------------------------------------------------------------

# Explicit async dispatch / background-job / queue producer APIs.
_DISPATCH_METHODS = {
    "delay", "apply_async", "enqueue", "enqueue_call", "add_task",
    "send_task", "publish", "produce", "schedule", "create_task",
    "ensure_future", "run_in_executor", "spawn",
}

# Known external I/O library bases (matched on the immediate attribute base).
_HTTP_BASES = {"requests", "httpx", "aiohttp", "urllib", "urllib3"}
_DB_BASES = {
    "sqlalchemy", "psycopg2", "psycopg", "sqlite3", "pymongo", "redis",
    "asyncpg", "cursor", "db", "conn", "connection",
}
_FS_BASES = {"shutil", "pathlib"}
_FS_FUNCTIONS = {"open"}


def _classify_boundary(site: _CallSite) -> Optional[Tuple[str, str, str, str]]:
    """Classify an external call into a boundary kind.

    Returns ``(boundary_kind, edge_type, resolution, label)`` or ``None``.
    ``label`` is used to build a stable synthetic node id.
    """
    if site.callee_name in _DISPATCH_METHODS:
        return ("dispatch", "dispatch", _RESOLVED, site.dotted or site.callee_name)
    base = site.base
    if base in _HTTP_BASES:
        return ("http", "http", _INFERRED, base)
    if base in _DB_BASES:
        return ("database", "database", _INFERRED, base)
    if base in _FS_BASES or site.callee_name in _FS_FUNCTIONS:
        return ("filesystem", "filesystem", _INFERRED, base or site.callee_name)
    return None


def _external_node_id(boundary_kind: str, label: str) -> str:
    return f"external::{boundary_kind}::{label}"


def _make_external_node(boundary_kind: str, label: str, dotted: str) -> FlowNode:
    denylist_hit = check_denylist(dotted.replace(".", "_"))
    risk = "high" if denylist_hit else "medium"
    node_type = "async_dispatch" if boundary_kind == "dispatch" else "external_io"
    return FlowNode(
        node_id=_external_node_id(boundary_kind, label),
        node_type=node_type,
        symbol_id=None,
        qualified_name=dotted or label,
        path="(external)",
        line_start=0,
        line_end=0,
        component_id=None,
        probe_capabilities=["boundary"],
        risk=risk,
        denylist_hit=denylist_hit,
        evidence=[EvidenceRef(
            path="(external)",
            start_line=0,
            end_line=0,
            summary=f"{boundary_kind} boundary: {dotted or label}()",
        )],
        boundary_kind=boundary_kind,
        is_external=True,
    )


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


class _SymbolIndex:
    def __init__(self, symbols: List[SymbolRecord]):
        self.by_key: Dict[Tuple[str, str], SymbolRecord] = {}
        self.by_simple_name: Dict[str, List[SymbolRecord]] = {}
        for sym in symbols:
            if sym.kind not in _FUNCTION_KINDS:
                continue
            if sym.is_test:
                continue
            self.by_key[(sym.path, sym.qualified_name)] = sym
            simple = sym.qualified_name.rsplit(".", 1)[-1]
            self.by_simple_name.setdefault(simple, []).append(sym)
        # Deterministic ordering of candidates.
        for cands in self.by_simple_name.values():
            cands.sort(key=lambda s: (s.path, s.start_line, s.qualified_name))

    def resolve(
        self, caller: SymbolRecord, site: _CallSite,
    ) -> Tuple[Optional[SymbolRecord], str]:
        """Resolve a call site to a target symbol and a resolution level."""
        if site.is_self:
            # self.method() -> sibling method in the same class.
            class_prefix = caller.qualified_name.rsplit(".", 1)[0]
            target_qname = f"{class_prefix}.{site.callee_name}"
            target = self.by_key.get((caller.path, target_qname))
            if target is not None:
                return target, _RESOLVED
            return None, _UNRESOLVED

        # Prefer a module-level function in the same file.
        same_file = self.by_key.get((caller.path, site.callee_name))
        if same_file is not None:
            return same_file, _RESOLVED

        candidates = self.by_simple_name.get(site.callee_name, [])
        same_file_cands = [c for c in candidates if c.path == caller.path]
        if len(same_file_cands) == 1:
            return same_file_cands[0], _RESOLVED
        if len(same_file_cands) > 1:
            return None, _UNRESOLVED
        if len(candidates) == 1:
            return candidates[0], _INFERRED
        if len(candidates) > 1:
            # Ambiguous cross-file dynamic resolution: keep as unresolved.
            return None, _UNRESOLVED
        # No project symbol matches: external/builtin call, not part of graph.
        return None, "external"


def _node_type(sym: SymbolRecord) -> str:
    if sym.route_method or sym.route_path:
        return "http_route"
    return sym.kind


def _probe_capabilities(sym: SymbolRecord) -> List[str]:
    return ["input", "output", "error", "duration"]


def _risk_for(sym: SymbolRecord) -> Tuple[str, Optional[str]]:
    hit = check_denylist(sym.qualified_name, sym.docstring)
    if hit:
        return "high", hit
    return "low", None


def _make_node(sym: SymbolRecord) -> FlowNode:
    risk, hit = _risk_for(sym)
    summary = (sym.docstring or "").strip().split("\n", 1)[0][:160]
    return FlowNode(
        node_id=_node_id(sym.path, sym.qualified_name),
        node_type=_node_type(sym),
        symbol_id=sym.symbol_id,
        qualified_name=sym.qualified_name,
        path=sym.path,
        line_start=sym.start_line,
        line_end=sym.end_line,
        component_id=sym.component_id,
        probe_capabilities=_probe_capabilities(sym),
        risk=risk,
        denylist_hit=hit,
        evidence=[EvidenceRef(
            path=sym.path,
            start_line=sym.start_line,
            end_line=sym.end_line,
            summary=summary,
        )],
    )


def list_entrypoints(symbols: List[SymbolRecord]) -> List[FlowEntrypoint]:
    """Enumerate deterministic entrypoints from snapshot symbols.

    Phase 1 supports FastAPI HTTP routes plus public module-level functions.
    """
    routes: List[FlowEntrypoint] = []
    functions: List[FlowEntrypoint] = []
    for sym in symbols:
        if sym.kind not in _FUNCTION_KINDS or sym.is_test:
            continue
        if sym.route_path or sym.route_method:
            method = (sym.route_method or "ANY").upper()
            path = sym.route_path or ""
            routes.append(FlowEntrypoint(
                entrypoint_type="http_route",
                entrypoint_id=f"{method}:{path}",
                label=f"{method} {path}".strip(),
                path=sym.path,
                qualified_name=sym.qualified_name,
                line_start=sym.start_line,
                line_end=sym.end_line,
                component_id=sym.component_id,
                route_method=method,
                route_path=path,
            ))
        elif "." not in sym.qualified_name and not sym.qualified_name.startswith("_"):
            functions.append(FlowEntrypoint(
                entrypoint_type="public_function",
                entrypoint_id=f"function:{_node_id(sym.path, sym.qualified_name)}",
                label=f"{sym.qualified_name} ({sym.path})",
                path=sym.path,
                qualified_name=sym.qualified_name,
                line_start=sym.start_line,
                line_end=sym.end_line,
                component_id=sym.component_id,
            ))
    routes.sort(key=lambda e: (e.route_path or "", e.route_method or "", e.path))
    functions.sort(key=lambda e: (e.path, e.qualified_name))
    return routes + functions


def _find_entrypoint_symbol(
    symbols: List[SymbolRecord], entrypoint_type: str, entrypoint_id: str,
) -> Optional[SymbolRecord]:
    for ep in list_entrypoints(symbols):
        if ep.entrypoint_type == entrypoint_type and ep.entrypoint_id == entrypoint_id:
            # Resolve back to the concrete symbol record.
            for sym in symbols:
                if (
                    sym.path == ep.path
                    and sym.qualified_name == ep.qualified_name
                    and sym.start_line == ep.line_start
                ):
                    return sym
    return None


def build_flow_graph(
    symbols: List[SymbolRecord],
    files: List[Tuple[str, str]],
    snapshot_id: int,
    commit_sha: str,
    entrypoint_type: str,
    entrypoint_id: str,
    max_depth: int = 8,
    max_nodes: int = 100,
) -> Optional[FlowGraph]:
    """Build a deterministic flow graph for a single entrypoint.

    Returns ``None`` when the entrypoint cannot be located in the snapshot.
    """
    max_depth = max(1, min(max_depth, 32))
    max_nodes = max(1, min(max_nodes, 500))

    entry_sym = _find_entrypoint_symbol(symbols, entrypoint_type, entrypoint_id)
    if entry_sym is None:
        return None

    index = _SymbolIndex(symbols)

    # Extract call sites per file, then group by caller qualified name.
    sources = {path: src for path, src in files}
    calls_by_caller: Dict[Tuple[str, str], List[_CallSite]] = {}
    for path, src in files:
        for site in parse_call_sites(path, src):
            calls_by_caller.setdefault(
                (path, site.caller_qualified_name), []
            ).append(site)

    nodes: Dict[str, FlowNode] = {}
    edges: List[FlowEdge] = []
    diagnostics: List[str] = []
    truncated = False

    entry_node = _make_node(entry_sym)
    entry_node.node_type = "http_route" if entrypoint_type == "http_route" else entry_node.node_type
    nodes[entry_node.node_id] = entry_node

    # BFS over resolved/inferred edges, recording unresolved edges as we go.
    queue: List[Tuple[SymbolRecord, int]] = [(entry_sym, 0)]
    visited_syms = {(entry_sym.path, entry_sym.qualified_name)}
    seen_edges: set = set()

    while queue:
        caller, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        sites = calls_by_caller.get((caller.path, caller.qualified_name), [])
        for site in sorted(sites, key=lambda s: (s.line, s.callee_name)):
            target, resolution = index.resolve(caller, site)
            source_id = _node_id(caller.path, caller.qualified_name)

            if resolution == "external":
                # Phase 2: surface explicitly-recognised boundaries as leaf
                # nodes; drop genuinely unknown external/builtin calls.
                classified = _classify_boundary(site)
                if classified is None:
                    continue
                boundary_kind, edge_type, b_resolution, label = classified
                ext_id = _external_node_id(boundary_kind, label)
                edge_key = (source_id, ext_id, edge_type)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(FlowEdge(
                    source_node_id=source_id,
                    target_node_id=ext_id,
                    edge_type=edge_type,
                    confidence=_CONFIDENCE[b_resolution],
                    resolution=b_resolution,
                    callee_name=site.callee_name,
                    line=site.line,
                    evidence=[EvidenceRef(
                        path=caller.path,
                        start_line=site.line,
                        end_line=site.line,
                        summary=f"{boundary_kind} boundary via {site.dotted or site.callee_name}()",
                    )],
                ))
                if ext_id not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[ext_id] = _make_external_node(
                        boundary_kind, label, site.dotted or site.callee_name,
                    )
                continue

            if target is None:
                edge_key = (source_id, None, site.callee_name, site.edge_type, site.line)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(FlowEdge(
                    source_node_id=source_id,
                    target_node_id=None,
                    edge_type=site.edge_type,
                    confidence=_CONFIDENCE[_UNRESOLVED],
                    resolution=_UNRESOLVED,
                    callee_name=site.callee_name,
                    line=site.line,
                    evidence=[EvidenceRef(
                        path=caller.path,
                        start_line=site.line,
                        end_line=site.line,
                        summary=f"unresolved call to {site.callee_name}()",
                    )],
                ))
                continue

            target_id = _node_id(target.path, target.qualified_name)
            edge_key = (source_id, target_id, site.edge_type)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(FlowEdge(
                source_node_id=source_id,
                target_node_id=target_id,
                edge_type=site.edge_type,
                confidence=_CONFIDENCE[resolution],
                resolution=resolution,
                callee_name=site.callee_name,
                line=site.line,
                evidence=[EvidenceRef(
                    path=caller.path,
                    start_line=site.line,
                    end_line=site.line,
                    summary=f"{resolution} call to {target.qualified_name}()",
                )],
            ))

            if target_id not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                nodes[target_id] = _make_node(target)
            key = (target.path, target.qualified_name)
            if key not in visited_syms:
                visited_syms.add(key)
                queue.append((target, depth + 1))

    if truncated:
        diagnostics.append(
            f"Graph truncated at max_nodes={max_nodes}; some branches are omitted."
        )
    missing_sources = [
        s.path for s in symbols
        if s.kind in _FUNCTION_KINDS and s.path not in sources
    ]
    if missing_sources:
        diagnostics.append(
            f"{len(set(missing_sources))} file(s) had symbols but no indexed "
            "source; their call edges were skipped."
        )

    candidate_paths = _enumerate_candidate_flows(
        entry_node.node_id, nodes, edges, max_depth,
    )

    ordered_nodes = _ordered_nodes(entry_node.node_id, nodes, edges)
    ordered_edges = sorted(
        edges,
        key=lambda e: (
            e.source_node_id,
            e.target_node_id or "~",
            e.line,
            e.callee_name,
        ),
    )

    return FlowGraph(
        snapshot_id=snapshot_id,
        commit_sha=commit_sha,
        entrypoint=FlowEntrypoint(
            entrypoint_type=entrypoint_type,
            entrypoint_id=entrypoint_id,
            label=(
                f"{entry_sym.route_method or ''} {entry_sym.route_path or ''}".strip()
                if entrypoint_type == "http_route"
                else f"{entry_sym.qualified_name} ({entry_sym.path})"
            ),
            path=entry_sym.path,
            qualified_name=entry_sym.qualified_name,
            line_start=entry_sym.start_line,
            line_end=entry_sym.end_line,
            component_id=entry_sym.component_id,
            route_method=entry_sym.route_method,
            route_path=entry_sym.route_path,
        ),
        nodes=ordered_nodes,
        edges=ordered_edges,
        candidate_paths=candidate_paths,
        diagnostics=diagnostics,
        truncated=truncated,
    )


def _ordered_nodes(
    entry_id: str, nodes: Dict[str, FlowNode], edges: List[FlowEdge],
) -> List[FlowNode]:
    """Return nodes in a stable BFS order rooted at the entrypoint."""
    adjacency: Dict[str, List[str]] = {}
    for e in sorted(edges, key=lambda e: (e.line, e.callee_name)):
        if e.target_node_id is not None:
            adjacency.setdefault(e.source_node_id, []).append(e.target_node_id)

    ordered: List[FlowNode] = []
    seen: set = set()
    queue = [entry_id]
    while queue:
        nid = queue.pop(0)
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        ordered.append(nodes[nid])
        for tgt in adjacency.get(nid, []):
            if tgt not in seen:
                queue.append(tgt)
    # Append any nodes not reached (defensive; should not happen).
    for nid in sorted(nodes):
        if nid not in seen:
            ordered.append(nodes[nid])
    return ordered


def _enumerate_candidate_flows(
    entry_id: str,
    nodes: Dict[str, FlowNode],
    edges: List[FlowEdge],
    max_depth: int,
) -> List[CandidateFlow]:
    """Deterministically enumerate distinct root-to-leaf flows."""
    out_edges: Dict[str, List[FlowEdge]] = {}
    unresolved_by_node: Dict[str, int] = {}
    for e in edges:
        if e.target_node_id is None:
            unresolved_by_node[e.source_node_id] = (
                unresolved_by_node.get(e.source_node_id, 0) + 1
            )
            continue
        out_edges.setdefault(e.source_node_id, []).append(e)
    for lst in out_edges.values():
        lst.sort(key=lambda e: (e.line, e.target_node_id or "", e.callee_name))

    paths: List[Tuple[List[str], float]] = []

    def dfs(node_id: str, path: List[str], min_conf: float) -> None:
        if len(paths) >= _MAX_CANDIDATE_FLOWS * 4:
            return
        children = [
            e for e in out_edges.get(node_id, [])
            if e.target_node_id not in path
        ]
        if not children or len(path) >= max_depth:
            paths.append((list(path), min_conf))
            return
        for e in children:
            dfs(
                e.target_node_id,
                path + [e.target_node_id],
                min(min_conf, e.confidence),
            )

    dfs(entry_id, [entry_id], 1.0)

    # Deduplicate identical paths, keep the longest/most-specific first.
    unique: List[Tuple[List[str], float]] = []
    seen_paths: set = set()
    for path, conf in sorted(paths, key=lambda p: (-len(p[0]), p[0])):
        key = tuple(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique.append((path, conf))

    flows: List[CandidateFlow] = []
    for i, (path, conf) in enumerate(unique[:_MAX_CANDIDATE_FLOWS]):
        unresolved = sum(unresolved_by_node.get(n, 0) for n in path)
        external = sum(
            1 for n in path if n in nodes and nodes[n].is_external
        )
        leaf = nodes.get(path[-1])
        entry = nodes.get(path[0])
        leaf_name = leaf.qualified_name if leaf else path[-1]
        entry_name = entry.qualified_name if entry else path[0]
        title = _flow_title(nodes, path)
        flows.append(CandidateFlow(
            flow_id=f"flow-{i + 1}",
            title=title,
            summary=(
                f"{entry_name} → … → {leaf_name}"
                if len(path) > 2 else " → ".join(
                    (nodes[n].qualified_name if n in nodes else n) for n in path
                )
            ),
            entrypoint_node_id=path[0],
            node_ids=path,
            node_count=len(path),
            max_depth=len(path) - 1,
            confidence=round(conf, 3),
            unresolved_edge_count=unresolved,
            external_boundary_count=external,
        ))
    return flows


def apply_observed_overlay(graph: FlowGraph) -> None:
    """Recompute observed-path overlay on candidate flows (Phase 3).

    Call after the API layer has set ``FlowNode.observed`` from real traces.
    For each candidate flow it records how many in-repo nodes have runtime
    observations and which probeable nodes remain unobserved, so the UI can
    diff the static candidate against what production actually exercised.
    """
    observed_ids = {n.node_id for n in graph.nodes if n.observed}
    for flow in graph.candidate_paths:
        probeable = [
            nid for nid in flow.node_ids
            if any(n.node_id == nid and not n.is_external for n in graph.nodes)
        ]
        flow.observed_node_count = sum(1 for nid in probeable if nid in observed_ids)
        flow.unobserved_node_ids = [
            nid for nid in probeable if nid not in observed_ids
        ]


def _flow_title(nodes: Dict[str, FlowNode], path: List[str]) -> str:
    names = [
        (nodes[n].qualified_name if n in nodes else n).rsplit(".", 1)[-1]
        for n in path
    ]
    if len(names) <= 4:
        return " → ".join(names)
    return " → ".join([names[0], names[1], "…", names[-1]])
