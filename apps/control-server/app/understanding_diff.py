"""Deterministic understanding-revision diffing (Issue #136).

Compares two ``current_understanding`` JSON snapshots (as produced by
``system_understanding_reviewer.generate_understanding_review``) section by
section, matching items by exact ``name`` equality only. This is a
structural diff, not a semantic one (Principle 6): renames show up as one
"removed" and one "added" entry, never as a match.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

UNDERSTANDING_SECTIONS = [
    "system_purpose",
    "core_capabilities",
    "capability_elements",
    "supporting_elements",
    "api_boundaries",
    "probe_flow_candidates",
]

DEFAULT_REVISION_LIMIT = 20


def revision_limit() -> int:
    raw = os.getenv(
        "INTERVIEW_UNDERSTANDING_REVISION_LIMIT", str(DEFAULT_REVISION_LIMIT)
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "INTERVIEW_UNDERSTANDING_REVISION_LIMIT must be an integer"
        ) from exc
    if value < 1:
        raise ValueError("INTERVIEW_UNDERSTANDING_REVISION_LIMIT must be positive")
    return value


def _items_by_name(items: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            by_name[name] = item
    return by_name


def _diff_section(
    previous_items: Optional[List[Dict[str, Any]]],
    current_items: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    prev_by_name = _items_by_name(previous_items)
    curr_by_name = _items_by_name(current_items)

    added = sorted(set(curr_by_name) - set(prev_by_name))
    removed = sorted(set(prev_by_name) - set(curr_by_name))

    confidence_changed: List[Dict[str, Optional[str]]] = []
    summary_changed: List[str] = []
    for name in sorted(set(prev_by_name) & set(curr_by_name)):
        prev_item = prev_by_name[name]
        curr_item = curr_by_name[name]
        prev_level = (prev_item.get("confidence") or {}).get("level")
        curr_level = (curr_item.get("confidence") or {}).get("level")
        if prev_level != curr_level:
            confidence_changed.append({"name": name, "before": prev_level, "after": curr_level})
        if (prev_item.get("summary") or "") != (curr_item.get("summary") or ""):
            summary_changed.append(name)

    return {
        "added": added,
        "removed": removed,
        "confidence_changed": confidence_changed,
        "summary_changed": summary_changed,
    }


def diff_understanding(
    previous: Optional[Dict[str, Any]],
    current: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Section-by-section structural diff between two understanding snapshots.

    ``previous``/``current`` may be None (e.g. an empty first revision);
    treated as a snapshot with no sections.
    """
    sections: List[Dict[str, Any]] = []
    for section in UNDERSTANDING_SECTIONS:
        prev_items = (previous or {}).get(section) if previous else None
        curr_items = (current or {}).get(section) if current else None
        result = _diff_section(prev_items, curr_items)
        result["section"] = section
        sections.append(result)
    return sections
