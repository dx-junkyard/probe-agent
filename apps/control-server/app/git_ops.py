"""Safe, read-only Git operations for committed-files-only snapshots.

All reads use `git show <sha>:<path>` and `git ls-tree <sha>` so that untracked,
ignored, and uncommitted content is never included.  Path traversal and
symlink escapes are rejected before any read.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import posixpath
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_FILE_SIZE = 512 * 1024  # 512 KiB per file
MAX_TOTAL_SIZE = 5 * 1024 * 1024  # 5 MiB total payload to LLM

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


@dataclass
class IndexedFile:
    path: str
    source_type: str  # documentation | source | test | configuration
    size_bytes: int
    content_hash: str
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
    total_size = 0

    for entry in sorted(entries, key=lambda item: item.path):
        path = entry.path
        # Symlinks and submodules are not repository content and must never be
        # resolved through the mutable working tree.
        if entry.object_type != "blob" or entry.mode == "120000":
            continue

        if include_patterns and not _matches_patterns(path, include_patterns):
            continue
        if _matches_patterns(path, all_exclude):
            continue

        size = _object_size(real_path, entry.object_id)
        if size > MAX_FILE_SIZE:
            raise GitError(
                f"Included file exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes): {path}"
            )
        if total_size + size > MAX_TOTAL_SIZE:
            raise GitError(
                f"Snapshot exceeds MAX_TOTAL_SIZE ({MAX_TOTAL_SIZE} bytes) at: {path}"
            )
        content = read_file_at_commit(real_path, commit_sha, path)
        if len(content) != size:
            raise GitError(f"Git object size changed while reading: {path}")
        total_size += size

        content_hash = hashlib.sha256(content).hexdigest()
        source_type = classify_source_type(path)

        files.append(IndexedFile(
            path=path,
            source_type=source_type,
            size_bytes=size,
            content_hash=content_hash,
            content=content,
        ))

    if not files:
        raise GitError("No tracked files matched the repository include/exclude policy")
    return commit_sha, files
