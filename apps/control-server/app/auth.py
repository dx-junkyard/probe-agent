import os
import secrets
import time
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request

from .db import get_conn
from .security import hash_token


SESSION_COOKIE_NAME = "probe_session"
CSRF_COOKIE_NAME = "probe_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CredentialTransport = Literal[
    "authorization", "x_api_key", "cookie", "legacy_api_key", "anonymous"
]


@dataclass
class Principal:
    """Resolved caller identity for a request."""

    auth: str  # "token" | "legacy_api_key" | "anonymous"
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None
    token_id: Optional[int] = None
    token_kind: Optional[str] = None
    system_id: Optional[int] = None
    credential_fingerprint: Optional[str] = None
    transport: CredentialTransport = "anonymous"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _legacy_keys() -> Optional[set]:
    from .environment import is_production

    if is_production():
        # Legacy shared keys are forbidden in production (Issue #225); even
        # if CONTROL_API_KEYS is somehow set at runtime, never accept it as
        # a credential. Startup validation also refuses to boot with it set.
        return None
    raw = os.getenv("CONTROL_API_KEYS", "").strip()
    if not raw:
        return None
    return {k.strip() for k in raw.split(",") if k.strip()}


def _users_exist() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def auth_enabled() -> bool:
    """Auth is active once any user exists or legacy keys are configured.

    In production (`CONTROL_ENV=production`) auth is always considered
    enabled, regardless of DB state: startup (`db._enforce_auth_requirement`)
    already refuses to boot without an active admin, and this keeps
    `get_principal` fail-closed (never the anonymous no-auth MVP-compat mode)
    if that invariant were ever violated later at runtime.
    """
    from .environment import is_production

    if is_production():
        return True
    return _users_exist() or _legacy_keys() is not None


def _extract_explicit_token(
    authorization: Optional[str], x_api_key: Optional[str]
) -> Optional[Tuple[str, CredentialTransport]]:
    """Resolve explicit credentials without falling back between transports.

    The presence of Authorization takes precedence over X-Api-Key, even when
    malformed. This prevents a stale browser cookie or a secondary header from
    silently changing the identity selected by the caller.
    """
    if authorization is not None:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip(), "authorization"
        return "", "authorization"
    if x_api_key is not None:
        return x_api_key.strip(), "x_api_key"
    return None


def _principal_from_token(
    token: str, *, transport: CredentialTransport
) -> Optional[Principal]:
    token_hash = hash_token(token)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT t.id AS token_id, t.kind AS token_kind, t.system_id AS system_id,
                   t.revoked AS revoked, t.expires_at AS expires_at,
                   u.id AS user_id, u.username AS username, u.role AS role,
                   u.is_active AS is_active
            FROM api_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
    if row is None:
        return None
    if row["revoked"]:
        return None
    if row["expires_at"] is not None and row["expires_at"] < time.time():
        return None
    if not row["is_active"]:
        return None
    return Principal(
        auth="token",
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        token_id=row["token_id"],
        token_kind=row["token_kind"],
        system_id=row["system_id"],
        credential_fingerprint=token_hash,
        transport=transport,
    )


def _enforce_cookie_csrf(request: Request) -> None:
    if request.method.upper() in _SAFE_METHODS:
        return
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or not secrets.compare_digest(
        cookie_token, header_token
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "csrf_validation_failed",
                "message": "A matching CSRF cookie and X-CSRF-Token header are required",
            },
        )


async def get_principal(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> Principal:
    if not auth_enabled():
        return Principal(auth="anonymous", transport="anonymous")

    explicit = _extract_explicit_token(authorization, x_api_key)
    if explicit is not None:
        token, transport = explicit
    else:
        token = request.cookies.get(SESSION_COOKIE_NAME, "")
        transport = "cookie"
    if not token:
        raise HTTPException(status_code=401, detail="Missing credentials")

    principal = _principal_from_token(token, transport=transport)
    if principal is not None:
        if transport == "cookie":
            # Browser cookies are reserved for login sessions. An SDK token
            # copied into the cookie jar must not acquire browser semantics.
            if principal.token_kind != "session":
                raise HTTPException(status_code=401, detail="Invalid session cookie")
            _enforce_cookie_csrf(request)
        # SDK API tokens are governed by the dedicated /traces token limiter.
        # Login sessions use a deliberately loose user-scoped management
        # limiter on every authenticated API path.
        if principal.user_id is not None and principal.token_kind != "api":
            from .resource_limits import (
                ResourceLimitExceeded,
                enforce_management_rate_limit,
            )

            try:
                enforce_management_rate_limit(principal.user_id)
            except ResourceLimitExceeded as exc:
                raise HTTPException(
                    status_code=429,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
        return principal

    legacy = _legacy_keys()
    if transport != "cookie" and legacy is not None and token in legacy:
        return Principal(
            auth="legacy_api_key",
            role="service",
            credential_fingerprint=hash_token(token),
            transport=transport,
        )

    raise HTTPException(status_code=401, detail="Invalid or revoked credentials")


_SDK_TOKEN_REJECTED_DETAIL = (
    "SDK API tokens cannot access management APIs; use a login session"
)


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.token_kind == "api":
        raise HTTPException(status_code=403, detail=_SDK_TOKEN_REJECTED_DETAIL)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return principal


async def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    """Require a caller backed by a user (login) session.

    SDK API tokens (`token_kind == "api"`) are rejected unconditionally, not
    just in production: they are scoped to data-plane routes only (traces,
    policies, ...), which depend on `get_principal`/`get_system_id` instead.
    Legacy keys and anonymous callers (no `user_id`) also fail.
    """
    if principal.token_kind == "api":
        raise HTTPException(status_code=403, detail=_SDK_TOKEN_REJECTED_DETAIL)
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="A user account is required")
    return principal


def _legacy_system_id() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM systems WHERE name = 'Legacy System' AND owner_user_id IS NULL"
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Legacy system is not initialized")
    return row["id"]


async def get_system_id(
    x_probe_system_id: Optional[int] = Header(default=None),
    principal: Principal = Depends(get_principal),
) -> AsyncIterator[int]:
    """Resolve the system for data-plane and dashboard requests.

    SDK API tokens are permanently bound to one system. Login sessions choose
    a system with X-Probe-System-Id, while legacy/anonymous callers retain the
    pre-system behavior through the automatically created Legacy System.
    """
    system_id: int
    if principal.token_kind == "api":
        if principal.system_id is None:
            raise HTTPException(status_code=403, detail="API token is not bound to a system")
        system_id = principal.system_id
    elif principal.auth in ("legacy_api_key", "anonymous"):
        system_id = _legacy_system_id()
    else:
        if x_probe_system_id is None:
            raise HTTPException(status_code=400, detail="X-Probe-System-Id is required")

        with get_conn() as conn:
            row = conn.execute(
                "SELECT owner_user_id FROM systems WHERE id = ?", (x_probe_system_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="System not found")
        if not principal.is_admin and row["owner_user_id"] != principal.user_id:
            raise HTTPException(status_code=403, detail="System access denied")
        system_id = x_probe_system_id

    from .resource_limits import reset_current_system_id, set_current_system_id

    quota_context = set_current_system_id(system_id)
    try:
        yield system_id
    finally:
        reset_current_system_id(quota_context)
