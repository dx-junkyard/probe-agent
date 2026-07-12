import base64
import hashlib
import hmac
import os
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$")
        if algo != _ALGO:
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Production password policy (Issue #225). Kept independent of
# `is_production()` so it stays a plain, deterministic, unit-testable
# validator; callers decide *when* to invoke it (only when
# `environment.is_production()` is true).
PRODUCTION_PASSWORD_MIN_LENGTH = 16
PRODUCTION_PASSWORD_DENYLIST = {
    "change-me",
    "dev-secret-key",
    "password",
    "admin",
    "example",
}


def validate_production_password(username: str, password: str) -> None:
    """Reject sample/placeholder/weak passwords used in production.

    Raises `ValueError` with a human-readable reason on rejection. This is a
    structural, finite-set check (CLAUDE.md Principle 6): minimum length,
    an exact-match (case-insensitive) denylist of known sample values, and
    a case-insensitive equality check against the username.
    """
    if len(password) < PRODUCTION_PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"password must be at least {PRODUCTION_PASSWORD_MIN_LENGTH} "
            "characters in production"
        )
    if password.casefold() in PRODUCTION_PASSWORD_DENYLIST:
        raise ValueError("password is a known sample/placeholder value")
    if password.casefold() == username.casefold():
        raise ValueError("password must not match the username")
