"""Patch generation for approved probe points.

Creates a temporary git worktree from a pinned commit, instruments approved
symbols with @probe decorators using Python AST, and produces a reviewable
diff.  Never modifies the target repository's branch or working tree.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .git_ops import GitError, _run_git, _validate_repo_path


@dataclass
class ApprovedPoint:
    component_id: str
    path: str
    symbol: str
    recommended_mode: str  # "trace" | "shadow"
    line_start: int
    line_end: int


@dataclass
class PatchFile:
    path: str
    original: str
    patched: str


@dataclass
class PatchResult:
    worktree_path: str
    diff: str
    files: List[PatchFile]
    skipped: List[str]
    error: Optional[str] = None


def _has_probe_decorator(func_node: ast.AST) -> bool:
    decorator_list = getattr(func_node, "decorator_list", [])
    for dec in decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                name = dec.func.id
            elif isinstance(dec.func, ast.Attribute):
                name = dec.func.attr
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name == "probe":
            return True
    return False


def _has_probe_import(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "probe_agent" in node.module:
                for alias in node.names:
                    if alias.name == "probe":
                        return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "probe_agent" in alias.name:
                    return True
    return False


def _find_import_insert_line(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    last_import_line = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            last_import_line = max(last_import_line, end)
    return last_import_line


def _find_function_node(
    source: str, symbol: str
) -> Optional[Tuple[ast.AST, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    parts = symbol.split(".")

    def _search(node: ast.AST, remaining: List[str]) -> Optional[ast.AST]:
        if not remaining:
            return node
        target_name = remaining[0]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name == target_name:
                    if len(remaining) == 1:
                        return child
                    return _search(child, remaining[1:])
            elif isinstance(child, ast.ClassDef):
                if child.name == target_name:
                    if len(remaining) == 1:
                        return child
                    return _search(child, remaining[1:])
        return None

    result = _search(tree, parts)
    if result is None:
        return None
    dec_line = result.lineno
    if hasattr(result, "decorator_list") and result.decorator_list:
        dec_line = result.decorator_list[0].lineno
    return result, dec_line


def instrument_file(
    source: str,
    points: List[ApprovedPoint],
) -> Tuple[str, List[str]]:
    skipped = []
    lines = source.splitlines(keepends=True)

    insertions: Dict[int, str] = {}
    needs_import = not _has_probe_import(source)

    for point in sorted(points, key=lambda p: p.line_start, reverse=True):
        result = _find_function_node(source, point.symbol)
        if result is None:
            skipped.append(f"{point.symbol}: not found in AST")
            continue

        func_node, dec_line = result
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            skipped.append(f"{point.symbol}: not a function/async_function")
            continue

        if _has_probe_decorator(func_node):
            skipped.append(f"{point.symbol}: already has @probe decorator")
            continue

        insert_line_idx = dec_line - 1
        indent = ""
        if insert_line_idx < len(lines):
            existing_line = lines[insert_line_idx]
            indent = existing_line[: len(existing_line) - len(existing_line.lstrip())]

        decorator_text = f'{indent}@probe(component_id="{point.component_id}")\n'
        insertions[insert_line_idx] = decorator_text

    if not insertions:
        return source, skipped

    for line_idx in sorted(insertions.keys(), reverse=True):
        lines.insert(line_idx, insertions[line_idx])

    if needs_import:
        import_line = _find_import_insert_line(source)
        import_stmt = "from probe_agent import probe\n"
        if import_line == 0:
            lines.insert(0, import_stmt)
        else:
            adjusted = import_line
            for idx in sorted(insertions.keys()):
                if idx <= import_line:
                    adjusted += 1
            lines.insert(adjusted, "\n" + import_stmt)

    return "".join(lines), skipped


def create_worktree(
    repo_path: str,
    commit_sha: str,
    worktree_base: str,
) -> str:
    real_path = _validate_repo_path(repo_path)

    worktree_dir = os.path.join(worktree_base, f"probe-patch-{commit_sha[:12]}")
    if os.path.exists(worktree_dir):
        shutil.rmtree(worktree_dir)
    os.makedirs(worktree_dir, exist_ok=True)

    result = _run_git(real_path, [
        "worktree", "add", "--detach", worktree_dir, commit_sha,
    ], timeout=30)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Failed to create worktree: {stderr}")

    return worktree_dir


def cleanup_worktree(repo_path: str, worktree_path: str) -> None:
    try:
        real_path = _validate_repo_path(repo_path)
        _run_git(real_path, ["worktree", "remove", "--force", worktree_path], timeout=30)
    except (GitError, Exception):
        pass
    if os.path.exists(worktree_path):
        try:
            shutil.rmtree(worktree_path)
        except OSError:
            pass


def generate_patch(
    repo_path: str,
    commit_sha: str,
    approved_points: List[ApprovedPoint],
    worktree_base: str,
) -> PatchResult:
    if not approved_points:
        return PatchResult(
            worktree_path="",
            diff="",
            files=[],
            skipped=[],
            error="No approved probe points",
        )

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

    points_by_file: Dict[str, List[ApprovedPoint]] = {}
    for point in approved_points:
        points_by_file.setdefault(point.path, []).append(point)

    patch_files = []
    all_skipped = []

    try:
        for path, file_points in sorted(points_by_file.items()):
            full_path = os.path.join(worktree_path, path)
            if not os.path.isfile(full_path):
                all_skipped.append(f"{path}: file not found in worktree")
                continue
            normalized = os.path.normpath(full_path)
            if not normalized.startswith(os.path.normpath(worktree_path) + os.sep):
                all_skipped.append(f"{path}: path traversal detected")
                continue

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()

            patched, skipped = instrument_file(original, file_points)
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
        diff = diff_result.stdout.decode("utf-8", errors="replace") if diff_result.returncode == 0 else ""

    except Exception as exc:
        return PatchResult(
            worktree_path=worktree_path,
            diff="",
            files=patch_files,
            skipped=all_skipped,
            error=str(exc),
        )

    return PatchResult(
        worktree_path=worktree_path,
        diff=diff,
        files=patch_files,
        skipped=all_skipped,
    )
