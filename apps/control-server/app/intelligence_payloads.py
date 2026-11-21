"""Persist the exact LLM request/response of a reasoning run (IK-0029).

A reasoning run used to keep only its parsed result and an error string, so a
run that failed validation ("risks must be an array") could not say what the
model actually wrote, and a run that drifted could not be traced back to its
prompt.  ``RecordingLLMClient`` wraps the client a generator is given and
captures every call; ``store_payloads`` writes the captured calls to
``intelligence_run_payloads`` once the run row exists.

The request is stored after the same secret redaction the quota wrapper
applies before the provider call, so the stored text is what left the server.
The response is stored raw and in full, including when it fails to parse.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .llm import LLMClient, MockLLMClient
from .llm_secret_redaction import redact_messages, redact_secret_text


@dataclass
class RecordedCall:
    request_text: str
    response_text: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class RecordingLLMClient(LLMClient):
    """Delegate every call and keep the request/response pair."""

    def __init__(self, delegate: Any):
        self._delegate = delegate
        self.calls: List[RecordedCall] = []

    @property
    def last_usage(self):  # type: ignore[override]
        return getattr(self._delegate, "last_usage", None)

    @property
    def last_finish_reason(self):  # type: ignore[override]
        return getattr(self._delegate, "last_finish_reason", None)

    def generate_text(self, messages, **kwargs) -> str:  # type: ignore[override]
        params = {k: v for k, v in kwargs.items() if v is not None}
        request_text = json.dumps(
            {"messages": redact_messages(list(messages)), "params": params},
            ensure_ascii=False,
            default=str,
        )
        call = RecordedCall(request_text=request_text)
        self.calls.append(call)
        try:
            text = self._delegate.generate_text(messages, **kwargs)
        except Exception as exc:
            call.error = str(exc)
            call.usage = self.last_usage
            call.finish_reason = self.last_finish_reason
            raise
        call.response_text = text if isinstance(text, str) else str(text)
        call.usage = self.last_usage
        call.finish_reason = self.last_finish_reason
        return text


def wrap_for_recording(client: Any) -> Any:
    """Wrap a real client; mock clients stay unwrapped.

    Generators detect the mock provider with ``isinstance(client,
    MockLLMClient)``; wrapping a mock would hide that and make them call it.
    """
    if isinstance(client, MockLLMClient):
        return client
    return RecordingLLMClient(client)


def recorded_calls(client: Any) -> List[RecordedCall]:
    return list(getattr(client, "calls", []) or []) if isinstance(
        client, RecordingLLMClient
    ) else []


def store_payloads(
    conn,
    run_id: int,
    prompt_version: str,
    calls: List[RecordedCall],
) -> None:
    for call in calls:
        conn.execute(
            """
            INSERT INTO intelligence_run_payloads
                (run_id, kind, prompt_version, text, usage_json,
                 finish_reason, created_at)
            VALUES (?, 'request', ?, ?, NULL, NULL, ?)
            """,
            (run_id, prompt_version, call.request_text, call.created_at),
        )
        response_text = call.response_text
        if response_text is None:
            # The provider call itself failed: keep the error where the body
            # would have been so the pair stays complete.
            response_text = f"[no response: {call.error or 'unknown error'}]"
        conn.execute(
            """
            INSERT INTO intelligence_run_payloads
                (run_id, kind, prompt_version, text, usage_json,
                 finish_reason, created_at)
            VALUES (?, 'response', ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                prompt_version,
                redact_secret_text(response_text),
                json.dumps(call.usage) if call.usage is not None else None,
                call.finish_reason,
                time.time(),
            ),
        )


def list_payloads(conn, run_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM intelligence_run_payloads
        WHERE run_id = ? ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "prompt_version": row["prompt_version"],
            "text": row["text"],
            "usage": json.loads(row["usage_json"]) if row["usage_json"] else None,
            "finish_reason": row["finish_reason"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
