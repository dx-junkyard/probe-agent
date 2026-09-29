"""Input budget for one assistant ask (Issue #470, contract §3.3).

``apply_context_budget`` trims a ``ContextPack`` until its LLM payload is at
most ``limit`` characters, in the contract's fixed order, and records exactly
what it dropped.  Deterministic and finite: no relevance scoring, no
similarity -- the order is structural ("deepest related first, then
already-green diagnostics, ...").

Never trimmed: the question, ``discussion_target``, ``ui_draft``, the focused
state item, ``screen_context_state`` and ``coverage`` (the latter grows by the
entries this function itself writes).  If everything trimmable is gone and the
payload is still over, it is sent as is with ``over_budget: true`` -- the
protected parts are never cut to make a number fit.

Size is ``len(json.dumps(payload, ensure_ascii=False))`` of the context payload
(the JSON the model receives under ``context``).  ``used_chars`` excludes the
``context_budget`` block itself.

The pack is duck-typed (no import of ``assistant``) to avoid a cycle.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .assistant_coverage import CoverageEntry

DEFAULT_CONTEXT_BUDGET_CHARS = 60000
BUDGET_ENV = "ASSISTANT_CONTEXT_BUDGET_CHARS"

# screen_data keys that are lists but are never a paginated data list.
_UNTRIMMABLE_SCREEN_LISTS = frozenset({"degraded_sections"})


def context_budget_chars() -> int:
    raw = os.environ.get(BUDGET_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_BUDGET_CHARS
    return value if value > 0 else DEFAULT_CONTEXT_BUDGET_CHARS


def _size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def measure(pack: Any, report: Any) -> int:
    return _size(pack.to_llm_payload(report))


def _omit(pack: Any, section: str, dropped: int) -> None:
    if dropped <= 0:
        return
    for row in pack.omitted_sections:
        if row.get("section") == section and row.get("reason") == "budget":
            row["dropped_items"] = int(row.get("dropped_items") or 0) + dropped
            return
    pack.omitted_sections.append(
        {"section": section, "reason": "budget", "dropped_items": dropped}
    )


def _upsert_truncated(pack: Any, section: str, *, returned: int, original: int) -> None:
    """Mark ``section`` truncated by budget, keeping a previously known exact total."""
    prev: Optional[Dict[str, Any]] = next(
        (c for c in pack.coverage if c.get("section") == section), None
    )
    total: Optional[int]
    if prev is None or prev.get("state") in ("complete", "empty"):
        total = original
    else:
        total = prev.get("total")
    entry = CoverageEntry(
        section=section, state="truncated", returned=returned, total=total,
        limit=prev.get("limit") if prev else None,
        more_available_via=prev.get("more_available_via") if prev else None,
        reason="budget",
    ).to_dict()
    if prev is None:
        pack.coverage.append(entry)
    else:
        prev.clear()
        prev.update(entry)


def _trim_related(pack: Any, report: Any, limit: int) -> None:
    entries: List[Dict[str, Any]] = pack.related_context or []
    if not entries:
        return
    original = len(entries)
    # Deepest first; within one depth, the later-emitted entry first.
    order = sorted(range(original), key=lambda i: (-int(entries[i].get("depth", 1)), -i))
    dropped_ids = set()
    for index in order:
        if measure(pack, report) <= limit:
            break
        dropped_ids.add(index)
        pack.related_context = [e for i, e in enumerate(entries) if i not in dropped_ids]
    if dropped_ids:
        _omit(pack, "related_context", len(dropped_ids))
        _upsert_truncated(
            pack, "related_context.related",
            returned=len([e for e in pack.related_context if int(e.get("depth", 1)) > 0]),
            original=len([e for e in entries if int(e.get("depth", 1)) > 0]),
        )


def _drop_from_tail(pack: Any, report: Any, limit: int, items: List[Any], protected) -> int:
    """Pop unprotected items from the tail of ``items`` (in place) until the
    payload fits; returns how many were dropped."""
    dropped = 0
    index = len(items) - 1
    while index >= 0 and measure(pack, report) > limit:
        if not protected(items[index]):
            items.pop(index)
            dropped += 1
        index -= 1
    return dropped


def _trim_screen_lists(pack: Any, report: Any, limit: int) -> None:
    data = pack.screen_data
    if not isinstance(data, dict):
        return
    pack.screen_data = data = dict(data)  # never mutate the caller's dict
    keys = [
        k for k, v in data.items()
        if isinstance(v, list) and v and k not in _UNTRIMMABLE_SCREEN_LISTS
    ]
    for key in keys:
        data[key] = list(data[key])
    originals = {k: len(data[k]) for k in keys}
    while measure(pack, report) > limit:
        candidates = [k for k in keys if data[k]]
        if not candidates:
            break
        # The list that costs the most gives up its tail first.
        key = max(candidates, key=lambda k: _size(data[k]))
        data[key].pop()
    for key in keys:
        dropped = originals[key] - len(data[key])
        if dropped:
            _omit(pack, f"screen_data.{key}", dropped)
            _upsert_truncated(pack, key, returned=len(data[key]), original=originals[key])


def apply_context_budget(
    pack: Any, report: Any, *, limit: Optional[int] = None, trim: bool = True,
) -> Dict[str, Any]:
    """Trim ``pack`` in place to fit ``limit`` characters (§3.3 order) and
    return ``{"limit_chars", "used_chars", "over_budget"}``.

    ``trim=False`` only measures (used when nothing is sent to a model)."""
    limit = context_budget_chars() if limit is None else limit
    if trim and measure(pack, report) > limit:
        # 1. related_context entries, deepest first.
        _trim_related(pack, report, limit)
        # 2. diagnostics checks that are `ok` (a mentioned check is kept).
        if measure(pack, report) > limit:
            mentioned = set(pack.mentioned_check_ids)
            pack.checks = list(pack.checks)
            dropped = _drop_from_tail(
                pack, report, limit, pack.checks,
                lambda c: c.severity != "ok" or c.check_id in mentioned,
            )
            _omit(pack, "diagnostics.checks", dropped)
        # 3. settings the question did not mention.
        if measure(pack, report) > limit:
            mentioned_keys = set(pack.mentioned_setting_keys)
            pack.settings = list(pack.settings)
            dropped = _drop_from_tail(
                pack, report, limit, pack.settings, lambda s: s.key in mentioned_keys,
            )
            _omit(pack, "settings", dropped)
        # 4. system-state items other than the focused one.
        if measure(pack, report) > limit and pack.state_items:
            pack.state_items = list(pack.state_items)
            dropped = _drop_from_tail(
                pack, report, limit, pack.state_items,
                lambda item: item.state_id == pack.focused_state_id,
            )
            _omit(pack, "system_state_items", dropped)
        # 5. lists inside screen_data, tail first.
        if measure(pack, report) > limit:
            _trim_screen_lists(pack, report, limit)
    used = measure(pack, report)
    info = {"limit_chars": limit, "used_chars": used, "over_budget": used > limit}
    pack.budget_info = info
    return info
