"""Interview output-language configuration (Issue #127).

The system-understanding interview surfaces LLM-generated text (questions,
confirmations, summaries) directly to the developer. The output language is
an explicit finite-set setting (`INTERVIEW_LANGUAGE`, default ``ja``), never
inferred from repository contents. JSON keys and enum values always stay in
English so schema contracts are unaffected.

Invalid values fail closed: callers surface the error through their normal
reasoning-run failure path instead of silently falling back to English.

Issue #138 extends this to the server's own fixed-text messages (assistant
messages the route inserts directly, not LLM output) via ``INTERVIEW_MESSAGES``
and ``interview_message()`` below, so ``INTERVIEW_LANGUAGE=en`` does not leave
Japanese boilerplate mixed into an otherwise-English conversation log.
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


# --- Server fixed-text messages (Issue #138) ---------------------------------
#
# Text the server itself composes and inserts into interview_message /
# interview_session rows (not LLM output) — a finite set of message keys, each
# with a ja/en entry. Selection is table lookup only (deterministic,
# Principle 6): no translation API, no inference. Every key must be present
# for every language in SUPPORTED_INTERVIEW_LANGUAGES (checked by tests).

INTERVIEW_MESSAGES: dict = {
    "understanding_update_failed": {
        "ja": "理解の更新に失敗しました: {error}",
        "en": "Failed to update understanding: {error}",
    },
    "graph_not_built": {
        "ja": (
            "このスナップショットの理解グラフが未構築です。"
            "先に System Understanding の build/refresh を実行してください。"
        ),
        "en": (
            "The understanding graph for this snapshot has not been built yet. "
            "Run System Understanding build/refresh first."
        ),
    },
    "review_failed": {
        "ja": "理解のレビューに失敗しました: {error}",
        "en": "Failed to review understanding: {error}",
    },
    "invalid_review_response": {
        "ja": "理解レビューの応答形式が不正です。もう一度「理解を更新」を実行してください。",
        "en": "The understanding review returned an invalid response. Run Update Understanding again.",
    },
    "understanding_built_intro": {
        "ja": "ドキュメントとコードを分析し、初期理解を構築しました。",
        "en": "Analyzed the documentation and code and built an initial understanding.",
    },
    "system_purpose_label": {
        "ja": "システムの目的",
        "en": "System purpose",
    },
    "core_capability_label": {
        "ja": "主要機能",
        "en": "Core capability",
    },
    "unknown_name": {
        "ja": "不明",
        "en": "unknown",
    },
    "key_questions_heading": {
        "ja": "主な確認事項:",
        "en": "Key questions to confirm:",
    },
    "suggested_next_action_label": {
        "ja": "推奨される次のステップ",
        "en": "Suggested next step",
    },
    "confirm_understanding_message": {
        "ja": "これまでの回答内容を確定し、提案生成に進みます。",
        "en": "Confirmed the answers so far; proceeding to proposal generation.",
    },
    # Deterministic no-evidence fallback question appended by the
    # system-understanding reviewer (previously a private table in
    # system_understanding_reviewer.py; registered here so the
    # all-keys-in-all-languages test covers it too).
    "no_evidence_question": {
        "ja": "「{name}」({section})の根拠がドキュメント・コードから見つかりませんでした。この項目は正しいですか?",
        "en": "No evidence for {section} item: {name}. Is this item correct?",
    },
    # Issue #285: fixed, non-LLM-fabricated message stored on an Inquiry when
    # the reasoning model determined it cannot answer (answerable=false).
    # Never the model's own wording -- a lookup-table sentence only.
    "inquiry_insufficient_information": {
        "ja": "回答に必要な情報が不足しています。「解消していない」から追加の質問をするか、「今回は保留する」を選んでください。",
        "en": (
            "There is not enough information to answer this. Ask a follow-up "
            "question, or choose \"hold for now\"."
        ),
    },
}

# Deterministic fallback language for the fixed-text messages above only,
# used when INTERVIEW_LANGUAGE itself is invalid/unset in a way that would
# otherwise prevent even the failure message from being composed. This never
# affects LLM-directed content: reasoning calls keep failing closed through
# get_interview_language() raising ValueError, exactly as before Issue #138.
FIXED_TEXT_FALLBACK_LANGUAGE = "ja"


def resolve_message_language() -> str:
    """Language for server fixed-text messages; falls back to ja on invalid config.

    Deliberately distinct from get_interview_language(), which stays
    fail-closed for reasoning-model calls. A misconfigured INTERVIEW_LANGUAGE
    must not prevent the server from composing the very failure message that
    reports the misconfiguration.
    """
    try:
        return get_interview_language()
    except ValueError:
        return FIXED_TEXT_FALLBACK_LANGUAGE


def interview_message(key: str, language: str, **kwargs) -> str:
    """Look up and format a server fixed-text message. Table lookup only."""
    return INTERVIEW_MESSAGES[key][language].format(**kwargs)


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
