"""Safe, read-only Git operations for committed-files-only snapshots.

All reads use `git show <sha>:<path>` and `git ls-tree <sha>` so that untracked,
ignored, and uncommitted content is never included.  Path traversal and
symlink escapes are rejected before any read.

Snapshot storage limits and LLM context limits are separate concerns:
- ``MAX_FILE_SIZE`` controls the per-file content storage threshold for
  snapshots.  Files exceeding this are recorded as ``too_large`` with
  metadata only (no content).  Configurable via ``SNAPSHOT_MAX_FILE_SIZE``.
- LLM context budgets are managed independently by each consumer
  (e.g. ``draft_generator._build_file_context``).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import posixpath
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_FILE_SIZE = int(os.getenv("SNAPSHOT_MAX_FILE_SIZE", str(512 * 1024)))

DEFAULT_EXCLUDE = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "credentials.*",
    "secrets/**",
    "secret/**",
    ".secrets/**",
]


class GitError(Exception):
    pass


INCLUSION_STATUSES = {
    "indexed", "metadata_only", "too_large", "binary", "excluded", "unsupported"
}


@dataclass
class IndexedFile:
    path: str
    source_type: str  # documentation | source | test | configuration
    size_bytes: int
    content_hash: str
    inclusion_status: str = "indexed"
    exclusion_reason: str = ""
    content: bytes = field(repr=False, default=b"")


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str


def _run_git(
    repo_path: str,
    args: List[str],
    *,
    timeout: int = 30,
    input_bytes: Optional[bytes] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", repo_path] + args,
            capture_output=True,
            timeout=timeout,
            input=input_bytes,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git command timed out after {timeout}s") from exc


def resolve_head(repo_path: str) -> str:
    result = _run_git(repo_path, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Not a git repository or no commits: {stderr}")
    return result.stdout.decode("utf-8").strip()


def _allowed_repository_roots() -> List[str]:
    raw = os.getenv("PROBE_REPOSITORY_ROOTS", "").strip()
    if not raw:
        raise GitError(
            "PROBE_REPOSITORY_ROOTS is not configured; repository access is disabled"
        )
    return [
        os.path.realpath(root.strip())
        for root in raw.split(os.pathsep)
        if root.strip()
    ]


def _validate_repo_path(repo_path: str) -> str:
    real = os.path.realpath(repo_path)
    if not os.path.isdir(real):
        raise GitError(f"Repository path does not exist: {repo_path}")
    allowed_roots = _allowed_repository_roots()
    allowed = any(
        real == root or real.startswith(root + os.sep)
        for root in allowed_roots
    )
    if not allowed:
        raise GitError("Repository path is outside PROBE_REPOSITORY_ROOTS")
    git_dir = os.path.join(real, ".git")
    if not os.path.exists(git_dir):
        raise GitError(f"Not a git repository: {repo_path}")
    result = _run_git(real, ["rev-parse", "--absolute-git-dir"])
    if result.returncode != 0:
        raise GitError("Cannot resolve repository Git directory")
    resolved_git_dir = os.path.realpath(
        result.stdout.decode("utf-8", errors="replace").strip()
    )
    if not any(
        resolved_git_dir == root or resolved_git_dir.startswith(root + os.sep)
        for root in allowed_roots
    ):
        raise GitError("Repository Git directory is outside PROBE_REPOSITORY_ROOTS")
    return real


def discover_repository_candidates(max_depth: int = 4) -> List[Tuple[str, str]]:
    """Return Git repositories located below the configured allowed roots.

    Symlinked directories are not traversed. Results contain the repository's
    display path relative to its allowed root and its canonical absolute path.
    """
    candidates: List[Tuple[str, str]] = []
    seen = set()
    for root in _allowed_repository_roots():
        if not os.path.isdir(root):
            continue
        root_real = os.path.realpath(root)
        for current, dirs, _files in os.walk(root_real, followlinks=False):
            relative = os.path.relpath(current, root_real)
            depth = 0 if relative == "." else len(relative.split(os.sep))
            dirs[:] = [
                name
                for name in dirs
                if name != ".git"
                and not os.path.islink(os.path.join(current, name))
                and depth < max_depth
            ]

            if os.path.exists(os.path.join(current, ".git")):
                real = os.path.realpath(current)
                if real not in seen:
                    label = os.path.basename(root_real) if relative == "." else relative
                    candidates.append((label.replace(os.sep, "/"), real))
                    seen.add(real)
                dirs[:] = []

    return sorted(candidates, key=lambda item: (item[0].lower(), item[1]))


def _is_safe_git_path(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path:
        return False
    if path.startswith("/") or ".." in path.split("/"):
        return False
    return posixpath.normpath(path) == path


def _matches_patterns(path: str, patterns: List[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if "/" in pattern and fnmatch.fnmatch(path, pattern):
            return True
        parts = path.split("/")
        for i in range(len(parts)):
            sub = "/".join(parts[i:])
            if fnmatch.fnmatch(sub, pattern):
                return True
    return False


def classify_source_type(path: str) -> str:
    lower = path.lower()
    name = os.path.basename(lower)

    if name in ("readme.md", "readme.rst", "readme.txt", "readme", "changelog.md",
                "contributing.md", "license", "license.md", "license.txt"):
        return "documentation"

    parts = lower.split("/")
    if "docs" in parts or "doc" in parts or "documentation" in parts:
        return "documentation"

    if "test" in parts or "tests" in parts or "test_" in name or name.startswith("test_") or name.endswith("_test.py"):
        return "test"

    config_names = {
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "package.json", "tsconfig.json", "webpack.config.js",
        "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        ".gitignore", ".flake8", "tox.ini", "mypy.ini",
        "makefile", "justfile",
    }
    config_extensions = {".toml", ".ini", ".cfg", ".yml", ".yaml"}
    if name in config_names:
        return "configuration"
    _, ext = os.path.splitext(name)
    if ext in config_extensions and "/" not in path:
        return "configuration"

    return "source"


def list_tree_entries(repo_path: str, commit_sha: str) -> List[GitTreeEntry]:
    result = _run_git(repo_path, ["ls-tree", "-r", "-z", commit_sha])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git ls-tree failed: {stderr}")
    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw:
        return []
    entries = []
    for record in (item for item in raw.split("\0") if item):
        try:
            metadata, path = record.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except ValueError as exc:
            raise GitError("git ls-tree returned an invalid record") from exc
        if not _is_safe_git_path(path):
            raise GitError(f"Unsafe path in Git tree: {path!r}")
        entries.append(
            GitTreeEntry(
                path=path,
                mode=mode,
                object_type=object_type,
                object_id=object_id,
            )
        )
    return entries


def read_file_at_commit(repo_path: str, commit_sha: str, path: str) -> bytes:
    if not _is_safe_git_path(path):
        raise GitError(f"Unsafe Git path: {path!r}")
    result = _run_git(repo_path, ["show", f"{commit_sha}:{path}"])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Cannot read {path} at {commit_sha}: {stderr}")
    return result.stdout


def _object_size(repo_path: str, object_id: str) -> int:
    result = _run_git(repo_path, ["cat-file", "-s", object_id])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Cannot inspect Git object {object_id}: {stderr}")
    try:
        return int(result.stdout.decode("ascii").strip())
    except ValueError as exc:
        raise GitError(f"Invalid Git object size for {object_id}") from exc


def create_snapshot(
    repo_path: str,
    include_patterns: List[str],
    exclude_patterns: List[str],
) -> Tuple[str, List[IndexedFile]]:
    real_path = _validate_repo_path(repo_path)
    commit_sha = resolve_head(real_path)
    entries = list_tree_entries(real_path, commit_sha)

    all_exclude = list(exclude_patterns) + DEFAULT_EXCLUDE

    files: List[IndexedFile] = []

    for entry in sorted(entries, key=lambda item: item.path):
        path = entry.path

        if include_patterns and not _matches_patterns(path, include_patterns):
            continue
        if _matches_patterns(path, all_exclude):
            size = (
                _object_size(real_path, entry.object_id)
                if entry.object_type == "blob"
                else 0
            )
            files.append(IndexedFile(
                path=path,
                source_type=classify_source_type(path),
                size_bytes=size,
                content_hash=entry.object_id,
                inclusion_status="excluded",
                exclusion_reason="Path matched the repository exclusion policy",
            ))
            continue

        # Symlinks and submodules are not repository content and must never be
        # resolved through the mutable working tree. Record the omission so it
        # is auditable without following the link or reading mutable content.
        if entry.object_type != "blob" or entry.mode == "120000":
            size = (
                _object_size(real_path, entry.object_id)
                if entry.object_type == "blob"
                else 0
            )
            files.append(IndexedFile(
                path=path,
                source_type=classify_source_type(path),
                size_bytes=size,
                content_hash=entry.object_id,
                inclusion_status="unsupported",
                exclusion_reason=(
                    "Git tree entry is a symlink or unsupported non-blob object"
                ),
            ))
            continue

        size = _object_size(real_path, entry.object_id)
        source_type = classify_source_type(path)

        if size > MAX_FILE_SIZE:
            files.append(IndexedFile(
                path=path,
                source_type=source_type,
                size_bytes=size,
                content_hash=entry.object_id,
                inclusion_status="too_large",
                exclusion_reason=(
                    f"File size ({size} bytes) exceeds "
                    f"SNAPSHOT_MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes)"
                ),
            ))
            continue

        content = read_file_at_commit(real_path, commit_sha, path)
        if len(content) != size:
            raise GitError(f"Git object size changed while reading: {path}")

        content_hash = hashlib.sha256(content).hexdigest()

        if b"\x00" in content:
            files.append(IndexedFile(
                path=path,
                source_type=source_type,
                size_bytes=size,
                content_hash=content_hash,
                inclusion_status="binary",
                exclusion_reason="File contains null bytes (binary content)",
            ))
            continue

        files.append(IndexedFile(
            path=path,
            source_type=source_type,
            size_bytes=size,
            content_hash=content_hash,
            inclusion_status="indexed",
            content=content,
        ))

    if not files:
        raise GitError("No tracked files matched the repository include/exclude policy")
    return commit_sha, files
