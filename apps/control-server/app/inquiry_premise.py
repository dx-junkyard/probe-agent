"""Deterministic Inquiry premise facts (Issue #308 / sub-issue #320).

An Inquiry (Issue #285) is a side-conversation about one confirmation item.
Until now the row recorded only ``origin_kind``/``origin_id``, so nothing
said WHICH Repository Snapshot, Understanding Revision, or review-item
content the developer's answer was reasoned against. Once the Alignment
build regenerates that review item, the resolved Inquiry silently keeps
looking current even though its premise is gone.

This module owns the deterministic, DB-free part of that premise: the exact
digests captured when the Inquiry is created, and (Issue #323) the finite
comparison of those digests against the current build. Everything here is a
pure structural digest or a first-match rule over a small explicit finite
set -- never similarity, never a reasoning decision (Principle 6). The
reading of rows feeding these functions stays in the routes/DB layer, the
same split ``app/alignment.py`` uses for ``compute_content_hash``.

probe-agent:
  role: deterministic premise digests + finite premise-change evaluation for
    the Inquiry lifecycle
  capability: interactive-system-understanding
  element_type: logic
  operation_kind: pure
  state_effects: []
  probe_value: Verify a premise evaluation only ever returns a member of the
    finite result set, never guesses a successor when the review subject is
    ambiguous or absent, and produces identical output for identical input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from .alignment import compute_intent_item_digest

# Version of the premise-tracking contract itself: which fields are captured
# and how they are digested. Stored on every Inquiry that captures a bundle
# so a later contract change can never be mistaken for a content change
# (Principle 7 -- the audit row says which rules produced it).
PREMISE_TRACKING_VERSION = "inquiry-premise-v1"

# Finite tracking states, derived (never stored) from the captured bundle:
#   not_applicable: the Inquiry's origin is not a review item -- v1 tracks
#                   origin_kind='review_item' only (#320 non-goal).
#   untrackable:    a review-item Inquiry whose premise cannot be compared
#                   automatically: pre-migration legacy row, an origin item
#                   whose content_hash was NULL, or an origin with no stable
#                   review subject anchor (#321).
#   tracked:        a full, comparable premise bundle.
PREMISE_TRACKING_STATES = ("not_applicable", "untrackable", "tracked")

# Finite premise-evaluation results (Issue #323). ``untrackable`` is not in
# this set: an untrackable Inquiry is never evaluated at all, so it keeps a
# NULL evaluation and is displayed from PREMISE_TRACKING_STATES instead.
PREMISE_EVALUATIONS = ("unchanged", "changed", "removed", "ambiguous")

# Finite reason codes written to interview_inquiry_transition when a premise
# evaluation supersedes an Inquiry (actor='system').
PREMISE_REASON_CODES = (
    "review_item_content_changed",
    "linked_intent_changed",
    "capability_scope_changed",
    "origin_removed",
    "successor_ambiguous",
)


def _canonical_digest(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_capability_scope_digest(
    *,
    dependencies: Sequence[Mapping[str, object]],
) -> str:
    """sha256 over one review item's confirmed Capability scope (Issue #312).

    ``dependencies`` are the item's ``alignment_item_capability_dependency``
    rows (``target_kind``/``entity_id``/``relation_id``/``captured_digest``).
    A digest is always produced -- an item with no confirmed scope digests
    the explicitly empty scope rather than returning ``None``, so comparing
    two premises is always total (no "unknown" third state to interpret).

    The owning ``alignment_item_capability_scope.confirmation_id`` is
    deliberately NOT an input. It identifies WHICH confirmed composition the
    row was written against, and Alignment requires a fresh confirmation
    whenever a new Understanding revision is built -- so digesting it would
    expire every tracked Inquiry on the next rebuild, including ones whose
    own dependencies never moved. Issue #323 expires a premise on MEANING,
    never on a version identifier, and Issue #312 already settled the same
    question the same way (``_capability_scope_changed`` treats a differing
    confirmation_id as a change only when the item's own entity/relation ids
    are in the changed set). ``captured_digest`` is the entity's/relation's
    ``semantic_digest``, and the ids are stable across confirmations, so the
    (id, digest) pairs below ARE the meaning of the scope.
    """
    entities = sorted(
        (int(d["entity_id"]), str(d["captured_digest"]))
        for d in dependencies
        if d["target_kind"] == "entity" and d["entity_id"] is not None
    )
    relations = sorted(
        (int(d["relation_id"]), str(d["captured_digest"]))
        for d in dependencies
        if d["target_kind"] == "relation" and d["relation_id"] is not None
    )
    return _canonical_digest(
        {
            "entities": [list(e) for e in entities],
            "relations": [list(r) for r in relations],
        }
    )


def capability_scope_digest_for_item(conn, item_id: int) -> str:
    """``compute_capability_scope_digest`` for a persisted alignment item.

    Reads only the Issue #312 dependency sidecar table. A legacy item with
    no dependency rows digests the empty scope, which is a real, comparable
    fact ("this item had no confirmed Capability scope") rather than an
    inferred one.
    """
    dependencies = conn.execute(
        """SELECT target_kind, entity_id, relation_id, captured_digest
           FROM alignment_item_capability_dependency
           WHERE alignment_item_id = ?
           ORDER BY target_kind, entity_id, relation_id""",
        (item_id,),
    ).fetchall()
    return compute_capability_scope_digest(
        dependencies=[
            {
                "target_kind": row["target_kind"],
                "entity_id": row["entity_id"],
                "relation_id": row["relation_id"],
                "captured_digest": row["captured_digest"],
            }
            for row in dependencies
        ],
    )


def premise_tracking_state(
    *,
    origin_kind: str,
    tracking_version: Optional[str],
    content_hash: Optional[str],
    review_subject_id: Optional[str],
) -> str:
    """Finite tracking state for one Inquiry row (never stored, always derived).

    Derived rather than persisted so the value can never drift out of sync
    with the bundle columns it describes.
    """
    if origin_kind != "review_item":
        return "not_applicable"
    if not tracking_version or not content_hash or not review_subject_id:
        return "untrackable"
    return "tracked"


@dataclass(frozen=True)
class SuccessorFacts:
    """The current-build facts of one candidate successor review item."""

    item_id: int
    base_content_hash: Optional[str]
    capability_digest: str
    intent_digest: Optional[str]


@dataclass(frozen=True)
class PremiseEvaluation:
    """One Inquiry's finite premise verdict (Issue #323)."""

    result: str
    reason_code: Optional[str]
    successor_item_id: Optional[int]


