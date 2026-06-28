"""AST-based docstring writer for probe-agent: metadata blocks.

Inserts or updates a ``probe-agent:`` YAML block inside a Python symbol's
docstring using AST node analysis.  Operates on literal source text (never
executes target code) and is idempotent — re-materializing an
already-applied block produces the same output.

The generated block round-trips through the extractor in
``code_indexer._extract_metadata_block`` / ``_parse_source_metadata``.
"""

from __future__ import annotations

import ast
import os
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class DocstringEdit:
    path: str
    qualified_name: str
    original: str
    patched: str


@dataclass
class MetadataValues:
    role: Optional[str] = None
    capability: Optional[str] = None
    system_purpose: Optional[str] = None
    probe_value: Optional[str] = None
    element_type: Optional[str] = None
    operation_kind: Optional[str] = None
    consumers: Optional[List[str]] = None
    state_effects: Optional[List[str]] = None


def _find_node(source: str, symbol: str, *, is_module: bool = False) -> Optional[ast.AST]:
    """Find an AST node by dotted symbol path (no module prefix).

    When *is_module* is True, return the ``ast.Module`` node itself.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    if is_module:
        return tree

    parts = symbol.split(".")

    def _search(node: ast.AST, remaining: List[str]) -> Optional[ast.AST]:
        if not remaining:
            return node
        target = remaining[0]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.name == target:
                    if len(remaining) == 1:
                        return child
                    return _search(child, remaining[1:])
        return None

    return _search(tree, parts)


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar if it contains characters that could break parsing."""
    if not value:
        return '""'
    needs_quoting = False
    for ch in (':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-',
               '<', '>', '=', '!', '%', '@', '`', '"', "'", '\n', '\r'):
        if ch in value:
            needs_quoting = True
            break
    if not needs_quoting:
        stripped = value.strip()
        if stripped != value:
            needs_quoting = True
        elif stripped.lower() in ('true', 'false', 'yes', 'no', 'null', 'on', 'off'):
            needs_quoting = True
        else:
            try:
                float(stripped)
                needs_quoting = True
            except ValueError:
                pass
    if not needs_quoting:
        return value
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def _render_block(values: MetadataValues, indent: str) -> str:
    """Render the probe-agent: YAML block with the given indentation."""
    lines = [f"{indent}probe-agent:"]
    if values.role is not None:
        lines.append(f"{indent}  role: {_yaml_scalar(values.role)}")
    if values.capability is not None:
        lines.append(f"{indent}  capability: {_yaml_scalar(values.capability)}")
    if values.system_purpose is not None:
        lines.append(f"{indent}  system_purpose: {_yaml_scalar(values.system_purpose)}")
    if values.probe_value is not None:
        lines.append(f"{indent}  probe_value: {_yaml_scalar(values.probe_value)}")
    if values.element_type is not None:
        lines.append(f"{indent}  element_type: {values.element_type}")
    if values.operation_kind is not None:
        lines.append(f"{indent}  operation_kind: {values.operation_kind}")
    if values.consumers is not None and values.consumers:
        items = ", ".join(values.consumers)
        lines.append(f"{indent}  consumers: [{items}]")
    if values.state_effects is not None and values.state_effects:
        items = ", ".join(values.state_effects)
        lines.append(f"{indent}  state_effects: [{items}]")
    return "\n".join(lines)


def _get_docstring_info(
    node: ast.AST,
) -> Optional[Tuple[int, int, str, str]]:
    """Return (start_line_1based, end_line_1based, raw_content, quote_style).

    Works for module, class, function, async_function nodes.
    """
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if not isinstance(first, ast.Expr):
        return None
    val = first.value
    if isinstance(val, ast.Constant) and isinstance(val.value, str):
        return (first.lineno, first.end_lineno, val.value,
                '"""')
    return None


def _detect_quote_style(source_lines: List[str], start_line_0: int) -> str:
    """Detect whether the docstring uses triple-double or triple-single quotes."""
    line = source_lines[start_line_0] if start_line_0 < len(source_lines) else ""
    stripped = line.lstrip()
    if stripped.startswith("'''") or stripped.startswith("r'''"):
        return "'''"
    return '"""'


def _get_docstring_indent(source_lines: List[str], node: ast.AST) -> str:
    """Get the indentation for docstring content (body indent of the node)."""
    if isinstance(node, ast.Module):
        return ""
    body = getattr(node, "body", None)
    if body:
        first = body[0]
        line_idx = first.lineno - 1
        if line_idx < len(source_lines):
            line = source_lines[line_idx]
            return line[: len(line) - len(line.lstrip())]
    node_line_idx = node.lineno - 1
    if node_line_idx < len(source_lines):
        line = source_lines[node_line_idx]
        node_indent = line[: len(line) - len(line.lstrip())]
        return node_indent + "    "
    return "    "


