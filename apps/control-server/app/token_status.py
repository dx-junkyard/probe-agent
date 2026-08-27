"""Issue #368: the one place that decides an API/session token's lifecycle status.

The Access Token list used to colour a row from `revoked` alone, so a token
whose `expires_at` was already in the past still rendered as a green
``active``. The status is a small, explicitly enumerated set decided by direct
structural comparison against the clock (Core Design Principle 6), so it is
computed **server-side** and returned on ``TokenOut``. The Dashboard renders
it; it must never re-derive it, or the two definitions drift apart the moment
one of them is touched.
"""

from typing import Optional

from .models import TokenStatus

# How long before `expires_at` a still-usable token is reported as
# `expiring_soon`. Named (not inlined) because it is the one tunable in this
# module and both the API contract and the tests refer to it.
#
# 7 days: `_SESSION_TTL_SECONDS` in `routes/auth.py` gives a login session
# exactly this lifetime, and the Connect SDK flow issues API tokens in whole
# days (`expires_in_days`). A week is the smallest window that still lets a
# developer rotate an SDK credential during a normal work week before the
# probe stops authenticating.
TOKEN_EXPIRING_SOON_SECONDS: float = 7 * 24 * 3600


def classify_token_status(
    *,
    revoked: bool,
    expires_at: Optional[float],
    now: float,
) -> TokenStatus:
    """Classify a token row into the finite `TokenStatus` set.

    First-match rules, in this order:

    1. ``revoked`` -> ``revoked``. Revocation is an explicit human/admin act
       and outranks expiry: a token that is both revoked and expired reports
       ``revoked``, because "someone turned this off" is the fact the operator
       needs, and re-issuing does not undo it.
    2. ``expires_at is None`` -> ``active``. A NULL column means "no expiry",
       never "expired".
    3. ``expires_at <= now`` -> ``expired``. The boundary is inclusive: at
       exactly the expiry instant the credential is already rejected by
       `auth.py`, so the list must not still call it usable.
    4. remaining time ``<= TOKEN_EXPIRING_SOON_SECONDS`` -> ``expiring_soon``
       (inclusive edge, so exactly one threshold's worth of time left already
       warns).
    5. otherwise -> ``active``.

    `expires_at` and `now` are POSIX timestamps (seconds since the UTC epoch),
    so the result carries no timezone dependency — a token issued and read in
    different local timezones classifies identically.
    """

    if revoked:
        return "revoked"
    if expires_at is None:
        return "active"
    if expires_at <= now:
        return "expired"
    if expires_at - now <= TOKEN_EXPIRING_SOON_SECONDS:
        return "expiring_soon"
    return "active"


def token_expires_in_seconds(
    *, expires_at: Optional[float], now: float
) -> Optional[float]:
    """Seconds remaining until `expires_at`, or ``None`` when there is no expiry.

    Clamped at 0 so an already-expired token never reports a negative
    countdown. Computed at the same instant as `classify_token_status` so the
    badge and the relative time on the same row can never disagree.
    """

    if expires_at is None:
        return None
    return max(0.0, expires_at - now)
