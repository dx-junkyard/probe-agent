import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import Principal, get_principal, require_admin, require_user
from ..db import get_conn
from ..models import (
    LoginRequest,
    MeResponse,
    PasswordResetRequest,
    RoleUpdate,
    SelfTokenCreate,
    TokenCreate,
    TokenCreateResponse,
    TokenOut,
    TokenResponse,
    UserCreate,
    UserOut,
)
from ..security import generate_token, hash_password, hash_token, verify_password

router = APIRouter()

# Login session tokens default to a fixed lifetime.
_SESSION_TTL_SECONDS = 7 * 24 * 3600


def _user_out(row) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


def _token_out(row) -> TokenOut:
    return TokenOut(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        user_id=row["user_id"],
        system_id=row["system_id"],
        revoked=bool(row["revoked"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


_TOKEN_COLUMNS = "id, name, kind, user_id, system_id, revoked, created_at, expires_at"


def _issue_token(
    conn,
    *,
    user_id: int,
    kind: str,
    name: Optional[str],
    expires_at: Optional[float],
    system_id: Optional[int] = None,
) -> str:
    raw = generate_token()
    conn.execute(
        """
        INSERT INTO api_tokens
            (token_hash, name, kind, user_id, system_id, revoked, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (hash_token(raw), name, kind, user_id, system_id, time.time(), expires_at),
    )
    return raw


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, is_active FROM users WHERE username = ?",
            (payload.username,),
        ).fetchone()
        if row is None or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if not row["is_active"]:
            raise HTTPException(status_code=403, detail="User is deactivated")
        expires_at = time.time() + _SESSION_TTL_SECONDS
        raw = _issue_token(
            conn,
            user_id=row["id"],
            kind="session",
            name="login session",
            expires_at=expires_at,
        )
    return TokenResponse(access_token=raw, expires_at=expires_at)


@router.post("/auth/logout", status_code=204)
def logout(principal: Principal = Depends(get_principal)) -> Response:
    # Revoke the calling token so it cannot be replayed. Legacy API keys have
    # no token row, so logout is a no-op for them.
    if principal.token_id is not None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE api_tokens SET revoked = 1 WHERE id = ?", (principal.token_id,)
            )
    return Response(status_code=204)


@router.get("/auth/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    if principal.user_id is None:
        return MeResponse(user=None, auth=principal.auth, system_id=principal.system_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (principal.user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return MeResponse(
        user=_user_out(row), auth=principal.auth, system_id=principal.system_id
    )


def _resolve_token_system(
    conn, principal: Principal, requested_system_id: Optional[int]
) -> int:
    if requested_system_id is None:
        if principal.is_admin:
            row = conn.execute(
                """
                SELECT id FROM systems
                WHERE name = 'Legacy System' AND owner_user_id IS NULL
                """
            ).fetchone()
            if row is not None:
                return row["id"]
        row = conn.execute(
            "SELECT id FROM systems WHERE owner_user_id = ? ORDER BY id LIMIT 1",
            (principal.user_id,),
        ).fetchone()
        if row is not None:
            return row["id"]
        now = time.time()
        cur = conn.execute(
            """
            INSERT INTO systems
                (name, environment, description, owner_user_id, created_at, updated_at)
            VALUES ('Default System', '', '', ?, ?, ?)
            """,
            (principal.user_id, now, now),
        )
        return cur.lastrowid

    row = conn.execute(
        "SELECT owner_user_id FROM systems WHERE id = ?", (requested_system_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="System not found")
    if not principal.is_admin and row["owner_user_id"] != principal.user_id:
        raise HTTPException(status_code=403, detail="System access denied")
    return requested_system_id


@router.get("/users", response_model=List[UserOut])
def list_users(_: Principal = Depends(require_admin)) -> List[UserOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users ORDER BY id"
        ).fetchall()
    return [_user_out(r) for r in rows]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, _: Principal = Depends(require_admin)) -> UserOut:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (payload.username,)
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Username already exists")
        cur = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (payload.username, hash_password(payload.password), payload.role, time.time()),
        )
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return _user_out(row)


def _active_admin_ids(conn) -> set:
    rows = conn.execute(
        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchall()
    return {r["id"] for r in rows}


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, _: Principal = Depends(require_admin)) -> UserOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        # Refuse to disable the only remaining active admin.
        if row["role"] == "admin" and row["is_active"] and _active_admin_ids(conn) == {user_id}:
            raise HTTPException(
                status_code=409, detail="Cannot deactivate the last active admin"
            )
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        # Revoke all tokens belonging to the deactivated user.
        conn.execute("UPDATE api_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _user_out(row)


@router.post("/users/{user_id}/password", response_model=UserOut)
def reset_password(
    user_id: int, payload: PasswordResetRequest, _: Principal = Depends(require_admin)
) -> UserOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
        # Old sessions were obtained with the old password; cut them off.
        # API tokens are independent credentials and stay valid.
        conn.execute(
            "UPDATE api_tokens SET revoked = 1 WHERE user_id = ? AND kind = 'session'",
            (user_id,),
        )
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _user_out(row)


@router.put("/users/{user_id}/role", response_model=UserOut)
def update_role(
    user_id: int, payload: RoleUpdate, _: Principal = Depends(require_admin)
) -> UserOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        # Refuse to demote the only remaining active admin.
        if (
            row["role"] == "admin"
            and row["is_active"]
            and payload.role != "admin"
            and _active_admin_ids(conn) == {user_id}
        ):
            raise HTTPException(
                status_code=409, detail="Cannot demote the last active admin"
            )
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (payload.role, user_id))
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _user_out(row)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: Principal = Depends(require_admin)) -> Response:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        # Self-delete is out of scope for the MVP (see issue #14).
        if user_id == admin.user_id:
            raise HTTPException(status_code=409, detail="Cannot delete your own account")
        # Refuse to remove the only remaining active admin.
        if row["role"] == "admin" and row["is_active"] and _active_admin_ids(conn) == {user_id}:
            raise HTTPException(
                status_code=409, detail="Cannot delete the last active admin"
            )
        # Drop the user's tokens so any existing session/API token stops working.
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return Response(status_code=204)


@router.get("/tokens/me", response_model=List[TokenOut])
def list_my_tokens(principal: Principal = Depends(require_user)) -> List[TokenOut]:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE user_id = ? ORDER BY id",
            (principal.user_id,),
        ).fetchall()
    return [_token_out(r) for r in rows]


@router.post("/tokens/me", response_model=TokenCreateResponse, status_code=201)
def create_my_token(
    payload: SelfTokenCreate, principal: Principal = Depends(require_user)
) -> TokenCreateResponse:
    expires_at: Optional[float] = None
    if payload.expires_in_days is not None:
        expires_at = time.time() + payload.expires_in_days * 24 * 3600

    with get_conn() as conn:
        system_id = _resolve_token_system(conn, principal, payload.system_id)
        raw = _issue_token(
            conn,
            user_id=principal.user_id,
            kind="api",
            name=payload.name,
            expires_at=expires_at,
            system_id=system_id,
        )
        row = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE token_hash = ?",
            (hash_token(raw),),
        ).fetchone()
    return TokenCreateResponse(**_token_out(row).model_dump(), token=raw)


@router.post("/tokens/me/{token_id}/revoke", response_model=TokenOut)
def revoke_my_token(
    token_id: int, principal: Principal = Depends(require_user)
) -> TokenOut:
    with get_conn() as conn:
        # 404 for both "does not exist" and "owned by someone else" so the
        # endpoint does not leak other users' token ids.
        cur = conn.execute(
            "UPDATE api_tokens SET revoked = 1 WHERE id = ? AND user_id = ?",
            (token_id, principal.user_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Token not found")
        row = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE id = ?", (token_id,)
        ).fetchone()
    return _token_out(row)


@router.get("/tokens", response_model=List[TokenOut])
def list_tokens(_: Principal = Depends(require_admin)) -> List[TokenOut]:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens ORDER BY id"
        ).fetchall()
    return [_token_out(r) for r in rows]


@router.post("/tokens", response_model=TokenCreateResponse, status_code=201)
def create_token(
    payload: TokenCreate, admin: Principal = Depends(require_admin)
) -> TokenCreateResponse:
    owner_id = payload.user_id if payload.user_id is not None else admin.user_id
    if owner_id is None:
        raise HTTPException(status_code=400, detail="No token owner could be determined")

    expires_at: Optional[float] = None
    if payload.expires_in_days is not None:
        expires_at = time.time() + payload.expires_in_days * 24 * 3600

    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE id = ? AND is_active = 1", (owner_id,)
        ).fetchone()
        if owner is None:
            raise HTTPException(status_code=404, detail="Token owner not found or inactive")
        system_principal = Principal(
            auth=admin.auth,
            user_id=owner_id,
            role=admin.role,
        )
        system_id = _resolve_token_system(conn, system_principal, payload.system_id)
        raw = _issue_token(
            conn,
            user_id=owner_id,
            kind="api",
            name=payload.name,
            expires_at=expires_at,
            system_id=system_id,
        )
        row = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE token_hash = ?",
            (hash_token(raw),),
        ).fetchone()
    return TokenCreateResponse(**_token_out(row).model_dump(), token=raw)


@router.post("/tokens/{token_id}/revoke", response_model=TokenOut)
def revoke_token(token_id: int, _: Principal = Depends(require_admin)) -> TokenOut:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked = 1 WHERE id = ?", (token_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Token not found")
        row = conn.execute(
            f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE id = ?", (token_id,)
        ).fetchone()
    return _token_out(row)
