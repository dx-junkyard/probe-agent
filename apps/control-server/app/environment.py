"""Deployment environment classification (Issue #225).

`CONTROL_ENV` selects between the permissive local/dev defaults used
throughout the MVP and a strict, fail-closed production mode. Per CLAUDE.md
Principle 6, this is a deterministic decision over a small explicit finite
set: any value outside {"development", "production"} is a misconfiguration
and must fail closed rather than silently falling back to development's
permissive behavior.
"""

import os

_ALLOWED_ENVIRONMENTS = {"development", "production"}


def control_env() -> str:
    """Return the effective `CONTROL_ENV`, defaulting to `development`.

    Raises `RuntimeError` for any value outside the finite allowed set so an
    operator typo (e.g. `CONTROL_ENV=prod` or `CONTROL_ENV=staging`) fails
    startup instead of silently running with development's permissive
    defaults.
    """
    raw = os.getenv("CONTROL_ENV", "").strip().lower()
    if not raw:
        return "development"
    if raw not in _ALLOWED_ENVIRONMENTS:
        raise RuntimeError(
            f"CONTROL_ENV={raw!r} is not a recognized environment; expected "
            f"one of {sorted(_ALLOWED_ENVIRONMENTS)} (empty/unset defaults to "
            "'development')."
        )
    return raw


def is_production() -> bool:
    return control_env() == "production"
