"""Deterministic, finite-set/structural guards for the publish job state
machine (Issue #216, sub-task 3).

Everything here is Principle 6 territory: branch naming/validation, push
target checks, diff file-path extraction/validation, and commit/PR text
templating are all structural checks or fixed templates -- no reasoning
model, no free-text interpretation. `probe_job.py` calls these guards before
every commit/push/PR-creating git or GitHub API operation.

Push safety is fail-closed on purpose: `GIT_ALLOW_DIRECT_PUSH` and
`GIT_ALLOW_FORCE_PUSH` are read but MVP never honors a non-"false" value --
direct pushes to the base/default branch and force pushes are always denied,
regardless of configuration (Principle 5/8: only a server-generated
``probe/``-prefixed branch may ever be pushed to, and never with --force).
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import List, Optional

from . import repo_manager
from .repo_manager import RepoManagerError

_BRANCH_SAFE_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

# Explicit, finite denylist of secret-candidate filenames (Principle 6: exact
# safety denylist match, not a heuristic). Mirrors git_ops.DEFAULT_EXCLUDE's
# spirit but is evaluated against the *patch diff's* file list, not a
# repository snapshot.
_SECRET_DENYLIST_GLOBS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials*",
    ".netrc",
]

_WORKFLOWS_PREFIX = (".github", "workflows")


class PublishGuardError(RuntimeError):
    pass


# --- configuration -----------------------------------------------------------


def _env_flag(name: str) -> str:
    return os.getenv(name, "").strip().lower()


def branch_prefix() -> str:
    return os.getenv("GIT_BRANCH_PREFIX", "probe/").strip() or "probe/"


def allow_workflow_changes() -> bool:
    return _env_flag("GIT_ALLOW_WORKFLOW_CHANGES") in ("1", "true", "yes", "on")


def bot_name() -> str:
    return os.getenv("GITHUB_APP_BOT_NAME", "probe-agent[bot]").strip() or "probe-agent[bot]"


def bot_email() -> str:
    return (
        os.getenv("GITHUB_APP_BOT_EMAIL", "probe-agent[bot]@users.noreply.github.com").strip()
        or "probe-agent[bot]@users.noreply.github.com"
    )


def assert_no_unsafe_push_config() -> None:
    """Fail closed if the deployment tries to opt into direct/force push.

    Neither is implemented in this MVP: the value is read only so a
    misconfigured deployment gets an explicit, immediate error instead of a
    silently-ignored setting (Principle 6/7 spirit -- no silent fallback).
    """
    for name in ("GIT_ALLOW_DIRECT_PUSH", "GIT_ALLOW_FORCE_PUSH"):
        raw = _env_flag(name)
        if raw and raw not in ("false", "0", "no", "off"):
            raise PublishGuardError(
                f"{name} is not supported in this MVP; direct/force push is always denied"
            )


# --- branch naming -------------------------------------------------------


def validate_branch_name(name: str) -> str:
    if not isinstance(name, str) or not name or ".." in name:
        raise PublishGuardError("Invalid branch name")
    if not _BRANCH_SAFE_RE.match(name):
        raise PublishGuardError("Invalid branch name")
    for part in name.split("/"):
        if not part or part.startswith("-"):
            raise PublishGuardError("Invalid branch name")
    try:
        repo_manager._validate_branch(name)
    except RepoManagerError as exc:
        raise PublishGuardError(str(exc)) from exc
    return name


def generate_branch_name(job_id: int, base_commit_sha: str) -> str:
    """Server-generated only: callers never accept a branch name from a
    request body. ``{prefix}job-{job_id}-{base_commit_sha[:8]}``."""
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
        raise PublishGuardError("Invalid job id")
    if not isinstance(base_commit_sha, str) or len(base_commit_sha) < 8:
        raise PublishGuardError("Invalid base commit sha")
    prefix = branch_prefix()
    name = f"{prefix}job-{job_id}-{base_commit_sha[:8]}"
    return validate_branch_name(name)


def validate_push_target(branch: str, base_branch: str, default_branch: Optional[str]) -> None:
    """Refuse to push to the base branch or the connection's default
    branch. Combined with `assert_no_unsafe_push_config`, this is the whole
    "no direct push" guarantee -- there is no configuration value that
    bypasses it in this MVP."""
    validate_branch_name(branch)
    if branch == base_branch:
        raise PublishGuardError("Refusing to push directly to the base branch")
    if default_branch and branch == default_branch:
        raise PublishGuardError(
            "Refusing to push directly to the repository default branch"
        )


# --- diff file-path extraction and staging validation -----------------------


def extract_diff_paths(diff: str) -> List[str]:
    """Structural parse of a unified `git diff` for the touched file paths.

    Reads only ``--- a/<path>`` / ``+++ b/<path>`` header lines (ignoring
    ``/dev/null``), which is the fixed grammar `git diff` / `git apply`
    already produce and consume -- not free-text interpretation.
    """
    paths: List[str] = []
    seen = set()
    for line in diff.splitlines():
        path = None
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
        elif line.startswith("--- a/"):
            path = line[len("--- a/") :]
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _is_secret_candidate(basename: str) -> bool:
    return any(fnmatch.fnmatch(basename, pattern) for pattern in _SECRET_DENYLIST_GLOBS)


def validate_patch_file_path(
    path: str, worktree_path: str, *, allow_workflow_changes: bool
) -> str:
    """Validate a single patch-diff file path is safe to `git add` inside
    `worktree_path`: no traversal/absolute path, not under `.git/`, not a
    workflow file unless explicitly allowed, not a secret-candidate
    filename, resolves inside the worktree, and is not a (newly added)
    symlink."""
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise PublishGuardError(f"Invalid patch file path: {path!r}")

    normalized = os.path.normpath(path)
    if normalized == "." or normalized.startswith("..") or normalized.split("/")[0] == "..":
        raise PublishGuardError(f"Patch file path escapes the worktree: {path!r}")

    parts = normalized.split("/")
    if ".git" in parts:
        raise PublishGuardError(f"Patch file path touches .git/: {path!r}")

    if (
        len(parts) >= 2
        and (parts[0], parts[1]) == _WORKFLOWS_PREFIX
        and not allow_workflow_changes
    ):
        raise PublishGuardError(
            f"Patch file path touches .github/workflows/ (GIT_ALLOW_WORKFLOW_CHANGES=false): {path!r}"
        )

    if _is_secret_candidate(parts[-1]):
        raise PublishGuardError(f"Patch file path matches the secret-candidate denylist: {path!r}")

    worktree_real = os.path.realpath(worktree_path)
    full_path = os.path.join(worktree_path, normalized)
    full_real = os.path.realpath(full_path)
    if full_real != worktree_real and not full_real.startswith(worktree_real + os.sep):
        raise PublishGuardError(f"Patch file path escapes the worktree: {path!r}")

    if os.path.islink(full_path):
        raise PublishGuardError(f"Patch file path is a symlink: {path!r}")

    return normalized


# --- commit / PR text templates (fixed structure, not free text) ------------


def build_commit_message(
    *, objective: str, patch_id: int, base_commit_sha: str, system_id: int
) -> str:
    first_line = (objective or "").strip().splitlines()[0].strip() if (objective or "").strip() else ""
    summary = first_line or f"apply probe patch {patch_id}"
    summary = summary[:72]
    return (
        f"chore(probe): {summary}\n"
        "\n"
        f"Probe-Agent-Patch-Id: {patch_id}\n"
        f"Probe-Agent-Snapshot: {base_commit_sha}\n"
        "Probe-Agent-Validation: passed\n"
        f"Probe-Agent-System-Id: {system_id}\n"
    )


def build_pr_title(objective: str, patch_id: int) -> str:
    first_line = (objective or "").strip().splitlines()[0].strip() if (objective or "").strip() else ""
    summary = first_line or f"Probe patch {patch_id}"
    return f"chore(probe): {summary}"[:120]


def build_pr_body(
    *,
    patch_id: int,
    base_commit_sha: str,
    validation_summary: dict,
    requested_by: Optional[str],
    approved_by: Optional[str],
) -> str:
    def _yn(value: Optional[bool]) -> str:
        return "passed" if value else "unknown"

    baseline_ok = (validation_summary or {}).get("baseline", {}).get("overall_success")
    probed_ok = (validation_summary or {}).get("probed", {}).get("overall_success")
    lines = [
        "Automated by probe-agent's GitHub App publish workflow (Issue #216).",
        "",
        f"- Probe Patch: #{patch_id}",
        f"- Base commit: `{base_commit_sha}`",
        f"- Validation: baseline={_yn(baseline_ok)}, probed={_yn(probed_ok)}",
        f"- Requested by: {requested_by or 'unknown'}",
        f"- Approved by: {approved_by or 'unknown'}",
        "",
        "probe-agent does not merge or close this pull request -- a human reviews and merges it.",
    ]
    return "\n".join(lines)