def evaluate_premise(
    *,
    premise_content_hash: str,
    premise_capability_digest: Optional[str],
    premise_intent_digest: Optional[str],
    successors: List[SuccessorFacts],
) -> PremiseEvaluation:
    """Compare a captured premise against the current build's successors.

    A first-match rule over an explicit finite result set (Principle 6). The
    caller resolves ``successors`` structurally, by the review subject id
    captured at Inquiry creation (Issue #321) -- never by claim similarity --
    so this function only has to decide between "no successor", "more than
    one successor", and an exact digest comparison against the single one.

    Snapshot/revision IDs are deliberately NOT inputs: re-pinning the same
    source text to a newer snapshot must not expire a conversation (Issue
    #323). Only meaning-bearing digests decide.
    """
    if not successors:
        return PremiseEvaluation("removed", "origin_removed", None)
    if len(successors) > 1:
        return PremiseEvaluation("ambiguous", "successor_ambiguous", None)
    successor = successors[0]
    if (
        successor.base_content_hash is not None
        and successor.base_content_hash == premise_content_hash
        and successor.capability_digest == premise_capability_digest
        and successor.intent_digest == premise_intent_digest
    ):
        return PremiseEvaluation("unchanged", None, successor.item_id)
    # Most specific cause first. The linked Intent digest and the confirmed
    # Capability scope are both inputs to (or siblings of) the content hash,
    # so reporting them ahead of the generic content change names the root
    # cause instead of its symptom.
    if successor.intent_digest != premise_intent_digest:
        reason = "linked_intent_changed"
    elif successor.capability_digest != premise_capability_digest:
        reason = "capability_scope_changed"
    else:
        reason = "review_item_content_changed"
    return PremiseEvaluation("changed", reason, successor.item_id)


