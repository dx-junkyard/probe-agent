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
    # Issue #286: deterministic Response Composer templates. Never LLM
    # output -- these are the fixed frame around a Question Router /
    # Investigation Agent result, never the model's own free-text wording.
    "inquiry_human_only_answer": {
        "ja": (
            "この点はコードから判断できるものではなく、あなたご自身の判断が必要です。\n\n"
            "ご質問: {question}\n\n"
            "AI 判断根拠: {reason}"
        ),
        "en": (
            "This is not something the code can determine -- it needs your "
            "own decision.\n\n"
            "Your question: {question}\n\n"
            "AI classification reason: {reason}"
        ),
    },
    "inquiry_hybrid_unresolved_note": {
        "ja": "コードからの調査だけでは十分な根拠が見つかりませんでした。",
        "en": "The code investigation alone did not find enough grounding.",
    },
    "inquiry_hybrid_decision_heading": {
        "ja": "確認したいこと:",
        "en": "What we'd like you to decide:",
    },
    "inquiry_hybrid_default_decision_question": {
        "ja": "この点について、あなたの判断を教えてください:「{question}」",
        "en": "Please share your own decision on this: \"{question}\"",
    },
    # Issue #290: fixed, non-LLM-fabricated pointer shown once an
    # observation proposal is approved. Approving never starts observation
    # itself -- this only points back at the existing policy endpoint.
    "observation_proposal_policy_hint": {
        "ja": (
            "この提案は承認されましたが、観測はまだ開始されていません。"
            "「{component_id}」の実行時観測を開始するには、"
            "既存のポリシー設定画面からこのコンポーネントの mode を "
            "trace または shadow に変更してください。"
        ),
        "en": (
            "This proposal has been approved, but observation has not started "
            "yet. To start runtime observation for \"{component_id}\", change "
            "this component's mode to trace or shadow from the existing "
            "policy settings."
        ),
    },
    # Epic #328 Phase C (#331): the finite next-action menu of a Joint
    # Understanding session. Two entries per app/joint_understanding.py's
    # ACTION_KINDS -- the button label and, per Epic #328, what choosing it
    # actually changes ("各選択肢が何を変えるか"). Fixed templates, never LLM
    # output, so the menu is identical for identical state.
    "joint_action_request_investigation_label": {
        "ja": "さらに調査する",
        "en": "Investigate further",
    },
    "joint_action_request_investigation_effect": {
        "ja": "固定したスナップショットを追加で読み、不足していた証拠を埋めます。回答は確定しません。",
        "en": "Reads more of the pinned snapshot to fill the missing evidence. Nothing is confirmed.",
    },
    "joint_action_explain_reasoning_label": {
        "ja": "判断の理由を説明してもらう",
        "en": "Explain the reasoning",
    },
    "joint_action_explain_reasoning_effect": {
        "ja": "今わかっている事実と推論のつながりを、目的への意味として説明し直します。新しい事実は増えません。",
        "en": "Re-explains how the known facts connect to your goal. No new facts are added.",
    },
    "joint_action_compare_options_label": {
        "ja": "選択肢を比較する",
        "en": "Compare the options",
    },
    "joint_action_compare_options_effect": {
        "ja": "取りうる選択肢と、それぞれで何が変わるか・何を諦めるかを並べます。選ぶのはあなたです。",
        "en": "Lays out the available options and what each changes or costs. You still choose.",
    },
    "joint_action_adopt_hypothesis_label": {
        "ja": "仮説を暫定的に採用する",
        "en": "Adopt the hypothesis provisionally",
    },
    "joint_action_adopt_hypothesis_effect": {
        "ja": "仮説を「暫定」として記録します。事実にはならず、後で再検証の対象として残ります。",
        "en": "Records the hypothesis as PROVISIONAL. It does not become a fact and stays subject to re-verification.",
    },
    "joint_action_revise_intent_label": {
        "ja": "意図を修正する",
        "en": "Revise the intent",
    },
    "joint_action_revise_intent_effect": {
        "ja": "あなたの目的や期待の記述を見直します。実装上の事実は変わりません。",
        "en": "Revisits your own goal or expectation. The implementation facts do not change.",
    },
    "joint_action_hold_label": {
        "ja": "今は保留する",
        "en": "Hold for now",
    },
    "joint_action_hold_effect": {
        "ja": "この対話を中断し、いつでも再開できるようにします。元の確認項目は未回答のまま残ります。",
        "en": "Pauses this conversation so it can resume later. The original item stays unanswered.",
    },
    "joint_action_handoff_label": {
        "ja": "他の人に引き継ぐ",
        "en": "Hand off to someone else",
    },
    "joint_action_handoff_effect": {
        "ja": "判断できる人へ引き継ぎます。引き継ぎ先の回答があなたの回答として記録されることはありません。",
        "en": "Hands the decision to someone who can make it. Their answer is never recorded as yours.",
    },
    "joint_action_decide_label": {
        "ja": "判断を確定する",
        "en": "Record the decision",
    },
    "joint_action_decide_effect": {
        "ja": "この対話での判断を確定として記録します。元の確認項目の回答は、項目ごとの操作で別途行います。",
        "en": "Records your decision for this conversation. Answering the original item is still a separate step.",
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
