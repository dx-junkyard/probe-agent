"""GitHub App authentication and Installation Token broker (Issue #216).

This module is the deterministic, structural building block for the GitHub
App publish workflow: it signs the App-level JWT, exchanges it for a
short-lived Installation Access Token, and makes a handful of GitHub REST
calls (read-only connection verification, and -- added in sub-task 3 --
listing/creating a Pull Request) used by the repository manager and the
publish job state machine. It does not clone/fetch a repository, does not
commit/push, and does not persist tokens anywhere (Principle 5/8).

Configuration is fail-closed (Principle 6 spirit: no heuristic fallback):
`github_app_configured()` is the single source of truth for whether the App
is usable, and every token/API operation raises `GitHubAppError` instead of
guessing when it is not.

Security: token/JWT/private-key material must never leak into an exception
message, log line, or persisted value. `_sanitize` strips known GitHub token
shapes and any secret value explicitly passed to it before the text is
allowed into a `GitHubAppError`.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jwt

DEFAULT_API_BASE_URL = "https://api.github.com"
DEFAULT_WEB_BASE_URL = "https://github.com"

# GitHub allows at most 10 minutes; keep a safety margin plus a small
# backdated `iat` to tolerate clock skew between this process and GitHub.
_JWT_IAT_SKEW_SECONDS = 60
_JWT_EXP_SECONDS = 540

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Known GitHub token shapes (fine-grained/classic PATs, installation/app
# tokens, OAuth tokens) plus a generic JWT shape, redacted defensively even
# when the caller didn't pass the exact secret value to `_sanitize`.
_TOKEN_SHAPE_RE = re.compile(r"gh[a-z]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}")
_JWT_SHAPE_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class GitHubAppError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: str


def _sanitize(text: str, *known_secrets: str) -> str:
    sanitized = text
    for secret in known_secrets:
        if secret:
            sanitized = sanitized.replace(secret, "***")
    sanitized = _TOKEN_SHAPE_RE.sub("***", sanitized)
    sanitized = _JWT_SHAPE_RE.sub("***", sanitized)
    return sanitized


def _app_id() -> str:
    return os.getenv("GITHUB_APP_ID", "").strip()


def _private_key_path() -> str:
    return os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()


def default_api_base_url() -> str:
    return os.getenv("GITHUB_API_BASE_URL", "").strip() or DEFAULT_API_BASE_URL


def default_web_base_url() -> str:
    return os.getenv("GITHUB_WEB_BASE_URL", "").strip() or DEFAULT_WEB_BASE_URL


def github_app_configured() -> bool:
    """Structural, finite check: app id set, key path set, key file exists."""
    app_id = _app_id()
    key_path = _private_key_path()
    if not app_id or not key_path:
        return False
    return os.path.isfile(key_path)


def _require_configured() -> None:
    if not github_app_configured():
        raise GitHubAppError(
            "GitHub App is not configured: set GITHUB_APP_ID and "
            "GITHUB_APP_PRIVATE_KEY_PATH to an existing private key file."
        )


def generate_app_jwt() -> str:
    """Sign an App-level JWT (RS256) per GitHub's App authentication scheme."""
    _require_configured()
    app_id = _app_id()
    key_path = _private_key_path()
    try:
        with open(key_path, "rb") as handle:
            private_key = handle.read()
    except OSError as exc:
        raise GitHubAppError(f"Failed to read GitHub App private key: {exc}") from exc

    now = int(time.time())
    payload = {
        "iat": now - _JWT_IAT_SKEW_SECONDS,
        "exp": now + _JWT_EXP_SECONDS,
        "iss": app_id,
    }
    try:
        token = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as exc:
        # PyJWT/cryptography raise several distinct exception types for a
        # malformed key; none of them, nor the key bytes, may reach the
        # caller.
        raise GitHubAppError("Failed to sign GitHub App JWT") from exc
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token


def _validate_owner_repo(owner: str, repo: str) -> None:
    if not owner or not _OWNER_REPO_RE.match(owner):
        raise GitHubAppError("Invalid GitHub owner")
    if not repo or not _OWNER_REPO_RE.match(repo):
        raise GitHubAppError("Invalid GitHub repository name")


