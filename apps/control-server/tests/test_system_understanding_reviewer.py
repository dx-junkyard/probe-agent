"""Tests for System Understanding Review (Issue #81)."""

import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.documentation_claim_scanner import (
    ChunkScanResult,
    ClaimEvidence,
    DocumentationClaim,
    PROMPT_VERSION as CLAIM_PROMPT_VERSION,
    SCHEMA_VERSION as CLAIM_SCHEMA_VERSION,
)
from app.understanding_graph import (
    UnderstandingGraph,
    build_understanding_graph,
    EvidenceRef,
)
from app.docs_code_reconciler import ReconciliationResult, ReconciliationMapping
from app.system_understanding_reviewer import (
    CONFIDENCE_LEVELS,
    GAP_TYPE_VALUES,
    NEXT_ACTION_VALUES,
    ReviewResult,
    generate_understanding_review,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    _build_review_prompt,
)
from app.llm import LLMConfig, MockLLMClient


def _mock_config():
    return LLMConfig(provider="mock", model="mock", api_key=None, base_url=None, timeout=10)


def _reasoning_config():
    return LLMConfig(provider="openai", model="o3-mini", api_key="test", base_url=None, timeout=10)


def _claim(claim_type="system_purpose", summary="claim", apis=None, symbols=None):
    return DocumentationClaim(
        claim_type=claim_type,
        summary=summary,
        evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
        confidence=0.9,
        mentioned_apis=apis or [],
        mentioned_symbols=symbols or [],
    )


def _scan_result(claims):
    return ChunkScanResult(
        chunk_id="c1",
        chunk_content_hash="h1",
        prompt_version=CLAIM_PROMPT_VERSION,
        schema_version=CLAIM_SCHEMA_VERSION,
        claims=claims,
    )


def _build_graph(claims):
    return build_understanding_graph([_scan_result(claims)])


def _empty_reconciliation():
    return ReconciliationResult(
        system_id=1,
        snapshot_id=1,
        mappings=[],
        gaps=[],
    )


class FakeReasoningClient:
    def __init__(self, response: dict):
        self._response = json.dumps(response)

    def generate_text(self, messages, **kwargs):
        return self._response


class ErrorReasoningClient:
    def generate_text(self, messages, **kwargs):
        from app.llm import LLMError
        raise LLMError("API timeout")


VALID_REVIEW_RESPONSE = {
    "system_purpose": [
        {
            "name": "Runtime probe evaluation",
            "summary": "The system provides runtime probe and evaluation",
            "confidence": {"level": "likely", "reason": "Multiple docs mention this"},
            "evidence": [{"path": "README.md", "start_line": 1, "end_line": 5, "summary": "Title says so"}],
            "why_core": "",
            "related_docs": ["README.md"],
            "related_apis": [],
            "children": [],
        }
    ],
    "core_capabilities": [
        {
            "name": "Trace recording",
            "summary": "Records function inputs and outputs",
            "confidence": {"level": "confirmed", "reason": "Well documented"},
            "evidence": [{"path": "docs/guide.md", "start_line": 10, "end_line": 20, "summary": "Describes tracing"}],
            "why_core": "Core to the system's value proposition",
            "related_docs": ["docs/guide.md"],
            "related_apis": ["GET /traces"],
            "children": ["Shadow comparison"],
        }
    ],
    "capability_elements": [],
    "supporting_elements": [],
    "api_boundaries": [],
    "probe_flow_candidates": [],
    "gap_analysis": [
        {"gap_type": "code_only", "name": "helper.utils", "summary": "Undocumented utility", "severity": "low"}
    ],
    "open_questions": [
        {"question": "What is the primary deployment target?", "category": "purpose", "priority": "high"},
        {"question": "Which API endpoints handle shadow results?", "category": "api", "priority": "medium"},
    ],
    "suggested_next_action": "confirm_purpose",
}