# --- Review subject identity (Issue #321) -------------------------------------
#
# ``alignment_item.content_hash`` answers "is this byte-identical?"; it can
# never answer "is this the same discussion point after an edit?". A rebuild
# DELETEs undecided open rows and INSERTs fresh physical rows, so without a
# separate identity there is nothing to carry a superseded Inquiry's
# recheck target forward to.
#
# The anchor is built ONLY from structural identity that exists independently
# of the reasoning model's wording:
#   - ``intent_field``: the finite Intent Brief field the item contrasts
#     against (Issue #284). One current row exists per field per session, so
#     the field name is itself the stable Intent identity.
#   - the confirmed Capability entity/relation ids the item depends on
#     (Issue #312's stable, rename-surviving ids).
# ``system_id``/``session_id`` are part of the digest so an id from one
# System can never collide with another's (Principle: System isolation).
#
# Claim text, evidence citations, summaries, and confidence are deliberately
# excluded: they are the reasoning model's wording, and joining subjects on
# them would be exactly the similarity matching Issue #321 forbids. The
# consequence is accepted and explicit: when two items in one build share an
# anchor, neither is bound to a predecessor -- they are reported
# ``ambiguous`` and a human decides (never a guessed successor).


def compute_review_subject_id(
    *,
    system_id: int,
    session_id: int,
    intent_field: Optional[str],
    capability_entity_ids: Sequence[int],
    capability_relation_ids: Sequence[int],
) -> Optional[str]:
    """Stable, System/session-scoped identity for one review discussion point.

    ``None`` when the item has no stable anchor at all (no Intent field and
    no confirmed Capability dependency) -- such an item is explicitly
    ``untrackable`` rather than being given a fabricated identity.
    """
    entity_ids = sorted({int(i) for i in capability_entity_ids})
    relation_ids = sorted({int(i) for i in capability_relation_ids})
    if not intent_field and not entity_ids and not relation_ids:
        return None
    return _canonical_digest(
        {
            "system_id": int(system_id),
            "session_id": int(session_id),
            "intent_field": intent_field,
            "capability_entity_ids": entity_ids,
            "capability_relation_ids": relation_ids,
        }
    )


# Finite per-item lineage states (Issue #321), recorded on every freshly
# built row:
#   new:         this subject has no earlier generation in the session.
#   unchanged:   exactly one predecessor, byte-identical base content hash.
#   changed:     exactly one predecessor, different content.
#   ambiguous:   this build and/or the predecessor generation holds more
#                than one row for the subject (a split or a merge). No
#                predecessor is bound.
#   untrackable: the item has no stable anchor, so it has no subject at all.
# ``removed`` is intentionally absent: it describes a subject that has no
# row in the current build, which is a premise-evaluation result (#323),
# not a property of a row that exists.
SUBJECT_STATES = ("new", "unchanged", "changed", "ambiguous", "untrackable")


def classify_subject_lineage(
    *,
    review_subject_id: Optional[str],
    same_subject_in_build: int,
    predecessors: Sequence[Tuple[int, Optional[str]]],
    base_content_hash: Optional[str],
) -> Tuple[str, Optional[int]]:
    """Finite ``(subject_state, replaces_item_id)`` for one freshly built row.

    ``predecessors`` are ``(item_id, base_content_hash)`` pairs from the
    latest earlier generation that carried this subject.
    ``same_subject_in_build`` counts rows sharing the subject in THIS build.
    """
    if review_subject_id is None:
        return "untrackable", None
    if same_subject_in_build > 1 or len(predecessors) > 1:
        return "ambiguous", None
    if not predecessors:
        return "new", None
    predecessor_id, predecessor_hash = predecessors[0]
    if (
        base_content_hash is not None
        and predecessor_hash is not None
        and base_content_hash == predecessor_hash
    ):
        return "unchanged", predecessor_id
    return "changed", predecessor_id


