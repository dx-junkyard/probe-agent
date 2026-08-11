"""Translation of investigation findings into purpose/impact (Phase C / #331).

Epic #328's explanation principle: the SUBJECT of the first explanation must
be what the system is trying to achieve, who it affects, what it can and
cannot do against that goal, what breaks system-wide consistency, and which
decision changes which outcome -- not an internal variable, function, API, or
column name. Internal names are not hidden; they are disclosed in the later
layers, and the correspondence between the plain explanation and the
technical evidence is never lost.

This module is the reasoning step that produces that wording, under one hard
constraint: **it may not invent technical facts.** Its only input is the
findings already recorded on the Joint Understanding session, and every
statement it produces must cite the finding ids it was derived from. A
statement referencing an id that is not in the supplied set fails the whole
call closed -- no partial translation is stored (Principle 6/7). It also may
not carry evidence of its own: the Phase A contract restricts snapshot
citations to ``origin_role='investigation'`` findings, so a translated
sentence always resolves back through a finding to the code it came from.

Option presentation is deliberately NOT part of the reasoning output's
freedom: which next actions exist is the finite ``ACTION_KINDS`` set, and the
menu is assembled deterministically (``build_action_menu``) from fixed server
templates. The model only supplies the option comparison for the options it
was asked to compare, each with its own finding references.

probe-agent:
  role: reasoning translation of investigation findings into purpose/impact
    statements with mandatory finding-level traceability
  capability: interactive-system-understanding
  element_type: core
  operation_kind: io
  state_effects: [external-api]
  probe_value: Verify a statement referencing an unknown finding id fails the whole call closed, that translation output never carries its own evidence, and that a mock/non-reasoning configuration produces no translation at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_language import interview_message, language_directive
from .joint_understanding import (
    ACTION_KINDS,
    completes_outside_session,
    formal_operation_for,
)
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "understanding-translation-v1"
SCHEMA_VERSION = "understanding-translation-v1"

# The layers of one translated explanation, in disclosure order. Finite set:
# an unknown layer is rejected, never rendered in some default position.
#   purpose      -- what this part of the system is trying to achieve
#   impact       -- who is affected and how
#   gap          -- what it can/cannot currently do against that purpose
#   consistency  -- what keeping the whole system coherent costs here
#   decision     -- which decision changes which outcome
STATEMENT_LAYERS = ("purpose", "impact", "gap", "consistency", "decision")

# A translation never asserts a NEW fact about the code: restating an
# investigation fact in goal terms is an interpretation of meaning, so the
# claim kinds available here exclude 'fact' and 'hypothesis' (a new
# hypothesis is investigation's job, with competing explanations and
# refutation conditions attached).
TRANSLATION_CLAIM_KINDS = ("inference", "unknown", "conflict")

MAX_STATEMENTS = 8
MAX_OPTIONS = 4


@dataclass
class TranslatedStatement:
    layer: str
    claim_kind: str
    text: str
    supports_finding_ids: List[int] = field(default_factory=list)


@dataclass
class TranslatedOption:
    label: str
    what_changes: str
    tradeoffs: str = ""
    supports_finding_ids: List[int] = field(default_factory=list)


@dataclass
class TranslationResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    purpose_summary: str = ""
    statements: List[TranslatedStatement] = field(default_factory=list)
    options: List[TranslatedOption] = field(default_factory=list)
    open_unknowns: List[str] = field(default_factory=list)
    # Only set when the remaining uncertainty genuinely needs the developer's
    # own value judgement (see ``ask_developer`` below).
    decision_question: Optional[str] = None
    error: Optional[str] = None


@dataclass
class FindingSummary:
    """The subset of a persisted finding the translator is allowed to see."""

    id: int
    origin_role: str
    claim_kind: str
    statement: str
    uncertainty: str = ""
    evidence_count: int = 0
    competing_explanations: List[str] = field(default_factory=list)


# --- Raw response schema ------------------------------------------------------


class _RawStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: str = Field(..., min_length=1, max_length=20)
    claim_kind: str = Field(..., min_length=1, max_length=20)
    text: str = Field(..., min_length=1, max_length=2_000)
    supports_finding_ids: List[int] = Field(default_factory=list, max_length=20)


class _RawOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=200)
    what_changes: str = Field(..., min_length=1, max_length=2_000)
    tradeoffs: str = Field(default="", max_length=2_000)
    supports_finding_ids: List[int] = Field(default_factory=list, max_length=20)


class _RawTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose_summary: str = Field(..., min_length=1, max_length=2_000)
    statements: List[_RawStatement] = Field(default_factory=list, max_length=MAX_STATEMENTS)
    options: List[_RawOption] = Field(default_factory=list, max_length=MAX_OPTIONS)
    open_unknowns: List[str] = Field(default_factory=list, max_length=10)
    decision_question: Optional[str] = Field(default=None, max_length=1_000)


_SYSTEM_PROMPT = """\
You are the translation step of probe-agent's joint-understanding flow. A \
read-only investigation has already produced findings about a system, and the \
developer -- who did NOT know the answer either -- now needs those findings in \
terms of what the system is trying to achieve and what their choices change.

