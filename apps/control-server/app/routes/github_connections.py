"""GitHub connection management API (Issue #216, sub-task 1).

Persists which remote repository + GitHub App installation a System is
connected to for the (later) publish workflow, and exposes a `/verify`
action that exchanges a short-lived Installation Access Token to confirm
access and read `default_branch`. No installation token or private key
material is ever stored or returned by this router (Principle 5/8); only
structural connection metadata is persisted.

probe-agent:
  role: GitHub connection persistence and verification REST API boundary
  capability: github-publish-workflow
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify GitHub connections are system-isolated, owner/repo input is validated before use, and no installation token ever reaches storage or the API response.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_system_id, require_admin, require_user
from ..db import get_conn
from ..github_app import (
    GitHubAppError,
    allowed_organization,
    create_installation_token,
    default_api_base_url,
    default_web_base_url,
    get_repository,
    github_app_configured,
    get_installation,
    list_installation_repositories,
)
from ..models import (
    GithubAppStatusOut,
    GithubConnectionCreate,
    GithubConnectionOut,
    GithubInstallationOut,
    GithubInstallationRegister,
    GithubInstallationRepositoryOut,
    GithubRepositoryStatusOut,
)
from ..github_installations import (
    GitHubInstallationAccessError,
    require_active_installation_assignment,
)
from .. import repo_manager
from ..repo_manager import RepoManagerError

router = APIRouter()

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _system_owner(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="System not found")
    return row["owner_user_id"]


def _can_manage(principal: Principal, owner_user_id: Optional[int]) -> bool:
    return principal.is_admin or owner_user_id == principal.user_id


def _require_manage(conn: sqlite3.Connection, principal: Principal, system_id: int) -> None:
    owner_user_id = _system_owner(conn, system_id)
    if not _can_manage(principal, owner_user_id):
        raise HTTPException(status_code=403, detail="System access denied")


def _connection_out(row: sqlite3.Row) -> GithubConnectionOut:
    return GithubConnectionOut(
        id=row["id"],
        system_id=row["system_id"],
        api_base_url=row["api_base_url"],
        web_base_url=row["web_base_url"],
        owner=row["owner"],
        repo=row["repo"],
        clone_url=row["clone_url"],
        installation_id=row["installation_id"],
        default_branch=row["default_branch"],
        credential_type=row["credential_type"],
        status=row["status"],
        last_error=row["last_error"],
        last_synced_at=row["last_synced_at"],
        last_synced_commit_sha=row["last_synced_commit_sha"],
        created_by_user_id=row["created_by_user_id"],
        updated_by_user_id=row["updated_by_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_connection_or_404(conn: sqlite3.Connection, connection_id: int, system_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM github_connections WHERE id = ? AND system_id = ?",
        (connection_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return row


def _installation_out(conn: sqlite3.Connection, row: sqlite3.Row) -> GithubInstallationOut:
    assignments = conn.execute(
        "SELECT system_id FROM github_installation_systems "
        "WHERE installation_id = ? ORDER BY system_id",
        (row["installation_id"],),
    ).fetchall()
    return GithubInstallationOut(
        installation_id=row["installation_id"],
        github_account_login=row["github_account_login"],
        github_account_type=row["github_account_type"],
        status=row["status"],
        registered_by_user_id=row["registered_by_user_id"],
        verified_at=row["verified_at"],
        disabled_by_user_id=row["disabled_by_user_id"],
        disabled_at=row["disabled_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assigned_system_ids=[assignment["system_id"] for assignment in assignments],
    )


def _require_installation_assignment(
    conn: sqlite3.Connection, installation_id: int, system_id: int
) -> None:
    try:
        require_active_installation_assignment(conn, installation_id, system_id)
    except GitHubInstallationAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


@router.get("/github/app-status", response_model=GithubAppStatusOut)
def get_app_status(principal: Principal = Depends(require_user)) -> GithubAppStatusOut:
    app_id = os.getenv("GITHUB_APP_ID", "").strip() or None
    return GithubAppStatusOut(
        configured=github_app_configured(),
        app_id=app_id,
        api_base_url=default_api_base_url(),
        web_base_url=default_web_base_url(),
        allowed_organization=allowed_organization() or None,
    )


@router.get("/github/installations", response_model=List[GithubInstallationOut])
def list_installations(
    principal: Principal = Depends(require_admin),
) -> List[GithubInstallationOut]:
    """List the administrator-owned installation allowlist and assignments."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM github_installations ORDER BY installation_id"
        ).fetchall()
        return [_installation_out(conn, row) for row in rows]


