"""Interview output-language configuration (Issue #127).

The system-understanding interview surfaces LLM-generated text (questions,
confirmations, summaries) directly to the developer. The output language is
an explicit finite-set setting (`INTERVIEW_LANGUAGE`, default ``ja``), never
inferred from repository contents. JSON keys and enum values always stay in
English so schema contracts are unaffected.

Invalid values fail closed: callers surface the error through their normal
reasoning-run failure path instead of silently falling back to English.
"""

from __future__ import annotations

import os

SUPPORTED_INTERVIEW_LANGUAGES = ("ja", "en")

_LANGUAGE_NAMES = {"ja": "Japanese", "en": "English"}


def get_interview_language() -> str:
    """Return the configured interview output language.

    Raises ValueError for values outside the supported finite set.
    """
    raw = os.getenv("INTERVIEW_LANGUAGE", "ja").strip().lower()
    if raw not in SUPPORTED_INTERVIEW_LANGUAGES:
        raise ValueError(
            "INTERVIEW_LANGUAGE must be one of "
            f"{list(SUPPORTED_INTERVIEW_LANGUAGES)}, got {raw!r}"
        )
    return raw


def language_directive(language: str) -> str:
    """Prompt rule that fixes the natural-language output language.

    JSON keys and enum values are explicitly exempted so the structured
    output keeps validating against the existing schemas.
    """
    name = _LANGUAGE_NAMES[language]
    return (
        f"- Write all natural-language output in {name}: assistant_message, "
        "question texts, hypotheses, answer options, summaries, reasons, and "
        "any free-text field values.\n"
        "- Keep all JSON keys and all enum values (element_type, "
        "operation_kind, state_effects, recommended_mode, side_effect_risk, "
        "replayability, confidence levels, categories, priorities, "
        "suggested_next_action) in English exactly as specified above."
    )
