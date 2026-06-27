"""Deterministic Python AST symbol extraction.

Parses Python source files from a snapshot and extracts module-level,
class-level, and function-level symbols.  Syntax errors in individual files
produce warnings instead of aborting the whole index.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml


def _hash_text(text: str) -> str:
    """Deterministic sha256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Source-anchored explanation metadata (Issue #54)
#
# Target repositories may embed a small, optional structured block inside a
# module / class / function docstring describing the symbol's role in the
# system.  The block is author-written (source-authored) and is copied
# verbatim into the index; probe-agent never infers meaning from free text and
# never writes this metadata back to the target repository.  See
# docs/project-intelligence.md for the documented vocabulary.
# ---------------------------------------------------------------------------

#: Marker line that opens the metadata block inside a docstring.
SOURCE_METADATA_MARKER = "probe-agent:"

#: Free-text string fields (copied verbatim, never interpreted).
_METADATA_STRING_FIELDS = {"role", "capability", "system_purpose", "probe_value"}

#: Free-form list-of-string fields.
_METADATA_LIST_FIELDS = {"consumers"}

#: Enumerated single-value fields constrained to explicit finite sets.
_METADATA_ENUM_FIELDS: Dict[str, set] = {
    "element_type": {
        "system",
        "core",
        "capability",
        "element",
        "supporting",
        "boundary",
    },
    "operation_kind": {
        "analysis",
        "read",
        "write",
        "mutation",
        "io",
        "orchestration",
        "validation",
        "other",
    },
}

#: Enumerated list-value fields whose every item must be in an explicit set.
_METADATA_ENUM_LIST_FIELDS: Dict[str, set] = {
    "state_effects": {
        "none",
        "database-read",
        "database-write",
        "network",
        "filesystem",
        "cache",
        "external-api",
        "queue",
    },
}

_METADATA_KNOWN_KEYS = (
    _METADATA_STRING_FIELDS
    | _METADATA_LIST_FIELDS
    | set(_METADATA_ENUM_FIELDS)
    | set(_METADATA_ENUM_LIST_FIELDS)
)


@dataclass
class SourceMetadata:
    """Author-written explanation metadata extracted from a docstring block.

    ``origin`` is always ``source_authored`` to keep these facts separate from
    reasoning-model interpretations.  ``start_line``/``end_line`` point at the
    block inside the pinned snapshot for review.
    """

    start_line: int
    end_line: int
    raw_block: str
    role: Optional[str] = None
    capability: Optional[str] = None
    element_type: Optional[str] = None
    system_purpose: Optional[str] = None
    operation_kind: Optional[str] = None
    consumers: List[str] = field(default_factory=list)
    state_effects: List[str] = field(default_factory=list)
    probe_value: Optional[str] = None
    origin: str = "source_authored"
    # sha256 of the extracted explanation block (Issue #55). A change signal
    # only; hash equality is not semantic equality.
    explanation_hash: Optional[str] = None


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
    source_metadata: Optional[SourceMetadata] = None
    # Source-hash provenance (Issue #55), computed from the pinned snapshot.
    # sha256 of the exact source span (signature + body, as committed).
    symbol_source_hash: Optional[str] = None
    # sha256 of a normalized AST structure with the docstring removed and
    # comments/formatting/line numbers excluded. A change signal for behavior.
    symbol_body_hash: Optional[str] = None


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


def _docstring_node(node: ast.AST) -> Optional[ast.Constant]:
    """Return the Constant node holding the symbol's docstring, if any."""
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value
    return None


def _leading_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _extract_metadata_block(raw_doc: str) -> Optional[Tuple[int, int, str]]:
    """Locate the ``probe-agent:`` block inside the raw docstring text.

    Returns ``(marker_line_index, last_block_line_index, dedented_block)`` using
    0-based indices into ``raw_doc`` split on ``\\n``.  Indices are relative to
    the start of the string literal so callers can offset by the literal's
    source line.  Returns ``None`` when no marker is present.
    """
    lines = raw_doc.split("\n")
    for idx, line in enumerate(lines):
        if line.strip() != SOURCE_METADATA_MARKER:
            continue
        marker_indent = _leading_indent(line)
        block_lines: List[str] = []
        last_idx = idx
        for offset in range(idx + 1, len(lines)):
            candidate = lines[offset]
            if candidate.strip() == "":
                # Blank lines are tolerated only between block entries.
                block_lines.append("")
                continue
            if _leading_indent(candidate) <= marker_indent:
                break
            block_lines.append(candidate)
            last_idx = offset
        # Trim trailing blank lines that were optimistically appended.
        while block_lines and block_lines[-1].strip() == "":
            block_lines.pop()
        if not block_lines:
            return idx, idx, ""
        common = min(
            _leading_indent(bl) for bl in block_lines if bl.strip() != ""
        )
        dedented = "\n".join(bl[common:] if bl.strip() else "" for bl in block_lines)
        return idx, last_idx, dedented
    return None


def _validate_metadata_fields(
    parsed: dict,
) -> Tuple[Dict[str, object], List[str]]:
    """Validate parsed key/value pairs against the explicit vocabulary.

    Returns ``(valid_fields, warnings)``.  Unknown keys, wrong types, and
    out-of-set enum values become deterministic warnings; valid fields are
    retained.  Free text is never interpreted.
    """
    valid: Dict[str, object] = {}
    warnings: List[str] = []

    for key, value in parsed.items():
        if not isinstance(key, str) or key not in _METADATA_KNOWN_KEYS:
            warnings.append(f"unknown metadata key {key!r}")
            continue
        if key in _METADATA_STRING_FIELDS:
            if isinstance(value, str) and value.strip():
                valid[key] = value.strip()
            else:
                warnings.append(f"field {key!r} must be a non-empty string")
        elif key in _METADATA_LIST_FIELDS:
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                valid[key] = [v.strip() for v in value if v.strip()]
            else:
                warnings.append(f"field {key!r} must be a list of strings")
        elif key in _METADATA_ENUM_FIELDS:
            allowed = _METADATA_ENUM_FIELDS[key]
            if isinstance(value, str) and value in allowed:
                valid[key] = value
            else:
                warnings.append(
                    f"field {key!r} must be one of {sorted(allowed)}"
                )
        elif key in _METADATA_ENUM_LIST_FIELDS:
            allowed = _METADATA_ENUM_LIST_FIELDS[key]
            if isinstance(value, list) and all(v in allowed for v in value):
                valid[key] = list(value)
            else:
                warnings.append(
                    f"field {key!r} must be a list of {sorted(allowed)}"
                )

    return valid, warnings


def _parse_source_metadata(
    node: ast.AST,
) -> Tuple[Optional[SourceMetadata], List[str]]:
    """Extract and validate the ``probe-agent:`` block for a single symbol.

    Returns ``(metadata_or_none, warning_messages)``.  Parsing never executes
    target code (the docstring is already a literal) and never fails the
    surrounding index.
    """
    doc_node = _docstring_node(node)
    if doc_node is None:
        return None, []
    raw_doc = doc_node.value
    if SOURCE_METADATA_MARKER not in raw_doc:
        return None, []

    located = _extract_metadata_block(raw_doc)
    if located is None:
        return None, []
    marker_idx, last_idx, block_text = located

    doc_start = getattr(doc_node, "lineno", None)
    if doc_start is None:
        return None, ["probe-agent metadata: cannot resolve source location"]
    block_start = doc_start + marker_idx
    block_end = doc_start + last_idx

    if not block_text.strip():
        return None, ["probe-agent metadata: empty block"]

    try:
        parsed = yaml.safe_load(block_text)
    except yaml.YAMLError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "parse error"
        return None, [f"probe-agent metadata: invalid YAML ({first})"]

    if not isinstance(parsed, dict):
        return None, ["probe-agent metadata: block must be a key/value mapping"]

    valid, field_warnings = _validate_metadata_fields(parsed)
    warnings = [f"probe-agent metadata: {w}" for w in field_warnings]

    if not valid:
        warnings.append("probe-agent metadata: no recognized fields")
        return None, warnings

    metadata = SourceMetadata(
        start_line=block_start,
        end_line=block_end,
        raw_block=block_text,
        role=valid.get("role"),
        capability=valid.get("capability"),
        element_type=valid.get("element_type"),
        system_purpose=valid.get("system_purpose"),
        operation_kind=valid.get("operation_kind"),
        consumers=list(valid.get("consumers", [])),
        state_effects=list(valid.get("state_effects", [])),
        probe_value=valid.get("probe_value"),
        explanation_hash=_hash_text(block_text),
    )
    return metadata, warnings


def _symbol_source_hash(source_lines: List[str], start_line: int, end_line: int) -> str:
    """sha256 of the symbol's exact source span (1-based inclusive lines)."""
    span = "".join(source_lines[start_line - 1 : end_line])
    return _hash_text(span)


def _symbol_body_hash(node: ast.AST) -> str:
    """sha256 of a normalized AST structure for a module/class/function.

    The leading docstring is stripped and ``ast.dump`` is taken without
    attributes, so comments, docstrings, formatting, and line numbers do not
    affect the hash; structural code changes do.  This is a change signal for
    implementation behavior, not a proof of semantic equivalence.
    """
    node_copy = copy.deepcopy(node)
    body = getattr(node_copy, "body", None)
    if body:
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node_copy.body = body[1:]
    dumped = ast.dump(node_copy, annotate_fields=True, include_attributes=False)
    return _hash_text(dumped)


def _is_test_function(name: str, path: str) -> bool:
    return name.startswith("test_") or name.endswith("_test")


def _end_line(node: ast.AST) -> int:
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        return node.end_lineno
    return getattr(node, "lineno", 0)


@dataclass
class FileIndexResult:
    symbols: List[CodeSymbol]
    imports: List[ImportInfo]
    warnings: List[IndexWarning]


def index_python_file(
    path: str, source: str
) -> Tuple[List[CodeSymbol], List[ImportInfo], Optional[IndexWarning]]:
    """Backward-compatible wrapper returning a single optional warning.

    Use :func:`index_python_file_full` to receive every warning, including
    per-symbol source-metadata warnings.
    """
    result = index_python_file_full(path, source)
    return result.symbols, result.imports, result.warnings[0] if result.warnings else None


def index_python_file_full(path: str, source: str) -> FileIndexResult:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return FileIndexResult([], [], [IndexWarning(path=path, message=f"SyntaxError: {exc}")])

    symbols: List[CodeSymbol] = []
    imports: List[ImportInfo] = []
    warnings: List[IndexWarning] = []
    source_lines = source.splitlines(keepends=True)

    def _attach_metadata(node: ast.AST, symbol: CodeSymbol) -> None:
        metadata, messages = _parse_source_metadata(node)
        symbol.source_metadata = metadata
        for message in messages:
            warnings.append(
                IndexWarning(path=path, message=f"{symbol.qualified_name}: {message}")
            )

    def _attach_hashes(node: ast.AST, symbol: CodeSymbol) -> None:
        # Decorators are part of the callable's externally observable role
        # (especially for API entrypoints), so the exact source span starts at
        # the first decorator line when present. ``symbol.start_line`` stays on
        # the def/class line for display and downstream line-range consumers.
        span_start = symbol.start_line
        decorators = getattr(node, "decorator_list", None)
        if decorators:
            span_start = min(span_start, min(d.lineno for d in decorators))
        symbol.symbol_source_hash = _symbol_source_hash(
            source_lines, span_start, symbol.end_line
        )
        symbol.symbol_body_hash = _symbol_body_hash(node)

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
    module_symbol = CodeSymbol(
        path=path,
        qualified_name=module_name,
        kind="module",
        start_line=1,
        end_line=module_end_line,
        imports=import_names,
        docstring=_get_docstring(tree),
    )
    _attach_metadata(tree, module_symbol)
    _attach_hashes(tree, module_symbol)
    symbols.append(module_symbol)

    def _visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                decorator_strs = [_decorator_str(d) for d in child.decorator_list]
                route_path, route_method = _extract_route_info(child.decorator_list)
                component_id = _extract_component_id(child.decorator_list)
                is_test = _is_test_function(child.name, path)
                kind = "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function"
                func_symbol = CodeSymbol(
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
                )
                _attach_metadata(child, func_symbol)
                _attach_hashes(child, func_symbol)
                symbols.append(func_symbol)
                _visit(child, qname)

            elif isinstance(child, ast.ClassDef):
                qname = f"{prefix}.{child.name}" if prefix else child.name
                decorator_strs = [_decorator_str(d) for d in child.decorator_list]
                pydantic = _is_pydantic_model(child)
                class_symbol = CodeSymbol(
                    path=path,
                    qualified_name=qname,
                    kind="class",
                    start_line=child.lineno,
                    end_line=_end_line(child),
                    decorators=decorator_strs,
                    docstring=_get_docstring(child),
                    is_pydantic_model=pydantic,
                )
                _attach_metadata(child, class_symbol)
                _attach_hashes(child, class_symbol)
                symbols.append(class_symbol)
                _visit(child, qname)

    _visit(tree, "")
    return FileIndexResult(symbols=symbols, imports=imports, warnings=warnings)


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

        result = index_python_file_full(path, source)
        all_symbols.extend(result.symbols)
        all_imports.extend(result.imports)
        warnings.extend(result.warnings)

    return IndexResult(symbols=all_symbols, imports=all_imports, warnings=warnings)
