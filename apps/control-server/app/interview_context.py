"""Snapshot-grounded Interview Context Pack Builder (Issue #68).

Deterministic, no-LLM context assembly for the system-understanding
interview (#66). Given a system + pinned snapshot, produces a bounded
interview context pack containing:

- Indexed symbols from the #24 symbol index.
- Discovered entrypoints from #48/#51 entrypoint discovery.
- Already-extracted ``probe-agent:`` metadata from #54 symbol_source_metadata.
- Classification status from #56 capability hierarchy (if present).

Every item carries a snapshot-relative evidence location (path + symbol +
line span). Unclassified / empty regions are explicitly flagged so the
interview can prioritize them. Selection and truncation are deterministic
and reproducible for the same snapshot + budget.

This module never calls an LLM, never reads source outside the pinned
snapshot, and adds no new DB tables.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

from .models import (
    InterviewContextPack,
    InterviewEntrypointItem,
    InterviewEvidenceLocation,
    InterviewSymbolItem,
)

DEFAULT_BUDGET_CHARS = int(os.getenv("INTERVIEW_CONTEXT_MAX_CHARS", "60000"))

MAX_SYMBOLS = 500
MAX_ENTRYPOINTS = 200


def _evidence(snapshot_id: int, path: str, qname: str, start: int, end: int) -> InterviewEvidenceLocation:
    return InterviewEvidenceLocation(
        snapshot_id=snapshot_id,
        path=path,
        qualified_name=qname,
        start_line=start,
        end_line=end,
    )


def _classified_symbol_ids(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> Set[int]:
    """Return symbol IDs that are considered 'classified'.

    A symbol is classified if it has source-authored metadata (#54) OR
    appears in the capability hierarchy (#56) with a non-null classification
    of 'classified'.
    """
    ids: Set[int] = set()

    metadata_rows = conn.execute(
        "SELECT DISTINCT symbol_id FROM symbol_source_metadata WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchall()
    for row in metadata_rows:
        ids.add(row["symbol_id"])

    hierarchy_rows = conn.execute(
        """SELECT DISTINCT symbol_id FROM capability_hierarchy_nodes
           WHERE system_id = ? AND snapshot_id = ? AND symbol_id IS NOT NULL
             AND classification = 'classified'""",
        (system_id, snapshot_id),
    ).fetchall()
    for row in hierarchy_rows:
        ids.add(row["symbol_id"])

    return ids


def _metadata_by_symbol_id(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> Dict[int, sqlite3.Row]:
    rows = conn.execute(
        """SELECT * FROM symbol_source_metadata
           WHERE system_id = ? AND snapshot_id = ?
           ORDER BY symbol_id""",
        (system_id, snapshot_id),
    ).fetchall()
    result: Dict[int, sqlite3.Row] = {}
    for row in rows:
        result[row["symbol_id"]] = row
    return result


def _entrypoint_handler_symbol_ids(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> Set[int]:
    rows = conn.execute(
        """SELECT DISTINCT handler_symbol_id FROM code_entrypoints
           WHERE system_id = ? AND snapshot_id = ? AND handler_symbol_id IS NOT NULL""",
        (system_id, snapshot_id),
    ).fetchall()
    return {row["handler_symbol_id"] for row in rows}


def build_interview_context(
    conn: sqlite3.Connection,
    system_id: int,
    snapshot_id: int,
    budget_chars: Optional[int] = None,
) -> InterviewContextPack:
    """Build a deterministic interview context pack.

    Selection order:
    1. Unclassified symbols first (they most need authoring), then classified.
    2. Within each group, order by (path, start_line) for reproducibility.
    3. Entrypoints follow the same unclassified-first, then (path, line) order.
    4. Truncate tail items to fit within the LLM context budget.
    """
    budget = budget_chars if budget_chars is not None else DEFAULT_BUDGET_CHARS
    omission_notes: List[str] = []

    classified_ids = _classified_symbol_ids(conn, system_id, snapshot_id)
    metadata_map = _metadata_by_symbol_id(conn, system_id, snapshot_id)
    entrypoint_handler_ids = _entrypoint_handler_symbol_ids(conn, system_id, snapshot_id)

    # --- Symbols ---
    all_symbols = conn.execute(
        """SELECT * FROM code_symbols
           WHERE system_id = ? AND snapshot_id = ?
           ORDER BY path, start_line""",
        (system_id, snapshot_id),
    ).fetchall()
    total_symbols = len(all_symbols)

    unclassified_symbols: List[sqlite3.Row] = []
    classified_symbols: List[sqlite3.Row] = []
    for sym in all_symbols:
        if sym["id"] in classified_ids:
            classified_symbols.append(sym)
        else:
            unclassified_symbols.append(sym)

    ordered_symbols = unclassified_symbols + classified_symbols
    if len(ordered_symbols) > MAX_SYMBOLS:
        omission_notes.append(
            f"symbols: {len(ordered_symbols) - MAX_SYMBOLS} symbol(s) omitted "
            "due to the per-category item limit"
        )
        ordered_symbols = ordered_symbols[:MAX_SYMBOLS]

    symbol_items: List[InterviewSymbolItem] = []
    for sym in ordered_symbols:
        sym_id = sym["id"]
        is_classified = sym_id in classified_ids
        meta = metadata_map.get(sym_id)
        symbol_items.append(InterviewSymbolItem(
            symbol_id=sym_id,
            path=sym["path"],
            qualified_name=sym["qualified_name"],
            kind=sym["kind"],
            start_line=sym["start_line"],
            end_line=sym["end_line"],
            classification="classified" if is_classified else "unclassified",
            has_metadata=meta is not None,
            element_type=meta["element_type"] if meta else None,
            role=meta["role"] if meta else None,
            capability=meta["capability"] if meta else None,
            operation_kind=meta["operation_kind"] if meta else None,
            probe_value=meta["probe_value"] if meta else None,
            evidence=_evidence(
                snapshot_id, sym["path"], sym["qualified_name"],
                sym["start_line"], sym["end_line"],
            ),
        ))

    # --- Entrypoints ---
    all_entrypoints = conn.execute(
        """SELECT * FROM code_entrypoints
           WHERE system_id = ? AND snapshot_id = ?
           ORDER BY handler_path, line_start""",
        (system_id, snapshot_id),
    ).fetchall()
    total_entrypoints = len(all_entrypoints)

    unclassified_eps: List[sqlite3.Row] = []
    classified_eps: List[sqlite3.Row] = []
    for ep in all_entrypoints:
        handler_sym_id = ep["handler_symbol_id"]
        if handler_sym_id is not None and handler_sym_id in classified_ids:
            classified_eps.append(ep)
        else:
            unclassified_eps.append(ep)

    ordered_eps = unclassified_eps + classified_eps
    if len(ordered_eps) > MAX_ENTRYPOINTS:
        omission_notes.append(
            f"entrypoints: {len(ordered_eps) - MAX_ENTRYPOINTS} entrypoint(s) omitted "
            "due to the per-category item limit"
        )
        ordered_eps = ordered_eps[:MAX_ENTRYPOINTS]

    entrypoint_items: List[InterviewEntrypointItem] = []
    for ep in ordered_eps:
        handler_sym_id = ep["handler_symbol_id"]
        is_classified = handler_sym_id is not None and handler_sym_id in classified_ids
        has_meta = handler_sym_id is not None and handler_sym_id in metadata_map
        entrypoint_items.append(InterviewEntrypointItem(
            entrypoint_id=ep["id"],
            entrypoint_type=ep["entrypoint_type"],
            category=ep["category"],
            label=ep["label"],
            handler_path=ep["handler_path"],
            handler_qualified_name=ep["handler_qualified_name"],
            line_start=ep["line_start"],
            line_end=ep["line_end"],
            classification="classified" if is_classified else "unclassified",
            has_metadata=has_meta,
            evidence=_evidence(
                snapshot_id, ep["handler_path"], ep["handler_qualified_name"],
                ep["line_start"], ep["line_end"],
            ),
        ))

    classified_count = sum(1 for s in symbol_items if s.classification == "classified")
    unclassified_count = sum(1 for s in symbol_items if s.classification == "unclassified")

    pack = InterviewContextPack(
        system_id=system_id,
        snapshot_id=snapshot_id,
        total_symbols=total_symbols,
        total_entrypoints=total_entrypoints,
        classified_count=classified_count,
        unclassified_count=unclassified_count,
        budget_max_chars=budget,
        budget_used_chars=0,
        truncated=False,
        symbols=symbol_items,
        entrypoints=entrypoint_items,
        omission_notes=omission_notes,
    )

    pack = _apply_budget(pack, budget)
    return pack


def _apply_budget(pack: InterviewContextPack, budget: int) -> InterviewContextPack:
    """Deterministically truncate tail items until the serialized pack fits.

    Truncation removes from the *tail* of each list (classified items first,
    since unclassified items are at the head and are the priority). This
    mirrors the Decision Workspace pattern of removing lower-priority items.
    """
    truncated = False

    # First pass: remove classified symbols from the tail (they have metadata
    # already). Then remove classified entrypoints. Then unclassified items
    # from both lists as a last resort.
    while len(pack.model_dump_json()) > budget:
        removed = False
        # Try removing the last entrypoint (classified ones are at the tail).
        if pack.entrypoints:
            pack.entrypoints.pop()
            removed = True
            truncated = True
            continue
        # Try removing the last symbol (classified ones are at the tail).
        if pack.symbols:
            pack.symbols.pop()
            removed = True
            truncated = True
            continue
        if not removed:
            break

    if truncated:
        classified_count = sum(1 for s in pack.symbols if s.classification == "classified")
        unclassified_count = sum(1 for s in pack.symbols if s.classification == "unclassified")
        pack.classified_count = classified_count
        pack.unclassified_count = unclassified_count
        pack.truncated = True
        pack.omission_notes.append(
            "items truncated to fit within the LLM context budget"
        )

    pack.budget_used_chars = len(pack.model_dump_json())
    return pack