# --- Persistence-facing evaluation (Issue #323) -------------------------------
#
# The only functions in this module that touch the database. They are
# called from run_alignment_build's write transaction, so every transition
# they record is committed or rolled back with the build itself.


def _successor_facts(conn, *, system_id: int, item_row) -> SuccessorFacts:
    intent_digest: Optional[str] = None
    if item_row["intent_item_id"] is not None:
        intent = conn.execute(
            "SELECT field, value_text, status FROM interview_intent_item "
            "WHERE id = ? AND system_id = ?",
            (item_row["intent_item_id"], system_id),
        ).fetchone()
        if intent is not None:
            intent_digest = compute_intent_item_digest(
                field=intent["field"],
                value_text=intent["value_text"],
                status=intent["status"],
            )
    return SuccessorFacts(
        item_id=item_row["id"],
        base_content_hash=item_row["base_content_hash"],
        capability_digest=capability_scope_digest_for_item(conn, item_row["id"]),
        intent_digest=intent_digest,
    )


def evaluate_inquiry_premises(
    conn, *, system_id: int, session_id: int, built_at: float
) -> List[dict]:
    """Expire Inquiries whose premise this Alignment build invalidated.

    Called by ``run_alignment_build`` INSIDE its write transaction, after the
    fresh rows for this build exist, so the current review-item set is final
    and a rolled-back build also rolls back every transition it implied
    (Issue #323).

    Only ``origin_kind='review_item'`` Inquiries with a complete premise
    bundle are evaluated; legacy/NULL and anchor-less ones stay untouched and
    are reported ``untrackable`` by the API instead of being compared against
    a reconstructed premise. Terminal Inquiries (``cancelled`` /
    ``unresolved`` / already ``superseded``) are never revisited.

    Effects per finite verdict:
      unchanged -> record the verdict and the successor; no status change.
      changed   -> supersede; flag the single successor for manual recheck.
      removed   -> supersede; the discussion point no longer exists.
      ambiguous -> supersede; no successor is bound (a split/merge is for a
                   human to resolve, never guessed).

    Idempotent: ``superseded`` is terminal, so re-running the evaluation (or
    the whole build) can never write a second transition for the same
    Inquiry. Returns one summary dict per evaluated Inquiry; the persisted
    ``premise_evaluation``/``interview_inquiry_transition`` rows are the
    authoritative audit record (the Alignment build response is unchanged by
    Issue #323), so the return value is for callers and tests only.
    """
    candidates = conn.execute(
        """SELECT * FROM interview_inquiry
           WHERE session_id = ? AND system_id = ? AND origin_kind = 'review_item'
             AND status IN ('open', 'held', 'resolved')
             AND premise_tracking_version IS NOT NULL
             AND premise_content_hash IS NOT NULL
             AND premise_review_subject_id IS NOT NULL
           ORDER BY id""",
        (session_id, system_id),
    ).fetchall()
    results: List[dict] = []
    for inquiry in candidates:
        successor_rows = conn.execute(
            """SELECT id, base_content_hash, intent_item_id FROM alignment_item
               WHERE session_id = ? AND system_id = ? AND review_subject_id = ?
                 AND created_at = ? AND superseded = 0
               ORDER BY id""",
            (session_id, system_id, inquiry["premise_review_subject_id"], built_at),
        ).fetchall()
        evaluation = evaluate_premise(
            premise_content_hash=inquiry["premise_content_hash"],
            premise_capability_digest=inquiry["premise_capability_digest"],
            premise_intent_digest=inquiry["premise_intent_digest"],
            successors=[
                _successor_facts(conn, system_id=system_id, item_row=row)
                for row in successor_rows
            ],
        )
        results.append(
            {
                "inquiry_id": inquiry["id"],
                "result": evaluation.result,
                "reason_code": evaluation.reason_code,
                "successor_item_id": evaluation.successor_item_id,
            }
        )
        if evaluation.result == "unchanged":
            conn.execute(
                """UPDATE interview_inquiry
                   SET premise_evaluation = ?, premise_successor_item_id = ?
                   WHERE id = ?""",
                ("unchanged", evaluation.successor_item_id, inquiry["id"]),
            )
            continue
        _supersede_inquiry(
            conn,
            inquiry=inquiry,
            evaluation=evaluation,
            system_id=system_id,
            session_id=session_id,
            now=built_at,
        )
    return results


