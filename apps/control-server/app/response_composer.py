"""Response Composer (Issue #286).

The last, deterministic (no LLM) step of an Inquiry answer: assembles the
Question Router category and (when applicable) the Investigation Agent's
result into the conclusion-first, 4-layer structure an Inquiry assistant
message uses -- conclusion (1-3 sentences), key_points,
evidence_and_uncertainty, and a decision_question for hybrid questions.

Deterministic assembly only (Principle 6): every human-readable sentence
this module produces is either a fixed server template
(``interview_language.INTERVIEW_MESSAGES``) or text a reasoning step already
produced and validated (the investigation's own conclusion/key_points/
evidence/uncertainty, or the router's own reason). No new wording is
invented here, and no LLM is called from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .interview_language import interview_message
from .investigation_agent import InvestigationEvidenceItem, InvestigationResult


@dataclass
class ComposedAnswer:
    answerable: bool
    conclusion: str
    key_points: List[str] = field(default_factory=list)
    evidence: List[InvestigationEvidenceItem] = field(default_factory=list)
    uncertainty: str = ""
    decision_question: Optional[str] = None
    route_category: Optional[str] = None


def compose_human_only(*, question_text: str, route_reason: str, language: str) -> ComposedAnswer:
    """human_only: no investigation ran. Ask the developer for their decision.

    Always ``answerable=True`` -- this is a valid, complete outcome (not a
    "not enough information" case), so the fixed
    "inquiry_insufficient_information" template must never be substituted
    for it.
    """
    content = interview_message(
        "inquiry_human_only_answer", language, question=question_text, reason=route_reason,
    )
    return ComposedAnswer(
        answerable=True, conclusion=content, key_points=[], evidence=[], uncertainty="",
        decision_question=None, route_category="human_only",
    )


def compose_system_researchable(*, investigation: InvestigationResult, language: str) -> ComposedAnswer:
    """system_researchable: surface the investigation's own result verbatim.

    ``status="completed"`` -> answerable with the investigation's conclusion.
    ``status="unresolved"`` -> ``answerable=False`` so the caller substitutes
    the fixed "insufficient information" template instead of fabricating a
    conclusion from an incomplete investigation.
    """
    if investigation.status == "completed":
        return ComposedAnswer(
            answerable=True,
            conclusion=investigation.conclusion,
            key_points=list(investigation.key_points),
            evidence=list(investigation.evidence),
            uncertainty=investigation.uncertainty,
            decision_question=None,
            route_category="system_researchable",
        )
    return ComposedAnswer(
        answerable=False, conclusion="", key_points=[], evidence=[],
        uncertainty=investigation.uncertainty, decision_question=None,
        route_category="system_researchable",
    )


def compose_hybrid(
    *, investigation: InvestigationResult, question_text: str, language: str,
) -> ComposedAnswer:
    """hybrid: always answerable -- append a decision-question section.

    Unlike system_researchable, an unresolved investigation does not make
    the whole answer "insufficient information": the human-decision half of
    a hybrid question is always presentable, so a fixed note about the
    unresolved code-investigation half is used instead of withholding the
    message. The decision question itself comes from the investigation's
    own (validated) ``decision_question`` field when the model provided
    one, or a fixed template echoing the question when it did not.
    """
    if investigation.status == "completed":
        base = investigation.conclusion
        key_points = list(investigation.key_points)
        evidence = list(investigation.evidence)
        uncertainty = investigation.uncertainty
    else:
        base = interview_message("inquiry_hybrid_unresolved_note", language)
        key_points = []
        evidence = []
        uncertainty = investigation.uncertainty

    decision_question = investigation.decision_question or interview_message(
        "inquiry_hybrid_default_decision_question", language, question=question_text,
    )
    heading = interview_message("inquiry_hybrid_decision_heading", language)
    conclusion = f"{base}\n\n{heading} {decision_question}"

    return ComposedAnswer(
        answerable=True,
        conclusion=conclusion,
        key_points=key_points,
        evidence=evidence,
        uncertainty=uncertainty,
        decision_question=decision_question,
        route_category="hybrid",
    )
