"""Related context for one assistant ask (Issue #470, contract §3.1).

When an ask carries a discussion thread whose target is a registered adapter
kind and still matches what the thread captured, the ask reads the SAME
discussion context bundle the Dashboard's context view reads
(``discussion_context_bundle.build_context_bundle``) -- with a smaller budget --
and hands a compact projection to the model as ``related_context``.

Nothing here decides anything open-ended: the selection is "the thread's own
target and what the adapter registry already relates to it".  LLM-generated
claims (#458) are NOT reused: they are interpretations, and an ask must ground
in canonical facts only.

Failure never fails the ask.  Every non-happy outcome is one of the finite
``omitted_sections`` reasons (``stale_target`` / ``unsupported`` /
``unavailable``) so the model can say "I was not given that" instead of guessing.

Never called while a ``get_conn()`` connection is held: the builder opens its own.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import discussion_adapters, discussion_context_bundle
from .assistant_coverage import (
    CoverageEntry,
    unavailable_entry,
    unsupported_entry,
)
from .discussion_context_bundle import ContextBudget, ContextBundleError

logger = logging.getLogger(__name__)

SECTION = "related_context"

# An ask sends its whole context to the model on every turn, unlike the
# Dashboard's context view (64 KiB / 200 items, `DEFAULT_BUDGET`), which is a
# read the developer asks for once.  The whole payload budget is
# `ASSISTANT_CONTEXT_BUDGET_CHARS` (60000 chars by default); related context is
# an *addition* to the screen's own facts, so it gets roughly a quarter of it:
# depth 2 (the same hop limit -- neighbours of neighbours are where a chain
# such as Gap -> Journey -> Requirement lives), at most 20 entries, 16 KiB of
# facts.  `version` is distinct so a later tuning is visible in audits.
ASK_BUNDLE_BUDGET = ContextBudget(
    version="ask-context-budget-v1",
    max_depth=2,
    section_item_limit=20,
    total_item_limit=20,
    total_byte_limit=16 * 1024,
)

_FRESHNESS_FOR_MANIFEST = {
    "current": "current",
    "stale": "stale",
    "not_tracked": "not_tracked",
    # A bundle entry that no longer resolves is not "outdated", it is unknown.
    "unresolvable": "unknown",
}


@dataclass
class RelatedContext:
    entries: List[Dict[str, Any]] = field(default_factory=list)
    coverage: List[CoverageEntry] = field(default_factory=list)
    omitted_sections: List[Dict[str, Any]] = field(default_factory=list)


def _omit(reason: str) -> Dict[str, Any]:
    return {"section": SECTION, "reason": reason, "dropped_items": None}


def _coverage_for_section(section: Any) -> Optional[CoverageEntry]:
    name = f"{SECTION}.{section.section_id}"
    cov = section.coverage
    state = section.operation_state
    if state == "not_applicable":
        return None  # structurally nothing to relate; not a gap
    if state == "unavailable":
        return unavailable_entry(name, cov.stop_reason)
    if state == "unsupported":
        # No relation registered for this kind: a structural gap, said so.
        if section.section_id == "self":
            return None  # the root's own facts already live in screen_data
        return unsupported_entry(name, cov.stop_reason)
    returned = len(section.facts)
    if cov.completeness == "complete":
        if returned == 0:
            return CoverageEntry(section=name, state="empty", returned=0, total=cov.total_count)
        return CoverageEntry(section=name, state="complete", returned=returned, total=cov.total_count)
    return CoverageEntry(
        section=name, state="truncated", returned=returned, total=cov.total_count,
        limit=None, more_available_via="context_expansion", reason=cov.stop_reason or None,
    )


def project_bundle(bundle: Any) -> RelatedContext:
    """Compact, model-facing projection of one bundle (pure)."""
    freshness = {
        (s.target_kind, s.target_ref): _FRESHNESS_FOR_MANIFEST.get(s.freshness, "unknown")
        for s in bundle.sources
    }
    depths = discussion_context_bundle.entry_depths(bundle)
    allowed = {(s.target_kind, s.target_ref) for s in bundle.sources}
    out = RelatedContext()
    by_key: Dict[Any, Dict[str, Any]] = {}

    def add(entry: Any, facts: Dict[str, Any]) -> None:
        key = (entry.target_kind, entry.target_ref)
        if key not in allowed:
            return  # only registered sources are citable, and only those are shown
        existing = by_key.get(key)
        if existing is not None:
            # The Overview root and its objective section share one identity.
            existing["facts"].update(facts)
            return
        item: Dict[str, Any] = {
            "source_id": f"{entry.target_kind}:{entry.target_ref}",
            "target_kind": entry.target_kind,
            "target_ref": entry.target_ref,
            "title": entry.title,
            "revision_id": entry.revision_id,
            "digest": entry.digest,
            "freshness": freshness.get(key, "unknown"),
            "depth": depths.get(key, 1),
            "facts": dict(facts),
        }
        if entry.truncated:
            item["truncated"] = True
        by_key[key] = item
        out.entries.append(item)

    for section in bundle.sections:
        if section.section_id == "self":
            # The root's own facts are already the screen's `screen_data`
            # (`selected_*`); only its identity/freshness is added here.
            for entry in section.facts:
                add(entry, {})
        elif section.section_id == "objective":
            for entry in section.facts:
                add(entry, {"objective": entry.facts} if entry.facts else {})
        else:
            for entry in section.facts:
                add(entry, entry.facts)
        cov = _coverage_for_section(section)
        if cov is not None:
            out.coverage.append(cov)
    return out


def gather_related_context(
    system_id: int,
    thread_row: Optional[Dict[str, Any]],
    thread_target_state: Optional[str],
    *,
    trace: Optional[Any] = None,
) -> RelatedContext:
    """First match:

    1. no thread                              -> nothing (no target to relate)
    2. target stale / unresolvable            -> omitted ``stale_target``
    3. kind not registered                    -> omitted + coverage ``unsupported``
    4. bundle raised (any reason)             -> omitted + coverage ``unavailable``
    5. otherwise                              -> the projected bundle
    """
    result = RelatedContext()
    if thread_row is None:
        return result
    if thread_target_state not in ("current", "not_tracked"):
        result.omitted_sections.append(_omit("stale_target"))
        return result
    kind = thread_row["target_kind"]
    ref = thread_row["target_ref"]
    if discussion_adapters.get_adapter(kind) is None:
        result.omitted_sections.append(_omit("unsupported"))
        result.coverage.append(unsupported_entry(SECTION, "discussion_target_kind_unregistered"))
        return result
    try:
        with (trace.stage("context_bundle") if trace is not None else nullcontext()):
            bundle = discussion_context_bundle.build_context_bundle(
                system_id, kind, ref, thread_id=int(thread_row["id"]),
                budget=ASK_BUNDLE_BUDGET, mint_cursor=False,
            )
        return project_bundle(bundle)
    except ContextBundleError as exc:
        reason = exc.code
    except Exception:  # noqa: BLE001 - an ask never fails on related context
        logger.warning("assistant related-context bundle failed", exc_info=True)
        reason = "context_bundle_error"
    result.omitted_sections.append(_omit("unavailable"))
    result.coverage.append(unavailable_entry(SECTION, reason))
    return result
