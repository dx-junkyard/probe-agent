import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException

from .db import get_conn
from .security import hash_token


@dataclass
class Principal:
    """Resolved caller identity for a request."""

    auth: str  # "token" | "legacy_api_key" | "anonymous"
    user_id: Optional[int] = None
    username: Optional[str] = None
    role: Optional[str] = None
    token_id: Optional[int] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _legacy_keys() -> Optional[set]:
    raw = os.getenv("CONTROL_API_KEYS", "").strip()
    if not raw:
        return None
    return {k.strip() for k in raw.split(",") if k.strip()}


def _users_exist() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def auth_enabled() -> bool:
    """Auth is active once any user exists or legacy keys are configured."""
    return _users_exist() or _legacy_keys() is not None


def _extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def _principal_from_token(token: str) -> Optional[Principal]:
    token_hash = hash_token(token)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT t.id AS token_id, t.revoked AS revoked, t.expires_at AS expires_at,
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
    )


async def get_principal(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> Principal:
    if not auth_enabled():
        return Principal(auth="anonymous")

    token = _extract_token(authorization, x_api_key)
    if not token:
        raise HTTPException(status_code=401, detail="Missing credentials")

    principal = _principal_from_token(token)
    if principal is not None:
        return principal

    legacy = _legacy_keys()
    if legacy is not None and token in legacy:
        return Principal(auth="legacy_api_key", role="service")

    raise HTTPException(status_code=401, detail="Invalid or revoked credentials")


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return principal