def _validate_installation_id(installation_id: int) -> None:
    if isinstance(installation_id, bool) or not isinstance(installation_id, int):
        raise GitHubAppError("installation_id must be an integer")


def _request(
    url: str,
    *,
    method: str = "GET",
    token: Optional[str] = None,
    data: Optional[bytes] = None,
    sanitize_secrets: tuple = (),
) -> Any:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        detail = _sanitize(detail, *sanitize_secrets)
        raise GitHubAppError(
            f"GitHub API request failed: HTTP {exc.code}: {detail}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        message = _sanitize(str(exc), *sanitize_secrets)
        raise GitHubAppError(f"GitHub API request failed: {message}") from None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise GitHubAppError("GitHub API response was not JSON") from None


def create_installation_token(
    installation_id: int, api_base_url: Optional[str] = None
) -> InstallationToken:
    """Exchange the App JWT for a short-lived Installation Access Token.

    The returned token is never persisted by this module; callers must not
    write it to the database (Principle 5/8).
    """
    _validate_installation_id(installation_id)
    base = (api_base_url or default_api_base_url()).rstrip("/")
    app_jwt = generate_app_jwt()
    url = f"{base}/app/installations/{installation_id}/access_tokens"
    data = _request(url, method="POST", token=app_jwt, sanitize_secrets=(app_jwt,))
    token = data.get("token")
    expires_at = data.get("expires_at")
    if not token or not expires_at:
        raise GitHubAppError(
            "GitHub installation token response is missing token/expires_at"
        )
    return InstallationToken(token=token, expires_at=expires_at)


def get_repository(
    owner: str, repo: str, token: str, api_base_url: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch repository metadata; used to verify access and read default_branch."""
    _validate_owner_repo(owner, repo)
    base = (api_base_url or default_api_base_url()).rstrip("/")
    url = f"{base}/repos/{owner}/{repo}"
    return _request(url, token=token, sanitize_secrets=(token,))


def create_pull_request(
    owner: str,
    repo: str,
    token: str,
    *,
    title: str,
    head: str,
    base: str,
    body: str,
    api_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Open a Pull Request (used only after explicit human approval, from a
    server-generated ``probe/``-prefixed branch). probe-agent never merges
    or closes the PR itself -- see Principle 5/8."""
    _validate_owner_repo(owner, repo)
    base_url = (api_base_url or default_api_base_url()).rstrip("/")
    url = f"{base_url}/repos/{owner}/{repo}/pulls"
    payload = json.dumps({"title": title, "head": head, "base": base, "body": body}).encode("utf-8")
    return _request(url, method="POST", token=token, data=payload, sanitize_secrets=(token,))


def list_open_pull_requests_for_branch(
    owner: str,
    repo: str,
    token: str,
    *,
    head_branch: str,
    api_base_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List open PRs whose head is ``head_branch`` in this same repository,
    used to make PR creation idempotent (never open a duplicate PR for a
    branch a previous, partially-completed publish job already pushed)."""
    _validate_owner_repo(owner, repo)
    if not isinstance(head_branch, str) or not head_branch:
        raise GitHubAppError("Invalid head branch")
    base_url = (api_base_url or default_api_base_url()).rstrip("/")
    head_param = urllib.parse.quote(f"{owner}:{head_branch}", safe="")
    url = f"{base_url}/repos/{owner}/{repo}/pulls?head={head_param}&state=open"
    data = _request(url, token=token, sanitize_secrets=(token,))
    return data if isinstance(data, list) else []


def list_installation_repositories(
    token: str, api_base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List repositories reachable by the installation the token belongs to."""
    base = (api_base_url or default_api_base_url()).rstrip("/")
    url = f"{base}/installation/repositories"
    data = _request(url, token=token, sanitize_secrets=(token,))
    repositories = data.get("repositories") or []
    result: List[Dict[str, Any]] = []
    for entry in repositories:
        owner = (entry.get("owner") or {}).get("login", "")
        result.append(
            {
                "owner": owner,
                "name": entry.get("name", ""),
                "default_branch": entry.get("default_branch"),
                "private": bool(entry.get("private", False)),
            }
        )
    return result
