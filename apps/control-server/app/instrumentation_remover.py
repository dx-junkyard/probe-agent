"""Pre-release probe removal patch generation (Issue #168).

Removes ``@probe(...)`` decorators from selected symbols inside an isolated
git worktree pinned to a snapshot commit and produces a reviewable diff.
Everything here is a deterministic structural edit: which decorator lines to
delete and whether the ``from probe_agent import probe`` import is still used
are direct AST facts, not inferences. The target working tree is only touched
through the separate, explicitly-confirmed apply boundary shared with
instrumentation patches.

probe-agent:
  role: Deterministic removal of probe instrumentation as a reviewable diff
  capability: probe-pattern-lifecycle
  element_type: core
  operation_kind: transformation
  consumers: [control-server]
  state_effects: []
  probe_value: Verify probe decorators and stale imports are removed without touching unrelated code and the target repository stays unchanged until explicit apply.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .git_ops import GitError, _run_git
from .patch_generator import (
    CleanupResult,
    PatchFile,
    PatchResult,
    _find_function_node,
    cleanup_worktree,
    create_worktree,
)


@dataclass
class RemovalPoint:
    path: str
    symbol: str


def _decorator_base_name(dec: ast.expr) -> str:
    node = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _probe_decorator_spans(func_node: ast.AST) -> List[Tuple[int, int]]:
    """1-based inclusive line spans of ``@probe`` decorators on a function."""
    spans = []
    for dec in getattr(func_node, "decorator_list", []):
        if _decorator_base_name(dec) == "probe":
            end = getattr(dec, "end_lineno", dec.lineno) or dec.lineno
            spans.append((dec.lineno, end))
    return spans


def _probe_name_still_used(tree: ast.Module) -> bool:
    """True when the name ``probe`` is referenced outside its own import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "probe":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "probe":
            return True
    return False


def _probe_import_edits(source: str) -> Tuple[Set[int], Dict[int, str]]:
    """Line deletions/rewrites that drop an unused ``probe`` import.

    Only single-line ``from probe_agent... import ...`` statements are edited;
    anything more complex is left in place (a stale import is harmless while a
    broken rewrite is not).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), {}
    if _probe_name_still_used(tree):
        return set(), {}

    delete_lines: Set[int] = set()
    rewrite_lines: Dict[int, str] = {}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not (node.module and "probe_agent" in node.module):
            continue
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        if end != node.lineno:
            continue
        probe_aliases = [
            a for a in node.names
            if a.name == "probe" and a.asname in (None, "probe")
        ]
        if not probe_aliases:
            continue
        remaining = [a for a in node.names if a not in probe_aliases]
        if not remaining:
            delete_lines.add(node.lineno)
        else:
            names = ", ".join(
                a.name if a.asname is None else f"{a.name} as {a.asname}"
                for a in remaining
            )
            rewrite_lines[node.lineno] = f"from {node.module} import {names}\n"
    return delete_lines, rewrite_lines


def remove_instrumentation(
    source: str,
    points: List[RemovalPoint],
) -> Tuple[str, List[str]]:
    """Remove @probe decorators for the given symbols from one file."""
    skipped: List[str] = []
    delete_lines: Set[int] = set()

    for point in points:
        result = _find_function_node(source, point.symbol)
        if result is None:
            skipped.append(f"{point.symbol}: not found in AST")
            continue
        func_node, _dec_line = result
        spans = _probe_decorator_spans(func_node)
        if not spans:
            skipped.append(f"{point.symbol}: no @probe decorator present")
            continue
        for start, end in spans:
            delete_lines.update(range(start, end + 1))

    if not delete_lines:
        return source, skipped

    lines = source.splitlines(keepends=True)
    patched_lines = [
        line for idx, line in enumerate(lines, start=1)
        if idx not in delete_lines
    ]
    patched = "".join(patched_lines)

    import_deletes, import_rewrites = _probe_import_edits(patched)
    if import_deletes or import_rewrites:
        lines = patched.splitlines(keepends=True)
        out = []
        for idx, line in enumerate(lines, start=1):
            if idx in import_deletes:
                continue
            if idx in import_rewrites:
                out.append(import_rewrites[idx])
                continue
            out.append(line)
        patched = "".join(out)

    return patched, skipped


def generate_removal_patch(
    repo_path: str,
    commit_sha: str,
    points: List[RemovalPoint],
    worktree_base: str,
) -> PatchResult:
    """Produce a reviewable removal diff from an isolated worktree."""
    if not points:
        return PatchResult(
            worktree_path="",
            diff="",
            files=[],
            skipped=[],
            error="No probe points to remove",
        )

    cleanup: Optional[CleanupResult] = None
    try:
        worktree_path = create_worktree(repo_path, commit_sha, worktree_base)
    except GitError as exc:
        return PatchResult(
            worktree_path="",
            diff="",
            files=[],
            skipped=[],
            error=str(exc),
        )

    points_by_file: Dict[str, List[RemovalPoint]] = {}
    for point in points:
        points_by_file.setdefault(point.path, []).append(point)

    patch_files: List[PatchFile] = []
    all_skipped: List[str] = []

    try:
        for path, file_points in sorted(points_by_file.items()):
            full_path = os.path.join(worktree_path, path)
            normalized = os.path.realpath(full_path)
            worktree_real = os.path.realpath(worktree_path)
            if (
                os.path.islink(full_path)
                or not normalized.startswith(worktree_real + os.sep)
            ):
                all_skipped.append(f"{path}: path traversal or symlink detected")
                continue
            if not os.path.isfile(full_path):
                all_skipped.append(f"{path}: file not found in worktree")
                continue

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()

            patched, skipped = remove_instrumentation(original, file_points)
            all_skipped.extend(skipped)

            if patched != original:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(patched)
                patch_files.append(PatchFile(
                    path=path,
                    original=original,
                    patched=patched,
                ))

        diff_result = _run_git(worktree_path, ["diff"], timeout=30)
        diff = (
            diff_result.stdout.decode("utf-8", errors="replace")
            if diff_result.returncode == 0
            else ""
        )
    except Exception as exc:
        error = str(exc)
        diff = ""
    else:
        error = None
    finally:
        cleanup = cleanup_worktree(repo_path, worktree_path)

    return PatchResult(
        worktree_path=worktree_path,
        diff=diff,
        files=patch_files,
        skipped=all_skipped,
        error=error,
        cleanup_state=cleanup.state,
        cleanup_error=cleanup.error,
    )
