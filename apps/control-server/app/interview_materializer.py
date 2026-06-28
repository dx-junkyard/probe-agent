"""Worktree materialization for approved interview proposals.

Combines docstring metadata edits and ``@probe`` instrumentation from an
approved set (#70) into a single isolated worktree, produces one unified
diff covering both, and cleans up.  The target repository's tracked branches
are never written to.

Reuses ``patch_generator`` worktree mechanics and extends them with the
``docstring_writer`` for ``probe-agent:`` blocks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .docstring_writer import (
    MetadataValues,
    apply_docstring_edits,
    _strip_module_prefix,
)
from .patch_generator import (
    ApprovedPoint,
    CleanupResult,
    create_worktree,
    cleanup_worktree,
    instrument_file,
)
from .git_ops import _run_git, _validate_repo_path


@dataclass
class MaterializationItem:
    path: str
    qualified_name: str
    metadata: MetadataValues
    component_id: str
    recommended_mode: str
    line_start: int
    line_end: int


@dataclass
class MaterializationResult:
    worktree_path: str
    diff: str
    files_changed: int
    skipped: List[str]
    error: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None


def materialize_approved_set(
    repo_path: str,
    commit_sha: str,
    items: List[MaterializationItem],
    worktree_base: str,
) -> MaterializationResult:
    """Create a worktree, apply docstring + probe edits, and return the diff."""
    if not items:
        return MaterializationResult(
            worktree_path="",
            diff="",
            files_changed=0,
            skipped=[],
            error="No approved items to materialize",
        )

    real_path = _validate_repo_path(repo_path)

    try:
        worktree_path = create_worktree(real_path, commit_sha, worktree_base)
    except Exception as exc:
        return MaterializationResult(
            worktree_path="",
            diff="",
            files_changed=0,
            skipped=[],
            error=str(exc),
        )

    all_skipped: List[str] = []
    files_changed = 0
    cleanup: Optional[CleanupResult] = None

    try:
        items_by_file: Dict[str, List[MaterializationItem]] = {}
        for item in items:
            items_by_file.setdefault(item.path, []).append(item)

        for path, file_items in sorted(items_by_file.items()):
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
                source = f.read()

            original = source

            doc_edits: List[Tuple[str, MetadataValues]] = []
            probe_points: List[ApprovedPoint] = []

            for item in file_items:
                in_file_symbol = _strip_module_prefix(item.qualified_name, path)
                doc_edits.append((in_file_symbol, item.metadata))
                probe_points.append(ApprovedPoint(
                    component_id=item.component_id,
                    path=path,
                    symbol=in_file_symbol,
                    recommended_mode=item.recommended_mode,
                    line_start=item.line_start,
                    line_end=item.line_end,
                ))

            source, doc_skipped = apply_docstring_edits(source, doc_edits)
            all_skipped.extend(f"{path}: {s}" for s in doc_skipped)

            source, probe_skipped = instrument_file(source, probe_points)
            all_skipped.extend(f"{path}: {s}" for s in probe_skipped)

            if source != original:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(source)
                files_changed += 1

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
        cleanup = cleanup_worktree(real_path, worktree_path)

    return MaterializationResult(
        worktree_path=worktree_path,
        diff=diff,
        files_changed=files_changed,
        skipped=all_skipped,
        error=error,
        cleanup_state=cleanup.state if cleanup else "not_attempted",
        cleanup_error=cleanup.error if cleanup else None,
    )