class TestReviewGeneration:
    def test_mock_client_rejected(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        result = generate_understanding_review(
            MockLLMClient(), _mock_config(),
            graph=graph, reconciliation=recon,
        )
        assert result.error is not None
        assert "reasoning model" in result.error.lower()

    def test_successful_review(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert result.error is None
        assert result.current_understanding is not None
        assert "system_purpose" in result.current_understanding
        assert "core_capabilities" in result.current_understanding
        assert len(result.current_understanding["system_purpose"]) == 1
        assert result.gap_analysis is not None
        assert result.open_questions is not None
        assert result.suggested_next_action != ""

    def test_evidence_preserved(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        purpose = result.current_understanding["system_purpose"][0]
        assert len(purpose["evidence"]) > 0
        assert purpose["evidence"][0]["path"] == "README.md"

    def test_gap_analysis_separates_types(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert any(g["gap_type"] == "code_only" for g in result.gap_analysis)

    def test_open_questions_ordered(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert len(result.open_questions) == 2
        assert result.open_questions[0]["category"] == "purpose"

    def test_no_proposal_in_review(self):
        """Review must not contain proposal fields."""
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        json_str = json.dumps(result.current_understanding)
        assert "probe_plan" not in json_str
        assert "metadata" not in json_str.lower() or "element_type" not in json_str

    def test_invalid_schema_fails_closed(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = FakeReasoningClient({"invalid": "response"})
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert result.error is not None

    def test_llm_error_captured(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        result = generate_understanding_review(
            ErrorReasoningClient(), _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_runs_without_raw_documents(self):
        """Verify review runs from graph + reconciliation, not raw doc content."""
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        prompt = _build_review_prompt(graph, recon)
        assert "Understanding Graph Nodes" in prompt
        assert "Code Intelligence Reconciliation" in prompt

    def test_missing_graph_handled(self):
        empty_graph = build_understanding_graph([])
        recon = _empty_reconciliation()
        client = FakeReasoningClient({
            "system_purpose": [],
            "core_capabilities": [],
            "capability_elements": [],
            "supporting_elements": [],
            "api_boundaries": [],
            "probe_flow_candidates": [],
            "gap_analysis": [],
            "open_questions": [
                {"question": "No graph available - start documentation?", "category": "general", "priority": "high"}
            ],
            "suggested_next_action": "confirm_purpose",
        })
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=empty_graph, reconciliation=recon,
        )
        assert result.error is None
        assert len(result.open_questions) > 0


class TestEnumValidation:
    """P1: invalid enum values must be rejected by schema validation."""

    def test_invalid_confidence_level_rejected(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["system_purpose"] = [{
            "name": "Test",
            "summary": "Test",
            "confidence": {"level": "definitely", "reason": "bad"},
            "evidence": [{"path": "a.md", "start_line": 1, "end_line": 5, "summary": "s"}],
        }]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None

    def test_invalid_gap_type_rejected(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["gap_analysis"] = [{"gap_type": "nonsense", "name": "bad", "severity": "low"}]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None

    def test_invalid_severity_rejected(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["gap_analysis"] = [{"gap_type": "code_only", "name": "x", "severity": "critical"}]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None

    def test_invalid_category_rejected(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["open_questions"] = [{"question": "?", "category": "random", "priority": "high"}]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None

    def test_invalid_next_action_rejected(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["suggested_next_action"] = "Generate probe proposals now"
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None


class TestEvidenceRequired:
    """P1: major understanding items without evidence must be downgraded."""

    def test_purpose_without_evidence_downgraded(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["system_purpose"] = [{
            "name": "Unevidenced purpose",
            "summary": "Claimed without evidence",
            "confidence": {"level": "confirmed", "reason": "trust me"},
            "evidence": [],
        }]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        purpose = result.current_understanding["system_purpose"][0]
        assert purpose["confidence"]["level"] == "uncertain"
        evidence_questions = [q for q in result.open_questions
                             if "no evidence" in q["question"].lower()]
        assert len(evidence_questions) >= 1

    def test_capability_without_evidence_downgraded(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["core_capabilities"] = [{
            "name": "Unevidenced cap",
            "summary": "No proof",
            "confidence": {"level": "likely", "reason": "maybe"},
            "evidence": [],
        }]
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        cap = result.current_understanding["core_capabilities"][0]
        assert cap["confidence"]["level"] == "uncertain"

    def test_item_with_evidence_not_downgraded(self):
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(VALID_REVIEW_RESPONSE)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        purpose = result.current_understanding["system_purpose"][0]
        assert purpose["confidence"]["level"] == "likely"
