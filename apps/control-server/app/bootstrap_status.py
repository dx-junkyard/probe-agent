"""Issue #265: deterministic, System-id-free "phase 0" bootstrap facts.

Before any System exists (and, more fundamentally, before any admin user
exists), the whole System-scoped guidance stack (`GET /system-state`,
`system_diagnostics`, `PrerequisiteGuide`, the header `DiagnosticsBadge`) is
unusable -- every one of those requires an `X-Probe-System-Id`. This module
is the deliberately independent, System-id-free counterpart: a small set of
finite, deterministic facts (Principle 6 -- no reasoning model, no request
context) that `GET /auth/bootstrap-status` can answer for a caller who has
neither a session token nor a System yet.

Every function here is a pure-ish `() -> value` / `(conn) -> value` reader,
mirroring `state_facts.py`'s shape but intentionally NOT living there:
`state_facts.py`'s own docstring scopes it to System-scoped DB reads, and
these facts must be answerable with no System in scope at all.

Nothing here returns a secret: no usernames, no key values, no hostnames,
no paths. `admin_exists` / `auth_mode` / `llm_configured` are coarse
booleans/enum tokens, not configuration values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .auth import auth_enabled
from .db import get_conn
from .environment import is_production
from .llm import KNOWN_PROVIDERS, PROVIDER_KEY_ENV


def admin_exists(conn=None) -> bool:
    """True if at least one active admin user exists.

    This is the System-id-free sibling of
    `system_diagnostics._check_auth_scope`'s "0 admins" detection: that
    check counts *any* active user (any role) to decide its open/anonymous
    -mode diagnostic warning, scoped to one already-selected System. Here we
    need a stricter, independent fact -- "does an admin exist at all" -- to
    answer the pre-System, pre-login question of whether the first-boot
    bootstrap step (`CONTROL_ADMIN_USERNAME`/`CONTROL_ADMIN_PASSWORD` +
    restart) has ever completed. It takes no `system_id` because admin users
    are not System-scoped.
    """
    if conn is not None:
        row = conn.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1"
        ).fetchone()
        return row is not None
    with get_conn() as c:
        row = c.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1"
        ).fetchone()
    return row is not None


def llm_configured() -> bool:
    """Deterministic env-*presence* check only -- never validates the key.

    Mirrors `system_diagnostics._api_key_status`'s presence logic (generic
    `LLM_API_KEY` or the provider-specific key env var), but this endpoint
    must stay a cheap, side-effect-free fact lookup rather than a
    diagnostics-grade check, so it does not call that private helper or
    attempt any live API probe. `LLM_PROVIDER=mock` is deterministically
    "configured" (a known, valid value that needs no key), matching
    `_check_llm_base_config`'s treatment of mock as a non-error state.
    """
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        return False
    if provider == "mock":
        return True
    generic = bool((os.getenv("LLM_API_KEY") or "").strip())
    specific_env = PROVIDER_KEY_ENV.get(provider)
    specific = bool((os.getenv(specific_env) or "").strip()) if specific_env else False
    return generic or specific


@dataclass(frozen=True)
class BootstrapStatus:
    admin_exists: bool
    auth_mode: str  # "anonymous" | "user" -- mirrors auth.auth_enabled()
    llm_configured: bool
    environment: str  # "development" | "production" -- environment.control_env()


def get_bootstrap_status() -> BootstrapStatus:
    return BootstrapStatus(
        admin_exists=admin_exists(),
        auth_mode="user" if auth_enabled() else "anonymous",
        llm_configured=llm_configured(),
        environment="production" if is_production() else "development",
    )
