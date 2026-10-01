"""Fixed failure-response table for ``POST /assistant/ask`` (Issue #472).

Canonical contract: docs/01-specifications/capabilities/assistant-answer-quality.md §5.

One Japanese message, one retryability verdict and one recovery list per
``AssistantFailureClass``.  The text is FIXED: it never embeds ``str(exc)``, a
provider response body, or a key value (Principle 9), so what the developer
reads, what is persisted, and what a metric row can hold are the same
non-secret sentence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, get_args

from .models import AssistantFailureClass

FAILURE_CLASSES = get_args(AssistantFailureClass)


def _r(kind: str, label: str, target: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "label": label, "target": target}


_RETRY = _r("retry", "もう一度送信する")
_SETTINGS = _r("open_settings", "設定画面を開く", "/settings")

FAILURE_TABLE: Dict[str, Dict[str, Any]] = {
    "provider_not_configured": {
        "message": "AI アシスタントの LLM プロバイダーまたは API キーが設定されていないため、回答を生成できませんでした。",
        "retryable": False,
        "recovery": [_r("configure", "LLM_API_KEY を設定する", "LLM_API_KEY"), _SETTINGS],
    },
    "provider_test_only": {
        "message": "LLM プロバイダーが mock (テスト用) のため、AI アシスタントの回答は生成しません。",
        "retryable": False,
        "recovery": [_r("configure", "LLM_PROVIDER を実プロバイダーに設定する", "LLM_PROVIDER"), _SETTINGS],
    },
    "timeout": {
        "message": "LLM の応答が時間内に返らなかったため、回答を生成できませんでした。",
        "retryable": True,
        "recovery": [_RETRY, _r("rephrase", "質問を短く絞って送り直す")],
    },
    "network": {
        "message": "LLM プロバイダーへ接続できなかったため、回答を生成できませんでした。",
        "retryable": True,
        "recovery": [_RETRY],
    },
    "auth": {
        "message": "LLM プロバイダーの認証に失敗したため、回答を生成できませんでした。API キーを確認してください。",
        "retryable": False,
        "recovery": [_r("configure", "LLM_API_KEY を確認する", "LLM_API_KEY"), _SETTINGS],
    },
    "rate_limited": {
        "message": "LLM プロバイダーの利用上限に達したため、回答を生成できませんでした。少し待ってから再試行してください。",
        "retryable": True,
        "recovery": [_RETRY],
    },
    "provider_error": {
        "message": "LLM プロバイダー側でエラーが発生したため、回答を生成できませんでした。",
        "retryable": True,
        "recovery": [_RETRY],
    },
    "malformed_output": {
        "message": "LLM の出力が回答の形式を満たさなかったため、回答として表示できませんでした。",
        "retryable": True,
        "recovery": [_RETRY, _r("rephrase", "質問を具体的にして送り直す")],
    },
    "truncated_output": {
        "message": "LLM の出力が途中で切れたため、回答として表示できませんでした。",
        "retryable": True,
        "recovery": [_RETRY, _r("rephrase", "質問を絞って送り直す")],
    },
    "context_unavailable": {
        "message": "回答に必要な画面の情報を読み込めなかったため、回答を生成できませんでした。",
        "retryable": True,
        "recovery": [_RETRY],
    },
    "budget_exceeded": {
        "message": "LLM の利用上限または時間の予算を超えたため、回答を生成できませんでした。",
        "retryable": False,
        "recovery": [_SETTINGS, _r("rephrase", "質問を絞って送り直す")],
    },
}

assert set(FAILURE_TABLE) == set(FAILURE_CLASSES), "failure table must cover every class"


def failure_message(failure_class: str) -> str:
    return FAILURE_TABLE[failure_class]["message"]


def failure_dict(failure_class: str) -> Dict[str, Any]:
    entry = FAILURE_TABLE[failure_class]
    return {
        "failure_class": failure_class,
        "message": entry["message"],
        "retryable": entry["retryable"],
        "recovery": [dict(r) for r in entry["recovery"]],
    }


def compose_failed_answer(failure_class: str) -> str:
    """The `answer` text of a `failed` response: the reason and the next
    operations only -- never a screen overview or a settings dump (§5.2)."""
    entry = FAILURE_TABLE[failure_class]
    lines: List[str] = [entry["message"], "", "次の操作:"]
    lines.extend(f"- {r['label']}" for r in entry["recovery"])
    return "\n".join(lines)
