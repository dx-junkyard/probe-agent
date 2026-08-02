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
    GraphNode,
)
from app.docs_code_reconciler import ReconciliationResult, ReconciliationMapping
from app.system_understanding_reviewer import (
    CONFIDENCE_LEVELS,
    DEFAULT_REVIEW_MAX_OUTPUT_TOKENS,
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


class CapturingReasoningClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_text(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        response = self._responses.pop(0)
        if isinstance(response, dict):
            return json.dumps(response)
        return response


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
        # Issue #229: even after the schema-reminder retry also fails, the
        # session-facing error must stay a catalog message the Dashboard can
        # show as-is -- never a raw Pydantic ValidationError repr (field
        # paths, "validation error for...", etc).
        lowered = result.error.lower()
        assert "validationerror" not in lowered
        assert "pydantic" not in lowered
        assert "field required" not in lowered

    def test_llm_error_captured(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        result = generate_understanding_review(
            ErrorReasoningClient(), _reasoning_config(),
            graph=graph, reconciliation=recon,
        )
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_default_review_output_budget_is_not_8192(self, monkeypatch):
        monkeypatch.delenv("INTELLIGENCE_REVIEW_MAX_OUTPUT_TOKENS", raising=False)
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )

        assert result.error is None
        assert DEFAULT_REVIEW_MAX_OUTPUT_TOKENS == 32_768
        assert client.calls[0]["kwargs"]["max_tokens"] == DEFAULT_REVIEW_MAX_OUTPUT_TOKENS

    def test_review_output_budget_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("INTELLIGENCE_REVIEW_MAX_OUTPUT_TOKENS", "12345")
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )

        assert result.error is None
        assert client.calls[0]["kwargs"]["max_tokens"] == 12345

    def test_truncated_json_retries_compact_review(self, monkeypatch):
        monkeypatch.delenv("INTELLIGENCE_REVIEW_MAX_OUTPUT_TOKENS", raising=False)
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = CapturingReasoningClient([
            '{"system_purpose": [{"name": "cut off',
            VALID_REVIEW_RESPONSE,
        ])

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
        )

        assert result.error is None
        assert len(client.calls) == 2
        assert "compact JSON object" in client.calls[1]["messages"][0]["content"]

    def test_invalid_next_action_retries_with_literal_enum(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["suggested_next_action"] = "Generate probe proposals now"
        client = CapturingReasoningClient([response, VALID_REVIEW_RESPONSE])

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=_build_graph([_claim()]), reconciliation=_empty_reconciliation(),
        )

        assert result.error is None
        assert result.suggested_next_action == "confirm_purpose"
        assert len(client.calls) == 2
        retry_system_prompt = client.calls[1]["messages"][0]["content"]
        assert "suggested_next_action MUST be" in retry_system_prompt
        assert "ready_for_proposal" in retry_system_prompt

    def test_runs_without_raw_documents(self):
        """Verify review runs from graph + reconciliation, not raw doc content."""
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        prompt = _build_review_prompt(graph, recon)
        assert "Understanding Graph Nodes" in prompt
        assert "Code Intelligence Reconciliation" in prompt

    def test_review_prompt_is_compacted_for_large_graph(self, monkeypatch):
        monkeypatch.setenv("INTELLIGENCE_REVIEW_MAX_NODES_PER_TYPE", "3")
        monkeypatch.setenv("INTELLIGENCE_REVIEW_MAX_PROMPT_CHARS", "20000")
        nodes = {}
        for i in range(40):
            node_id = f"cap-{i}"
            nodes[node_id] = GraphNode(
                node_id=node_id,
                node_type="core_capability",
                name=f"Capability {i} " + ("x" * 200),
                summary="summary " + ("y" * 500),
                evidence=[
                    EvidenceRef(
                        path=f"docs/{i}.md",
                        start_line=1,
                        end_line=2,
                        chunk_id=f"chunk-{i}",
                        confidence=0.9,
                        summary="evidence " + ("z" * 500),
                    )
                ],
                confidence=0.9,
            )
        graph = UnderstandingGraph(
            nodes=nodes,
            claim_count=40,
            valid_claim_count=40,
            confidence_summary={"core_capability": 0.9},
            conflicts=[],
            weak_nodes=[],
            source_hash="hash",
        )

        prompt = _build_review_prompt(graph, _empty_reconciliation())

        assert "total_nodes: 40" in prompt
        assert "included_nodes: 3" in prompt
        assert prompt.count("[core_capability]") == 3
        assert len(prompt) < 20_000

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


