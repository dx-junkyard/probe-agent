"""Inquiry answer generation (Issue #285).

When a developer has a doubt about a confirmation item (a Q&A question, an
Intent Brief item, or — from Issue #287 onward — a review item), the
Inquiry conversation answers *that doubt*, strictly separate from answering
or confirming the original item (see ``routes/interview_inquiry.py``). This
module generates the assistant's answer inside that conversation.

Grounded in the same snapshot context pack ``interview_context.py`` builds
for the main interview dialogue (Issue #68): the model may cite
``(path, start_line, end_line)`` spans that appear in the context pack, each
with a short summary of what it shows (Principle 6 — deterministic
containment check, not a heuristic reinterpretation of the model's
citations). Unlike the two-pass evidence-selection-then-read flow in
``interview_agent.py`` (Issue #130), this is a single reasoning call: the
model answers directly from the context pack it is given, citing spans
already surfaced there. This keeps the generation intentionally simple and
isolated in one function so Issue #286 (Question Router / Investigation
Agent) can replace the internals without touching the inquiry lifecycle
routes.

Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
failure, or a structured-output validation failure all return an error and
no conclusion — callers must not persist an assistant message, only the
failed intelligence_runs audit row (Principle 7). ``answerable=false`` is a
distinct, non-error outcome: the model successfully determined it lacks
grounding to answer, and the caller stores a fixed, server-authored
message (never LLM-fabricated text) instead of the model's own wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_language import get_interview_language, language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .models import InterviewContextPack

PROMPT_VERSION = "inquiry-answer-v1"
SCHEMA_VERSION = "inquiry-answer-v1"

MAX_CONVERSATION_MESSAGES = 20
MAX_EVIDENCE_ITEMS = 5


# --- Raw response schema (what we require the model to return) --------------


class _RawInquiryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class _RawInquiryAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(..., min_length=1, max_length=1_000)
    key_points: List[str] = Field(default_factory=list, max_length=10)
    evidence: List[_RawInquiryEvidence] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    uncertainty: str = Field(default="", max_length=1_000)
    answerable: bool = True


# --- Public result types ------------------------------------------------------


@dataclass
class InquiryEvidenceItem:
    path: str
    start_line: int
    end_line: int
    summary: str = ""


@dataclass
class InquiryAnswerResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    conclusion: str = ""
    key_points: List[str] = field(default_factory=list)
    evidence: List[InquiryEvidenceItem] = field(default_factory=list)
    uncertainty: str = ""
    answerable: bool = False
    evidence_dropped: int = 0
    error: Optional[str] = None


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are answering a developer's follow-up doubt (Inquiry) raised while \
confirming an item during a probe-agent system-understanding interview. The \
Inquiry is a side conversation: resolving it never changes the original \
item's own state; the developer confirms or corrects the original item \
separately, afterward, through its own explicit action.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "conclusion": "a short 1-3 sentence answer to the developer's question",
  "key_points": ["..."],
  "evidence": [
    {"path": "src/module.py", "start_line": 1, "end_line": 20, "summary": "what this span shows"}
  ],
  "uncertainty": "what remains uncertain or unverified, or empty string if none",
  "answerable": true
}

Rules:
- Only cite "path"/"start_line"/"end_line" that appear in the supplied \
context pack. Never invent a path or a line range.
- "conclusion" must be short: 1-3 sentences, the direct answer first.
- Set "answerable" to false, leave "conclusion" as your best short summary of \
what is missing, and leave "evidence" empty when the context pack does not \
contain enough grounding to answer the question. Do not guess or fabricate \
an answer merely to appear helpful.
- Never claim the original item has been answered, confirmed, or changed by \
this conversation. This conversation only resolves the developer's doubt.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


def _allowed_spans(context_pack: InterviewContextPack) -> Dict[str, List[Tuple[int, int]]]:
    """Snapshot-grounded (path -> line spans) the model may cite as evidence.

    Mirrors ``interview_agent._allowed_evidence_spans`` for the context-pack
    portion (Inquiry answers are not grounded in ``current_understanding`,
    only in the context pack passed to this call) -- kept as an independent,
    small copy rather than importing interview_agent internals so this
    module stays a single self-contained swap point for Issue #286.
    """
    spans: Dict[str, List[Tuple[int, int]]] = {}
    for sym in context_pack.symbols:
        spans.setdefault(sym.path, []).append((sym.start_line, sym.end_line))
    for ep in context_pack.entrypoints:
        spans.setdefault(ep.handler_path, []).append((ep.line_start, ep.line_end))
    return spans


def _span_contains(start_line: int, end_line: int, span: Tuple[int, int]) -> bool:
    start, end = span
    if start < 1 or end < start:
        return False
    return start_line >= start and end_line <= end


def _is_verifiable(item: _RawInquiryEvidence, spans: Dict[str, List[Tuple[int, int]]]) -> bool:
    if item.path not in spans:
        return False
    if item.start_line < 1 or item.end_line < item.start_line:
        return False
    return any(_span_contains(item.start_line, item.end_line, span) for span in spans[item.path])


def _build_user_prompt(
    context_pack: InterviewContextPack,
    question_text: str,
    conversation: List[Dict[str, str]],
    origin_summary: Optional[str],
) -> str:
    parts: List[str] = []
    if origin_summary:
        parts.append("## Original item under discussion (context only; not being answered here)")
        parts.append(origin_summary)
    parts.append("## Context Pack (the only allowed source of evidence paths/line ranges)")
    parts.append(context_pack.model_dump_json())
    recent = conversation[-MAX_CONVERSATION_MESSAGES:]
    if recent:
        parts.append("## Inquiry conversation so far")
        for msg in recent:
            parts.append(f"{msg.get('role', '?')}: {msg.get('content', '')}")
    parts.append("## Developer's question")
    parts.append(question_text)
    return "\n\n".join(parts)


def generate_inquiry_answer(
    client: LLMClient,
    config: LLMConfig,
    *,
    context_pack: InterviewContextPack,
    question_text: str,
    conversation: Optional[List[Dict[str, str]]] = None,
    origin_summary: Optional[str] = None,
) -> InquiryAnswerResult:
    """Generate one assistant answer for an Inquiry conversation turn.

    Fail-closed: a mock client, a non-reasoning model, invalid language
    configuration, an API failure, or a structured-output validation failure
    all return a result with ``error`` set and no conclusion -- callers must
    not persist an assistant message for these. ``answerable=false`` is not
    an error: the model determined (grounded in the context pack) that it
    cannot answer, and the caller substitutes a fixed, non-fabricated
    message instead of ``conclusion``. Unverifiable evidence citations are
    dropped (not fatal), matching the interview dialogue's Issue #142
    graceful-degradation pattern.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return InquiryAnswerResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Inquiry answer generation requires a configured reasoning "
                "model; mock/heuristic fallback is prohibited"
            ),
        )

    try:
        language = get_interview_language()
    except ValueError as exc:
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    prompt = _build_user_prompt(
        context_pack, question_text, conversation or [], origin_summary,
    )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
        )
    except LLMError as exc:
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawInquiryAnswer.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return InquiryAnswerResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    spans = _allowed_spans(context_pack)
    kept_evidence: List[InquiryEvidenceItem] = []
    dropped = 0
    for item in validated.evidence:
        if _is_verifiable(item, spans):
            kept_evidence.append(InquiryEvidenceItem(
                path=item.path, start_line=item.start_line, end_line=item.end_line,
                summary=item.summary,
            ))
        else:
            dropped += 1

    return InquiryAnswerResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        conclusion=validated.conclusion,
        key_points=list(validated.key_points),
        evidence=kept_evidence,
        uncertainty=validated.uncertainty,
        answerable=validated.answerable,
        evidence_dropped=dropped,
    )
