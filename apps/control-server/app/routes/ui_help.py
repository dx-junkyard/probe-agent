"""UI 機能解説モードの API 境界 (Issue #440, Epic #436).

- GET /assistant/ui-help?screen_id=  一覧 (screen_id 省略時は全画面)。
- GET /assistant/ui-help/{help_id}   完全一致のみ。未知の id は 404
  (最も近い候補へのフォールバックは絶対に行わない)。

`app/ui_help_registry.py` の static registry を verbatim に返すだけで、
LLM は一切呼ばない。応答は常に `registry_version` と
`decision_method: "deterministic"` を含む (`docs/assistant-discussion.md`
§3)。

probe-agent:
  role: API boundary for the deterministic UI help-mode registry
  capability: ui-help-mode
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: []
  probe_value: Verify the endpoints only ever serve the static registry verbatim, never invoke an LLM, and 404 on an unknown help_id rather than guessing.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from ..models import UiHelpActionOut, UiHelpDocRefOut, UiHelpEntriesOut, UiHelpEntryOut
from ..ui_help_registry import (
    UI_HELP_ENTRIES,
    UI_HELP_REGISTRY_VERSION,
    HELP_BY_ID,
    UiHelpEntry,
    entries_for_screen,
)

router = APIRouter()


def _entry_out(entry: UiHelpEntry) -> UiHelpEntryOut:
    return UiHelpEntryOut(
        help_id=entry.help_id,
        screen_id=entry.screen_id,
        scope=entry.scope,
        title=entry.title,
        summary=entry.summary,
        usage=entry.usage,
        doc_refs=[
            UiHelpDocRefOut(doc_path=d.doc_path, title=d.title, anchor=d.anchor)
            for d in entry.doc_refs
        ],
        related_actions=[
            UiHelpActionOut(label=a.label, kind=a.kind, target=a.target)
            for a in entry.related_actions
        ],
        related_help_ids=list(entry.related_help_ids),
        registry_version=UI_HELP_REGISTRY_VERSION,
    )


@router.get("/assistant/ui-help", response_model=UiHelpEntriesOut)
def list_ui_help(screen_id: Optional[str] = None) -> UiHelpEntriesOut:
    entries = entries_for_screen(screen_id) if screen_id else UI_HELP_ENTRIES
    return UiHelpEntriesOut(
        entries=[_entry_out(e) for e in entries],
        registry_version=UI_HELP_REGISTRY_VERSION,
    )


@router.get("/assistant/ui-help/{help_id}", response_model=UiHelpEntryOut)
def get_ui_help(help_id: str) -> UiHelpEntryOut:
    entry = HELP_BY_ID.get(help_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown help_id: {help_id}")
    return _entry_out(entry)
