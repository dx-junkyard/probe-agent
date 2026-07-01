"""Tests for documentation claim scanner (Issue #78)."""

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.documentation_chunker import MarkdownChunk
from app.documentation_claim_scanner import (
    CLAIM_TYPES,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ChunkScanResult,
    DocumentationClaim,
    scan_chunk,
    scan_all_chunks,
    _validate_claim_bounds,
    _extract_api_paths,
    _extract_symbols,
    _RawClaim,
)
from app.llm import LLMConfig, MockLLMClient


def _make_chunk(
    chunk_id="c1",
    path="README.md",
    heading_path=None,
    start_line=1,
    end_line=10,
    content="# Title\n\nSome content here.",
    content_hash="abc123",
):
    return MarkdownChunk(
        chunk_id=chunk_id,
        path=path,
        heading_path=heading_path or ["Title"],
        start_line=start_line,
        end_line=end_line,
        content_hash=content_hash,
        content=content,
    )


def _mock_config():
    return LLMConfig(provider="mock", model="mock", api_key=None, base_url=None, timeout=10)


class FakeLLMClient:
    """Test client that returns predetermined JSON."""

    def __init__(self, response: dict):
        self._response = json.dumps(response)

    def generate_text(self, messages, **kwargs):
        return self._response


class ErrorLLMClient:
    """Test client that raises LLMError."""

    def generate_text(self, messages, **kwargs):
        from app.llm import LLMError
        raise LLMError("Connection failed")


class TestClaimValidation:
    def test_valid_claim_in_bounds(self):
        chunk = _make_chunk(start_line=5, end_line=15)
        claim = _RawClaim(
            claim_type="system_purpose",
            summary="The system does X",
            evidence_start_line=5,
            evidence_end_line=10,
            confidence=0.9,
        )
        valid, reason = _validate_claim_bounds(claim, chunk)
        assert valid
        assert reason is None

    def test_claim_before_chunk_start(self):
        chunk = _make_chunk(start_line=10, end_line=20)
        claim = _RawClaim(
            claim_type="system_purpose",
            summary="claim",
            evidence_start_line=5,
            evidence_end_line=15,
        )
        valid, reason = _validate_claim_bounds(claim, chunk)
        assert not valid
        assert "start_line" in reason

    def test_claim_after_chunk_end(self):
        chunk = _make_chunk(start_line=10, end_line=20)
        claim = _RawClaim(
            claim_type="system_purpose",
            summary="claim",
            evidence_start_line=15,
            evidence_end_line=25,
        )
        valid, reason = _validate_claim_bounds(claim, chunk)
        assert not valid
        assert "end_line" in reason

    def test_inverted_line_range(self):
        chunk = _make_chunk(start_line=1, end_line=20)
        claim = _RawClaim(
            claim_type="system_purpose",
            summary="claim",
            evidence_start_line=15,
            evidence_end_line=5,
        )
        valid, reason = _validate_claim_bounds(claim, chunk)
        assert not valid