You are given ONLY those findings. You must not add technical facts: every \
sentence you write has to be derivable from the supplied findings, and has to \
name the finding ids it came from.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "purpose_summary": "1-3 sentences: what this part of the system is for, in the developer's terms",
  "statements": [
    {
      "layer": "purpose | impact | gap | consistency | decision",
      "claim_kind": "inference | unknown | conflict",
      "text": "one sentence a developer can act on",
      "supports_finding_ids": [1, 2]
    }
  ],
  "options": [
    {
      "label": "short name of the option",
      "what_changes": "what actually changes if this option is taken",
      "tradeoffs": "what it costs or risks",
      "supports_finding_ids": [1]
    }
  ],
  "open_unknowns": ["what is still not established, stated plainly"],
  "decision_question": "the one question only the developer can answer, or null"
}

Rules:
- The SUBJECT of "purpose_summary" and of every "purpose"/"impact" statement \
is the goal, the affected users, or the observable behavior -- never an \
internal variable, function, API, or column name. Internal names belong in \
the evidence layer, which the reader can already open; do not hide them, but \
do not lead with them.
- "supports_finding_ids" may only contain ids from the supplied findings. \
Never invent an id, and never write a statement you cannot attribute.
- Do not upgrade uncertainty. A finding whose claim_kind is "hypothesis" or \
"unknown" must not be restated as something established; carry it into \
"open_unknowns" or a "claim_kind": "unknown" statement instead.
- Set "decision_question" ONLY when the remaining question is a value \
judgement the developer must make (priority, acceptable trade-off, intended \
behavior). If more investigation could still answer it, leave it null and say \
what is missing in "open_unknowns" instead.
- Offer options only when the findings actually support more than one course \
of action. An empty "options" list is a valid answer.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