class TestQaInjection:
    """Issue #263: Q&A-panel answers must reach the understanding review the
    same way conversational answers already do."""

    def test_prompt_includes_answered_qa(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        answered_qa = [
            {"question": "What does this system do?", "answer": "It runs probes."}
        ]
        prompt = _build_review_prompt(graph, recon, answered_qa=answered_qa)
        assert "Confirmed Q&A" in prompt
        assert "What does this system do?" in prompt
        assert "It runs probes." in prompt

    def test_prompt_includes_unconfirmed_qa(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        unconfirmed_qa = [
            {"question": "Who owns this component?", "answer": "わかりません"}
        ]
        prompt = _build_review_prompt(graph, recon, unconfirmed_qa=unconfirmed_qa)
        assert "Unconfirmed Q&A" in prompt
        assert "Who owns this component?" in prompt
        assert "わかりません" in prompt

    def test_prompt_omits_qa_sections_when_absent(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        prompt = _build_review_prompt(graph, recon)
        assert "Confirmed Q&A" not in prompt
        assert "Unconfirmed Q&A" not in prompt

    def test_prompt_includes_human_alignment_feedback_before_graph(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        feedback = [{
            "alignment_item_id": 12,
            "current_claim": "The API owns authorization",
            "proposed_interpretation": "Authorization is automatic",
            "decision": {
                "action": "corrected",
                "note": "Authorization requires an explicit operator approval.",
            },
        }]

        prompt = _build_review_prompt(
            graph, recon, alignment_feedback=feedback,
        )

        assert "Human Alignment Review Feedback" in prompt
        assert "corrected" in prompt
        assert "explicit operator approval" in prompt
        assert prompt.index("Human Alignment Review Feedback") < prompt.index(
            "Understanding Graph Nodes"
        )

    def test_generate_understanding_review_forwards_qa_to_prompt(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])
        answered_qa = [{"question": "Panel Q1", "answer": "Panel A1"}]
        unconfirmed_qa = [{"question": "Panel Q2", "answer": "不明"}]

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
            answered_qa=answered_qa, unconfirmed_qa=unconfirmed_qa,
        )

        assert result.error is None
        user_prompt = client.calls[0]["messages"][1]["content"]
        assert "Panel Q1" in user_prompt
        assert "Panel A1" in user_prompt
        assert "Panel Q2" in user_prompt
        assert "不明" in user_prompt

    def test_generate_understanding_review_forwards_alignment_feedback_to_prompt(self):
        graph = _build_graph([_claim()])
        recon = _empty_reconciliation()
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])
        feedback = [{
            "alignment_item_id": 8,
            "current_claim": "Wrong claim",
            "decision": {
                "action": "reject_interpretation",
                "note": "Do not retain this interpretation.",
            },
        }]

        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=recon,
            alignment_feedback=feedback,
        )

        assert result.error is None
        user_prompt = client.calls[0]["messages"][1]["content"]
        assert "reject_interpretation" in user_prompt
        assert "Do not retain this interpretation." in user_prompt

    def test_prompt_version_is_bumped_when_the_prompt_changes(self):
        # Bumped for the Alignment-feedback injection (v5) and again for the
        # Vision section (v6). The audit record must never claim an output was
        # produced by a prompt that no longer exists.
        assert PROMPT_VERSION == "understanding-review-v6"


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

    def test_descriptive_next_action_is_normalized(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["suggested_next_action"] = "Clarify the top-level product goal with the user."
        graph = _build_graph([_claim()])
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        assert result.suggested_next_action == "resolve_open_questions"


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
        # The no-evidence follow-up question is localized (ja by default,
        # Issue #127); match on the item name it embeds instead of wording.
        evidence_questions = [q for q in result.open_questions
                             if "Unevidenced purpose" in q["question"]
                             and q["priority"] == "high"]
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


class TestOutputLanguage:
    """Issue #127: LLM-facing prompts pin the configured output language."""

    def test_japanese_directive_by_default(self, monkeypatch):
        monkeypatch.delenv("INTERVIEW_LANGUAGE", raising=False)
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=_build_graph([_claim()]), reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        system_msg = client.calls[0]["messages"][0]["content"]
        assert "in Japanese" in system_msg
        assert "enum values" in system_msg
        assert result.prompt_version == "understanding-review-v6"
        assert "review_capabilities" in system_msg

    def test_english_directive(self, monkeypatch):
        monkeypatch.setenv("INTERVIEW_LANGUAGE", "en")
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=_build_graph([_claim()]), reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        assert "in English" in client.calls[0]["messages"][0]["content"]

    def test_invalid_language_fails_closed(self, monkeypatch):
        monkeypatch.setenv("INTERVIEW_LANGUAGE", "xx")
        client = CapturingReasoningClient([VALID_REVIEW_RESPONSE])
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=_build_graph([_claim()]), reconciliation=_empty_reconciliation(),
        )
        assert result.error is not None
        assert "INTERVIEW_LANGUAGE" in result.error
        assert client.calls == []

    def test_no_evidence_question_localized(self, monkeypatch):
        monkeypatch.delenv("INTERVIEW_LANGUAGE", raising=False)
        response = dict(VALID_REVIEW_RESPONSE)
        response["system_purpose"] = [{
            "name": "Runtime probe evaluation",
            "summary": "no evidence supplied",
            "confidence": {"level": "likely", "reason": "guess"},
            "evidence": [],
            "why_core": "",
            "related_docs": [],
            "related_apis": [],
            "children": [],
        }]
        client = FakeReasoningClient(response)
        result = generate_understanding_review(
            client, _reasoning_config(),
            graph=_build_graph([_claim()]), reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        questions = [q["question"] for q in result.open_questions]
        assert any("根拠" in q and "Runtime probe evaluation" in q for q in questions)


class TestVisionSection:
    """Issue #352: Vision is a claim in its own right, and never a settled one
    unless the repository actually evidences it."""

    def test_vision_is_returned_as_its_own_section(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["vision"] = [{
            "name": "開発者が自分のシステムを説明できる状態にする",
            "summary": "",
            "confidence": {"level": "likely", "reason": "README states the goal"},
            "evidence": [
                {"path": "README.md", "start_line": 1, "end_line": 3, "summary": "目的"}
            ],
        }]
        graph = _build_graph([_claim()])
        result = generate_understanding_review(
            FakeReasoningClient(response), _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        vision = result.current_understanding["vision"]
        assert len(vision) == 1
        assert vision[0]["name"] == "開発者が自分のシステムを説明できる状態にする"
        # Vision never merges into System Purpose.
        assert result.current_understanding["system_purpose"][0]["name"] != vision[0]["name"]

    def test_vision_without_evidence_cannot_be_presented_as_settled(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["vision"] = [{
            "name": "推定した Vision",
            "summary": "",
            "confidence": {"level": "confirmed", "reason": "trust me"},
            "evidence": [],
        }]
        graph = _build_graph([_claim()])
        result = generate_understanding_review(
            FakeReasoningClient(response), _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        vision = result.current_understanding["vision"][0]
        assert vision["confidence"]["level"] == "uncertain"
        assert vision["confidence"]["reason"]
        # Unlike the evidence-required sections, it does NOT manufacture a
        # high-priority question: the developer's Vision belongs to the Intent
        # Brief, and firing this on every session would be pure noise.
        assert not [q for q in result.open_questions if "推定した Vision" in q["question"]]

    def test_only_one_vision_item_is_kept(self):
        response = dict(VALID_REVIEW_RESPONSE)
        response["vision"] = [
            {"name": "Vision A", "summary": "", "confidence": {"level": "likely", "reason": ""},
             "evidence": [{"path": "README.md", "start_line": 1, "end_line": 2, "summary": "a"}]},
            {"name": "Vision B", "summary": "", "confidence": {"level": "likely", "reason": ""},
             "evidence": [{"path": "README.md", "start_line": 3, "end_line": 4, "summary": "b"}]},
        ]
        graph = _build_graph([_claim()])
        result = generate_understanding_review(
            FakeReasoningClient(response), _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        assert [v["name"] for v in result.current_understanding["vision"]] == ["Vision A"]

    def test_absent_vision_is_an_empty_section_not_a_fabrication(self):
        graph = _build_graph([_claim()])
        result = generate_understanding_review(
            FakeReasoningClient(VALID_REVIEW_RESPONSE), _reasoning_config(),
            graph=graph, reconciliation=_empty_reconciliation(),
        )
        assert result.error is None
        assert result.current_understanding["vision"] == []

    def test_prompt_and_schema_versions_record_the_vision_change(self):
        from app.system_understanding_reviewer import PROMPT_VERSION, SCHEMA_VERSION

        assert PROMPT_VERSION == "understanding-review-v6"
        assert SCHEMA_VERSION == "understanding-review-v2"
