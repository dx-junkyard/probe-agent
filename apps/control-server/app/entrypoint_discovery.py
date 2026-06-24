"""Backend-entrypoint discovery (Issue #51).

Flow Explorer is *backend-entrypoint-first*: the primary data source is the set
of API routes / message-queue consumers / scheduled jobs / CLI commands that act
as the real entry points into a system, not the raw ``code_symbols`` function
index. Public functions are demoted to an explicit *Advanced* fallback.

This module turns a pinned snapshot's symbols and committed Python source into:

- ``entrypoints``  — backend entrypoints (api / message_queue / scheduled_job /
  cli), each resolved to a handler symbol.
- ``functions``    — public module-level functions, the Advanced fallback.
- ``diagnostics``  — human-readable reasons when backend discovery is thin
  (no routes found, Python-only indexing, OpenAPI spec absent, etc.) so the UI
  never silently dumps a giant function list as if it were the intended UX.
- ``counts``       — per-category counts for the Repository / Flow Explorer.

Detection is deterministic and structural (CLAUDE.md principle 6: a finite,
explicitly enumerated set of framework decorators / constructors). No reasoning
model is involved; nothing is promoted from a naming guess alone.

The framework-aware part this adds over the symbol-only ``list_entrypoints`` is
**FastAPI/Starlette router prefix composition**: a route defined on a
``router = APIRouter(prefix="/users")`` that is mounted via
``app.include_router(router, prefix="/api")`` is reported as ``/api/users/...``,
including across modules. Flask blueprints (``url_prefix``) get the same
treatment.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .flow_graph import (
    FlowEntrypoint,
    SymbolRecord,
    _entry_evidence,
    build_route_entrypoint,
    enumerate_symbol_entrypoints,
)

# Schema/prompt version markers for the deterministic discovery audit run.
DISCOVERY_VERSION = "entrypoints-v1"

# HTTP method decorators that mount a route on a router/app (FastAPI/Starlette).
_HTTP_METHODS = {
    "get", "post", "put", "delete", "patch", "head", "options", "trace",
}
# Route registration shapes that carry an explicit ``methods=[...]`` list.
_GENERIC_ROUTE_DECORATORS = {"route", "api_route"}

# Constructors that create a router/app we can compose prefixes for.
_FASTAPI_ROUTER_CTORS = {"APIRouter", "FastAPI"}
_STARLETTE_ROUTER_CTORS = {"Router"}
_FLASK_APP_CTORS = {"Flask"}
_FLASK_BLUEPRINT_CTORS = {"Blueprint"}


@dataclass
class EntrypointDiscovery:
    entrypoints: List[FlowEntrypoint]  # api / message_queue / scheduled_job / cli
    functions: List[FlowEntrypoint]    # public-function Advanced fallback
    diagnostics: List[str]
    counts: Dict[str, int]
    indexed_function_count: int
    frameworks: List[str]

    @property
    def backend_total(self) -> int:
        return (
            self.counts.get("api", 0)
            + self.counts.get("message_queue", 0)
            + self.counts.get("scheduled_job", 0)
            + self.counts.get("cli", 0)
        )


# ---------------------------------------------------------------------------
# Module <-> file resolution for cross-module include_router composition
# ---------------------------------------------------------------------------


def _module_name(path: str) -> str:
    mod = path.replace("/", ".")
    if mod.endswith(".py"):
        mod = mod[: -len(".py")]
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def _build_module_index(paths: List[str]) -> Dict[str, str]:
    return {_module_name(p): p for p in paths}


@dataclass
class _RouterDef:
    file_path: str
    var: str
    prefix: str
    framework: str
    ctor: str


@dataclass
class _RouteDecl:
    file_path: str
    router_var: str
    methods: List[str]
    subpath: str
    handler_sym: SymbolRecord
    framework: str


@dataclass
class _IncludeDecl:
    file_path: str
    parent_var: str
    child_file: Optional[str]
    child_var: Optional[str]
    prefix: str


def _const_str(node: Optional[ast.expr]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kw_str(call: ast.Call, name: str) -> Optional[str]:
    for kw in call.keywords:
        if kw.arg == name:
            return _const_str(kw.value)
    return None


def _ctor_framework(ctor: str) -> str:
    if ctor in ("FastAPI", "APIRouter"):
        return "fastapi"
    if ctor == "Router":
        return "starlette"
    if ctor in ("Flask", "Blueprint"):
        return "flask"
    return "unknown"


def _join_path(*parts: str) -> str:
    """Join URL path segments with exactly one separating slash."""
    out = ""
    for part in parts:
        if not part:
            continue
        seg = part if part.startswith("/") else "/" + part
        out += seg
    out = out.replace("//", "/")
    if len(out) > 1 and out.endswith("/"):
        out = out.rstrip("/")
    return out or "/"


class _ImportResolver:
    """Resolves an imported name to a (module, original_name) pair per file."""

    def __init__(self) -> None:
        # name -> (target_module, original_name|None). When original_name is
        # None the name refers to a module alias; otherwise to an attribute.
        self.by_file: Dict[str, Dict[str, Tuple[str, Optional[str]]]] = {}

    def add_file(self, file_path: str, tree: ast.Module) -> None:
        table: Dict[str, Tuple[str, Optional[str]]] = {}
        pkg = _module_name(file_path).rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    table[bound] = (target, None)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:  # relative import
                    parts = pkg.split(".") if pkg else []
                    if node.level > 1:
                        parts = parts[: -(node.level - 1)] if node.level - 1 <= len(parts) else []
                    base = ".".join([p for p in parts if p] + ([base] if base else []))
                for alias in node.names:
                    bound = alias.asname or alias.name
                    # Could be a submodule import (from pkg import mod) or an
                    # attribute (from pkg.mod import router). Record both views;
                    # the lookup tries module first then attribute.
                    table[bound] = (f"{base}.{alias.name}" if base else alias.name, alias.name)
        self.by_file[file_path] = table

    def resolve_module(
        self, module_index: Dict[str, str], target: str,
    ) -> Optional[str]:
        if target in module_index:
            return module_index[target]
        # Lenient suffix match so repo-root-relative module names still resolve.
        suffix = "." + target
        matches = [m for m in module_index if m == target or m.endswith(suffix)]
        if len(matches) == 1:
            return module_index[matches[0]]
        return None


def _collect_file(
    file_path: str, source: str,
    symbols_by_path: Dict[str, List[SymbolRecord]],
    resolver: _ImportResolver,
) -> Tuple[Dict[str, _RouterDef], List[_RouteDecl], List[Tuple[str, ast.Call]]]:
    """Parse one file into router defs, route decls and raw include calls."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return {}, [], []

    resolver.add_file(file_path, tree)

    router_defs: Dict[str, _RouterDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            ctor = _ctor_name(node.value.func)
            if ctor in (
                _FASTAPI_ROUTER_CTORS | _STARLETTE_ROUTER_CTORS
                | _FLASK_APP_CTORS | _FLASK_BLUEPRINT_CTORS
            ):
                prefix = (
                    _kw_str(node.value, "url_prefix")
                    if ctor in _FLASK_BLUEPRINT_CTORS
                    else _kw_str(node.value, "prefix")
                ) or ""
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        router_defs[tgt.id] = _RouterDef(
                            file_path=file_path, var=tgt.id, prefix=prefix,
                            framework=_ctor_framework(ctor), ctor=ctor,
                        )

    # Index handler symbols by start line for decorator -> symbol mapping.
    syms = symbols_by_path.get(file_path, [])
    sym_by_line = {s.start_line: s for s in syms}

    route_decls: List[_RouteDecl] = []
    include_calls: List[Tuple[str, ast.Call]] = []

    def _handler_symbol(func: ast.AST) -> Optional[SymbolRecord]:
        return sym_by_line.get(getattr(func, "lineno", -1))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                decl = _route_from_decorator(dec, node, file_path, _handler_symbol)
                if decl is not None:
                    route_decls.append(decl)
        if isinstance(node, ast.Call):
            attr = node.func
            if isinstance(attr, ast.Attribute) and attr.attr in (
                "include_router", "register_blueprint",
            ):
                include_calls.append((file_path, node))

    return router_defs, route_decls, include_calls


def _ctor_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _route_from_decorator(
    dec: ast.expr, func: ast.AST, file_path: str, handler_lookup,
) -> Optional[_RouteDecl]:
    if not isinstance(dec, ast.Call):
        return None
    target = dec.func
    if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
        return None
    router_var = target.value.id
    method = target.attr.lower()
    handler = handler_lookup(func)
    if handler is None:
        return None
    subpath = _const_str(dec.args[0]) if dec.args else None
    subpath = subpath if subpath is not None else (_kw_str(dec, "path") or "")

    if method in _HTTP_METHODS:
        return _RouteDecl(
            file_path=file_path, router_var=router_var, methods=[method.upper()],
            subpath=subpath, handler_sym=handler, framework="fastapi",
        )
    if method in _GENERIC_ROUTE_DECORATORS:
        methods: List[str] = []
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for elt in kw.value.elts:
                    s = _const_str(elt)
                    if s:
                        methods.append(s.upper())
        if not methods:
            methods = ["GET"] if method == "route" else ["ANY"]
        framework = "flask" if method == "route" else "fastapi"
        return _RouteDecl(
            file_path=file_path, router_var=router_var, methods=methods,
            subpath=subpath, handler_sym=handler, framework=framework,
        )
    return None


def _resolve_child(
    call: ast.Call, file_path: str, module_index: Dict[str, str],
    resolver: _ImportResolver, router_keys: set,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the child router argument of include_router/register_blueprint."""
    if not call.args:
        return None, None
    arg = call.args[0]
    table = resolver.by_file.get(file_path, {})

    if isinstance(arg, ast.Name):
        if (file_path, arg.id) in router_keys:
            return file_path, arg.id
        if arg.id in table:
            target_mod, orig = table[arg.id]
            mod_file = resolver.resolve_module(module_index, target_mod)
            if mod_file is None and orig is not None:
                # ``from pkg import router`` — strip the trailing attr to get the
                # module, the attr is the router var name in that module.
                parent_mod = target_mod.rsplit(".", 1)[0]
                mod_file = resolver.resolve_module(module_index, parent_mod)
            if mod_file is not None:
                return mod_file, orig or arg.id
        return None, None

    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        base = arg.value.id
        var = arg.attr
        if base in table:
            target_mod, _ = table[base]
            mod_file = resolver.resolve_module(module_index, target_mod)
            if mod_file is not None:
                return mod_file, var
    return None, None


def _mount_prefixes(
    key: Tuple[str, str],
    router_defs: Dict[Tuple[str, str], _RouterDef],
    includes_by_child: Dict[Tuple[str, str], List[Tuple[Tuple[str, str], str]]],
    _seen: Optional[set] = None,
) -> List[str]:
    """Return every URL prefix at which a router's own routes are mounted."""
    _seen = _seen or set()
    if key in _seen:  # cycle guard
        return [""]
    rd = router_defs.get(key)
    own = rd.prefix if rd else ""
    parents = includes_by_child.get(key, [])
    if not parents:
        return [own]
    _seen = _seen | {key}
    out: List[str] = []
    for parent_key, include_prefix in parents:
        for pm in _mount_prefixes(parent_key, router_defs, includes_by_child, _seen):
            out.append(_join_path(pm, include_prefix, own))
    # Deduplicate, preserve order.
    seen: set = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def discover_api_routes(
    symbols: List[SymbolRecord], files: List[Tuple[str, str]],
) -> Tuple[List[FlowEntrypoint], List[str]]:
    """Framework-aware API route discovery with router-prefix composition."""
    diagnostics: List[str] = []
    symbols_by_path: Dict[str, List[SymbolRecord]] = {}
    for s in symbols:
        symbols_by_path.setdefault(s.path, []).append(s)

    py_files = [(p, src) for p, src in files if p.endswith(".py")]
    module_index = _build_module_index([p for p, _ in py_files])
    resolver = _ImportResolver()

    router_defs: Dict[Tuple[str, str], _RouterDef] = {}
    all_routes: List[_RouteDecl] = []
    raw_includes: List[Tuple[str, ast.Call]] = []

    for file_path, source in py_files:
        defs, routes, includes = _collect_file(
            file_path, source, symbols_by_path, resolver,
        )
        for var, rd in defs.items():
            router_defs[(file_path, var)] = rd
        all_routes.extend(routes)
        raw_includes.extend(includes)

    router_keys = set(router_defs.keys())
    includes_by_child: Dict[Tuple[str, str], List[Tuple[Tuple[str, str], str]]] = {}
    for file_path, call in raw_includes:
        parent = call.func.value if isinstance(call.func, ast.Attribute) else None
        parent_var = parent.id if isinstance(parent, ast.Name) else None
        child_file, child_var = _resolve_child(
            call, file_path, module_index, resolver, router_keys,
        )
        if child_file is None or child_var is None or parent_var is None:
            continue
        prefix = _kw_str(call, "prefix") or _kw_str(call, "url_prefix") or ""
        includes_by_child.setdefault((child_file, child_var), []).append(
            ((file_path, parent_var), prefix)
        )

    entrypoints: List[FlowEntrypoint] = []
    composed = False
    for decl in all_routes:
        key = (decl.file_path, decl.router_var)
        mounts = _mount_prefixes(key, router_defs, includes_by_child)
        if key in includes_by_child or (router_defs.get(key) and router_defs[key].prefix):
            composed = True
        for mount in mounts:
            full = _join_path(mount, decl.subpath)
            for method in decl.methods:
                operation = f"{method} {full}".strip()
                sym = decl.handler_sym
                entrypoints.append(FlowEntrypoint(
                    entrypoint_type="http_route",
                    entrypoint_id=f"{method}:{full}",
                    label=operation,
                    path=sym.path,
                    qualified_name=sym.qualified_name,
                    line_start=sym.start_line,
                    line_end=sym.end_line,
                    component_id=sym.component_id,
                    route_method=method,
                    route_path=full,
                    category="api",
                    framework=decl.framework,
                    operation=operation,
                    confidence=1.0,
                    evidence=_entry_evidence(
                        sym, f"{decl.framework} HTTP route {operation}",
                    ),
                ))

    # Fallback: routes the indexer saw via ``code_symbols`` decorators but whose
    # router variable we could not parse (e.g. dynamically-built apps). Keep the
    # decorator-only path so they are not lost. Skip handlers already captured
    # by the AST graph walk above, even under a different (composed) id.
    seen_ids = {e.entrypoint_id for e in entrypoints}
    seen_handlers = {(decl.handler_sym.path, decl.handler_sym.qualified_name) for decl in all_routes}
    for sym in symbols:
        if (sym.path, sym.qualified_name) in seen_handlers:
            continue
        if (sym.route_path or sym.route_method) and sym.kind in {"function", "async_function"}:
            ep = build_route_entrypoint(sym)
            if ep.entrypoint_id not in seen_ids:
                seen_ids.add(ep.entrypoint_id)
                entrypoints.append(ep)

    if composed:
        diagnostics.append(
            "FastAPI/Flask router prefixes were composed from include_router / "
            "register_blueprint mounts."
        )

    entrypoints.sort(key=lambda e: (e.route_path or "", e.route_method or "", e.path))
    return entrypoints, diagnostics


def _framework_diagnostics(
    files: List[Tuple[str, str]], api_count: int, frameworks: List[str],
) -> List[str]:
    notes: List[str] = []
    notes.append(
        "Python indexer only; JS/TS routes (Express/NestJS) and Django/DRF "
        "routers are not indexed."
    )
    has_openapi = any(
        os.path.basename(p).lower() in (
            "openapi.json", "openapi.yaml", "openapi.yml",
            "swagger.json", "swagger.yaml", "swagger.yml",
        )
        for p, _ in files
    )
    if api_count == 0:
        notes.append("No FastAPI/Starlette/Flask route decorators were found.")
        if not has_openapi:
            notes.append("No OpenAPI/Swagger spec found in the snapshot.")
    return notes


def discover_entrypoints(
    symbols: List[SymbolRecord], files: List[Tuple[str, str]],
) -> EntrypointDiscovery:
    """Discover backend entrypoints and the public-function fallback.

    Returns backend entrypoints (api/message_queue/scheduled_job/cli) resolved
    to handler symbols, the public-function Advanced fallback, deterministic
    diagnostics, and per-category counts.
    """
    api_routes, route_diags = discover_api_routes(symbols, files)
    _routes_symbolonly, message_queue, scheduled, cli, functions = (
        enumerate_symbol_entrypoints(symbols)
    )

    backend = api_routes + message_queue + scheduled + cli
    counts = {
        "api": len(api_routes),
        "message_queue": len(message_queue),
        "scheduled_job": len(scheduled),
        "cli": len(cli),
        "function": len(functions),
    }
    frameworks = sorted({e.framework for e in backend if e.framework})

    diagnostics: List[str] = list(route_diags)
    diagnostics.extend(_framework_diagnostics(files, counts["api"], frameworks))
    if counts["api"] + counts["message_queue"] + counts["scheduled_job"] + counts["cli"] == 0:
        diagnostics.insert(
            0,
            "No backend entrypoints detected. Only raw functions are available "
            "as an advanced fallback. Check repository indexing / framework "
            "support.",
        )

    return EntrypointDiscovery(
        entrypoints=backend,
        functions=functions,
        diagnostics=diagnostics,
        counts=counts,
        indexed_function_count=len(functions),
        frameworks=frameworks,
    )
