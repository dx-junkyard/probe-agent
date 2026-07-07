"""GitHub issue creation for the External Issue Loop (Issue #158).

Issue drafts remain probe-agent's source of truth (Issue #107); this module
only adds an optional path for creating the *external* GitHub issue when the
environment has GitHub configured, and recording the resulting URL back onto
the draft via the existing `update_draft` external_url flow. This does not
relax Principle 5: probe-agent still never writes to the target repository's
tracked branches — creating an issue is a tracker-metadata call, not a repo
write.

Availability is a finite, structural check (Principle 6): a token env var is
present and an owner/repo could be determined from the configured repository's
`origin` remote (or an explicit override). There is no heuristic guessing of
which repo to file against.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

from .git_ops import GitError, _run_git

GITHUB_API_BASE = "https://api.github.com"

_REMOTE_URL_PATTERNS = [
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>.+?)(\.git)?$"),
    re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(\.git)?$"),
]


class GitHubIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepoConfig:
    token: str
    owner: str
    repo: str


def _parse_github_remote(url: str) -> Optional[Tuple[str, str]]:
    url = url.strip()
    for pattern in _REMOTE_URL_PATTERNS:
        match = pattern.match(url)
        if match:
            return match.group("owner"), match.group("repo")
    return None


def detect_github_repo(repo_path: str) -> Optional[Tuple[str, str]]:
    """Best-effort owner/repo detection for the configured repository.

    An explicit `GITHUB_REPO=owner/repo` env var overrides remote detection
    (useful when `origin` is not a github.com URL, e.g. an SSH alias).
    """
    explicit = os.getenv("GITHUB_REPO", "").strip()
    if explicit and "/" in explicit:
        owner, _, repo = explicit.partition("/")
        owner, repo = owner.strip(), repo.strip()
        if owner and repo:
            return owner, repo

    try:
        # `git config --get` (unlike `remote get-url`) returns the URL as
        # configured, without expanding any `url.<base>.insteadOf` rewrite --
        # we want the real github.com identity, not a rewritten mirror/proxy.
        result = _run_git(repo_path, ["config", "--get", "remote.origin.url"])
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return _parse_github_remote(result.stdout.decode("utf-8", errors="replace"))


def resolve_github_config(repo_path: str) -> Optional[GitHubRepoConfig]:
    """Return the GitHub config to use, or None when integration is unavailable.

    Unavailable when no token is configured or owner/repo cannot be
    determined. Never falls back to a guess (Principle 6).
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    detected = detect_github_repo(repo_path)
    if detected is None:
        return None
    owner, repo = detected
    return GitHubRepoConfig(token=token, owner=owner, repo=repo)


def create_github_issue(config: GitHubRepoConfig, title: str, body: str) -> str:
    """Create a GitHub issue via the REST API and return its `html_url`."""
    url = f"{GITHUB_API_BASE}/repos/{config.owner}/{config.repo}/issues"
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GitHubIntegrationError(
            f"GitHub issue creation failed: HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubIntegrationError(f"GitHub issue creation failed: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubIntegrationError(
            f"GitHub response was not JSON: {raw[:500]}"
        ) from exc

    html_url = data.get("html_url")
    if not html_url:
        raise GitHubIntegrationError("GitHub response missing html_url")
    return html_url