def _build_user_prompt(
    *, question: str, findings: Sequence[FindingSummary], goal_hint: Optional[str],
) -> str:
    parts = [f"What the developer wants to understand: {question}"]
    if goal_hint:
        parts.append(f"Stated goal/intent context: {goal_hint}")
    lines = []
    for f in findings:
        detail = f"#{f.id} [{f.origin_role}/{f.claim_kind}] {f.statement}"
        if f.uncertainty:
            detail += f" (uncertainty: {f.uncertainty})"
        if f.competing_explanations:
            detail += f" (competing: {'; '.join(f.competing_explanations)})"
        detail += f" (evidence citations: {f.evidence_count})"
        lines.append(detail)
    parts.append("## Findings you may use (the ONLY ids you may cite)\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def translate_findings(
    client: LLMClient,
    config: LLMConfig,
    *,
    question: str,
    findings: Sequence[FindingSummary],
    goal_hint: Optional[str] = None,
    language: str = "ja",
) -> TranslationResult:
    """Translate ``findings`` into purpose/impact statements and options.

    Fail-closed, with nothing partially usable returned:

    - a mock client or non-reasoning model,
    - an empty finding set (there is nothing to translate -- translating
      before any investigation would be exactly the "present a hypothesis and
      ask the same user to confirm it" loop Epic #328 replaces),
    - an LLM/API failure or invalid structured output,
    - a statement or option citing a finding id that was not supplied,
    - an unknown ``layer`` or a ``claim_kind`` outside
      ``TRANSLATION_CLAIM_KINDS``

    all return a result whose ``error`` is set and whose statements/options
    are empty. Callers must persist nothing but the failed audit row.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=is_mock,
            error=(
                "Translation requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )
    if not findings:
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="Nothing to translate: this session has no findings yet",
        )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
                {
                    "role": "user",
                    "content": _build_user_prompt(
                        question=question, findings=findings, goal_hint=goal_hint,
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=2048,
        )
    except LLMError as exc:
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        validated = _RawTranslation.model_validate(json.loads(_strip_fences(raw)))
    except (json.JSONDecodeError, ValidationError) as exc:
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    known_ids: Set[int] = {f.id for f in findings}
    statements: List[TranslatedStatement] = []
    for raw_statement in validated.statements:
        if raw_statement.layer not in STATEMENT_LAYERS:
            return TranslationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Unknown statement layer: {raw_statement.layer!r}",
            )
        if raw_statement.claim_kind not in TRANSLATION_CLAIM_KINDS:
            return TranslationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=(
                    f"A translation may not claim {raw_statement.claim_kind!r}; "
                    f"allowed: {list(TRANSLATION_CLAIM_KINDS)}"
                ),
            )
        unknown = [i for i in raw_statement.supports_finding_ids if i not in known_ids]
        if unknown or not raw_statement.supports_finding_ids:
            return TranslationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=(
                    "Every translated statement must cite supplied finding ids; "
                    f"offending refs: {unknown or 'none supplied'}"
                ),
            )
        statements.append(TranslatedStatement(
            layer=raw_statement.layer,
            claim_kind=raw_statement.claim_kind,
            text=raw_statement.text,
            supports_finding_ids=list(raw_statement.supports_finding_ids),
        ))

    options: List[TranslatedOption] = []
    first_layer = [s for s in statements if s.layer in ("purpose", "impact")]
    if not first_layer:
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="Translation must contain a grounded purpose or impact statement",
        )
    for raw_option in validated.options:
        unknown = [i for i in raw_option.supports_finding_ids if i not in known_ids]
        if unknown or not raw_option.supports_finding_ids:
            return TranslationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=(
                    "Every translated option must cite supplied finding ids; "
                    f"offending refs: {unknown or 'none supplied'}"
                ),
            )
        options.append(TranslatedOption(
            label=raw_option.label,
            what_changes=raw_option.what_changes,
            tradeoffs=raw_option.tradeoffs,
            supports_finding_ids=list(raw_option.supports_finding_ids),
        ))

    if not statements:
        return TranslationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error="Translation produced no attributable statement",
        )

    return TranslationResult(
        provider=config.provider, model=config.model, is_mock=False,
        # The primary summary is assembled only from already-validated,
        # attributable first-layer statements.  The model's standalone draft
        # is deliberately not persisted because it has no finding-id field.
        purpose_summary=" ".join(s.text for s in first_layer),
        statements=statements,
        options=options,
        open_unknowns=list(validated.open_unknowns),
        decision_question=validated.decision_question,
    )


# --- Deterministic assembly (no LLM) -----------------------------------------


@dataclass
class ActionMenuEntry:
    action_kind: str
    label: str
    what_changes: str
    # Issue #336: the existing formal operation this action leads to, and
    # whether it is performed by an endpoint outside this feature. Both come
    # from the deterministic server catalog in app/joint_understanding.py.
    formal_operation: str = ""
    completes_outside_session: bool = False


def build_action_menu(language: str) -> List[ActionMenuEntry]:
    """The finite next-action menu, assembled from fixed server templates.

    Deterministic and order-stable (``ACTION_KINDS`` order): what the
    developer may choose is never a model decision, and the wording is a
    catalog lookup, never generated text (Principle 6).
    """
    return [
        ActionMenuEntry(
            action_kind=kind,
            label=interview_message(f"joint_action_{kind}_label", language),
            what_changes=interview_message(f"joint_action_{kind}_effect", language),
            formal_operation=formal_operation_for(kind),
            completes_outside_session=completes_outside_session(kind),
        )
        for kind in ACTION_KINDS
    ]


def ask_developer(
    *, statements: Sequence[TranslatedStatement], decision_question: Optional[str],
    open_unknowns: Sequence[str],
) -> bool:
    """Whether this translation should actually put a question to the developer.

    Epic #328 replaces "classified as human-only, therefore hand it back with
    no material". A question is only worth asking when there IS a decision
    question AND the developer has something to decide with: at least one
    decision-layer statement, or an option comparison the caller supplies.
    Remaining unknowns alone are never a reason to ask -- they are a reason to
    investigate further, which is a different action.
    """
    if not decision_question:
        return False
    if any(s.layer == "decision" for s in statements):
        return True
    # No decision-layer material: ask only when there is nothing left to
    # investigate (no open unknowns) but there are still grounded statements
    # the developer can weigh.
    return not open_unknowns and bool(statements)