def _supersede_inquiry(
    conn, *, inquiry, evaluation: PremiseEvaluation, system_id: int, session_id: int, now: float,
) -> None:
    """``resolved|open|held -> superseded`` as a system transition.

    ``closed_at`` is never written here at all. It means "the developer
    closed this conversation" (resolve / unresolved / cancel), so a resolved
    Inquiry keeps the exact moment it was resolved, and an open/held one that
    the system expires keeps NULL rather than gaining a resolved-looking
    timestamp it never earned. ``superseded_at`` is the separate, authoritative
    record of when the premise expired.

    An open/held Inquiry is expired too: continuing to answer against a
    premise that no longer exists is exactly what Issue #323 forbids, so it
    fails closed here rather than staying answerable (``superseded`` has no
    outgoing transition, so /message, /resolve and /resume all 409).
    """
    from_status = inquiry["status"]
    conn.execute(
        """UPDATE interview_inquiry
           SET status = 'superseded', status_reason = ?, premise_evaluation = ?,
               premise_successor_item_id = ?, superseded_at = ?, updated_at = ?
           WHERE id = ?""",
        (
            evaluation.reason_code,
            evaluation.result,
            evaluation.successor_item_id,
            now,
            now,
            inquiry["id"],
        ),
    )
    conn.execute(
        """INSERT INTO interview_inquiry_transition
            (inquiry_id, system_id, from_status, to_status, actor, reason, created_at)
           VALUES (?, ?, ?, 'superseded', 'system', ?, ?)""",
        (inquiry["id"], system_id, from_status, evaluation.reason_code, now),
    )
    # The origin row keeps its own decision, but must not stay locked behind
    # an Inquiry that can no longer be continued. Mirrors the Issue #287
    # release rule (never 'answered' -- the developer still has to decide).
    other_active = conn.execute(
        """SELECT id FROM interview_inquiry
           WHERE session_id = ? AND system_id = ? AND origin_kind = 'review_item'
             AND origin_id = ? AND id != ? AND status IN ('open', 'held')""",
        (session_id, system_id, inquiry["origin_id"], inquiry["id"]),
    ).fetchone()
    if other_active is None:
        conn.execute(
            """UPDATE alignment_item SET status = 'open', updated_at = ?
               WHERE id = ? AND session_id = ? AND system_id = ? AND status = 'inquiry'""",
            (now, inquiry["origin_id"], session_id, system_id),
        )
    if evaluation.successor_item_id is None:
        return
    # The old physical row is history once a unique successor exists; the
    # successor is what the developer must now re-check. Neither reopens nor
    # answers anything (Principle 2) -- manual_recheck_required only makes
    # the successor visible in the normal Review Queue.
    conn.execute(
        """UPDATE alignment_item SET superseded = 1, updated_at = ?
           WHERE id = ? AND session_id = ? AND system_id = ? AND superseded = 0""",
        (now, inquiry["origin_id"], session_id, system_id),
    )
    conn.execute(
        """UPDATE alignment_item SET manual_recheck_required = 1, updated_at = ?
           WHERE id = ? AND session_id = ? AND system_id = ?""",
        (now, evaluation.successor_item_id, session_id, system_id),
    )
