"""Issue #367: no plaintext credential may leave the SDK.

The leak these tests protect against is specific: key-based redaction masks
``{"api_key": ...}`` but never saw ``Config(api_key=...)``, because ``repr``
renders an object's attributes without ever consulting a mapping key.
"""

import dataclasses
import enum
import uuid
from datetime import datetime

import pytest

from probe_agent import redaction as R
from probe_agent import secret_patterns as SP


# A value that is unmistakable in an assertion failure and matches no rule by
# accident: the tests below assert it never appears in any rendered payload.
SECRET = "sk-live-CANARYvalue0123456789"
PLAIN_SECRET = "hunter2-canary-passphrase"


# --- value-shaped credential signatures -------------------------------------


@pytest.mark.parametrize(
    "text, rule",
    [
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key_id"),
        ("ASIAIOSFODNN7EXAMPLE", "aws_access_key_id"),
        ("ghp_" + "a" * 36, "github_token"),
        ("github_pat_" + "b" * 40, "github_fine_grained_pat"),
        ("sk-ant-api03-" + "c" * 20, "anthropic_api_key"),
        ("sk-proj-" + "d" * 24, "openai_api_key"),
        ("sk_live_" + "e" * 20, "stripe_secret_key"),
        ("xoxb-1234567890-abcdefghij", "slack_token"),
        ("AIza" + "f" * 35, "google_api_key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.c2lnbmF0dXJl", "jwt"),
        ("Authorization: Bearer abcdefgh12345678", "authorization_header"),
    ],
)
def test_documented_credential_shapes_are_masked(text, rule):
    redacted, rules = SP.redact_secret_values(text)
    assert rule in rules
    assert SP.secret_marker(rule) in redacted


@pytest.mark.parametrize(
    "text",
    [
        "the user requested a password reset",
        "session token expired, please sign in again",
        "authorization is required for this endpoint",
        "normal prose with no credential in it at all",
        "def build_token(kind): return kind",
        # A short identifier is not a credential shape.
        "id=42",
    ],
)
def test_prose_is_not_redacted(text):
    redacted, rules = SP.redact_secret_values(text)
    assert rules == []
    assert redacted == text


def test_assignment_masks_only_the_value_and_keeps_quotes_balanced():
    redacted, rules = SP.redact_secret_values(f"Settings(password='{PLAIN_SECRET}')")
    assert rules == ["sensitive_assignment"]
    assert PLAIN_SECRET not in redacted
    # The name stays readable, and the quote pair survives.
    assert redacted == (
        "Settings(password='" + SP.secret_marker("sensitive_assignment") + "')"
    )


def test_quoted_mapping_key_assignment_is_masked():
    redacted, rules = SP.redact_secret_values(
        "{'api_key': '" + PLAIN_SECRET + "', 'host': 'db'}"
    )
    assert rules == ["sensitive_assignment"]
    assert PLAIN_SECRET not in redacted
    assert "'host': 'db'" in redacted


def test_url_userinfo_masks_only_the_password():
    redacted, rules = SP.redact_secret_values("postgres://svc:s3cr3tpw@db:5432/app")
    assert rules == ["url_userinfo"]
    assert "s3cr3tpw" not in redacted
    # The endpoint stays diagnosable.
    assert "postgres://svc:" in redacted and "@db:5432/app" in redacted


def test_rules_do_not_re_mask_an_earlier_rule_s_output():
    """A mask must not itself look like a secret to the next rule."""
    redacted, rules = SP.redact_secret_values(f"api_key={SECRET}")
    assert rules == ["openai_api_key"]
    assert redacted.count("REDACTED_SECRET") == 1


def test_sdk_key_mask_is_not_re_masked():
    """The key-based mask must stay distinguishable from a value mask."""
    text = "{'secret': " + R.REDACTION_MARKER + "}"
    redacted, rules = SP.redact_secret_values(text)
    assert rules == []
    assert redacted == text


def test_redaction_is_idempotent():
    once, _ = SP.redact_secret_values(f"token={SECRET} and AKIAIOSFODNN7EXAMPLE")
    twice, rules = SP.redact_secret_values(once)
    assert twice == once
    assert rules == []


