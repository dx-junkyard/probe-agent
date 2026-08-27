"""Deterministic secret redaction at the Control Server LLM boundary.

Only the finite, documented credential signatures below are recognized.  The
scanner deliberately does not guess based on entropy or redact generic words,
so prompt semantics outside known secret values remain unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Pattern


AWS_ACCESS_KEY_ID_MARKER = "[REDACTED_SECRET:aws_access_key_id]"
GITHUB_TOKEN_MARKER = "[REDACTED_SECRET:github_token]"
GITHUB_FINE_GRAINED_PAT_MARKER = "[REDACTED_SECRET:github_fine_grained_pat]"
PEM_PRIVATE_KEY_MARKER = "[REDACTED_SECRET:pem_private_key]"


@dataclass(frozen=True)
class _RedactionRule:
    pattern: Pattern[str]
    marker: str


# AWS access key IDs are 20 uppercase alphanumeric characters.  AKIA denotes
# long-lived keys and ASIA denotes temporary STS credentials.  Identifier
# boundaries prevent a 20-character prefix of a longer value from matching.
_AWS_ACCESS_KEY_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"
)

# GitHub documents the prefixes, but suffixes are not all fixed length.  In
# particular, the staged stateless ``ghs_APPID_JWT`` format introduced in 2026
# makes an "exactly 40 characters" assumption unsafe:
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github
# Keep matching finite with explicit 20..512 / 20..255 policy bounds and a
# conservative token alphabet.  The final GitHub-token character excludes a
# dot so normal sentence punctuation is not consumed; dots remain valid inside
# stateless/JWT-style values.
_GITHUB_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_~-])gh[pousr]_"
    r"[A-Za-z0-9_~-](?:[A-Za-z0-9._~-]{18,510}[A-Za-z0-9_~-])"
    r"(?![A-Za-z0-9_~-])"
)
_GITHUB_FINE_GRAINED_PAT = re.compile(
    r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,255}(?![A-Za-z0-9_])"
)

# Match the complete private-key block, including its body, and require the
# END label to agree with BEGIN.  Incomplete/mismatched blocks and public PEM
# material are intentionally left unchanged.
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>(?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY)-----"
    r"[ \t]*\r?\n.+?\r?\n-----END (?P=label)-----",
    re.DOTALL,
)


_RULES = (
    _RedactionRule(_PEM_PRIVATE_KEY, PEM_PRIVATE_KEY_MARKER),
    _RedactionRule(_AWS_ACCESS_KEY_ID, AWS_ACCESS_KEY_ID_MARKER),
    _RedactionRule(_GITHUB_FINE_GRAINED_PAT, GITHUB_FINE_GRAINED_PAT_MARKER),
    _RedactionRule(_GITHUB_TOKEN, GITHUB_TOKEN_MARKER),
)


def redact_secret_text(text: str) -> str:
    """Return ``text`` with only known credential signatures replaced."""
    redacted = text
    for rule in _RULES:
        redacted = rule.pattern.sub(rule.marker, redacted)
    return redacted


def redact_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Copy-on-write redaction of chat message ``content`` values.

    The input list and dictionaries are never mutated.  If no content changes,
    the original list can be returned.  Once a secret is found, only affected
    message dictionaries are copied and the list is shallow-copied once.
    """
    result: List[Dict[str, str]] = messages
    copied = False
    for index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        redacted = redact_secret_text(content)
        if redacted == content:
            continue
        if not copied:
            result = list(messages)
            copied = True
        replacement = dict(message)
        replacement["content"] = redacted
        result[index] = replacement
    return result