@router.post("/github/installations", response_model=GithubInstallationOut, status_code=201)
def register_installation(
    payload: GithubInstallationRegister,
    principal: Principal = Depends(require_admin),
) -> GithubInstallationOut:
    """Admin-only registration of an installation after GitHub-side verification."""
    organization = allowed_organization()
    if not organization:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_APP_ALLOWED_ORGANIZATION must be configured before registering installations",
        )
    try:
        installation = get_installation(payload.installation_id)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    account = installation.get("account") or {}
    login = account.get("login")
    account_type = account.get("type")
    if not isinstance(login, str) or not isinstance(account_type, str):
        raise HTTPException(status_code=502, detail="GitHub installation response is missing account identity")
    if account_type != "Organization" or login.casefold() != organization.casefold():
        raise HTTPException(
            status_code=422,
            detail="GitHub installation account must be the configured Organization",
        )

    now = _now_iso()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM github_installations WHERE installation_id = ?",
            (payload.installation_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO github_installations (
                    installation_id, github_account_login, github_account_type, status,
                    registered_by_user_id, verified_at, disabled_by_user_id, disabled_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?, NULL, NULL, ?, ?)
                """,
                (payload.installation_id, login, account_type, principal.user_id, now, now, now),
            )
        else:
            # Re-registration intentionally re-verifies the GitHub account and
            # is the explicit admin action that re-enables a disabled record.
            conn.execute(
                """
                UPDATE github_installations
                SET github_account_login = ?, github_account_type = ?, status = 'active',
                    registered_by_user_id = ?, verified_at = ?, disabled_by_user_id = NULL,
                    disabled_at = NULL, updated_at = ?
                WHERE installation_id = ?
                """,
                (login, account_type, principal.user_id, now, now, payload.installation_id),
            )
        row = conn.execute(
            "SELECT * FROM github_installations WHERE installation_id = ?",
            (payload.installation_id,),
        ).fetchone()
        return _installation_out(conn, row)


@router.post("/github/installations/{installation_id}/disable", response_model=GithubInstallationOut)
def disable_installation(
    installation_id: int,
    principal: Principal = Depends(require_admin),
) -> GithubInstallationOut:
    now = _now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE github_installations
            SET status = 'disabled', disabled_by_user_id = ?, disabled_at = ?, updated_at = ?
            WHERE installation_id = ? AND status = 'active'
            """,
            (principal.user_id, now, now, installation_id),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT * FROM github_installations WHERE installation_id = ?", (installation_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="GitHub installation not found")
            raise HTTPException(status_code=409, detail="GitHub installation is already disabled")
        row = conn.execute(
            "SELECT * FROM github_installations WHERE installation_id = ?", (installation_id,)
        ).fetchone()
        return _installation_out(conn, row)


@router.post(
    "/github/installations/{installation_id}/systems/{target_system_id}",
    response_model=GithubInstallationOut,
)
def assign_installation_to_system(
    installation_id: int,
    target_system_id: int,
    principal: Principal = Depends(require_admin),
) -> GithubInstallationOut:
    now = _now_iso()
    with get_conn() as conn:
        installation = conn.execute(
            "SELECT * FROM github_installations WHERE installation_id = ?", (installation_id,)
        ).fetchone()
        if installation is None:
            raise HTTPException(status_code=404, detail="GitHub installation not found")
        if installation["status"] != "active":
            raise HTTPException(status_code=409, detail="GitHub installation is disabled")
        system = conn.execute("SELECT 1 FROM systems WHERE id = ?", (target_system_id,)).fetchone()
        if system is None:
            raise HTTPException(status_code=404, detail="System not found")
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO github_installation_systems
                (installation_id, system_id, assigned_by_user_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (installation_id, target_system_id, principal.user_id, now),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="GitHub installation is already assigned to this System")
        return _installation_out(conn, installation)


@router.delete(
    "/github/installations/{installation_id}/systems/{target_system_id}",
    status_code=204,
)
def unassign_installation_from_system(
    installation_id: int,
    target_system_id: int,
    principal: Principal = Depends(require_admin),
) -> None:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM github_installation_systems WHERE installation_id = ? AND system_id = ?",
            (installation_id, target_system_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="GitHub installation assignment not found")


@router.get("/github/system-installations", response_model=List[GithubInstallationOut])
def list_system_installations(
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> List[GithubInstallationOut]:
    """List only this System's active assigned installations for connection setup."""
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        rows = conn.execute(
            """
            SELECT i.* FROM github_installations AS i
            JOIN github_installation_systems AS s ON s.installation_id = i.installation_id
            WHERE s.system_id = ? AND i.status = 'active'
            ORDER BY i.installation_id
            """,
            (system_id,),
        ).fetchall()
        return [_installation_out(conn, row) for row in rows]


@router.get(
    "/github/installations/{installation_id}/repositories",
    response_model=List[GithubInstallationRepositoryOut],
)
def list_installation_repositories_endpoint(
    installation_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> List[GithubInstallationRepositoryOut]:
    """Read-only: list repositories reachable by a GitHub App installation, so
    the Dashboard's connection-creation form can offer a picker instead of
    requiring the developer to type owner/repo by hand. Same authorization as
    connection management (admin or system owner); no installation token is
    ever returned (Principle 5/8)."""
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        _require_installation_assignment(conn, installation_id, system_id)

    try:
        installation_token = create_installation_token(installation_id)
        repositories = list_installation_repositories(installation_token.token)
    except GitHubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return [GithubInstallationRepositoryOut(**repo) for repo in repositories]


@router.post("/github/connections", response_model=GithubConnectionOut, status_code=201)
def create_connection(
    payload: GithubConnectionCreate,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubConnectionOut:
    owner = payload.owner.strip()
    repo = payload.repo.strip()
    if not _OWNER_REPO_RE.match(owner):
        raise HTTPException(status_code=422, detail="Invalid owner")
    if not _OWNER_REPO_RE.match(repo):
        raise HTTPException(status_code=422, detail="Invalid repo")
    if payload.installation_id <= 0:
        raise HTTPException(status_code=422, detail="installation_id must be a positive integer")

    api_base = default_api_base_url()
    web_base = default_web_base_url()
    clone_url = f"{web_base}/{owner}/{repo}.git"
    now = _now_iso()

    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        _require_installation_assignment(conn, payload.installation_id, system_id)
        try:
            cur = conn.execute(
                """
                INSERT INTO github_connections (
                    system_id, api_base_url, web_base_url, owner, repo, clone_url,
                    installation_id, default_branch, credential_type, status,
                    last_error, created_by_user_id, updated_by_user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'github_app', 'pending',
                          NULL, ?, ?, ?, ?)
                """,
                (
                    system_id,
                    api_base,
                    web_base,
                    owner,
                    repo,
                    clone_url,
                    payload.installation_id,
                    principal.user_id,
                    principal.user_id,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="A connection for this owner/repo already exists in this system",
            )
        row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _connection_out(row)


@router.get("/github/connections", response_model=List[GithubConnectionOut])
def list_connections(
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> List[GithubConnectionOut]:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        rows = conn.execute(
            "SELECT * FROM github_connections WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
    return [_connection_out(row) for row in rows]


@router.get("/github/connections/{connection_id}", response_model=GithubConnectionOut)
def get_connection(
    connection_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubConnectionOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        row = _get_connection_or_404(conn, connection_id, system_id)
    return _connection_out(row)


@router.post("/github/connections/{connection_id}/verify", response_model=GithubConnectionOut)
def verify_connection(
    connection_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubConnectionOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        row = _get_connection_or_404(conn, connection_id, system_id)
        _require_installation_assignment(conn, row["installation_id"], system_id)

    now = _now_iso()
    try:
        installation_token = create_installation_token(
            row["installation_id"], api_base_url=row["api_base_url"]
        )
        repo_data = get_repository(
            row["owner"],
            row["repo"],
            installation_token.token,
            api_base_url=row["api_base_url"],
        )
    except GitHubAppError as exc:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE github_connections
                SET status = 'error', last_error = ?, updated_by_user_id = ?, updated_at = ?
                WHERE id = ? AND system_id = ?
                """,
                (str(exc), principal.user_id, now, connection_id, system_id),
            )
    else:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE github_connections
                SET status = 'connected', default_branch = ?, last_error = NULL,
                    updated_by_user_id = ?, updated_at = ?
                WHERE id = ? AND system_id = ?
                """,
                (
                    repo_data.get("default_branch"),
                    principal.user_id,
                    now,
                    connection_id,
                    system_id,
                ),
            )

    with get_conn() as conn:
        row = _get_connection_or_404(conn, connection_id, system_id)
    return _connection_out(row)


@router.post("/github/connections/{connection_id}/sync", response_model=GithubConnectionOut)
def sync_connection(
    connection_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubConnectionOut:
    """Bring the managed mirror up to date and record the resolved default
    branch commit SHA. Requires a verified connection (status='connected');
    on failure the connection is marked status='error' with a sanitized
    (token-free) `last_error` (repo_manager.RepoManagerError / GitHubAppError
    text never contains the installation token)."""
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        row = _get_connection_or_404(conn, connection_id, system_id)
        _require_installation_assignment(conn, row["installation_id"], system_id)

    if row["status"] != "connected":
        raise HTTPException(
            status_code=409,
            detail="Connection must be verified (status=connected) before sync",
        )

    now = _now_iso()
    try:
        installation_token = create_installation_token(
            row["installation_id"], api_base_url=row["api_base_url"]
        )
        repo_manager.ensure_mirror(row, installation_token.token)
        branch = row["default_branch"]
        if not branch:
            raise RepoManagerError(
                "Connection has no default_branch; verify the connection first"
            )
        commit_sha = repo_manager.resolve_mirror_branch_sha(connection_id, branch)
    except (GitHubAppError, RepoManagerError) as exc:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE github_connections
                SET status = 'error', last_error = ?, updated_by_user_id = ?, updated_at = ?
                WHERE id = ? AND system_id = ?
                """,
                (str(exc), principal.user_id, now, connection_id, system_id),
            )
            row = _get_connection_or_404(conn, connection_id, system_id)
        return _connection_out(row)

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE github_connections
            SET last_synced_at = ?, last_synced_commit_sha = ?, last_error = NULL,
                updated_by_user_id = ?, updated_at = ?
            WHERE id = ? AND system_id = ?
            """,
            (now, commit_sha, principal.user_id, now, connection_id, system_id),
        )
        row = _get_connection_or_404(conn, connection_id, system_id)
    return _connection_out(row)


@router.get(
    "/github/connections/{connection_id}/repository-status",
    response_model=GithubRepositoryStatusOut,
)
def get_repository_status(
    connection_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubRepositoryStatusOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        row = _get_connection_or_404(conn, connection_id, system_id)

    mirror_exists = False
    mirror_rel_path: Optional[str] = None
    try:
        mirror = repo_manager.mirror_path(connection_id)
        root = repo_manager.repository_root()
        mirror_exists = os.path.isdir(os.path.join(mirror, ".git"))
        mirror_rel_path = os.path.relpath(mirror, root).replace(os.sep, "/")
    except RepoManagerError:
        mirror_exists = False
        mirror_rel_path = None

    return GithubRepositoryStatusOut(
        connection_id=connection_id,
        mirror_exists=mirror_exists,
        mirror_path=mirror_rel_path,
        default_branch=row["default_branch"],
        last_synced_at=row["last_synced_at"],
        last_synced_commit_sha=row["last_synced_commit_sha"],
    )


@router.delete("/github/connections/{connection_id}", response_model=GithubConnectionOut)
def delete_connection(
    connection_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> GithubConnectionOut:
    now = _now_iso()
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        _get_connection_or_404(conn, connection_id, system_id)
        conn.execute(
            """
            UPDATE github_connections
            SET status = 'disconnected', updated_by_user_id = ?, updated_at = ?
            WHERE id = ? AND system_id = ?
            """,
            (principal.user_id, now, connection_id, system_id),
        )
        row = _get_connection_or_404(conn, connection_id, system_id)
    return _connection_out(row)
