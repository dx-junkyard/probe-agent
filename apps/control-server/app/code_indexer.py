"""Deterministic Python AST symbol extraction.

Parses Python source files from a snapshot and extracts module-level,
class-level, and function-level symbols.  Syntax errors in individual files
produce warnings instead of aborting the whole index.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class CodeSymbol:
    path: str
    qualified_name: str
    kind: str  # module | class | function | async_function
    start_line: int
    end_line: int
    decorators: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_test: bool = False
    is_pydantic_model: bool = False
    route_path: Optional[str] = None
    route_method: Optional[str] = None
    component_id: Optional[str] = None


@dataclass
class ImportInfo:
    path: str
    module: str
    names: List[str] = field(default_factory=list)
    is_from_import: bool = False


@dataclass
class IndexWarning:
    path: str
    message: str


@dataclass
class IndexResult:
    symbols: List[CodeSymbol]
    imports: List[ImportInfo]
    warnings: List[IndexWarning]


_PYDANTIC_BASES = {"BaseModel", "BaseSettings"}

_ROUTE_DECORATORS = {
    "get", "post", "put", "delete", "patch", "head", "options", "trace",
    "route", "api_route", "websocket",
}


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _decorator_str(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        name = _decorator_name(node.func)
        args = []
        for arg in node.args:
            args.append(ast.dump(arg))
        for kw in node.keywords:
            args.append(f"{kw.arg}={ast.dump(kw.value)}")
        return f"{name}({', '.join(args)})"
    return _decorator_name(node)


def _extract_route_info(decorators: List[ast.expr]) -> Tuple[Optional[str], Optional[str]]:
    for dec in decorators:
        if isinstance(dec, ast.Call):
            name = _decorator_name(dec.func)
            parts = name.split(".")
            method_part = parts[-1].lower() if parts else ""
            if method_part in _ROUTE_DECORATORS:
                path = None
                method = method_part.upper() if method_part != "route" else None
                if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    path = dec.args[0].value
                for kw in dec.keywords:
                    if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        path = kw.value.value
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = []
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                methods.append(elt.value.upper())
                        if methods:
                            method = ",".join(methods)
                return path, method
    return None, None


def _extract_component_id(decorators: List[ast.expr]) -> Optional[str]:
    for dec in decorators:
        if not isinstance(dec, ast.Call):
            continue
        if _decorator_name(dec.func).split(".")[-1] != "probe":
            continue
        for kw in dec.keywords:
            if (
                kw.arg == "component_id"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                return kw.value.value
    return None


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = _decorator_name(base)
        parts = name.split(".")
        if parts[-1] in _PYDANTIC_BASES:
            return True
    return False


def _get_docstring(node: ast.AST) -> Optional[str]:
    try:
        doc = ast.get_docstring(node)
        return doc.strip() if doc else None
    except (TypeError, AttributeError):
        return None


def _is_test_function(name: str, path: str) -> bool:
    return name.startswith("test_") or name.endswith("_test")


def _end_line(node: ast.AST) -> int:
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        return node.end_lineno
    return getattr(node, "lineno", 0)


def index_python_file(path: str, source: str) -> Tuple[List[CodeSymbol], List[ImportInfo], Optional[IndexWarning]]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [], [], IndexWarning(path=path, message=f"SyntaxError: {exc}")

    symbols: List[CodeSymbol] = []
    imports: List[ImportInfo] = []

    module_name = path.replace("/", ".").removesuffix(".py")
    if module_name.endswith(".__init__"):
        module_name = module_name[: -len(".__init__")]

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportInfo(
                    path=path,
                    module=alias.name,
                    names=[alias.asname or alias.name],
                ))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.append(ImportInfo(
                path=path,
                module=mod,
                names=[alias.name for alias in node.names],
                is_from_import=True,
            ))

    import_names = [
        f"{item.module}:{','.join(item.names)}" if item.names else item.module
        for item in imports
    ]
    module_end_line = max(
        (getattr(node, "end_lineno", None) or getattr(node, "lineno", 1))
        for node in tree.body
    ) if tree.body else 1
    symbols.append(CodeSymbol(
        path=path,
        qualified_name=module_name,
        kind="module",
        start_line=1,
        end_line=module_end_line,
        imports=import_names,
        docstring=_get_docstring(tree),
    ))

    def _visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                decorator_strs = [_decorator_str(d) for d in child.decorator_list]
                route_path, route_method = _extract_route_info(child.decorator_list)
                component_id = _extract_component_id(child.decorator_list)
                is_test = _is_test_function(child.name, path)
                kind = "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function"
                symbols.append(CodeSymbol(
                    path=path,
                    qualified_name=qname,
                    kind=kind,
                    start_line=child.lineno,
                    end_line=_end_line(child),
                    decorators=decorator_strs,
                    docstring=_get_docstring(child),
                    is_test=is_test,
                    route_path=route_path,
                    route_method=route_method,
                    component_id=component_id,
                ))
                _visit(child, qname)

            elif isinstance(child, ast.ClassDef):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                decorator_strs = [_decorator_str(d) for d in child.decorator_list]
                pydantic = _is_pydantic_model(child)
                symbols.append(CodeSymbol(
                    path=path,
                    qualified_name=qname,
                    kind="class",
                    start_line=child.lineno,
                    end_line=_end_line(child),
                    decorators=decorator_strs,
                    docstring=_get_docstring(child),
                    is_pydantic_model=pydantic,
                ))
                _visit(child, qname)

    _visit(tree, "")
    return symbols, imports, None


def index_snapshot_files(
    files: List[Tuple[str, bytes]],
) -> IndexResult:
    all_symbols: List[CodeSymbol] = []
    all_imports: List[ImportInfo] = []
    warnings: List[IndexWarning] = []

    for path, content in files:
        if not path.endswith(".py"):
            continue
        try:
            source = content.decode("utf-8", errors="replace")
        except Exception:
            warnings.append(IndexWarning(path=path, message="Cannot decode file as UTF-8"))
            continue

        syms, imps, warn = index_python_file(path, source)
        all_symbols.extend(syms)
        all_imports.extend(imps)
        if warn:
            warnings.append(warn)

    return IndexResult(symbols=all_symbols, imports=all_imports, warnings=warnings)
