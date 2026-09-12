"""Tests for Issue #459 (Epic #457) §9.2: `app/discussion_claims.py`, the
semantic half of the Gap <-> Vision/UX/機能 matching operation.

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §9.2 is the canonical contract. Acceptance
criteria under test:

1. A bundle with nothing `available` anywhere never calls the LLM and
   returns `decision_method="deterministic"` (``TestNoSemanticMaterial``).
2. A registered "unsupported"/"not_applicable"/"unavailable" section always
   yields its own fixed deterministic `unknown` claim, independent of the
   LLM outcome (``TestDeterministicClaims``).
3. No usable reasoning-model client (mock/unset) fails closed with
   `error_kind="unavailable"` -- never a heuristic/mock success
   (``TestNoUsableClient``).
4. A response citing an id outside `allowed_source_ids` triggers exactly one
   regeneration attempt; a second invalid response fails explicitly with
   `error_kind="invalid_citation"` -- never an unlimited retry loop
   (``TestCitationValidationAndRetry``).
5. A call error / unparseable structured output are reported with their own
   distinct `error_kind`, and the deterministic claims (if any) are still
   returned either way (``TestCallAndParseFailures``).
6. An unknown claim `kind` invalidates the WHOLE response, same as an
   invalid citation (``TestUnknownClaimKind``).

This module is pure (no database, no HTTP) -- bundles are built directly
from `app.discussion_context_bundle`'s dataclasses rather than through a
live thread, mirroring `app/discussion_claims.py`'s own "no database access
anywhere in this module" contract.
"""

from __future__ import annotations

import json

import pytest

from app import discussion_claims
from app.discussion_context_bundle import (
    BundleCoverage,
    BundleEntry,
    BundleNextAction,
    BundleRootRef,
    BundleSection,
    BundleSnapshotRef,
    BundleSource,
    DiscussionContextBundle,
)
from app.llm import LLMConfig, LLMError, MockLLMClient


def _config(provider="openai", model="gpt-5", api_key="unused"):
    return LLMConfig(provider=provider, api_key=api_key, model=model, base_url=None, timeout=30.0)


def _coverage(returned=1, total=1, completeness="complete", stop_reason="complete"):
    return BundleCoverage(
        returned_count=returned, total_count=total, completeness=completeness, stop_reason=stop_reason,
    )


def _available_section(section_id="related", *, entries=None):
    entries = entries if entries is not None else [
        BundleEntry(
            target_kind="ux_journey", target_ref="j1", title="Journey 1",
            revision_id=1, digest="d1", resolution="resolved", facts={"perspective": "as_is"},
        ),
    ]
    return BundleSection(
        section_id=section_id, operation_state="available",
        facts=tuple(entries), coverage=_coverage(returned=len(entries), total=len(entries)),
    )


def _empty_section(section_id, operation_state, reason="stop"):
    return BundleSection(
        section_id=section_id, operation_state=operation_state,
        facts=(), coverage=_coverage(returned=0, total=0 if operation_state == "not_applicable" else None,
                                      completeness="complete" if operation_state == "not_applicable" else "unknown",
                                      stop_reason=operation_state),
    )


def _bundle(sections, *, extra_sources=()):
    root = BundleRootRef(target_kind="product_gap", target_ref="gap-1", revision_id=1, digest="rootdigest")
    root_source = BundleSource(
        source_id="product_gap:gap-1", target_kind="product_gap", target_ref="gap-1",
        revision_id=1, digest="rootdigest", snapshot_id=1, freshness="current",
        deep_link="/objective-map?view=gaps&gap=gap-1", deep_link_state="selected",
    )
    sources = [root_source]
    for section in sections:
        for entry in section.facts:
            sources.append(
                BundleSource(
                    source_id=f"{entry.target_kind}:{entry.target_ref}", target_kind=entry.target_kind,
                    target_ref=entry.target_ref, revision_id=entry.revision_id, digest=entry.digest,
                    snapshot_id=1, freshness="current", deep_link=None, deep_link_state="unavailable",
                ),
            )
    sources.extend(extra_sources)
    return DiscussionContextBundle(
        schema_version="discussion-context-v1", bundle_digest="bundledigest",
        root=root, snapshot=BundleSnapshotRef(id=1, commit_sha="abc123"),
        sections=tuple(sections), sources=tuple(sources), dependencies=(),
        next_action=BundleNextAction(kind="none", target="", enabled=False, reason=""),
    )


