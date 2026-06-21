"""Safe, read-only Git operations for committed-files-only snapshots.

All reads use `git show <sha>:<path>` and `git ls-files` so that untracked,
ignored, and uncommitted content is never included.  Path traversal and
symlink escapes are rejected before any read.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
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


def _run_git(
    repo_path: str, args: List[str], *, timeout: int = 30
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", repo_path] + args,
            capture_output=True,
            timeout=timeout,
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


def _validate_repo_path(repo_path: str) -> str:
    real = os.path.realpath(repo_path)
    if not os.path.isdir(real):
        raise GitError(f"Repository path does not exist: {repo_path}")
    git_dir = os.path.join(real, ".git")
    if not os.path.exists(git_dir):
        raise GitError(f"Not a git repository: {repo_path}")
    return real


def _is_safe_path(path: str, repo_real: str) -> bool:
    if ".." in path.split("/"):
        return False
    if os.path.isabs(path):
        return False
    resolved = os.path.realpath(os.path.join(repo_real, path))
    return resolved.startswith(repo_real + os.sep) or resolved == repo_real


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


def list_tracked_files(repo_path: str) -> List[str]:
    result = _run_git(repo_path, ["ls-files", "-z"])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git ls-files failed: {stderr}")
    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw:
        return []
    return [f for f in raw.split("\0") if f]


def read_file_at_commit(repo_path: str, commit_sha: str, path: str) -> bytes:
    result = _run_git(repo_path, ["show", f"{commit_sha}:{path}"])
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Cannot read {path} at {commit_sha}: {stderr}")
    return result.stdout


def create_snapshot(
    repo_path: str,
    include_patterns: List[str],
    exclude_patterns: List[str],
) -> Tuple[str, List[IndexedFile]]:
    real_path = _validate_repo_path(repo_path)
    commit_sha = resolve_head(repo_path)
    tracked = list_tracked_files(repo_path)

    all_exclude = list(exclude_patterns) + DEFAULT_EXCLUDE

    files: List[IndexedFile] = []
    total_size = 0

    for path in sorted(tracked):
        if not _is_safe_path(path, real_path):
            continue

        if include_patterns and not _matches_patterns(path, include_patterns):
            continue
        if _matches_patterns(path, all_exclude):
            continue

        try:
            content = read_file_at_commit(repo_path, commit_sha, path)
        except GitError:
            continue

        size = len(content)
        if size > MAX_FILE_SIZE:
            continue
        if total_size + size > MAX_TOTAL_SIZE:
            continue
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

    return commit_sha, files
