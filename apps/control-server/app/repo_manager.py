"""Managed repository clone/fetch/worktree layer for the GitHub publish
workflow (Issue #216, sub-task 2).

This module owns the server-managed on-disk area under ``GIT_REPOSITORY_ROOT``:
a non-bare "mirror" clone per GitHub connection (kept in sync with the remote
via fetch) and per-job isolated worktrees pinned to a commit SHA. It never
commits or pushes anything -- publish job / commit / push / PR are later
sub-tasks of Issue #216.

Fail-closed (same spirit as ``git_ops._allowed_repository_roots``):
``GIT_REPOSITORY_ROOT`` must be set explicitly. There is no built-in default,
and every entry point raises ``RepoManagerError`` instead of guessing when it
is not configured.

Credential handling (Principle 5/8): a short-lived Installation Access Token
is only ever passed to `git` via a `GIT_ASKPASS` helper script backed by an
environment variable read at process-launch time. The token is never written
into a script file, a clone URL, or `.git/config`, and every error message
that may embed subprocess `stderr` is sanitized through
``github_app._sanitize`` before it is allowed into a ``RepoManagerError``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Optional

from . import github_app

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
    Username*) printf '%s' "x-access-token" ;;
    *) printf '%s' "$GIT_PUBLISH_TOKEN" ;;
esac
"""


class RepoManagerError(RuntimeError):
    pass


# --- configuration -----------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def clone_timeout() -> int:
    return _env_int("GIT_CLONE_TIMEOUT", 300)


def fetch_timeout() -> int:
    return _env_int("GIT_FETCH_TIMEOUT", 120)


def job_retention_hours() -> float:
    try:
        return float(os.getenv("GIT_JOB_RETENTION_HOURS", "24"))
    except (TypeError, ValueError):
        return 24.0


def _repository_root() -> str:
    raw = os.getenv("GIT_REPOSITORY_ROOT", "").strip()
    if not raw:
        raise RepoManagerError(
            "GIT_REPOSITORY_ROOT is not configured; repository manager is disabled"
        )
    os.makedirs(raw, exist_ok=True)
    return os.path.realpath(raw)


def repository_root() -> str:
    """Public accessor for the managed root, used to display root-relative
    paths (e.g. the repository-status API) without leaking the absolute host
    path."""
    return _repository_root()


# --- validation ----------------------------------------------------------


def _validate_id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoManagerError(f"{label} must be an integer")
    if value <= 0:
        raise RepoManagerError(f"{label} must be a positive integer")
    return value


def _validate_sha(commit_sha: str) -> str:
    if not isinstance(commit_sha, str) or not _SHA_RE.match(commit_sha):
        raise RepoManagerError("Invalid commit SHA")
    return commit_sha


def _validate_branch(branch: str) -> str:
    if not isinstance(branch, str) or not branch or ".." in branch:
        raise RepoManagerError("Invalid branch name")
    if not _BRANCH_RE.match(branch):
        raise RepoManagerError("Invalid branch name")
    for part in branch.split("/"):
        if not part or part.startswith("-"):
            raise RepoManagerError("Invalid branch name")
    return branch


def _validate_within_root(path: str, root: str) -> str:
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        raise RepoManagerError("Resolved path escapes GIT_REPOSITORY_ROOT")
    return path


def _validate_clone_url(url: str, *, owner: Optional[str] = None, repo: Optional[str] = None) -> str:
    """Accept only canonical github.com HTTPS clone URLs without credentials."""
    if not isinstance(url, str):
        raise RepoManagerError("Invalid GitHub remote URL")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise RepoManagerError("Invalid GitHub remote URL") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RepoManagerError("GitHub remote destination is not allowed")
    expected_path = f"/{owner}/{repo}.git" if owner and repo else None
    if expected_path is not None and parsed.path != expected_path:
        raise RepoManagerError("GitHub remote does not match the connection")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", parsed.path):
        raise RepoManagerError("Invalid GitHub remote URL")
    return url