class TestScanChunk:
    def test_mock_client_rejected(self):
        chunk = _make_chunk()
        client = MockLLMClient()
        config = _mock_config()
        result = scan_chunk(client, config, chunk)
        assert result.error is not None
        assert "mock" in result.error.lower()

    def test_valid_claims_extracted(self):
        chunk = _make_chunk(start_line=1, end_line=10)
        response = {
            "claims": [
                {
                    "claim_type": "system_purpose",
                    "summary": "The system provides runtime probe evaluation",
                    "evidence_start_line": 1,
                    "evidence_end_line": 3,
                    "confidence": 0.9,
                    "mentioned_apis": [],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        config = _mock_config()
        result = scan_chunk(client, config, chunk)
        assert result.error is None
        assert len(result.claims) == 1
        assert result.claims[0].claim_type == "system_purpose"
        assert result.claims[0].is_valid
        assert result.claims[0].confidence == 0.9

    def test_invalid_claim_type_rejected(self):
        chunk = _make_chunk(start_line=1, end_line=10)
        response = {
            "claims": [
                {
                    "claim_type": "invalid_type",
                    "summary": "Some claim",
                    "evidence_start_line": 1,
                    "evidence_end_line": 5,
                    "confidence": 0.8,
                    "mentioned_apis": [],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert len(result.claims) == 1
        assert not result.claims[0].is_valid
        assert "invalid" in result.claims[0].invalid_reason.lower()

    def test_out_of_bounds_claim_marked_invalid(self):
        chunk = _make_chunk(start_line=10, end_line=20)
        response = {
            "claims": [
                {
                    "claim_type": "core_capability",
                    "summary": "A capability claim",
                    "evidence_start_line": 1,
                    "evidence_end_line": 5,
                    "confidence": 0.8,
                    "mentioned_apis": [],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert len(result.claims) == 1
        assert not result.claims[0].is_valid
        assert "start_line" in result.claims[0].invalid_reason

    def test_missing_evidence_marked_invalid(self):
        chunk = _make_chunk(start_line=1, end_line=10)
        response = {
            "claims": [
                {
                    "claim_type": "risk",
                    "summary": "ab",
                    "evidence_start_line": 1,
                    "evidence_end_line": 2,
                    "confidence": 0.5,
                    "mentioned_apis": [],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert len(result.claims) == 1
        assert not result.claims[0].is_valid

    def test_mentioned_apis_captured(self):
        chunk = _make_chunk(start_line=1, end_line=10)
        response = {
            "claims": [
                {
                    "claim_type": "api_boundary",
                    "summary": "Exposes GET /users endpoint",
                    "evidence_start_line": 1,
                    "evidence_end_line": 5,
                    "confidence": 0.9,
                    "mentioned_apis": ["GET /users"],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert result.claims[0].mentioned_apis == ["GET /users"]

    def test_mentioned_symbols_captured(self):
        chunk = _make_chunk(start_line=1, end_line=10)
        response = {
            "claims": [
                {
                    "claim_type": "capability_element",
                    "summary": "Uses app.models.User for data",
                    "evidence_start_line": 1,
                    "evidence_end_line": 5,
                    "confidence": 0.85,
                    "mentioned_apis": [],
                    "mentioned_symbols": ["app.models.User"],
                }
            ]
        }
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert result.claims[0].mentioned_symbols == ["app.models.User"]

    def test_cache_hit(self):
        chunk = _make_chunk(content_hash="hash123")
        response = {
            "claims": [
                {
                    "claim_type": "system_purpose",
                    "summary": "System does X",
                    "evidence_start_line": 1,
                    "evidence_end_line": 3,
                    "confidence": 0.9,
                    "mentioned_apis": [],
                    "mentioned_symbols": [],
                }
            ]
        }
        client = FakeLLMClient(response)
        config = _mock_config()
        cache = {}

        result1 = scan_chunk(client, config, chunk, cache=cache)
        assert not result1.is_cached
        assert len(cache) == 1

        result2 = scan_chunk(client, config, chunk, cache=cache)
        assert result2.is_cached
        assert len(result2.claims) == len(result1.claims)

    def test_llm_error_captured(self):
        chunk = _make_chunk()
        client = ErrorLLMClient()
        result = scan_chunk(client, _mock_config(), chunk)
        assert result.error is not None
        assert "Connection failed" in result.error

    def test_malformed_json_captured(self):
        chunk = _make_chunk()

        class BadJsonClient:
            def generate_text(self, messages, **kwargs):
                return "not json at all"

        result = scan_chunk(BadJsonClient(), _mock_config(), chunk)
        assert result.error is not None
        assert "parse" in result.error.lower()

    def test_all_claim_types_accepted(self):
        chunk = _make_chunk(start_line=1, end_line=100)
        claims = []
        for ct in sorted(CLAIM_TYPES):
            claims.append({
                "claim_type": ct,
                "summary": f"Claim of type {ct}",
                "evidence_start_line": 1,
                "evidence_end_line": 5,
                "confidence": 0.8,
                "mentioned_apis": [],
                "mentioned_symbols": [],
            })
        response = {"claims": claims}
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert all(c.is_valid for c in result.claims)
        assert len(result.claims) == len(CLAIM_TYPES)


class TestScanAllChunks:
    def test_scans_in_order(self):
        chunks = [
            _make_chunk(chunk_id="c1", content_hash="h1"),
            _make_chunk(chunk_id="c2", content_hash="h2"),
        ]
        response = {"claims": []}
        client = FakeLLMClient(response)
        results = scan_all_chunks(client, _mock_config(), chunks)
        assert len(results) == 2
        assert results[0].chunk_id == "c1"
        assert results[1].chunk_id == "c2"


class TestConfidenceClamping:
    """P1: out-of-range confidence must be clamped, not crash."""

    def test_confidence_above_1_clamped(self):
        chunk = _make_chunk(content="# Title\n\nSystem does X", start_line=1, end_line=3)
        response = {"claims": [{
            "claim_type": "system_purpose",
            "summary": "System does X",
            "evidence_start_line": 1,
            "evidence_end_line": 3,
            "confidence": 2.0,
            "mentioned_apis": [],
            "mentioned_symbols": [],
        }]}
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert result.error is None
        assert len(result.claims) == 1
        assert result.claims[0].is_valid
        assert result.claims[0].confidence == 1.0

    def test_confidence_below_0_clamped(self):
        chunk = _make_chunk(content="# Title\n\nSomething", start_line=1, end_line=3)
        response = {"claims": [{
            "claim_type": "core_capability",
            "summary": "Has capability",
            "evidence_start_line": 1,
            "evidence_end_line": 3,
            "confidence": -0.5,
            "mentioned_apis": [],
            "mentioned_symbols": [],
        }]}
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        assert result.error is None
        assert len(result.claims) == 1
        assert result.claims[0].is_valid
        assert result.claims[0].confidence == 0.0


class TestCacheEvidence:
    """P2: cache must not return evidence from a different path/offset."""

    def test_same_content_different_path_no_cache_collision(self):
        content = "# Title\n\nIdentical content here"
        chunk_a = _make_chunk(chunk_id="a", path="README.md", start_line=1, end_line=3,
                              content=content, content_hash="samehash")
        chunk_b = _make_chunk(chunk_id="b", path="docs/copy.md", start_line=101, end_line=103,
                              content=content, content_hash="samehash")

        response = {"claims": [{
            "claim_type": "system_purpose",
            "summary": "System purpose claim",
            "evidence_start_line": 1,
            "evidence_end_line": 3,
            "confidence": 0.9,
            "mentioned_apis": [],
            "mentioned_symbols": [],
        }]}
        client = FakeLLMClient(response)
        cache: dict = {}

        result_a = scan_chunk(client, _mock_config(), chunk_a, cache=cache)
        assert result_a.claims[0].evidence.path == "README.md"

        result_b = scan_chunk(client, _mock_config(), chunk_b, cache=cache)
        assert not result_b.is_cached
        assert result_b.claims[0].evidence.path == "docs/copy.md"

    def test_same_path_same_offset_cache_hit(self):
        chunk = _make_chunk(content_hash="h1")
        response = {"claims": []}
        client = FakeLLMClient(response)
        cache: dict = {}

        scan_chunk(client, _mock_config(), chunk, cache=cache)
        result2 = scan_chunk(client, _mock_config(), chunk, cache=cache)
        assert result2.is_cached


class TestDeterministicExtraction:
    """P2: deterministic API/symbol extraction must augment model output."""

    def test_api_path_merged_from_evidence(self):
        content = "# API\n\nUse GET /users to list users\nand POST /items to create"
        chunk = _make_chunk(content=content, start_line=1, end_line=4)
        response = {"claims": [{
            "claim_type": "api_boundary",
            "summary": "Lists users and creates items",
            "evidence_start_line": 1,
            "evidence_end_line": 4,
            "confidence": 0.8,
            "mentioned_apis": ["GET /users"],
            "mentioned_symbols": [],
        }]}
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        apis = result.claims[0].mentioned_apis
        assert "GET /users" in apis
        assert any("/items" in a for a in apis)

    def test_symbol_merged_from_evidence(self):
        content = "# Symbols\n\nThe app.models.User class handles users"
        chunk = _make_chunk(content=content, start_line=1, end_line=3)
        response = {"claims": [{
            "claim_type": "capability_element",
            "summary": "User model handles users",
            "evidence_start_line": 1,
            "evidence_end_line": 3,
            "confidence": 0.8,
            "mentioned_apis": [],
            "mentioned_symbols": [],
        }]}
        client = FakeLLMClient(response)
        result = scan_chunk(client, _mock_config(), chunk)
        symbols = result.claims[0].mentioned_symbols
        assert "app.models.User" in symbols

    def test_extract_api_paths_standalone(self):
        text = "Use GET /api/v1/users and POST /items/create for operations"
        apis = _extract_api_paths(text)
        assert "/api/v1/users" in apis
        assert "/items/create" in apis

    def test_extract_symbols_standalone(self):
        text = "The app.models.User class and utils.helpers.parse function"
        symbols = _extract_symbols(text)
        assert "app.models.User" in symbols
        assert "utils.helpers.parse" in symbols