def _strip_module_prefix(qualified_name: str, path: str) -> str:
    """Remove the module-name prefix from a qualified_name to get the in-file symbol.

    Returns the empty string ``""`` when the qualified_name refers to the
    module itself (no in-file symbol), signaling that the caller should
    target the module-level docstring.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = qualified_name.split(".")
    if parts and parts[0] == stem:
        return ".".join(parts[1:]) if len(parts) > 1 else ""
    return qualified_name


def write_metadata_to_source(
    source: str,
    symbol: str,
    values: MetadataValues,
) -> Tuple[str, Optional[str]]:
    """Insert or update a ``probe-agent:`` block in *symbol*'s docstring.

    *symbol* is the in-file dotted path (no module prefix).
    An empty string targets the module-level docstring.
    Returns ``(new_source, error_or_none)``.
    """
    is_module = symbol == ""
    node = _find_node(source, symbol, is_module=is_module)
    if node is None:
        label = symbol or "<module>"
        return source, f"{label}: not found in AST"

    source_lines = source.split("\n")
    info = _get_docstring_info(node)

    if info is not None:
        return _update_existing_docstring(source_lines, node, info, values)
    else:
        return _insert_new_docstring(source_lines, node, values)


def _update_existing_docstring(
    source_lines: List[str],
    node: ast.AST,
    info: Tuple[int, int, str, str],
    values: MetadataValues,
) -> Tuple[str, Optional[str]]:
    """Update or append probe-agent: block in an existing docstring."""
    start_line_1, end_line_1, raw_content, _ = info
    start_idx = start_line_1 - 1
    end_idx = end_line_1 - 1

    quote_style = _detect_quote_style(source_lines, start_idx)

    doc_indent = _get_docstring_indent(source_lines, node)

    from .code_indexer import _extract_metadata_block
    existing_block = _extract_metadata_block(raw_content)

    if existing_block is not None:
        marker_rel, last_rel, _ = existing_block
        doc_lines = raw_content.split("\n")
        before = doc_lines[:marker_rel]
        after = doc_lines[last_rel + 1:]

        new_block = _render_block(values, doc_indent)
        new_block_lines = new_block.split("\n")

        new_doc_lines = before + new_block_lines + after
        new_raw = "\n".join(new_doc_lines)
    else:
        if raw_content.rstrip():
            new_raw = raw_content.rstrip("\n") + "\n\n" + _render_block(values, doc_indent) + "\n" + doc_indent
        else:
            new_raw = _render_block(values, doc_indent) + "\n" + doc_indent

    new_docstring_source = f'{doc_indent}{quote_style}{new_raw}{quote_style}'

    rebuilt = (
        source_lines[:start_idx]
        + new_docstring_source.split("\n")
        + source_lines[end_idx + 1:]
    )
    return "\n".join(rebuilt), None


def _insert_new_docstring(
    source_lines: List[str],
    node: ast.AST,
    values: MetadataValues,
) -> Tuple[str, Optional[str]]:
    """Insert a new docstring with probe-agent: block after the def/class line."""
    body = getattr(node, "body", None)
    if not body:
        label = getattr(node, "name", "<module>" if isinstance(node, ast.Module) else "?")
        return "\n".join(source_lines), f"{label}: empty body"

    if isinstance(node, ast.Module):
        block = _render_block(values, "")
        docstring = f'"""\n{block}\n"""'
        rebuilt = docstring.split("\n") + [""] + source_lines
        return "\n".join(rebuilt), None

    first_body_line = body[0].lineno - 1
    node_indent = ""
    node_line_idx = node.lineno - 1
    if node_line_idx < len(source_lines):
        line = source_lines[node_line_idx]
        node_indent = line[: len(line) - len(line.lstrip())]
    doc_indent = node_indent + "    "

    block = _render_block(values, doc_indent)
    docstring = f'{doc_indent}"""\n{block}\n{doc_indent}"""'

    rebuilt = (
        source_lines[:first_body_line]
        + docstring.split("\n")
        + source_lines[first_body_line:]
    )
    return "\n".join(rebuilt), None


def apply_docstring_edits(
    source: str,
    edits: List[Tuple[str, MetadataValues]],
) -> Tuple[str, List[str]]:
    """Apply multiple docstring edits to a single source file.

    *edits* is a list of ``(in_file_symbol, metadata_values)`` pairs.
    Returns ``(new_source, skipped_messages)``.

    Edits are applied one at a time; each subsequent edit re-parses
    the (possibly modified) source to keep AST offsets correct.
    """
    skipped: List[str] = []
    for symbol, values in edits:
        source, err = write_metadata_to_source(source, symbol, values)
        if err:
            skipped.append(err)
    return source, skipped