class _ScriptedClient:
    """Returns each entry of `responses` in order (as a JSON string already
    unless it is an exception instance, which is raised instead)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None, timeout=None):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response) if not isinstance(response, str) else response


# ---------------------------------------------------------------------------
# 1. No semantic material anywhere -> no LLM call at all.
# ---------------------------------------------------------------------------


class TestNoSemanticMaterial:
    def test_all_sections_unsupported_or_not_applicable_never_calls_llm(self):
        bundle = _bundle([
            _empty_section("related", "unsupported"),
            _empty_section("objective", "not_applicable"),
        ])
        client = _ScriptedClient([])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.decision_method == "deterministic"
        assert result.error is None
        assert client.calls == []
        # Two deterministic claims, one per structurally-absent section.
        assert len(result.claims) == 2
        assert all(c.basis == "deterministic" for c in result.claims)
        assert all(c.kind == "unknown" for c in result.claims)

    def test_empty_bundle_is_deterministic_and_not_an_error(self):
        bundle = _bundle([])
        result = discussion_claims.generate_context_claims(
            None, _config(), bundle=bundle, question="十分か",
        )
        assert result.decision_method == "deterministic"
        assert result.claims == []
        assert result.error is None


# ---------------------------------------------------------------------------
# 2. Deterministic claims always survive regardless of LLM outcome.
# ---------------------------------------------------------------------------


class TestDeterministicClaims:
    def test_unsupported_and_unavailable_each_get_their_own_fixed_message(self):
        bundle = _bundle([
            _available_section(),
            _empty_section("stakeholder", "unsupported"),
            _empty_section("purpose_relation", "unavailable"),
        ])
        client = _ScriptedClient([
            {"claims": [{"kind": "fact", "statement": "s1", "cited_source_ids": ["product_gap:gap-1"]}],
             "scope_note": "note"},
        ])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        kinds_by_section = {c.statement.split("」")[0].strip("「") for c in result.claims if c.basis == "deterministic"}
        assert kinds_by_section == {"stakeholder", "purpose_relation"}
        assert any(c.basis == "reasoning_llm" for c in result.claims)


# ---------------------------------------------------------------------------
# 3. No usable client -- fail closed, never mock/heuristic success.
# ---------------------------------------------------------------------------


class TestNoUsableClient:
    def test_none_client_fails_closed(self):
        bundle = _bundle([_available_section()])
        result = discussion_claims.generate_context_claims(
            None, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "unavailable"
        assert result.decision_method == "reasoning_llm"
        assert result.claims == []  # no deterministic sections in this bundle

    def test_mock_client_fails_closed_even_with_material(self):
        bundle = _bundle([_available_section()])
        client = MockLLMClient()
        result = discussion_claims.generate_context_claims(
            client, _config(provider="mock", model="mock"), bundle=bundle, question="十分か",
        )
        assert result.is_mock is True
        assert result.error_kind == "unavailable"

    def test_non_reasoning_model_fails_closed(self):
        bundle = _bundle([_available_section()])
        client = _ScriptedClient([{"claims": [], "scope_note": ""}])
        result = discussion_claims.generate_context_claims(
            client, _config(model="gpt-4o-mini"), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "unavailable"
        assert client.calls == []


# ---------------------------------------------------------------------------
# 4. Citation validation + exactly-one-retry.
# ---------------------------------------------------------------------------


class TestCitationValidationAndRetry:
    def test_invalid_citation_then_valid_retry_succeeds(self):
        bundle = _bundle([_available_section()])
        client = _ScriptedClient([
            {"claims": [{"kind": "fact", "statement": "bad", "cited_source_ids": ["does_not_exist:1"]}],
             "scope_note": ""},
            {"claims": [{"kind": "fact", "statement": "good", "cited_source_ids": ["product_gap:gap-1"]}],
             "scope_note": "final"},
        ])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error is None
        assert result.retried is True
        assert len(client.calls) == 2
        assert [c.statement for c in result.claims] == ["good"]
        # The retry prompt must name the allowed ids so the model can correct.
        retry_user_msg = client.calls[1][-1]["content"]
        assert "allowed_source_ids" in retry_user_msg

    def test_invalid_citation_twice_fails_explicitly(self):
        bundle = _bundle([_available_section()])
        bad = {"claims": [{"kind": "fact", "statement": "bad", "cited_source_ids": ["nope:1"]}], "scope_note": ""}
        client = _ScriptedClient([bad, bad])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "invalid_citation"
        assert result.retried is True
        assert len(client.calls) == 2
        assert result.claims == []  # no deterministic claims in this bundle either

    def test_empty_cited_source_ids_is_itself_invalid(self):
        bundle = _bundle([_available_section()])
        bad = {"claims": [{"kind": "fact", "statement": "no citation", "cited_source_ids": []}], "scope_note": ""}
        client = _ScriptedClient([bad, bad])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "invalid_citation"


# ---------------------------------------------------------------------------
# 5. Call / parse failures.
# ---------------------------------------------------------------------------


class TestCallAndParseFailures:
    def test_llm_error_reports_call_error_and_keeps_deterministic_claims(self):
        bundle = _bundle([_available_section(), _empty_section("stakeholder", "unsupported")])
        client = _ScriptedClient([LLMError("boom")])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "call_error"
        assert len(result.claims) == 1
        assert result.claims[0].basis == "deterministic"

    def test_invalid_json_reports_invalid_response(self):
        bundle = _bundle([_available_section()])
        client = _ScriptedClient(["not json at all"])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "invalid_response"

    def test_fenced_json_is_stripped_before_parsing(self):
        bundle = _bundle([_available_section()])
        payload = json.dumps(
            {"claims": [{"kind": "unknown", "statement": "u", "cited_source_ids": ["product_gap:gap-1"]}],
             "scope_note": ""},
        )
        client = _ScriptedClient([f"```json\n{payload}\n```"])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error is None
        assert [c.kind for c in result.claims] == ["unknown"]


# ---------------------------------------------------------------------------
# 6. Unknown claim kind invalidates the whole response.
# ---------------------------------------------------------------------------


class TestUnknownClaimKind:
    def test_unknown_kind_triggers_retry_then_failure(self):
        bundle = _bundle([_available_section()])
        bad = {
            "claims": [{"kind": "speculation", "statement": "x", "cited_source_ids": ["product_gap:gap-1"]}],
            "scope_note": "",
        }
        client = _ScriptedClient([bad, bad])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        assert result.error_kind == "invalid_citation"
        assert len(client.calls) == 2

    def test_more_than_twenty_claims_rejected_by_schema(self):
        bundle = _bundle([_available_section()])
        too_many = {
            "claims": [
                {"kind": "fact", "statement": f"s{i}", "cited_source_ids": ["product_gap:gap-1"]}
                for i in range(21)
            ],
            "scope_note": "",
        }
        client = _ScriptedClient([too_many])
        result = discussion_claims.generate_context_claims(
            client, _config(), bundle=bundle, question="十分か",
        )
        # Pydantic's max_length rejects the response outright -> invalid_response
        # (a parse/validation failure returns immediately -- it never
        # consumes the single retry attempt, which is reserved for a
        # response that parsed fine but cited an invalid/unknown-kind claim).
        assert result.error_kind == "invalid_response"
        assert len(client.calls) == 1


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
