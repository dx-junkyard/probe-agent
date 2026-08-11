"""Value-shaped credential signatures (Issue #367).

The SDK's :mod:`probe_agent.redaction` masks values by *key name*.  That is
enough for structured containers, but a payload can still carry a credential
where no key name is visible: inside a custom ``__repr__``, an exception
message, a traceback, a log line, or a URL.  This module is the second layer:
it recognises credential *values* by their documented, vendor-published shape.

Principle 6 (finite deterministic decisions) applies.  Every rule below is an
explicitly enumerated, documented credential signature -- the same category as
an exact denylist match.  There is deliberately **no** entropy scoring, no
"looks random" heuristic, and no attempt to guess unknown secret formats: a
value only matches when it carries a vendor prefix, a standard structural
marker, or an explicit assignment to a name on the finite sensitive-key list.

The Control Server imports this module directly (it already depends on
``probe_agent``; see ``apps/control-server/Dockerfile``), so the SDK boundary
and the ingestion boundary share one rule table rather than two drifting
copies.
"""

from __future__ import annotations

import re
from typing import Dict, List, Pattern, Tuple

__all__ = [
    "RULE_NAMES",
    "SECRET_VALUE_MARKER",
    "redact_secret_values",
    "find_secret_rules",
]


def secret_marker(rule_name: str) -> str:
    """Return the mask written in place of a ``rule_name`` match."""
    return f"[REDACTED_SECRET:{rule_name}]"


#: Mask used when a rule replaces only part of a match (see ``url_userinfo``).
SECRET_VALUE_MARKER = "[REDACTED_SECRET]"


# --- rule table -------------------------------------------------------------
#
# Ordering matters: the first rule that matches a region wins, so the more
# specific vendor prefixes are listed before the generic ones they overlap
# with (``anthropic_api_key`` before ``openai_api_key``; both before
# ``sensitive_assignment``).

# Names whose *assigned value* is a credential.  This is the free-text mirror
# of ``redaction.SENSITIVE_KEYS`` and exists for strings that were already
# rendered before the Control Server saw them (an older SDK's repr, a
# traceback, a candidate's stdout).  Only an explicit ``name = value`` /
# ``name: value`` form matches -- prose such as "password reset" does not.
_SENSITIVE_NAME_ALTERNATION = (
    r"api[_-]?keys?|apikeys?|x-api-key"
    r"|access[_-]?tokens?|refresh[_-]?tokens?|id[_-]?tokens?|bearer[_-]?tokens?"
    r"|tokens?|secrets?|secret[_-]?keys?|client[_-]?secrets?"
    r"|private[_-]?keys?|passwords?|passwd|passphrases?"
    r"|authorizations?|credentials?|session[_-]?ids?|sessionid|cookies?"
)

# Rules run in sequence over already-rewritten text, so a later rule must not
# treat an earlier rule's mask as the value it was looking for (which would
# both double-mask and mis-report which rule fired). The SDK's own key-based
# mask is excluded for the same reason: re-masking it would erase the
# deliberate distinction between a masked value and a user string that merely
# happens to equal the marker (``probe_agent.replay_capture``'s ``literal``
# encoding).
_SDK_KEY_MASK = "██redacted██"
_NOT_ALREADY_MASKED = r"(?!\[REDACTED_SECRET)(?!" + re.escape(_SDK_KEY_MASK) + r")"