def _connection_clone_url(connection_row: Mapping[str, Any]) -> str:
    return _validate_clone_url(
        connection_row.get("clone_url", ""),
        owner=connection_row.get("owner"),
        repo=connection_row.get("repo"),
    )


# --- layout ----------------------------------------------------------------


def mirror_path(connection_id: int) -> str:
    root = _repository_root()
    cid = _validate_id(connection_id, "connection_id")
    path = os.path.join(root, "mirrors", str(cid))
    return _validate_within_root(path, root)


def job_dir(job_id: int) -> str:
    root = _repository_root()
    jid = _validate_id(job_id, "job_id")
    path = os.path.join(root, "jobs", str(jid))
    return _validate_within_root(path, root)


def job_path(job_id: int) -> str:
    return os.path.join(job_dir(job_id), "worktree")


# --- per-connection exclusion -----------------------------------------------

# RLock (not Lock): the publish job state machine (Issue #216, sub-task 3)
# holds this lock for an entire prepare/publish phase and, within that phase,
# calls helpers below (`ensure_mirror`, `create_job_worktree`,
# `resolve_mirror_branch_sha`, `cleanup_job_worktree`) that each also acquire
# it. A plain Lock would deadlock on that same-thread re-entry; RLock keeps
# the same per-connection mutual exclusion across *different* threads/jobs
# while allowing the owning thread to nest calls safely.
_locks_guard = threading.Lock()
_connection_locks: Dict[int, threading.RLock] = {}


@contextmanager
def connection_lock(connection_id: int) -> Iterator[None]:
    cid = _validate_id(connection_id, "connection_id")
    with _locks_guard:
        lock = _connection_locks.setdefault(cid, threading.RLock())
    with lock:
        yield


# --- credentials -------------------------------------------------------------


