"""Deterministic explanation-drift detection (Issue #57).

Compares the source hashes captured when a capability hierarchy was generated
(#56) against the hashes in a newer pinned snapshot (#55). A changed hash means
"review needed", never "the explanation is wrong": this module produces only a
deterministic review trigger, with no semantic guessing, embeddings, or
heuristic similarity.

Anchors are resolved across snapshots by stable identifiers (path + qualified
symbol name); source line ranges are weak evidence only and are not used for
matching. Statuses:

- ``fresh``           every captured hash still matches the newer snapshot
- ``stale``           one or more captured hashes changed
- ``partially_stale`` (aggregate only) some dependencies changed, not all
- ``missing_source``  the file or symbol the explanation depended on is gone
- ``unknown``         the node carries no comparable hash (e.g. a draft-linked
                      purpose node)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

FRESH = "fresh"
PARTIALLY_STALE = "partially_stale"
STALE = "stale"
MISSING_SOURCE = "missing_source"
UNKNOWN = "unknown"

# Hash-dependency kinds tracked per anchor.
FILE = "file"
SYMBOL = "symbol"
EXPLANATION = "explanation"


@dataclass
class NodeAnchor:
    """The captured provenance of a single hierarchy node (from the base run)."""

    node_id: int
    node_type: str
    name: str
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    entrypoint_id: Optional[int] = None
    file_content_hash: Optional[str] = None
    symbol_source_hash: Optional[str] = None
    explanation_hash: Optional[str] = None


@dataclass
class SnapshotFacts:
    """Deterministic facts read from the newer (target) pinned snapshot."""

    file_hash_by_path: Dict[str, str] = field(default_factory=dict)
    # (path, qualified_name) -> (symbol_source_hash, explanation_hash)
    symbol_by_key: Dict[Tuple[str, str], Tuple[Optional[str], Optional[str]]] = (
        field(default_factory=dict)
    )


@dataclass
class AnchorDrift:
    node_id: int
    node_type: str
    name: str
    path: Optional[str]
    qualified_name: Optional[str]
    entrypoint_id: Optional[int]
    status: str
    changed_hashes: List[str] = field(default_factory=list)
    captured_file_content_hash: Optional[str] = None
    captured_symbol_source_hash: Optional[str] = None
    captured_explanation_hash: Optional[str] = None
    current_file_content_hash: Optional[str] = None
    current_symbol_source_hash: Optional[str] = None
    current_explanation_hash: Optional[str] = None


@dataclass
class DriftCounts:
    total: int = 0
    fresh: int = 0
    stale: int = 0
    missing: int = 0
    unknown: int = 0
    symbol_deps_total: int = 0
    symbol_deps_changed: int = 0
    file_deps_total: int = 0
    file_deps_changed: int = 0
    explanation_blocks_total: int = 0
    explanation_blocks_changed: int = 0
    missing_anchors: int = 0
    mismatch_ratio: float = 0.0


def compute_anchor_drift(anchor: NodeAnchor, facts: SnapshotFacts) -> AnchorDrift:
    """Compare one node's captured hashes against the newer snapshot."""
    result = AnchorDrift(
        node_id=anchor.node_id,
        node_type=anchor.node_type,
        name=anchor.name,
        path=anchor.path,
        qualified_name=anchor.qualified_name,
        entrypoint_id=anchor.entrypoint_id,
        status=UNKNOWN,
        captured_file_content_hash=anchor.file_content_hash,
        captured_symbol_source_hash=anchor.symbol_source_hash,
        captured_explanation_hash=anchor.explanation_hash,
    )

    has_hash = any([
        anchor.file_content_hash,
        anchor.symbol_source_hash,
        anchor.explanation_hash,
    ])
    if not has_hash or anchor.path is None:
        result.status = UNKNOWN
        return result

    changed: List[str] = []
    file_present = anchor.path in facts.file_hash_by_path
    sym = (
        facts.symbol_by_key.get((anchor.path, anchor.qualified_name))
        if anchor.qualified_name is not None
        else None
    )
    symbol_missing = anchor.qualified_name is not None and sym is None

    if not file_present:
        # The whole file the explanation depended on is gone.
        if anchor.file_content_hash:
            changed.append(FILE)
        if anchor.symbol_source_hash:
            changed.append(SYMBOL)
        if anchor.explanation_hash:
            changed.append(EXPLANATION)
        result.changed_hashes = changed
        result.status = MISSING_SOURCE
        return result

    current_file = facts.file_hash_by_path[anchor.path]
    result.current_file_content_hash = current_file

    if symbol_missing:
        # File still exists but the symbol was deleted or renamed.
        if anchor.file_content_hash and anchor.file_content_hash != current_file:
            changed.append(FILE)
        if anchor.symbol_source_hash:
            changed.append(SYMBOL)
        if anchor.explanation_hash:
            changed.append(EXPLANATION)
        result.changed_hashes = changed
        result.status = MISSING_SOURCE
        return result

    current_symbol = sym[0] if sym else None
    current_explanation = sym[1] if sym else None
    result.current_symbol_source_hash = current_symbol
    result.current_explanation_hash = current_explanation

    if anchor.file_content_hash and anchor.file_content_hash != current_file:
        changed.append(FILE)
    if anchor.symbol_source_hash and (
        current_symbol is None or anchor.symbol_source_hash != current_symbol
    ):
        changed.append(SYMBOL)
    if anchor.explanation_hash and (
        current_explanation is None or anchor.explanation_hash != current_explanation
    ):
        changed.append(EXPLANATION)

    result.changed_hashes = changed
    result.status = STALE if changed else FRESH
    return result


def aggregate_drift(drifts: List[AnchorDrift]) -> Tuple[str, DriftCounts]:
    """Roll anchor drifts up into a status + counts for a capability/system."""
    counts = DriftCounts(total=len(drifts))
    changed_file_paths: set = set()
    file_paths: set = set()

    for d in drifts:
        if d.status == FRESH:
            counts.fresh += 1
        elif d.status == STALE:
            counts.stale += 1
        elif d.status == MISSING_SOURCE:
            counts.missing += 1
            counts.missing_anchors += 1
        else:
            counts.unknown += 1

        if d.captured_symbol_source_hash is not None:
            counts.symbol_deps_total += 1
            if SYMBOL in d.changed_hashes:
                counts.symbol_deps_changed += 1
        if d.captured_explanation_hash is not None:
            counts.explanation_blocks_total += 1
            if EXPLANATION in d.changed_hashes:
                counts.explanation_blocks_changed += 1
        if d.captured_file_content_hash is not None and d.path is not None:
            file_paths.add(d.path)
            if FILE in d.changed_hashes:
                changed_file_paths.add(d.path)

    # File dependencies are counted over distinct paths.
    counts.file_deps_total = len(file_paths)
    counts.file_deps_changed = len(changed_file_paths)

    comparable = counts.fresh + counts.stale + counts.missing
    changed = counts.stale + counts.missing
    counts.mismatch_ratio = round(changed / comparable, 4) if comparable else 0.0

    if comparable == 0:
        status = UNKNOWN
    elif changed == 0:
        status = FRESH
    elif counts.missing == comparable:
        status = MISSING_SOURCE
    elif changed == comparable:
        status = STALE
    else:
        status = PARTIALLY_STALE
    return status, counts


REVIEW_NOTE = (
    "Source has changed since this explanation was generated. Hash drift is a "
    "review trigger, not a correctness verdict: review the affected capability "
    "and API explanations and refresh them if needed."
)


def is_review_recommended(status: str) -> bool:
    return status in (PARTIALLY_STALE, STALE, MISSING_SOURCE)