_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    # Complete PEM private-key blocks (BEGIN and END labels must agree).
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?P<label>(?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY)-----"
            r"[ \t]*\r?\n.+?\r?\n-----END (?P=label)-----",
            re.DOTALL,
        ),
    ),
    # AWS access key IDs: AKIA (long-lived) / ASIA (STS), 20 chars total.
    (
        "aws_access_key_id",
        re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    ),
    (
        "github_fine_grained_pat",
        re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,255}(?![A-Za-z0-9_])"),
    ),
    # gh[pousr]_ prefixes.  Suffix length is not fixed across formats (the
    # stateless ``ghs_APPID_JWT`` form is much longer), so explicit policy
    # bounds are used instead of an "exactly 40 characters" assumption.
    (
        "github_token",
        re.compile(
            r"(?<![A-Za-z0-9_~-])gh[pousr]_"
            r"[A-Za-z0-9_~-](?:[A-Za-z0-9._~-]{18,510}[A-Za-z0-9_~-])"
            r"(?![A-Za-z0-9_~-])"
        ),
    ),
    (
        "anthropic_api_key",
        re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"),
    ),
    (
        "stripe_secret_key",
        re.compile(
            r"(?<![A-Za-z0-9_-])[sr]k_(?:live|test)_[0-9A-Za-z]{10,}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "openai_api_key",
        re.compile(
            r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-|admin-)?"
            r"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "slack_token",
        re.compile(r"(?<![A-Za-z0-9_-])xox[baprse]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9_-])"),
    ),
    (
        "google_api_key",
        re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
    ),
    # Signed JWTs: two base64url segments that both start with a JSON object
    # marker, plus a signature segment.
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}"
            r"\.[A-Za-z0-9_-]{6,}(?![A-Za-z0-9_-])"
        ),
    ),
    # ``Authorization: Bearer <value>`` and friends, wherever they were
    # rendered into text.
    (
        "authorization_header",
        re.compile(
            r"(?<![A-Za-z0-9_-])(?:Bearer|Basic|Token|Digest)\s+"
            r"[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    # ``scheme://user:password@host`` -- only the password segment is masked so
    # the endpoint stays diagnosable.
    (
        "url_userinfo",
        re.compile(
            r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:]+:)"
            + _NOT_ALREADY_MASKED
            + r"(?P<secret>[^\s/@]+)(?=@)"
        ),
    ),
    # ``api_key='...'`` / ``"token": "..."`` / ``PASSWORD=...`` in free text.
    # Only the value is masked; the name stays visible so the reader can see
    # *what* was removed.  The closing quote is deliberately outside the match
    # so the rendered text keeps its quote pair.
    (
        "sensitive_assignment",
        re.compile(
            # ``['\"]?`` after the name covers a quoted mapping key such as
            # ``{'api_key': '...'}`` as well as a bare ``api_key=...``.
            r"(?P<prefix>(?<![A-Za-z0-9_])(?:" + _SENSITIVE_NAME_ALTERNATION + r")"
            r"(?![A-Za-z0-9_])['\"]?\s*[=:]\s*(?P<quote>['\"])?)"
            + _NOT_ALREADY_MASKED
            + r"(?P<secret>(?(quote)[^'\"\r\n]+|[^\s'\",;)}\]]+))",
            re.IGNORECASE,
        ),
    ),
)

#: Finite set of rule names, in evaluation order.
RULE_NAMES: Tuple[str, ...] = tuple(name for name, _ in _RULES)

# Rules that mask only their ``secret`` group; every other rule masks the
# whole match.
_PARTIAL_RULES = frozenset({"url_userinfo", "sensitive_assignment"})


def _substitute(rule_name: str):
    marker = secret_marker(rule_name)

    def _replace(match: "re.Match[str]") -> str:
        if rule_name not in _PARTIAL_RULES:
            return marker
        return f"{match.group('prefix') or ''}{marker}"

    return _replace


def redact_secret_values(text: str) -> Tuple[str, List[str]]:
    """Mask known credential shapes in ``text``.

    Returns ``(redacted_text, rule_names)`` where ``rule_names`` is the sorted,
    de-duplicated list of rules that fired.  ``text`` is returned unchanged
    (and the list is empty) when nothing matched, so callers can cheaply detect
    "nothing to do".
    """
    if not text:
        return text, []
    fired: List[str] = []
    redacted = text
    for rule_name, pattern in _RULES:
        replaced, count = pattern.subn(_substitute(rule_name), redacted)
        if count:
            fired.append(rule_name)
            redacted = replaced
    return redacted, sorted(set(fired))


def find_secret_rules(text: str) -> List[str]:
    """Return the rule names that match ``text`` without rewriting it."""
    if not text:
        return []
    return sorted({name for name, pattern in _RULES if pattern.search(text)})


def redact_mapping(values: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Copy-on-write :func:`redact_secret_values` over a ``str -> str`` map."""
    fired: List[str] = []
    result = values
    copied = False
    for key, value in values.items():
        if not isinstance(value, str):
            continue
        replaced, rules = redact_secret_values(value)
        if not rules:
            continue
        if not copied:
            result = dict(values)
            copied = True
        result[key] = replaced
        fired.extend(rules)
    return result, sorted(set(fired))