@contextmanager
def _ephemeral_credentials(token: str) -> Iterator[Dict[str, str]]:
    """Yield subprocess env additions that hand a short-lived token to `git`
    via GIT_ASKPASS without ever writing the token into a file on disk.

    The askpass script only ever reads the token from the `GIT_PUBLISH_TOKEN`
    environment variable at prompt time; it answers the Username prompt with
    the GitHub App convention (`x-access-token`) and the Password prompt with
    the token. The temporary directory (and script) is removed on exit, even
    on exception.
    """
    if not token:
        raise RepoManagerError("Missing installation token")
    tmp_dir = tempfile.mkdtemp(prefix="probe-git-askpass-")
    try:
        os.chmod(tmp_dir, 0o700)
        script_path = os.path.join(tmp_dir, "askpass.sh")
        with open(script_path, "w") as handle:
            handle.write(_ASKPASS_SCRIPT)
        os.chmod(script_path, 0o700)
        yield {
            "GIT_ASKPASS": script_path,
            "GIT_PUBLISH_TOKEN": token,
            "GIT_TERMINAL_PROMPT": "0",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- subprocess helper ---------------------------------------------------


def _run_git(
    cwd: str,
    args: List[str],
    *,
    timeout: int,
    env_extra: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
    })
    if env_extra:
        env.update(env_extra)
    secret = (env_extra or {}).get("GIT_PUBLISH_TOKEN", "")
    try:
        return subprocess.run(
            [
                "git", "-c", f"safe.directory={cwd}",
                "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
                "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                "-c", "submodule.recurse=false", "-C", cwd,
            ] + args,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RepoManagerError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        message = github_app._sanitize(str(exc), secret) if secret else str(exc)
        raise RepoManagerError(f"git command timed out after {timeout}s: {message}") from exc


def _stderr(result: subprocess.CompletedProcess, *secrets: str) -> str:
    text = result.stderr.decode("utf-8", errors="replace").strip()
    return github_app._sanitize(text, *secrets)


def _validate_existing_remote(repo_path: str, expected_url: str) -> None:
    """Re-check local config immediately before authenticated network access."""
    expected_url = _validate_clone_url(expected_url)
    result = _run_git(repo_path, ["config", "--local", "--null", "--list"], timeout=30)
    if result.returncode != 0:
        raise RepoManagerError("Cannot inspect repository configuration")
    entries = result.stdout.decode("utf-8", errors="replace").split("\0")
    origin_urls: List[str] = []
    forbidden_prefixes = ("url.", "credential.", "filter.", "include.", "includeif.")
    for entry in entries:
        if not entry:
            continue
        key, _, value = entry.partition("\n")
        lower = key.lower()
        if lower.startswith(forbidden_prefixes) or lower in {
            "core.hookspath", "core.sshcommand", "remote.origin.uploadpack", "remote.origin.receivepack"
        }:
            raise RepoManagerError("Unsafe Git repository configuration")
        if lower == "remote.origin.url":
            origin_urls.append(value)
    if origin_urls != [expected_url]:
        raise RepoManagerError("Git remote does not match the allowed connection URL")


def push(connection_row: Mapping[str, Any], repo_path: str, token: str, refspec: str) -> subprocess.CompletedProcess:
    """Push to the validated URL, never to a mutable remote name."""
    clone_url = _connection_clone_url(connection_row)
    _validate_existing_remote(repo_path, clone_url)
    with _ephemeral_credentials(token) as env_extra:
        return _run_git(
            repo_path,
            ["push", clone_url, refspec],
            timeout=fetch_timeout(),
            env_extra=env_extra,
        )


# --- operations --------------------------------------------------------------


def ensure_mirror(connection_row: Mapping[str, Any], token: str) -> None:
    """Create the managed mirror clone if missing, otherwise fetch updates.

    Non-bare by design: the same worktree/snapshot machinery that reads a
    plain working-tree Git repository (`git_ops`) can then be pointed at the
    mirror directly.
    """
    connection_id = _validate_id(connection_row["id"], "connection_id")
    clone_url = _connection_clone_url(connection_row)
    mirror = mirror_path(connection_id)

    with connection_lock(connection_id):
        with _ephemeral_credentials(token) as env_extra:
            if os.path.isdir(os.path.join(mirror, ".git")):
                _validate_existing_remote(mirror, clone_url)
                result = _run_git(
                    mirror,
                    ["fetch", "--no-tags", "--no-recurse-submodules", clone_url, "+refs/heads/*:refs/remotes/origin/*"],
                    timeout=fetch_timeout(),
                    env_extra=env_extra,
                )
                if result.returncode != 0:
                    raise RepoManagerError(f"git fetch failed: {_stderr(result, token)}")
            else:
                mirrors_root = os.path.dirname(mirror)
                os.makedirs(mirrors_root, exist_ok=True)
                if os.path.exists(mirror):
                    shutil.rmtree(mirror)
                result = _run_git(
                    mirrors_root,
                    ["clone", "--no-tags", "--no-recurse-submodules", "--no-checkout", clone_url, mirror],
                    timeout=clone_timeout(),
                    env_extra=env_extra,
                )
                if result.returncode != 0:
                    raise RepoManagerError(f"git clone failed: {_stderr(result, token)}")
                _validate_existing_remote(mirror, clone_url)
                checkout = _run_git(mirror, ["checkout", "-f"], timeout=clone_timeout())
                if checkout.returncode != 0:
                    raise RepoManagerError(f"git checkout failed: {_stderr(checkout, token)}")
    mirror_path(connection_id)  # re-validate no symlink escape was introduced


def resolve_remote_branch_sha(connection_row: Mapping[str, Any], token: str, branch: str) -> str:
    """Resolve a branch's SHA directly from the remote (no mirror required).

    Non-destructive: used both for connection verification/staleness checks
    and as the basis for `sync`'s local `git rev-parse` after fetch.
    """
    branch = _validate_branch(branch)
    clone_url = _connection_clone_url(connection_row)
    root = _repository_root()
    with _ephemeral_credentials(token) as env_extra:
        result = _run_git(
            root,
            ["ls-remote", clone_url, f"refs/heads/{branch}"],
            timeout=fetch_timeout(),
            env_extra=env_extra,
        )
    if result.returncode != 0:
        raise RepoManagerError(f"git ls-remote failed: {_stderr(result, token)}")
    output = result.stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RepoManagerError(f"Branch not found on remote: {branch}")
    sha = output.split()[0]
    if not re.match(r"^[0-9a-f]{40}$", sha):
        raise RepoManagerError("Unexpected git ls-remote output")
    return sha


def resolve_mirror_branch_sha(connection_id: int, branch: str) -> str:
    """Resolve a branch's SHA from the local mirror (`git rev-parse
    origin/<branch>`); the mirror must already be up to date (call
    `ensure_mirror` first)."""
    branch = _validate_branch(branch)
    mirror = mirror_path(connection_id)
    if not os.path.isdir(os.path.join(mirror, ".git")):
        raise RepoManagerError("Mirror does not exist; call ensure_mirror first")
    with connection_lock(connection_id):
        result = _run_git(mirror, ["rev-parse", f"origin/{branch}"], timeout=fetch_timeout())
    if result.returncode != 0:
        raise RepoManagerError(f"Failed to resolve origin/{branch}: {_stderr(result)}")
    sha = result.stdout.decode("utf-8", errors="replace").strip()
    if not re.match(r"^[0-9a-f]{40}$", sha):
        raise RepoManagerError("Unexpected git rev-parse output")
    return sha


def create_job_worktree(connection_id: int, job_id: int, commit_sha: str) -> str:
    commit_sha = _validate_sha(commit_sha)
    mirror = mirror_path(connection_id)
    if not os.path.isdir(os.path.join(mirror, ".git")):
        raise RepoManagerError("Mirror does not exist; call ensure_mirror first")

    worktree = job_path(job_id)
    if os.path.exists(worktree):
        raise RepoManagerError(f"Job worktree already exists: {worktree}")
    os.makedirs(os.path.dirname(worktree), exist_ok=True)

    with connection_lock(connection_id):
        result = _run_git(
            mirror,
            ["worktree", "add", "--detach", worktree, commit_sha],
            timeout=clone_timeout(),
        )
    if result.returncode != 0:
        raise RepoManagerError(f"git worktree add failed: {_stderr(result)}")
    return _validate_within_root(worktree, _repository_root())


def cleanup_job_worktree(connection_id: int, job_id: int) -> None:
    mirror = mirror_path(connection_id)
    worktree = job_path(job_id)

    with connection_lock(connection_id):
        if os.path.isdir(os.path.join(mirror, ".git")):
            _run_git(mirror, ["worktree", "remove", "--force", worktree], timeout=30)
            _run_git(mirror, ["worktree", "prune"], timeout=30)
        if os.path.exists(worktree):
            shutil.rmtree(worktree, ignore_errors=True)
        parent = job_dir(job_id)
        try:
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass

    if os.path.exists(worktree):
        raise RepoManagerError(f"Failed to remove job worktree: {worktree}")


def cleanup_expired_jobs() -> List[str]:
    """Remove job directories whose mtime is older than
    `GIT_JOB_RETENTION_HOURS`, then prune stale worktree registrations on
    every mirror. Returns the list of removed job directories."""
    root = _repository_root()
    jobs_root = os.path.join(root, "jobs")
    removed: List[str] = []
    if os.path.isdir(jobs_root):
        cutoff = time.time() - job_retention_hours() * 3600.0
        for name in os.listdir(jobs_root):
            candidate = os.path.join(jobs_root, name)
            real = os.path.realpath(candidate)
            if real != candidate and not real.startswith(root + os.sep):
                continue  # symlink escape; never follow it
            if not os.path.isdir(candidate):
                continue
            try:
                mtime = os.path.getmtime(candidate)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate)

    mirrors_root = os.path.join(root, "mirrors")
    if os.path.isdir(mirrors_root):
        for name in os.listdir(mirrors_root):
            mirror_dir = os.path.join(mirrors_root, name)
            if os.path.isdir(os.path.join(mirror_dir, ".git")):
                _run_git(mirror_dir, ["worktree", "prune"], timeout=30)

    return removed
