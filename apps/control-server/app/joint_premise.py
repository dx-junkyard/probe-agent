"""Joint Understanding premise, provenance, and decision audit (Issue #337).

Epic #328's Joint Understanding sessions pinned exactly one premise fact: the
interview session's ``snapshot_id`` at creation time. Everything else that can
change the ground a conversation stands on -- the developer correcting the
Intent the question was about, the Alignment build regenerating the review
item, the Inquiry the session hangs off being superseded, the pinned snapshot
row being deleted outright -- was invisible. Worse, the two paths that had no
premise at all (``premise_snapshot_id IS NULL``, and an interview session that
no longer resolves) both reported ``fresh``: a missing premise was read as a
satisfied one, which is exactly backwards for a gate.

This module is the deterministic half of the fix. It reuses Issue #308's
premise bundle -- the same column names, the same digest helpers
(``inquiry_premise``), the same "meaning-bearing digests decide, version
identifiers do not" rule -- and generalizes it from
``origin_kind='review_item'`` to all four Joint Understanding origins.

Two axes that Epic #328 requires be kept apart, and that this module
separates mechanically:

- **premise**: which facts the conversation was reasoned against, captured
  immutably at creation and compared structurally afterwards. Finite verdicts
  ``current`` / ``stale`` / ``missing`` / ``invalid`` -- never a boolean, and
  never "fresh because nothing was pinned".
- **provenance**: WHO produced a row. ``producer_kind`` is the code path that
  wrote it (an internal producer, or the authenticated developer API);
  ``actor_kind`` is whether an authenticated human stands behind it. Neither
  is ever taken from a request body's free text, so a caller cannot post a
  finding that claims to be the developer's own words.

Everything here is a pure structural digest or a first-match rule over a
small explicit finite set (Principle 6). No similarity, no reasoning call, no
heuristic recovery: a premise that cannot be compared is ``invalid`` and
blocks the outcomes that assert something, rather than being promoted to
``current`` for convenience.

probe-agent:
  role: deterministic premise capture/evaluation and producer/actor
    provenance rules for Joint Understanding sessions
  capability: interactive-system-understanding
  element_type: logic
  operation_kind: pure
  state_effects: []
  probe_value: Verify a session with no captured bundle evaluates 'invalid' rather than 'current', that a deleted pinned snapshot is 'missing', that an identical commit re-pinned under a new snapshot id stays 'current', and that a developer finding cannot be produced by a non-developer producer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .alignment import compute_intent_item_digest
from .inquiry_premise import (
    capability_scope_digest_for_item,
    compute_capability_scope_digest,
)

# Version of the Joint Understanding premise contract: which facts are
# captured and how they are digested. Stored on every session that captures a
# bundle, so a later contract change is never mistaken for a content change
# (Principle 7 -- the audit row says which rules produced it).
JOINT_PREMISE_VERSION = "joint-premise-v1"


class JointPremiseError(ValueError):
    """A premise/provenance contract violation. Routes translate to 422."""


# --- Finite premise verdicts ---------------------------------------------------
#
# Replaces the ("fresh", "stale") pair. The two added values are the two cases
# the old pair had to lie about:
#   missing -- a premise fact WAS captured and no longer resolves (the pinned
#              snapshot row is gone, the origin item was deleted). Distinct
#              from stale: nothing changed, the ground disappeared.
#   invalid -- there is no comparable bundle at all (a pre-migration row, or a
#              session created without a pinned snapshot). Evaluating it would
#              mean inventing the premise, so it is refused instead.
PREMISE_STATES = ("current", "stale", "missing", "invalid")

# Verdicts that permit an outcome/reflux that asserts something forward-looking.
ASSERTABLE_PREMISE_STATES = ("current",)

# Legacy values readable from rows written before this contract existed. They
# are never produced again; they exist so a stored `outcome_premise_state`
# still round-trips through the API instead of failing validation.
LEGACY_PREMISE_STATES = ("fresh",)

# Finite reason codes, one per way a premise can fail. First-match order is
# the order of `evaluate_joint_premise`'s checks: the most fundamental cause
# is reported, never a symptom of it.
PREMISE_REASON_CODES = (
    # invalid
    "premise_not_captured",
    "premise_incomplete",
    # missing
    "pinned_snapshot_removed",
    "origin_removed",
    # stale
    "origin_superseded",
    "pinned_commit_changed",
    "origin_content_changed",
    "capability_scope_changed",
    "linked_intent_changed",
)


@dataclass(frozen=True)
class PremiseBundle:
    """The immutable premise facts captured when a session was created.

    Field names deliberately mirror ``interview_inquiry``'s Issue #308
    columns, because they are the same bundle: sharing the names is what lets
    an operator read one premise contract across both features instead of
    two similar-looking ones.

    ``commit_sha`` is the addition Joint Understanding needs. Issue #308
    compares review-item CONTENT, so it can ignore which snapshot the content
    was pinned to; a Joint Understanding investigation reads source code, so
    the commit it read IS part of its premise. Comparing the commit rather
    than the snapshot id is deliberate: re-pinning the identical commit under
    a new snapshot row changes nothing the investigation relied on
    (Issue #323's rule, applied to the snapshot axis).
    """

    tracking_version: Optional[str]
    snapshot_id: Optional[int]
    commit_sha: Optional[str]
    revision_id: Optional[int]
    content_hash: Optional[str]
    capability_digest: Optional[str]
    intent_digest: Optional[str]
    review_subject_id: Optional[str]

    @property
    def is_complete(self) -> bool:
        """Whether this bundle was captured completely enough to compare.

        The pinned COMMIT and the origin content hash are the minimum:
        without the commit there is nothing to say the investigation read the
        same code, and without the content hash nothing to say the question is
        still the same question. ``capability_digest`` is always produced (the
        empty scope digests to a real value), so its absence also means "not
        captured".

        ``snapshot_id`` is deliberately NOT required here even though it is
        always captured. Its foreign key is ``ON DELETE SET NULL``, so a
        deleted snapshot row nulls the column on a bundle that WAS complete --
        that is a premise which disappeared (``missing``), not one that was
        never recorded (``invalid``), and the two must not be conflated.
        """
        return bool(
            self.tracking_version
            and self.commit_sha
            and self.content_hash
            and self.capability_digest
        )


@dataclass(frozen=True)
class PremiseFacts:
    """The same facts as they stand NOW, resolved by the caller from the DB.

    ``current_origin_id`` is the origin row that is current TODAY. For the
    origins whose corrections are additive (``qa``, ``intent``: a correction
    inserts a new row and marks the old one superseded) it is the end of that
    chain, which is generally not the ``origin_id`` the session pinned. It is
    reported rather than silently substituted: the session keeps pointing at
    the row the conversation actually started from, and the consumers that
    must reach the live row (reflux) resolve it explicitly.
    """

    snapshot_exists: bool
    commit_sha: Optional[str]
    origin_exists: bool
    origin_superseded: bool
    current_origin_id: Optional[int]
    revision_id: Optional[int]
    content_hash: Optional[str]
    capability_digest: Optional[str]
    intent_digest: Optional[str]


@dataclass(frozen=True)
class PremiseVerdict:
    """One session's finite premise state plus the single reason for it."""

    state: str
    reason_code: Optional[str] = None

    @property
    def is_assertable(self) -> bool:
        return self.state in ASSERTABLE_PREMISE_STATES


def evaluate_joint_premise(
    *, bundle: PremiseBundle, facts: Optional[PremiseFacts]
) -> PremiseVerdict:
    """Compare a captured premise against the current facts (first-match).

    ``facts`` is ``None`` when the caller could not resolve the current state
    at all (the interview session itself is gone). That is ``missing``, never
    ``current``: the whole point of Issue #337's first completion gate is that
    a premise which cannot be found is not a premise which still holds.

    Deliberately NOT inputs to the verdict:

    - ``snapshot_id`` on its own. Two snapshot rows over the same commit are
      the same premise; the id differing is not a change the developer made.
    - ``revision_id``. Rebuilding the Understanding does not invalidate a
      conversation about code that did not move (Issue #323 settled this for
      Inquiries and the same reasoning applies here). It is captured for
      audit and for the hypothesis re-confirmation lineage, which is a
      different question from "may this session still assert something".
    """
    if not bundle.tracking_version:
        return PremiseVerdict("invalid", "premise_not_captured")
    if not bundle.is_complete:
        return PremiseVerdict("invalid", "premise_incomplete")
    if bundle.snapshot_id is None:
        # The bundle is complete, so the snapshot WAS pinned; the column is
        # NULL because the row it referenced was deleted (ON DELETE SET NULL).
        return PremiseVerdict("missing", "pinned_snapshot_removed")
    if facts is None:
        return PremiseVerdict("missing", "pinned_snapshot_removed")
    if not facts.snapshot_exists:
        return PremiseVerdict("missing", "pinned_snapshot_removed")
    if not facts.origin_exists:
        return PremiseVerdict("missing", "origin_removed")
    if facts.origin_superseded:
        return PremiseVerdict("stale", "origin_superseded")
    if facts.commit_sha != bundle.commit_sha:
        return PremiseVerdict("stale", "pinned_commit_changed")
    # Most specific cause before the generic one, the same ordering Issue
    # #323 uses: the linked Intent and the confirmed Capability scope are
    # inputs to (or siblings of) the content hash, so naming them reports the
    # root cause instead of its symptom.
    if facts.intent_digest != bundle.intent_digest:
        return PremiseVerdict("stale", "linked_intent_changed")
    if facts.capability_digest != bundle.capability_digest:
        return PremiseVerdict("stale", "capability_scope_changed")
    if facts.content_hash != bundle.content_hash:
        return PremiseVerdict("stale", "origin_content_changed")
    return PremiseVerdict("current", None)


# --- Producer / actor provenance ----------------------------------------------
#
# The hole this closes: `POST /joint-understanding/{id}/findings` accepted any
# `origin_role`, including `developer`. `origin_role` is the VOICE of a
# statement, and a request body claiming `developer` was enough to record a
# model's (or any caller's) sentence as the human's own judgement -- with
# `decision_method='manual'` attached to it. Separating the two axes makes
# that unrepresentable rather than merely discouraged.

# WHICH code path wrote the row. 'legacy' is only ever read, never written:
# it is what a row persisted before this contract reports.
PRODUCER_KINDS = ("investigation_loop", "translator", "developer_api", "legacy")

# Producers whose rows are generated by the system. They may never carry a
# human actor, and they are not reachable from the public findings endpoint.
INTERNAL_PRODUCER_KINDS = ("investigation_loop", "translator")

# The single `origin_role` each producer is allowed to write. A producer that
# could write more than one role would put the choice back into the request.
PRODUCER_ROLES: Dict[str, str] = {
    "investigation_loop": "investigation",
    "translator": "translation",
    "developer_api": "developer",
}

# WHO stands behind the row.
#   user   -- an authenticated human (a login session; never an SDK token)
#   system -- an internal producer; no human asserted this
#   legacy -- written before provenance was recorded; unknown, never assumed
ACTOR_KINDS = ("user", "system", "legacy")


@dataclass(frozen=True)
class ProducerContext:
    """Who is writing, resolved from the ROUTE and the authenticated caller.

    Never from a request body. ``actor_username`` exists for display only;
    ``actor_user_id`` is the identity that matters, and it comes from the
    resolved ``Principal``.
    """

    producer_kind: str
    actor_kind: str
    actor_user_id: Optional[int] = None
    actor_username: Optional[str] = None

    @property
    def origin_role(self) -> str:
        return PRODUCER_ROLES[self.producer_kind]


def system_producer(producer_kind: str) -> ProducerContext:
    """The context for an internal producer (investigation / translation)."""
    if producer_kind not in INTERNAL_PRODUCER_KINDS:
        raise JointPremiseError(
            f"producer_kind {producer_kind!r} is not an internal producer"
        )
    return ProducerContext(producer_kind=producer_kind, actor_kind="system")


def developer_producer(
    *, user_id: Optional[int], username: Optional[str]
) -> ProducerContext:
    """The context for a developer statement made by an authenticated human.

    ``user_id`` may be ``None`` only in the pre-auth MVP-compat mode (no user
    exists yet, so ``get_principal`` yields the anonymous principal). The
    actor is still recorded as ``user`` there -- the request did come from the
    single local operator -- but with no user id, which is the honest record
    rather than a fabricated one.
    """
    return ProducerContext(
        producer_kind="developer_api",
        actor_kind="user",
        actor_user_id=user_id,
        actor_username=username,
    )


def validate_provenance(
    *, producer_kind: str, origin_role: str, actor_kind: str, actor_user_id: Optional[int]
) -> None:
    """Fail-closed check that a row's provenance triple is self-consistent.

    Three invariants:

    - a producer writes only its own ``origin_role``,
    - a system producer never carries a human actor,
    - a developer statement always does.

    Called on the path where the triple is assembled from a request -- the
    public developer endpoint. The two internal producers satisfy the same
    invariants structurally, by writing their ``(origin_role, producer_kind,
    actor_kind)`` triple as SQL literals with no actor column at all, so there
    is no runtime choice left for this function to check.
    """
    if producer_kind not in PRODUCER_KINDS:
        raise JointPremiseError(
            f"producer_kind must be one of {list(PRODUCER_KINDS)}, got {producer_kind!r}"
        )
    if actor_kind not in ACTOR_KINDS:
        raise JointPremiseError(
            f"actor_kind must be one of {list(ACTOR_KINDS)}, got {actor_kind!r}"
        )
    if producer_kind == "legacy" or actor_kind == "legacy":
        raise JointPremiseError(
            "'legacy' provenance is a read-only compatibility value and must "
            "not be written"
        )
    expected_role = PRODUCER_ROLES[producer_kind]
    if origin_role != expected_role:
        raise JointPremiseError(
            f"producer_kind={producer_kind!r} may only write "
            f"origin_role={expected_role!r}, got {origin_role!r}"
        )
    if producer_kind in INTERNAL_PRODUCER_KINDS:
        if actor_kind != "system":
            raise JointPremiseError(
                f"producer_kind={producer_kind!r} is a system producer and must "
                "record actor_kind='system'"
            )
        if actor_user_id is not None:
            raise JointPremiseError(
                "A system-produced record must not name a human actor"
            )
    elif actor_kind != "user":
        raise JointPremiseError(
            "A developer record must record actor_kind='user'"
        )


# --- Outcome basis (the decision audit) ---------------------------------------

# Outcomes that assert something beyond "we talked", and therefore need a
# named, still-valid basis. Same set as Epic #328's, restated here because
# this module owns what "valid basis" now means.
OUTCOMES_REQUIRING_BASIS = ("hypothesis_adopted", "decided")

# Finite basis-rejection codes, so the UI can explain WHY a basis was refused
# rather than only that it was.
BASIS_REJECTION_CODES = (
    "basis_missing",
    "basis_foreign_session",
    "basis_superseded",
    "basis_mock",
    "basis_not_hypothesis",
    "basis_unverified",
)


@dataclass(frozen=True)
class BasisFinding:
    """The subset of a finding that decides whether it may be an outcome basis."""

    id: int
    origin_role: str
    claim_kind: str
    is_mock: bool
    superseded: bool
    intelligence_run_id: Optional[int]
    has_evidence: bool


def validate_basis(
    *,
    outcome: str,
    outcome_finding_ids: Sequence[int],
    findings_by_id: Dict[int, BasisFinding],
) -> Tuple[str, str]:
    """Validate an asserting outcome's basis. Returns ``(code, message)``.

    Returns ``("", "")`` when the basis is acceptable (or when the outcome
    needs none). Fail-closed per Issue #337's last completion gate: a
    superseded, mock, foreign, or unverified finding is refused rather than
    accepted with a caveat.

    ``hypothesis_adopted`` additionally requires that every basis finding IS a
    current investigation hypothesis. Adopting "a hypothesis" on the basis of
    a plain fact would make the provisional marker meaningless -- and adopting
    one that a later round already corrected would re-adopt a claim the
    investigation itself withdrew.
    """
    if outcome not in OUTCOMES_REQUIRING_BASIS:
        return "", ""
    if not outcome_finding_ids:
        return "basis_missing", f"Outcome '{outcome}' must name the findings it rests on"
    for fid in outcome_finding_ids:
        finding = findings_by_id.get(fid)
        if finding is None:
            return (
                "basis_foreign_session",
                f"outcome_finding_ids reference findings outside this session: [{fid}]",
            )
        if finding.superseded:
            return (
                "basis_superseded",
                f"Finding {fid} has been corrected by a later finding and cannot "
                "be the basis of an outcome",
            )
        if finding.is_mock:
            return (
                "basis_mock",
                f"Finding {fid} is mock output and cannot be the basis of an outcome",
            )
        if outcome == "hypothesis_adopted":
            if finding.origin_role != "investigation" or finding.claim_kind != "hypothesis":
                return (
                    "basis_not_hypothesis",
                    f"Finding {fid} is not an investigation hypothesis; "
                    "'hypothesis_adopted' may only adopt a current hypothesis",
                )
            if finding.intelligence_run_id is None or not finding.has_evidence:
                return (
                    "basis_unverified",
                    f"Finding {fid} carries no verified reasoning run or evidence",
                )
    return "", ""


# --- Hypothesis adoption lineage ----------------------------------------------

# A provisionally adopted hypothesis is not a fact, and the record of that has
# to survive the next rebuild. These states are DERIVED from the adoption's
# captured premise versus the current one -- never stored -- so they can never
# drift out of sync with the premise they describe (the same discipline
# Issue #349 applies to an unresolved blocking failure).
#   provisional             -- still adopted against an unchanged premise
#   reconfirmation_required -- the premise moved; a human must re-check it
#   basis_withdrawn         -- the investigation itself corrected the basis
ADOPTION_STATES = ("provisional", "reconfirmation_required", "basis_withdrawn")


def adoption_state(*, premise_state: str, basis_superseded: bool) -> str:
    """Finite state of one recorded hypothesis adoption (first-match)."""
    if basis_superseded:
        return "basis_withdrawn"
    if premise_state not in ASSERTABLE_PREMISE_STATES:
        return "reconfirmation_required"
    return "provisional"


# --- Origin content digests ---------------------------------------------------


def _canonical_digest(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_qa_content_hash(*, question_text: str) -> str:
    """Digest of the question a ``qa``-origin session is investigating.

    The ANSWER is deliberately not an input. A Joint Understanding session on
    a question is premised on the question, and attaching the answer would
    make the session stale the moment its own reflux reaches the Q&A row --
    the gate would fire on its own output.
    """
    return _canonical_digest({"kind": "qa", "question_text": question_text})


def compute_intent_content_hash(*, field: str, value_text: str) -> str:
    """Digest of the Intent VALUE an ``intent``-origin session is about.

    Deliberately excludes ``status``, unlike Issue #308's
    ``compute_intent_item_digest``. For an Inquiry the Intent item is a
    *linked* premise, so its confirmation state is part of the ground the
    conversation stood on. For a Joint Understanding session whose ORIGIN is
    the Intent item, confirming it is the decision the session exists to
    help make -- digesting the status would let the session invalidate its own
    premise the moment its work paid off, and then refuse to record the
    decision it just enabled. This is the same reasoning Issue #308 used to
    exclude ``confirmation_id`` from the Capability scope digest: expire a
    premise on MEANING, never on a decision marker.
    """
    return _canonical_digest(
        {"kind": "intent", "field": field, "value_text": value_text}
    )


def compute_purpose_need_content_hash(
    *, need_id: str, target_kind: str, target_id: str, target_digest: Optional[str]
) -> str:
    """Digest of a ``purpose_need``-origin session's premise (Issue #389).

    ``target_digest`` is ``purpose_needs.target_digest_for(...)`` re-evaluated
    against the CURRENT Purpose Chain projection -- never the value captured
    on the ``purpose_need_response`` row. Reusing the captured value here
    would make this content hash trivially equal to itself forever (it is
    read FROM that same row), which would silently defeat staleness
    detection rather than support it. ``None`` when the target can no longer
    be found in the current projection at all (its need resolved, or the
    session/System context changed) -- a real, distinct digest value, not an
    absence that gets treated as "unchanged".
    """
    return _canonical_digest(
        {
            "kind": "purpose_need",
            "need_id": need_id,
            "target_kind": target_kind,
            "target_id": target_id,
            "target_digest": target_digest,
        }
    )


def compute_inquiry_content_hash(
    *,
    inquiry_content_hash: Optional[str],
    inquiry_capability_digest: Optional[str],
    inquiry_intent_digest: Optional[str],
    inquiry_origin_kind: str,
    inquiry_origin_id: int,
) -> str:
    """Digest of an ``inquiry``-origin session's premise.

    An Inquiry has no question column of its own (the question is a message),
    so its premise IS its own Issue #308 bundle plus the item it hangs off.
    Inheriting it rather than inventing a parallel digest is the whole point
    of sharing the bundle: when the Alignment build supersedes the Inquiry,
    both features reach the same verdict from the same facts.
    """
    return _canonical_digest(
        {
            "kind": "inquiry",
            "origin_kind": inquiry_origin_kind,
            "origin_id": int(inquiry_origin_id),
            "content_hash": inquiry_content_hash,
            "capability_digest": inquiry_capability_digest,
            "intent_digest": inquiry_intent_digest,
        }
    )


EMPTY_CAPABILITY_SCOPE_DIGEST = compute_capability_scope_digest(dependencies=[])
"""The digest of an explicitly empty confirmed Capability scope.

Used for origins that have no Capability dependencies of their own (``qa`` /
``intent``). Producing a real digest for "no scope" rather than ``NULL`` keeps
every premise comparison total -- there is no third "unknown" state for a
caller to interpret differently from Issue #308's.
"""


# --- Persistence-facing capture and evaluation --------------------------------
#
# The only functions in this module that touch the database. They are the
# shared implementation both the capture (session creation) and every gate
# (close / reflux / adoption display) go through, so no caller can arrive at a
# different verdict from the same rows -- the failure mode Issue #337 exists to
# remove was two places disagreeing about what "fresh" meant.


def _column(row, name: str):
    """Value of an optional (additively migrated) column, else None."""
    return row[name] if name in row.keys() else None


def _linked_intent_digest(conn, *, system_id: int, intent_item_id: Optional[int]):
    if intent_item_id is None:
        return None
    intent = conn.execute(
        "SELECT field, value_text, status FROM interview_intent_item "
        "WHERE id = ? AND system_id = ?",
        (intent_item_id, system_id),
    ).fetchone()
    if intent is None:
        return None
    return compute_intent_item_digest(
        field=intent["field"], value_text=intent["value_text"], status=intent["status"],
    )


def _current_row_id(conn, table: str, *, row_id: int, system_id: int) -> Optional[int]:
    """Follow a ``superseded_by_id`` chain to the row that is current.

    Both ``interview_qa`` and ``interview_intent_item`` correct additively, so
    the pinned row is history the moment the developer revises it. The walk is
    bounded by the number of rows visited, so a (corrupt) cycle terminates
    instead of hanging.
    """
    seen = set()
    current = row_id
    while current not in seen:
        seen.add(current)
        row = conn.execute(
            f"SELECT id, superseded_by_id FROM {table} WHERE id = ? AND system_id = ?",
            (current, system_id),
        ).fetchone()
        if row is None:
            return None
        if row["superseded_by_id"] is None:
            return row["id"]
        current = row["superseded_by_id"]
    return current


def _origin_facts(
    conn, *, origin_kind: str, origin_id: int, system_id: int, session_id: Optional[int] = None
) -> Dict[str, object]:
    """Resolve one origin item's premise-bearing facts as they stand now.

    Returns ``{"exists", "superseded", "current_origin_id", "revision_id",
    "content_hash", "capability_digest", "intent_digest"}``. Per-origin, and
    deliberately not uniform: what "the premise moved" means differs by item.

    ``session_id`` is used only by the ``purpose_need`` branch (Issue #389),
    which must re-derive the Purpose Chain to compute the CURRENT digest of
    the need's target -- the other three origins read their own tables
    directly and need no session context. Both call sites
    (``capture_premise_bundle`` / ``resolve_premise_facts``) already have a
    session id on hand (the session row and the JU row respectively).
    """
    absent: Dict[str, object] = {
        "exists": False, "superseded": False, "current_origin_id": None,
        "revision_id": None, "content_hash": None,
        "capability_digest": None, "intent_digest": None,
    }
    if origin_kind == "qa":
        current_id = _current_row_id(
            conn, "interview_qa", row_id=origin_id, system_id=system_id,
        )
        if current_id is None:
            return absent
        row = conn.execute(
            "SELECT question_text FROM interview_qa WHERE id = ? AND system_id = ?",
            (current_id, system_id),
        ).fetchone()
        return {
            "exists": True, "superseded": False, "current_origin_id": current_id,
            "revision_id": None,
            "content_hash": compute_qa_content_hash(question_text=row["question_text"]),
            "capability_digest": EMPTY_CAPABILITY_SCOPE_DIGEST,
            "intent_digest": None,
        }
    if origin_kind == "intent":
        current_id = _current_row_id(
            conn, "interview_intent_item", row_id=origin_id, system_id=system_id,
        )
        if current_id is None:
            return absent
        row = conn.execute(
            "SELECT field, value_text FROM interview_intent_item "
            "WHERE id = ? AND system_id = ?",
            (current_id, system_id),
        ).fetchone()
        return {
            "exists": True, "superseded": False, "current_origin_id": current_id,
            "revision_id": None,
            "content_hash": compute_intent_content_hash(
                field=row["field"], value_text=row["value_text"],
            ),
            "capability_digest": EMPTY_CAPABILITY_SCOPE_DIGEST,
            "intent_digest": None,
        }
    if origin_kind == "review_item":
        row = conn.execute(
            "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?",
            (origin_id, system_id),
        ).fetchone()
        if row is None:
            return absent
        return {
            "exists": True,
            # Issue #323 sets `superseded` when a rebuild replaced this exact
            # discussion point. It is not a content change -- the row itself is
            # history -- so it is reported as its own reason code.
            "superseded": bool(_column(row, "superseded")),
            "current_origin_id": row["id"],
            "revision_id": _column(row, "revision_id"),
            "content_hash": _column(row, "base_content_hash"),
            "capability_digest": capability_scope_digest_for_item(conn, row["id"]),
            "intent_digest": _linked_intent_digest(
                conn, system_id=system_id, intent_item_id=_column(row, "intent_item_id"),
            ),
        }
    if origin_kind == "inquiry":
        row = conn.execute(
            "SELECT * FROM interview_inquiry WHERE id = ? AND system_id = ?",
            (origin_id, system_id),
        ).fetchone()
        if row is None:
            return absent
        content_hash = compute_inquiry_content_hash(
            inquiry_content_hash=_column(row, "premise_content_hash"),
            inquiry_capability_digest=_column(row, "premise_capability_digest"),
            inquiry_intent_digest=_column(row, "premise_intent_digest"),
            inquiry_origin_kind=row["origin_kind"],
            inquiry_origin_id=row["origin_id"],
        )
        return {
            "exists": True,
            # The Inquiry's own Issue #323 evaluation already decided the
            # premise is gone. Reaching the same verdict from the same fact is
            # the point of sharing the bundle.
            "superseded": row["status"] == "superseded",
            "current_origin_id": row["id"],
            "revision_id": _column(row, "premise_revision_id"),
            "content_hash": content_hash,
            "capability_digest": (
                _column(row, "premise_capability_digest")
                or EMPTY_CAPABILITY_SCOPE_DIGEST
            ),
            "intent_digest": _column(row, "premise_intent_digest"),
        }
    if origin_kind == "purpose_need":
        row = conn.execute(
            "SELECT * FROM purpose_need_response WHERE id = ? AND system_id = ?",
            (origin_id, system_id),
        ).fetchone()
        if row is None:
            return absent
        # Local imports: `purpose_chain`/`purpose_needs` do not import this
        # module (no cycle), but importing them at module scope here would
        # make joint_premise -- a module every JU read goes through -- pay
        # the Purpose Chain import cost even for Systems that never open a
        # purpose_need-origin session.
        from . import purpose_chain as _purpose_chain
        from .purpose_needs import resolve_need, derive_needs, target_digest_for

        effective_session_id = session_id if session_id is not None else row["session_id"]
        current_digest: Optional[str] = None
        target_kind = row["target_kind"] if "target_kind" in row.keys() else None
        try:
            chain = _purpose_chain.derive_purpose_chain(conn, system_id, effective_session_id)
            needs = derive_needs(chain)
            # The need itself may no longer be derivable (its signal cleared)
            # while its TARGET still is -- re-resolve the target directly
            # rather than only through `resolve_need`, so a resolved need
            # (relation now confirmed, element now present) still yields a
            # comparable "it changed" digest instead of silently reporting
            # `None` (which would misread as "gone" rather than "resolved").
            need = resolve_need(needs, row["need_id"])
            if need is not None:
                current_digest = need.target_digest
            elif target_kind == "element":
                element = next((e for e in chain.elements if e.id == row["target_id"]), None)
                current_digest = target_digest_for("element", element) if element is not None else None
            elif target_kind == "relation":
                relation = next((r for r in chain.relations if r.id == row["target_id"]), None)
                current_digest = target_digest_for("relation", relation) if relation is not None else None
        except Exception:  # pragma: no cover - defensive, mirrors purpose_chain's own guards
            current_digest = None
        content_hash = compute_purpose_need_content_hash(
            need_id=row["need_id"],
            target_kind=target_kind or "",
            target_id=row["target_id"],
            target_digest=current_digest,
        )
        return {
            "exists": True,
            "superseded": row["superseded_by_id"] is not None,
            "current_origin_id": row["id"],
            "revision_id": None,
            "content_hash": content_hash,
            "capability_digest": EMPTY_CAPABILITY_SCOPE_DIGEST,
            "intent_digest": None,
        }
    raise JointPremiseError(f"Unknown origin_kind: {origin_kind!r}")


def _latest_revision_id(conn, *, session_id: int, system_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM understanding_revision WHERE session_id = ? AND system_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (session_id, system_id),
    ).fetchone()
    return row["id"] if row is not None else None


def capture_premise_bundle(
    conn, *, origin_kind: str, origin_id: int, session_row, system_id: int, now: float
) -> Dict[str, object]:
    """The immutable premise facts to store on a newly created session.

    Mirrors ``routes/interview_inquiry._capture_premise`` (Issue #308) in shape
    and column names, and extends it in the two ways a code investigation
    needs: the pinned COMMIT (what the investigation will actually read) and
    coverage of all four Joint Understanding origins rather than review items
    alone.

    Nothing here is inferred. When a fact cannot be resolved the column stays
    NULL, which makes the bundle incomplete and the session explicitly
    ``invalid`` -- refused for the asserting outcomes -- rather than compared
    against a reconstructed premise.
    """
    snapshot_id = session_row["snapshot_id"]
    commit_sha = None
    if snapshot_id is not None:
        snapshot = conn.execute(
            "SELECT commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        if snapshot is not None:
            commit_sha = snapshot["commit_sha"]
    origin = _origin_facts(
        conn, origin_kind=origin_kind, origin_id=origin_id, system_id=system_id,
        session_id=session_row["id"],
    )
    review_subject_id = None
    if origin_kind == "review_item":
        item = conn.execute(
            "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?",
            (origin_id, system_id),
        ).fetchone()
        # An 'ambiguous'/'untrackable' subject is deliberately NOT captured
        # (Issue #321): storing it would let a later build resolve a single
        # successor for a subject that never identified this row uniquely.
        if item is not None and _column(item, "subject_state") in (
            "new", "unchanged", "changed",
        ):
            review_subject_id = _column(item, "review_subject_id")
    return {
        "premise_snapshot_id": snapshot_id,
        "premise_commit_sha": commit_sha,
        # The origin's own revision where it has one (a review item pins the
        # Understanding revision it was built from), else the session's latest.
        # Captured for audit and for the hypothesis re-confirmation lineage --
        # never an input to the staleness verdict (Issue #323).
        "premise_revision_id": (
            origin["revision_id"]
            if origin["revision_id"] is not None
            else _latest_revision_id(
                conn, session_id=session_row["id"], system_id=system_id,
            )
        ),
        "premise_content_hash": origin["content_hash"],
        "premise_capability_digest": origin["capability_digest"],
        "premise_intent_digest": origin["intent_digest"],
        "premise_review_subject_id": review_subject_id,
        "premise_tracking_version": JOINT_PREMISE_VERSION,
        "premise_captured_at": now,
    }


def load_premise_bundle(ju_row) -> PremiseBundle:
    """Read one session's captured bundle, tolerating pre-migration rows."""
    return PremiseBundle(
        tracking_version=_column(ju_row, "premise_tracking_version"),
        snapshot_id=_column(ju_row, "premise_snapshot_id"),
        commit_sha=_column(ju_row, "premise_commit_sha"),
        revision_id=_column(ju_row, "premise_revision_id"),
        content_hash=_column(ju_row, "premise_content_hash"),
        capability_digest=_column(ju_row, "premise_capability_digest"),
        intent_digest=_column(ju_row, "premise_intent_digest"),
        review_subject_id=_column(ju_row, "premise_review_subject_id"),
    )


def resolve_premise_facts(conn, ju_row) -> Optional[PremiseFacts]:
    """The current state of everything the session's bundle captured.

    ``None`` when the owning interview session no longer resolves at all --
    which ``evaluate_joint_premise`` reads as ``missing``. The pre-#337 code
    returned ``fresh`` for exactly this case.
    """
    system_id = ju_row["system_id"]
    session = conn.execute(
        "SELECT snapshot_id FROM interview_session WHERE id = ? AND system_id = ?",
        (ju_row["session_id"], system_id),
    ).fetchone()
    if session is None:
        return None
    pinned_id = _column(ju_row, "premise_snapshot_id")
    snapshot_exists = False
    if pinned_id is not None:
        snapshot_exists = conn.execute(
            "SELECT 1 FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (pinned_id, system_id),
        ).fetchone() is not None
    current_commit = None
    if session["snapshot_id"] is not None:
        current = conn.execute(
            "SELECT commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (session["snapshot_id"], system_id),
        ).fetchone()
        if current is not None:
            current_commit = current["commit_sha"]
    origin = _origin_facts(
        conn,
        origin_kind=ju_row["origin_kind"],
        origin_id=ju_row["origin_id"],
        system_id=system_id,
        session_id=ju_row["session_id"],
    )
    return PremiseFacts(
        snapshot_exists=snapshot_exists,
        commit_sha=current_commit,
        origin_exists=bool(origin["exists"]),
        origin_superseded=bool(origin["superseded"]),
        current_origin_id=origin["current_origin_id"],
        revision_id=origin["revision_id"],
        content_hash=origin["content_hash"],
        capability_digest=origin["capability_digest"],
        intent_digest=origin["intent_digest"],
    )


def evaluate_session_premise(conn, ju_row) -> PremiseVerdict:
    """The single premise gate every Joint Understanding consumer calls."""
    bundle = load_premise_bundle(ju_row)
    if not bundle.tracking_version or not bundle.is_complete:
        # No DB read is needed (and none should happen): an uncomparable
        # bundle is 'invalid' regardless of the current state.
        return evaluate_joint_premise(bundle=bundle, facts=None)
    return evaluate_joint_premise(
        bundle=bundle, facts=resolve_premise_facts(conn, ju_row),
    )


__all__ = [
    "ACTOR_KINDS",
    "ADOPTION_STATES",
    "ASSERTABLE_PREMISE_STATES",
    "BASIS_REJECTION_CODES",
    "BasisFinding",
    "EMPTY_CAPABILITY_SCOPE_DIGEST",
    "INTERNAL_PRODUCER_KINDS",
    "JOINT_PREMISE_VERSION",
    "JointPremiseError",
    "LEGACY_PREMISE_STATES",
    "OUTCOMES_REQUIRING_BASIS",
    "PREMISE_REASON_CODES",
    "PREMISE_STATES",
    "PRODUCER_KINDS",
    "PRODUCER_ROLES",
    "PremiseBundle",
    "PremiseFacts",
    "PremiseVerdict",
    "ProducerContext",
    "adoption_state",
    "capture_premise_bundle",
    "compute_intent_content_hash",
    "compute_intent_item_digest",
    "compute_inquiry_content_hash",
    "compute_purpose_need_content_hash",
    "compute_qa_content_hash",
    "developer_producer",
    "evaluate_joint_premise",
    "evaluate_session_premise",
    "load_premise_bundle",
    "resolve_premise_facts",
    "system_producer",
    "validate_basis",
    "validate_provenance",
]