# --- object-aware structural redaction --------------------------------------


class PlainConfig:
    def __init__(self):
        self.api_key = SECRET
        self.endpoint = "https://api.example.com"

    def __repr__(self):  # the exact leak path: repr prints every attribute
        return f"PlainConfig(api_key={self.api_key!r}, endpoint={self.endpoint!r})"


@dataclasses.dataclass
class DataclassConfig:
    token: str
    retries: int


class SlottedConfig:
    __slots__ = ("password", "user")

    def __init__(self):
        self.password = SECRET
        self.user = "svc"


class Role(enum.Enum):
    ADMIN = "admin"


def _rendered(value):
    """Render exactly as the decorator's payload path does."""
    from probe_agent.decorator import _payload_repr

    return _payload_repr(value)


def test_object_attribute_is_masked_despite_custom_repr():
    rendered = _rendered(PlainConfig())
    assert SECRET not in rendered
    # The type is still identifiable -- redaction must not blind the reader.
    assert "PlainConfig" in rendered
    assert "https://api.example.com" in rendered


def test_dataclass_field_is_masked():
    rendered = _rendered(DataclassConfig(token=SECRET, retries=3))
    assert SECRET not in rendered
    assert "DataclassConfig" in rendered
    assert "retries=3" in rendered


def test_slots_attribute_is_masked():
    rendered = _rendered(SlottedConfig())
    assert SECRET not in rendered
    assert "user='svc'" in rendered


def test_object_nested_in_dict_and_list_is_masked():
    rendered = _rendered({"configs": [PlainConfig(), {"inner": DataclassConfig(SECRET, 1)}]})
    assert SECRET not in rendered


def test_secret_under_a_harmless_attribute_name_is_still_masked():
    """No key name identifies this one -- the value-shape layer must catch it."""

    class Endpoint:
        def __init__(self):
            self.url = "https://svc:" + PLAIN_SECRET + "@api.example.com"

    rendered = _rendered(Endpoint())
    assert PLAIN_SECRET not in rendered


def test_uninspectable_object_is_never_rendered_verbatim():
    """An object whose state cannot be read is opaque, not trusted."""

    class Hostile:
        __slots__ = ()

        def __repr__(self):
            return f"Hostile(api_key={SECRET!r})"

    rendered = _rendered(Hostile())
    assert SECRET not in rendered
    assert "Hostile" in rendered


def test_value_types_with_fixed_reprs_are_preserved():
    stamp = datetime(2026, 8, 11, 3, 4, 5)
    identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
    rendered = _rendered({"at": stamp, "id": identifier, "role": Role.ADMIN})
    assert "2026" in rendered
    assert "12345678-1234-5678-1234-567812345678" in rendered
    assert "ADMIN" in rendered


def test_recursive_object_graph_terminates():
    class Node:
        def __init__(self):
            self.password = SECRET
            self.parent = None

    node = Node()
    node.parent = node
    rendered = _rendered(node)
    assert SECRET not in rendered
    assert "recursive" in rendered


def test_deeply_nested_secret_is_masked_within_the_depth_bound():
    value = {"password": SECRET}
    for _ in range(R.MAX_REPR_DEPTH - 2):
        value = {"next": value}
    rendered = _rendered(value)
    assert SECRET not in rendered


def test_beyond_the_depth_bound_nothing_is_rendered_at_all():
    """Past the bound the payload is cut off, so nothing can leak through it."""
    value = {"password": SECRET}
    for _ in range(R.MAX_REPR_DEPTH + 5):
        value = {"next": value}
    rendered = _rendered(value)
    assert SECRET not in rendered


def test_truncation_cannot_split_a_secret_open():
    """Masking runs before truncation, so a secret at the boundary is gone."""
    from probe_agent.decorator import redact_text

    padding = "x" * 3990
    rendered = redact_text(f"{padding} AKIAIOSFODNN7EXAMPLE trailing")
    assert "AKIAIOSFODNN7EXAMPLE" not in rendered
