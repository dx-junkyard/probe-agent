"""System-canonical Understanding head and Interview premise (Issue #464).

The Overview could report a Vision while a brand-new Interview treated the
same System as having none.  The cause was an ownership boundary that did not
exist: Vision / System Purpose / confirmed Capabilities / the developer's
confirmed intent are facts about the **System**, but every one of them was
stored only inside whichever Interview session happened to produce it, and
each screen then re-derived "the current understanding" for itself -- the
Overview from the newest session row, the Interview from its own
``current_understanding`` column.  Two independent interpretations of
"latest" cannot be kept consistent, because creation order is not promotion
order.

This module is the ownership boundary.

- A System points at exactly ONE canonical Understanding revision
  (``system_understanding_head``).  It moves only through an explicit human
  promotion with a compare-and-swap, never by "something was updated more
  recently".
- An Interview session pins, at creation, the canonical revision it started
  from plus an immutable **premise bundle** captured from it.  The bundle is
  the conversation's ground: it is copied once and never silently refreshed.
- Whether a session may still be continued as-is is then a deterministic
  comparison, not a guess: the finite verdict ``current`` / ``stale`` /
  ``missing`` / ``invalid`` (the same four values, for the same reasons, as
  Issue #337's Joint Understanding premise -- a premise that was never
  captured must read ``invalid``, not be promoted to a satisfied one, and a
  premise whose ground has disappeared is not the same answer as one that
  merely moved).

Three separations this module exists to keep, which any later change must
preserve:

1. **Canonical head vs session progress.**  An Interview's unconfirmed work
   is a ``candidate`` revision.  It changes nothing the Overview shows until a
   human promotes it.  Promotion is ``decision_method: manual`` and is
   rejected -- never merged, never last-write-wins -- when the head moved
   under it.
2. **Premise state vs session status.**  ``interview_session.status`` is
   Issue #349's suspend/resume axis and is untouched here.  The premise axis
   is DERIVED at read time from the pinned digests (the #337/#338/#349
   discipline: a stored lifecycle value drifts from the rows it describes, a
   derived one cannot).  The only persisted premise fact is the developer's
   own explicit disposition -- ``rebased`` / ``branched`` -- because that is a
   human decision, not an observation.
3. **Premise vs hypothesis.**  The bundle carries the canonical claims and the
   developer's **confirmed** Intent items only.  Per-session hypotheses,
   unconfirmed intent, question order and unverified evidence stay with the
   session that produced them (Issue #464 non-goal 1).

Everything here is a structural digest or a first-match rule over an
explicitly enumerated finite set (Principle 6).  No similarity, no reasoning
call, no heuristic recovery: a premise that cannot be compared blocks the
operations that would assert something, rather than being read as satisfied.

probe-agent:
  role: canonical Understanding head, Interview premise capture/evaluation,
    and compare-and-swap promotion
  capability: interactive-system-understanding
  element_type: logic
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify a session whose System head moved evaluates 'stale' rather than 'current', that a session with no captured bundle is 'invalid' rather than 'current', that a candidate built on an older head is rejected as a conflict instead of overwriting it, and that promotion is the only way the head moves.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Version of the premise contract: WHICH facts are captured and HOW they are
# digested.  Stored on every session that captures a bundle so a later
# contract change can never be mistaken for a content change (Principle 7).
CANONICAL_PREMISE_VERSION = "canonical-premise-v1"

# The understanding sections that make up the canonical claim set.  Same
# vocabulary as `understanding_brief.BRIEF_SECTIONS`; listed here explicitly
# because the digest's stability depends on it.
PREMISE_SECTIONS = ("vision", "system_purpose", "core_capabilities")


# --- Finite vocabularies ------------------------------------------------------

#: Verdict of comparing a session's pinned premise against the System's head.
PREMISE_STATES = ("current", "stale", "missing", "invalid")

#: The only state in which a session may consume its premise for new
#: reasoning, or have its candidate promoted.  Everything else requires the
#: developer to choose new / rebase / branch first.
CONTINUABLE_PREMISE_STATES = ("current",)

PREMISE_REASON_CODES = (
    # current
    "premise_matches_head",
    "no_canonical_head",
    # stale
    "head_moved",
    "promoted_by_this_session",
    "premise_content_changed",
    # missing
    "base_revision_missing",
    # invalid
    "premise_not_captured",
    "premise_version_unsupported",
)

#: The developer's explicit decision about a session whose premise moved.
#: `active` is the absence of such a decision, not a decision.
PREMISE_DISPOSITIONS = ("active", "rebased", "branched")

#: An Understanding revision's role in the System's canonical lineage.
REVISION_STATUSES = ("candidate", "canonical", "superseded")

#: Append-only audit of everything that moves the head or records a
#: disposition.  A premise MISMATCH is deliberately absent: it is derived at
#: read time, so recording it would mean writing on a read.
CANONICAL_EVENT_KINDS = (
    "head_initialized",
    "promoted",
    "promotion_conflict",
    "session_rebased",
    "session_branched",
)

#: Why a result produced OUTSIDE the database lock may not be persisted.
#: A reasoning call runs with the connection released (CLAUDE.md forbids
#: holding it across an external call), so the ground can move between the
#: pre-flight gate and the write. Two codes, because the developer's next
#: action differs: the System moved on, versus this conversation was re-pinned
#: underneath the run.
REVALIDATION_CODES = (
    "head_moved_during_run",
    "session_premise_changed_during_run",
)

REVALIDATION_MESSAGES = {
    "head_moved_during_run": (
        "処理中に正準 Understanding が更新されたため、この結果は保存しませんでした。"
        "新しい前提で実行し直してください。"
    ),
    "session_premise_changed_during_run": (
        "処理中にこのセッションの前提が変更されたため、この結果は保存しませんでした。"
        "もう一度実行してください。"
    ),
}

#: Why a promotion was refused.  Each is a distinct next action for the
#: developer, so they are never collapsed into one "conflict".
PROMOTION_REJECTION_CODES = (
    "premise_not_current",
    "head_revision_mismatch",
    "head_version_mismatch",
    "revision_not_found",
    "revision_not_candidate",
    "revision_empty",
)

PREMISE_STATE_LABELS = {
    "current": "現在の正準 Understanding と一致",
    "stale": "前提が古くなっています",
    "missing": "前提にしていた Understanding が見つかりません",
    "invalid": "前提が記録されていません",
}

PREMISE_REASON_MESSAGES = {
    "premise_matches_head": "このセッションは現在の正準 Understanding を前提にしています。",
    "no_canonical_head": "この System にはまだ正準 Understanding がありません。",
    "head_moved": (
        "このセッションの開始後に正準 Understanding が更新されました。"
        "新規セッション・rebase・branch のいずれかを選んでください。"
    ),
    "promoted_by_this_session": (
        "この会話の成果を正準 Understanding として確定しました。"
        "この会話自体は確定前の前提のまま残ります。続きは rebase で"
        "新しい前提のセッションへ引き継いでください。"
    ),
    "premise_content_changed": (
        "前提として固定した内容と、現在の正準 Understanding の内容が一致しません。"
    ),
    "base_revision_missing": (
        "前提にしていた Understanding リビジョンが存在しません。"
    ),
    "premise_not_captured": (
        "このセッションは前提を記録する前に作成されました（legacy-unbased）。"
        "続行する前に、現在の正準 Understanding を前提として明示的に選び直してください。"
    ),
    "premise_version_unsupported": (
        "このセッションの前提は現在の契約では比較できません。"
    ),
}

PREMISE_DISPOSITION_LABELS = {
    "active": "通常のセッション",
    "rebased": "rebase 済み（後続セッションへ引き継ぎ）",
    "branched": "古い前提のまま維持（branch）",
}


class CanonicalUnderstandingError(ValueError):
    """A canonical-head/premise contract violation. Routes translate to 4xx."""


class PromotionConflict(CanonicalUnderstandingError):
    """A candidate could not be promoted. Carries one finite rejection code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        head_revision_id: Optional[int] = None,
        head_version: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        if code not in PROMOTION_REJECTION_CODES:
            raise CanonicalUnderstandingError(f"unknown rejection code: {code}")
        self.code = code
        self.message = message
        self.head_revision_id = head_revision_id
        self.head_version = head_version


# --- Values -------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalHead:
    """The System's single pointer to its canonical Understanding."""

    system_id: int
    revision_id: int
    head_version: int
    updated_at: float
    updated_by: str = ""


@dataclass(frozen=True)
class PremiseBundle:
    """The immutable ground one Interview was started on.

    ``understanding`` is the canonical revision's own content, copied so the
    conversation's premise cannot change under it.  ``intent_items`` carries
    the developer's CONFIRMED Intent Brief statements only -- an unconfirmed
    proposal is a session hypothesis and stays with its session (#464 non-goal
    1).
    """

    premise_version: str
    revision_id: Optional[int]
    content_digest: str
    understanding: Optional[Dict[str, Any]]
    intent_items: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "premise_version": self.premise_version,
                "revision_id": self.revision_id,
                "content_digest": self.content_digest,
                "understanding": self.understanding,
                "intent_items": self.intent_items,
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    @property
    def digest(self) -> str:
        return compute_premise_digest(self)


@dataclass(frozen=True)
class PremiseVerdict:
    """The deterministic answer to 「この会話をそのまま続けてよいか」."""

    state: str
    reason_code: str
    message: str
    label: str
    base_revision_id: Optional[int]
    base_premise_digest: Optional[str]
    head_revision_id: Optional[int]
    head_premise_digest: Optional[str]
    head_version: Optional[int]
    disposition: str
    origin_kind: Optional[str] = None
    origin_session_id: Optional[int] = None
    result_revision_id: Optional[int] = None

    @property
    def continuable(self) -> bool:
        """May this session consume its premise for new reasoning?

        ``branched`` is the one deliberate exception: the developer has
        explicitly recorded that this conversation is an exploration of the
        OLD premise.  It stays readable and continuable, and its candidate can
        still never be promoted -- promotion requires ``current``.
        """
        return self.state in CONTINUABLE_PREMISE_STATES or self.disposition == "branched"


@dataclass(frozen=True)
class PromotionResult:
    revision_id: int
    previous_revision_id: Optional[int]
    head_version: int
    premise_digest: str
    content_digest: str
    promoted_at: float
    promoted_by: str


# --- Digests ------------------------------------------------------------------


def _canonical_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def normalize_understanding(understanding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The meaning-bearing content of an Understanding, section by section.

    Reuses `understanding_brief.claim_payload`, which is already the single
    definition of "a claim's content" (#351).  A second definition here would
    let the head's digest see a change the Brief's change list cannot name.
    The import is function-local because `understanding_brief` imports this
    module for the premise Intent fallback.
    """
    from .understanding_brief import claim_payload

    normalized: Dict[str, Any] = {}
    for section in PREMISE_SECTIONS:
        items = []
        raw = (understanding or {}).get(section) or []
        if not isinstance(raw, list):
            raw = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            items.append({"name": name, **claim_payload(item)})
        items.sort(key=lambda entry: entry["name"])
        normalized[section] = items
    return normalized


def compute_content_digest(understanding: Optional[Dict[str, Any]]) -> str:
    """"Is this the same Understanding content?" -- names included."""
    return _canonical_digest(normalize_understanding(understanding))


#: The one Intent status that RETRACTS a System-level confirmed statement.
#: `undecided` / `needs_review` / `proposed` are in-progress states of ONE
#: conversation, not decisions about the System: a session reopening a
#: question does not un-confirm what an earlier generation settled. Dropping a
#: confirmed Intent therefore needs its own explicit manual decision.
INTENT_RETRACTION_STATUS = "not_applicable"


def normalize_intent_items(rows: Any) -> List[Dict[str, Any]]:
    """The developer's confirmed Intent statements, normalized and ordered."""
    items: List[Dict[str, Any]] = []
    for row in rows or []:
        data = dict(row)
        if data.get("status") != "confirmed":
            continue
        items.append(
            {
                "field": str(data.get("field") or ""),
                "value_text": str(data.get("value_text") or ""),
                "status": "confirmed",
                "origin": str(data.get("origin") or ""),
                "is_mock": bool(data.get("is_mock")),
            }
        )
    items.sort(key=lambda entry: (entry["field"], entry["value_text"]))
    return items


def merge_intent_items(
    inherited: List[Dict[str, Any]], session_rows: Any
) -> List[Dict[str, Any]]:
    """Carry the canonical Intent forward, then apply THIS session's decisions.

    A confirmed Intent statement belongs to the System, so it must survive a
    promotion made by a session that never restated it. Without this, the
    second generation silently dropped it: the bundle was built from the
    promoting session's own rows alone, and a session that read the goal from
    its premise (rather than re-confirming it) had no row of its own to
    contribute.

    Only two of this session's rows change the result -- `confirmed` replaces,
    `not_applicable` removes. Everything else is an in-progress state of one
    conversation and leaves the inherited statement standing.
    """
    merged = {item["field"]: dict(item) for item in inherited}
    for row in session_rows or []:
        data = dict(row)
        field = str(data.get("field") or "")
        if not field:
            continue
        status = data.get("status")
        if status == "confirmed":
            merged[field] = {
                "field": field,
                "value_text": str(data.get("value_text") or ""),
                "status": "confirmed",
                "origin": str(data.get("origin") or ""),
                "is_mock": bool(data.get("is_mock")),
            }
        elif status == INTENT_RETRACTION_STATUS:
            merged.pop(field, None)
    items = list(merged.values())
    items.sort(key=lambda entry: (entry["field"], entry["value_text"]))
    return items


def compute_premise_digest(bundle: PremiseBundle) -> str:
    """Digest of what a conversation is entitled to treat as settled.

    Deliberately over the contract version, the content digest and the
    confirmed Intent items -- NOT over the revision id (the same content
    re-promoted is the same premise) and NOT over gap analysis (a docs/code
    mismatch list is an analysis of the premise, not part of it).
    """
    return _canonical_digest(
        {
            "premise_version": bundle.premise_version,
            "content_digest": bundle.content_digest,
            "intent_items": bundle.intent_items,
        }
    )


# --- Head ---------------------------------------------------------------------


def load_head(conn, system_id: int) -> Optional[CanonicalHead]:
    """The System's canonical head, or ``None`` when it has never been set.

    ``None`` means "no human has promoted an Understanding for this System
    yet" -- never "use the newest thing you can find".
    """
    row = conn.execute(
        """SELECT system_id, revision_id, head_version, updated_at, updated_by
           FROM system_understanding_head WHERE system_id = ?""",
        (system_id,),
    ).fetchone()
    if row is None or row["revision_id"] is None:
        return None
    return CanonicalHead(
        system_id=row["system_id"],
        revision_id=row["revision_id"],
        head_version=int(row["head_version"]),
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"] or "",
    )


def load_revision(conn, system_id: int, revision_id: int):
    return conn.execute(
        "SELECT * FROM understanding_revision WHERE id = ? AND system_id = ?",
        (revision_id, system_id),
    ).fetchone()


def canonical_understanding(conn, system_id: int) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """The head revision id and its Understanding content.

    This is the ONLY answer to 「この System の現在の Understanding は何か」.
    Returns ``(None, None)`` when no head exists; the caller then says so,
    rather than substituting a session's in-progress state.
    """
    head = load_head(conn, system_id)
    if head is None:
        return None, None
    row = load_revision(conn, system_id, head.revision_id)
    if row is None:
        return None, None
    return row["id"], _loads(row["current_understanding"])


def _loads(raw: Optional[str]) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def current_intent_rows(conn, session_id: int, system_id: int):
    """Every non-superseded Intent row of one session, whatever its status.

    The status filter belongs to `merge_intent_items`, not to the query: a
    `not_applicable` row is exactly as load-bearing as a `confirmed` one,
    because it is how a developer retracts an inherited statement.
    """
    return conn.execute(
        """SELECT field, value_text, status, origin, is_mock
           FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
           ORDER BY id""",
        (session_id, system_id),
    ).fetchall()


def build_premise_bundle(
    conn,
    system_id: int,
    revision_row,
    *,
    inherited_intent: Optional[List[Dict[str, Any]]] = None,
) -> PremiseBundle:
    """Capture the premise a revision represents, at this moment.

    Called exactly once per revision, at promotion.  The result is stored on
    the revision, so every session that later pins this head pins the SAME
    bytes -- confirming a new Intent item afterwards does not silently move
    the canonical premise, it becomes part of the next promoted revision.

    ``inherited_intent`` is the CURRENT canonical premise's Intent. It is the
    base the promoting session's own decisions are applied on top of, so a
    confirmed statement survives a generation that never restated it.
    """
    understanding = _loads(revision_row["current_understanding"])
    understanding = understanding if isinstance(understanding, dict) else None
    intent_items = merge_intent_items(
        inherited_intent or [],
        current_intent_rows(conn, revision_row["session_id"], system_id),
    )
    return PremiseBundle(
        premise_version=CANONICAL_PREMISE_VERSION,
        revision_id=revision_row["id"],
        content_digest=compute_content_digest(understanding),
        understanding=understanding,
        intent_items=intent_items,
    )


def empty_bundle() -> PremiseBundle:
    """The premise of a System that has no canonical head yet.

    A real captured bundle, not an absent one: a session created before any
    promotion has genuinely agreed to start from nothing, and must become
    ``stale`` -- not stay ``current`` -- the moment a head appears.
    """
    return PremiseBundle(
        premise_version=CANONICAL_PREMISE_VERSION,
        revision_id=None,
        content_digest=compute_content_digest(None),
        understanding=None,
        intent_items=[],
    )


def load_bundle(revision_row) -> Optional[PremiseBundle]:
    """Re-read a revision's stored bundle. ``None`` when it was never promoted."""
    if revision_row is None:
        return None
    raw = _loads(_column(revision_row, "premise_json"))
    if not isinstance(raw, dict):
        return None
    understanding = raw.get("understanding")
    return PremiseBundle(
        premise_version=str(raw.get("premise_version") or ""),
        revision_id=raw.get("revision_id"),
        content_digest=str(raw.get("content_digest") or ""),
        understanding=understanding if isinstance(understanding, dict) else None,
        intent_items=[i for i in (raw.get("intent_items") or []) if isinstance(i, dict)],
    )


def _column(row, name: str):
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


# --- Session premise ----------------------------------------------------------


def capture_session_premise(
    conn,
    *,
    session_id: int,
    system_id: int,
    now: Optional[float] = None,
) -> PremiseBundle:
    """Pin the System's current canonical premise onto a new session.

    Step 2 of the required "new Interview" sequence: the head's revision id
    and premise digest are fixed on the session, and the bundle itself is
    copied so the conversation's ground cannot drift.  Must be called inside
    the same transaction that created the session, so a session can never
    exist without a premise decision having been made about it.
    """
    now = time.time() if now is None else now
    head = load_head(conn, system_id)
    bundle = empty_bundle()
    base_revision_id: Optional[int] = None
    if head is not None:
        revision = load_revision(conn, system_id, head.revision_id)
        stored = load_bundle(revision)
        if stored is not None:
            bundle = stored
            base_revision_id = head.revision_id
        elif revision is not None:
            bundle = build_premise_bundle(conn, system_id, revision)
            base_revision_id = head.revision_id
    conn.execute(
        """UPDATE interview_session
           SET base_understanding_revision_id = ?,
               base_premise_digest = ?,
               base_premise_json = ?,
               premise_tracking_version = ?,
               premise_captured_at = ?,
               premise_disposition = 'active'
           WHERE id = ? AND system_id = ?""",
        (
            base_revision_id,
            bundle.digest,
            bundle.to_json(),
            CANONICAL_PREMISE_VERSION,
            now,
            session_id,
            system_id,
        ),
    )

    # Pinning the premise and STARTING FROM IT are one act, so the seeding
    # lives here rather than in whichever route happened to create the
    # session: a rebase that pinned today's head but showed a blank
    # understanding would reintroduce the very disagreement this Issue fixes.
    # Existing content is never overwritten -- adopting a premise on a
    # conversation that already built something must not erase its work.
    if bundle.understanding:
        existing = conn.execute(
            """SELECT current_understanding FROM interview_session
               WHERE id = ? AND system_id = ?""",
            (session_id, system_id),
        ).fetchone()
        if existing is not None and not existing["current_understanding"]:
            gap_analysis = None
            if base_revision_id is not None:
                base_row = load_revision(conn, system_id, base_revision_id)
                if base_row is not None:
                    gap_analysis = base_row["gap_analysis"]
            conn.execute(
                """UPDATE interview_session
                   SET current_understanding = ?, gap_analysis = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (
                    json.dumps(bundle.understanding, ensure_ascii=False),
                    gap_analysis,
                    now,
                    session_id,
                    system_id,
                ),
            )
    return bundle


def session_premise_bundle(session_row) -> Optional[PremiseBundle]:
    """The immutable bundle a session captured, or ``None`` for a legacy row."""
    raw = _loads(_column(session_row, "base_premise_json"))
    if not isinstance(raw, dict):
        return None
    understanding = raw.get("understanding")
    return PremiseBundle(
        premise_version=str(raw.get("premise_version") or ""),
        revision_id=raw.get("revision_id"),
        content_digest=str(raw.get("content_digest") or ""),
        understanding=understanding if isinstance(understanding, dict) else None,
        intent_items=[i for i in (raw.get("intent_items") or []) if isinstance(i, dict)],
    )


def premise_intent_items(session_row) -> Dict[str, Dict[str, Any]]:
    """Confirmed Intent statements carried by the session's premise, by field.

    This is what makes a brand-new Interview show the same Vision the Overview
    shows: the developer's confirmed goal belongs to the System, so it reaches
    the new conversation through the premise instead of being re-asked from
    scratch.  The rows are NOT copied into the new session -- they keep their
    original session's authorship and are only read.
    """
    bundle = session_premise_bundle(session_row)
    if bundle is None:
        return {}
    return {
        str(item.get("field")): dict(item)
        for item in bundle.intent_items
        if item.get("field")
    }


def successor_session_id(conn, session_row) -> Optional[int]:
    """The session a rebase moved this conversation to, if any.

    Read from the successor's own backlink rather than stored on the original:
    a rebase writes the link once, on the row it creates, and duplicating it
    would give the same relationship two places to disagree.
    """
    row = conn.execute(
        """SELECT id FROM interview_session
           WHERE system_id = ? AND premise_origin_session_id = ?
             AND premise_origin_kind = 'rebase'
           ORDER BY id DESC LIMIT 1""",
        (session_row["system_id"], session_row["id"]),
    ).fetchone()
    return row["id"] if row else None


def _disposition(session_row) -> str:
    value = _column(session_row, "premise_disposition")
    return value if value in PREMISE_DISPOSITIONS else "active"


def _verdict(
    state: str,
    reason_code: str,
    *,
    session_row,
    base_revision_id: Optional[int],
    base_digest: Optional[str],
    head: Optional[CanonicalHead],
    head_digest: Optional[str],
) -> PremiseVerdict:
    return PremiseVerdict(
        state=state,
        reason_code=reason_code,
        message=PREMISE_REASON_MESSAGES[reason_code],
        label=PREMISE_STATE_LABELS[state],
        base_revision_id=base_revision_id,
        base_premise_digest=base_digest,
        head_revision_id=head.revision_id if head else None,
        head_premise_digest=head_digest,
        head_version=head.head_version if head else None,
        disposition=_disposition(session_row),
        origin_kind=_column(session_row, "premise_origin_kind"),
        origin_session_id=_column(session_row, "premise_origin_session_id"),
        result_revision_id=_column(session_row, "result_understanding_revision_id"),
    )


def evaluate_session_premise(conn, session_row) -> PremiseVerdict:
    """First-match verdict on a session's premise against the System head.

    The order matters and is the contract:

    1. A session that captured nothing is ``invalid``.  It is NOT ``current``:
       "we never recorded what this conversation stands on" is the one answer
       a gate must never read as "it stands on the right thing".
    2. A captured premise whose base revision row is gone is ``missing`` --
       the ground disappeared, which is a different next action from the
       ground having moved.
    3. A base that is not the head is ``stale``, including the two asymmetric
       cases (nothing pinned but a head now exists; a head pinned but the
       System has none).
    4. Identical revision but a different digest is ``stale`` for a different
       reason, so the developer is told which.
    """
    system_id = session_row["system_id"]
    version = _column(session_row, "premise_tracking_version")
    head = load_head(conn, system_id)
    head_digest: Optional[str] = None
    if head is not None:
        head_revision = load_revision(conn, system_id, head.revision_id)
        head_digest = _column(head_revision, "premise_digest") if head_revision else None

    base_revision_id = _column(session_row, "base_understanding_revision_id")
    base_digest = _column(session_row, "base_premise_digest")
    # The immutable bundle is the RECORD of what was pinned; the FK column is
    # the live pointer. They disagree exactly when the pinned revision was
    # deleted (the column is ON DELETE SET NULL), which is the one way
    # `missing` is reachable -- and it must not be read as "this session
    # pinned nothing", which is a different answer with a different fix.
    captured_bundle = session_premise_bundle(session_row)
    captured_revision_id = captured_bundle.revision_id if captured_bundle else None

    common = {
        "session_row": session_row,
        "base_revision_id": base_revision_id,
        "base_digest": base_digest,
        "head": head,
        "head_digest": head_digest,
    }

    if not version:
        return _verdict("invalid", "premise_not_captured", **common)
    if version != CANONICAL_PREMISE_VERSION:
        return _verdict("invalid", "premise_version_unsupported", **common)

    if base_revision_id is None and captured_revision_id is not None:
        return _verdict("missing", "base_revision_missing", **common)
    if base_revision_id is not None:
        base_row = load_revision(conn, system_id, base_revision_id)
        if base_row is None:
            return _verdict("missing", "base_revision_missing", **common)

    if head is None and base_revision_id is None:
        return _verdict("current", "no_canonical_head", **common)
    # Derived, never stored: when the head this session no longer matches is the
    # one this session itself produced, the cause is its own promotion. Same
    # verdict, different cause, and a different next action -- so it gets its
    # own code rather than being reported as somebody else having moved on.
    # Checked before the asymmetric-None row below, because a session started
    # on an empty System pins no base at all and would otherwise fall through
    # to the generic `head_moved` after promoting the System's first head.
    if (
        head is not None
        and base_revision_id != head.revision_id
        and _column(session_row, "result_understanding_revision_id") == head.revision_id
    ):
        return _verdict("stale", "promoted_by_this_session", **common)
    if head is None or base_revision_id is None:
        return _verdict("stale", "head_moved", **common)
    if base_revision_id != head.revision_id:
        return _verdict("stale", "head_moved", **common)
    if head_digest is not None and base_digest != head_digest:
        return _verdict("stale", "premise_content_changed", **common)
    return _verdict("current", "premise_matches_head", **common)


# --- Revalidation across an external call -------------------------------------


@dataclass(frozen=True)
class PremiseToken:
    """What the premise was when a long-running job started.

    Taken before the database lock is released for a reasoning call, and
    checked again inside the write transaction. Carries the session's own
    pinned premise AND the System head it was current against, because either
    can move while the call is in flight.
    """

    session_id: int
    system_id: int
    base_revision_id: Optional[int]
    base_premise_digest: Optional[str]
    premise_tracking_version: Optional[str]
    disposition: str
    head_revision_id: Optional[int]
    head_version: Optional[int]


def premise_token(conn, session_row) -> PremiseToken:
    """Snapshot the premise facts a run is about to reason against."""
    head = load_head(conn, session_row["system_id"])
    return PremiseToken(
        session_id=session_row["id"],
        system_id=session_row["system_id"],
        base_revision_id=_column(session_row, "base_understanding_revision_id"),
        base_premise_digest=_column(session_row, "base_premise_digest"),
        premise_tracking_version=_column(session_row, "premise_tracking_version"),
        disposition=_disposition(session_row),
        head_revision_id=head.revision_id if head else None,
        head_version=head.head_version if head else None,
    )


def revalidate_premise(conn, token: PremiseToken) -> Optional[str]:
    """Has the ground moved since ``token`` was taken? Returns a finite code.

    Issue #464's structural-debt item 9. The pre-flight gate cannot cover a
    reasoning call, because the connection is released for its whole duration
    (CLAUDE.md: never hold `get_conn()` across an external call). Without this
    second check, a promotion that lands mid-call leaves the run's output --
    an assistant turn, its proposals, a rebuilt Understanding -- written
    against a premise the System has already moved past, and nothing
    afterwards can tell that it happened.

    Call it at the TOP of the write transaction, so the check and the write
    are the same atomic act.
    """
    session = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (token.session_id, token.system_id),
    ).fetchone()
    if session is None:
        return "session_premise_changed_during_run"
    if (
        _column(session, "base_understanding_revision_id") != token.base_revision_id
        or _column(session, "base_premise_digest") != token.base_premise_digest
        or _column(session, "premise_tracking_version") != token.premise_tracking_version
        or _disposition(session) != token.disposition
    ):
        return "session_premise_changed_during_run"
    head = load_head(conn, token.system_id)
    head_revision_id = head.revision_id if head else None
    head_version = head.head_version if head else None
    if head_revision_id != token.head_revision_id or head_version != token.head_version:
        return "head_moved_during_run"
    return None


def revalidation_message(code: str) -> str:
    return REVALIDATION_MESSAGES[code]


# --- Audit --------------------------------------------------------------------


def record_event(
    conn,
    *,
    system_id: int,
    event_kind: str,
    session_id: Optional[int] = None,
    revision_id: Optional[int] = None,
    previous_revision_id: Optional[int] = None,
    related_session_id: Optional[int] = None,
    head_version: Optional[int] = None,
    expected_head_revision_id: Optional[int] = None,
    expected_head_version: Optional[int] = None,
    rejection_code: Optional[str] = None,
    premise_state: Optional[str] = None,
    actor: str = "",
    actor_user_id: Optional[int] = None,
    note: str = "",
    now: Optional[float] = None,
) -> int:
    """Append one audit row. Every head move and disposition passes through here."""
    if event_kind not in CANONICAL_EVENT_KINDS:
        raise CanonicalUnderstandingError(f"unknown event kind: {event_kind}")
    now = time.time() if now is None else now
    cur = conn.execute(
        """INSERT INTO understanding_canonical_event
            (system_id, event_kind, session_id, revision_id, previous_revision_id,
             related_session_id, head_version, expected_head_revision_id,
             expected_head_version, rejection_code, premise_state,
             decision_method, actor, actor_user_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)""",
        (
            system_id, event_kind, session_id, revision_id, previous_revision_id,
            related_session_id, head_version, expected_head_revision_id,
            expected_head_version, rejection_code, premise_state, actor,
            actor_user_id, note, now,
        ),
    )
    return cur.lastrowid


# --- Promotion (compare-and-swap) --------------------------------------------


def promote_revision(
    conn,
    *,
    system_id: int,
    session_id: int,
    revision_id: int,
    expected_head_revision_id: Optional[int],
    expected_head_version: Optional[int],
    actor: str,
    actor_user_id: Optional[int] = None,
    note: str = "",
    now: Optional[float] = None,
) -> PromotionResult:
    """Promote one candidate revision to the System's canonical head.

    The caller MUST already hold a write transaction (``BEGIN IMMEDIATE``):
    the head pointer, the revision statuses, the session's new baseline and
    the audit row are one atomic decision, and a partial apply would leave the
    System pointing at a revision nothing else agrees with.

    Three independent guards, each with its own rejection code because each
    has a different next action:

    - the promoting session's premise must be ``current``.  This is what stops
      a candidate derived from an older head from overwriting a newer one --
      the exact last-write-wins this Issue exists to prevent.
    - the caller's expected head (id and/or version) must still be the actual
      head.  The developer confirmed a specific 「これで確定する」; if the head
      moved between reading and confirming, that confirmation was about
      something else.
    - the revision must be a candidate of this session with real content.
    """
    now = time.time() if now is None else now

    session = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if session is None:
        raise PromotionConflict("revision_not_found", "セッションが見つかりません。")

    verdict = evaluate_session_premise(conn, session)
    head = load_head(conn, system_id)
    if verdict.state != "current":
        raise PromotionConflict(
            "premise_not_current",
            verdict.message,
            head_revision_id=head.revision_id if head else None,
            head_version=head.head_version if head else None,
        )

    revision = conn.execute(
        """SELECT * FROM understanding_revision
           WHERE id = ? AND system_id = ? AND session_id = ?""",
        (revision_id, system_id, session_id),
    ).fetchone()
    if revision is None:
        raise PromotionConflict(
            "revision_not_found",
            "このセッションに、そのリビジョンはありません。",
            head_revision_id=head.revision_id if head else None,
            head_version=head.head_version if head else None,
        )
    status = _column(revision, "status") or "candidate"
    if status != "candidate":
        raise PromotionConflict(
            "revision_not_candidate",
            f"このリビジョンは候補ではありません（status={status}）。",
            head_revision_id=head.revision_id if head else None,
            head_version=head.head_version if head else None,
        )
    understanding = _loads(revision["current_understanding"])
    if not isinstance(understanding, dict) or not any(
        understanding.get(section) for section in PREMISE_SECTIONS
    ):
        raise PromotionConflict(
            "revision_empty",
            "内容のない Understanding は正準にできません。",
            head_revision_id=head.revision_id if head else None,
            head_version=head.head_version if head else None,
        )

    actual_head_revision_id = head.revision_id if head else None
    actual_head_version = head.head_version if head else 0
    if expected_head_revision_id != actual_head_revision_id:
        raise PromotionConflict(
            "head_revision_mismatch",
            "正準 Understanding が更新されています。再評価してください。",
            head_revision_id=actual_head_revision_id,
            head_version=actual_head_version,
        )
    if expected_head_version is not None and expected_head_version != actual_head_version:
        raise PromotionConflict(
            "head_version_mismatch",
            "正準 Understanding が更新されています。再評価してください。",
            head_revision_id=actual_head_revision_id,
            head_version=actual_head_version,
        )

    # The Intent the CURRENT canonical premise carries is the base this
    # promotion builds on, so a confirmed System-level statement survives a
    # generation that never restated it.
    inherited_intent: List[Dict[str, Any]] = []
    if head is not None:
        head_bundle = load_bundle(load_revision(conn, system_id, head.revision_id))
        if head_bundle is not None:
            inherited_intent = [dict(item) for item in head_bundle.intent_items]
    bundle = build_premise_bundle(
        conn, system_id, revision, inherited_intent=inherited_intent
    )
    premise_digest = bundle.digest

    conn.execute(
        """UPDATE understanding_revision
           SET status = 'canonical',
               parent_revision_id = ?,
               content_digest = ?,
               premise_digest = ?,
               premise_json = ?,
               confirmed_by = ?,
               confirmed_at = ?
           WHERE id = ? AND system_id = ?""",
        (
            actual_head_revision_id,
            bundle.content_digest,
            premise_digest,
            bundle.to_json(),
            actor,
            now,
            revision_id,
            system_id,
        ),
    )
    if actual_head_revision_id is not None:
        conn.execute(
            """UPDATE understanding_revision SET status = 'superseded'
               WHERE id = ? AND system_id = ?""",
            (actual_head_revision_id, system_id),
        )

    new_version = actual_head_version + 1
    if head is None:
        conn.execute(
            """INSERT INTO system_understanding_head
                (system_id, revision_id, head_version, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(system_id) DO UPDATE SET
                revision_id = excluded.revision_id,
                head_version = excluded.head_version,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by""",
            (system_id, revision_id, new_version, now, actor),
        )
    else:
        # Compare-and-swap at the storage layer too: even with the read above,
        # the UPDATE only applies while the head is still what we checked.
        updated = conn.execute(
            """UPDATE system_understanding_head
               SET revision_id = ?, head_version = ?, updated_at = ?, updated_by = ?
               WHERE system_id = ? AND head_version = ? AND revision_id = ?""",
            (
                revision_id, new_version, now, actor,
                system_id, actual_head_version, actual_head_revision_id,
            ),
        ).rowcount
        if not updated:
            raise PromotionConflict(
                "head_version_mismatch",
                "正準 Understanding が更新されています。再評価してください。",
                head_revision_id=actual_head_revision_id,
                head_version=actual_head_version,
            )

    # ONLY the outcome is recorded. The promoting session's own premise is NOT
    # advanced to the revision it just produced.
    #
    # Advancing it looked convenient -- the session stayed continuable instead
    # of immediately reading `stale` -- but it rewrote the ground the whole
    # conversation already happened on. Every existing message was reasoned
    # against the OLD premise while the session would then claim it had
    # started from the new one, and the next turn would mix the two. "Continue
    # only when the premise is the same" cannot survive silently changing the
    # premise.
    #
    # The session therefore reads `stale` with its own reason code
    # (`promoted_by_this_session`), which is the truth: what it was based on is
    # no longer the head, BECAUSE this conversation settled it. Continuing is a
    # rebase -- one explicit click, on a new session whose premise really is
    # the promoted revision.
    conn.execute(
        """UPDATE interview_session
           SET result_understanding_revision_id = ?
           WHERE id = ? AND system_id = ?""",
        (revision_id, session_id, system_id),
    )

    record_event(
        conn,
        system_id=system_id,
        event_kind="head_initialized" if head is None else "promoted",
        session_id=session_id,
        revision_id=revision_id,
        previous_revision_id=actual_head_revision_id,
        head_version=new_version,
        expected_head_revision_id=expected_head_revision_id,
        expected_head_version=expected_head_version,
        premise_state="current",
        actor=actor,
        actor_user_id=actor_user_id,
        note=note,
        now=now,
    )

    return PromotionResult(
        revision_id=revision_id,
        previous_revision_id=actual_head_revision_id,
        head_version=new_version,
        premise_digest=premise_digest,
        content_digest=bundle.content_digest,
        promoted_at=now,
        promoted_by=actor,
    )


# --- Explicit dispositions: rebase / branch -----------------------------------


def rebase_session(
    conn,
    *,
    system_id: int,
    session_id: int,
    actor: str,
    actor_user_id: Optional[int] = None,
    note: str = "",
    now: Optional[float] = None,
) -> int:
    """Continue this line of inquiry on the CURRENT head, as a new session.

    A rebase never edits the original conversation or its premise (#464
    non-goal 2).  It records a new history node: a fresh session pinned to
    today's canonical premise, carrying the original's title/focus/snapshot
    and a link back to it, while the original is marked ``rebased`` so it is
    no longer offered as the place to keep talking.

    Returns the new session id.  Caller owns the transaction.
    """
    now = time.time() if now is None else now
    session = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if session is None:
        raise CanonicalUnderstandingError("セッションが見つかりません。")

    title = session["title"] or ""
    cur = conn.execute(
        """INSERT INTO interview_session
            (system_id, snapshot_id, title, focus, status, stage,
             premise_origin_kind, premise_origin_session_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'open', 'understanding_initialized', 'rebase', ?, ?, ?)""",
        (
            system_id,
            session["snapshot_id"],
            title,
            session["focus"] or "",
            session_id,
            now,
            now,
        ),
    )
    new_session_id = cur.lastrowid
    capture_session_premise(
        conn, session_id=new_session_id, system_id=system_id, now=now
    )
    conn.execute(
        """UPDATE interview_session
           SET premise_disposition = 'rebased', updated_at = ?
           WHERE id = ? AND system_id = ?""",
        (now, session_id, system_id),
    )
    record_event(
        conn,
        system_id=system_id,
        event_kind="session_rebased",
        session_id=session_id,
        related_session_id=new_session_id,
        premise_state="stale",
        actor=actor,
        actor_user_id=actor_user_id,
        note=note,
        now=now,
    )
    return new_session_id


def branch_session(
    conn,
    *,
    system_id: int,
    session_id: int,
    actor: str,
    actor_user_id: Optional[int] = None,
    note: str = "",
    now: Optional[float] = None,
) -> None:
    """Keep this conversation explicitly as an OLD-premise exploration.

    The premise verdict still reads ``stale`` -- nothing about the System
    changed back.  What changes is that the developer has acknowledged it, so
    the conversation may continue.  Its candidate can still never be promoted:
    promotion requires ``current``, which is how acceptance condition 9 holds
    even with several open sessions.
    """
    now = time.time() if now is None else now
    verdict_state = None
    session = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if session is None:
        raise CanonicalUnderstandingError("セッションが見つかりません。")
    verdict_state = evaluate_session_premise(conn, session).state
    conn.execute(
        """UPDATE interview_session
           SET premise_disposition = 'branched', updated_at = ?
           WHERE id = ? AND system_id = ?""",
        (now, session_id, system_id),
    )
    record_event(
        conn,
        system_id=system_id,
        event_kind="session_branched",
        session_id=session_id,
        premise_state=verdict_state,
        actor=actor,
        actor_user_id=actor_user_id,
        note=note,
        now=now,
    )


def adopt_current_premise(
    conn,
    *,
    system_id: int,
    session_id: int,
    actor: str,
    actor_user_id: Optional[int] = None,
    note: str = "",
    now: Optional[float] = None,
) -> PremiseBundle:
    """Re-pin a legacy (``invalid``) session onto the current head, explicitly.

    The migration deliberately leaves pre-#464 sessions ``legacy-unbased``
    rather than guessing which revision they started from.  This is the
    developer's explicit 「この会話は今の正準 Understanding を前提にする」 --
    recorded as a rebase event on the same session, never applied implicitly
    at read time.
    """
    now = time.time() if now is None else now
    bundle = capture_session_premise(
        conn, session_id=session_id, system_id=system_id, now=now
    )
    head = load_head(conn, system_id)
    record_event(
        conn,
        system_id=system_id,
        event_kind="session_rebased",
        session_id=session_id,
        revision_id=head.revision_id if head else None,
        head_version=head.head_version if head else None,
        premise_state="current",
        actor=actor,
        actor_user_id=actor_user_id,
        note=note or "legacy session adopted the current canonical premise",
        now=now,
    )
    return bundle


# --- Observability ------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalMetrics:
    system_id: int
    head_revision_id: Optional[int]
    head_version: Optional[int]
    head_updated_at: Optional[float]
    session_premise_counts: Dict[str, int]
    disposition_counts: Dict[str, int]
    event_counts: Dict[str, int]
    promotion_conflict_codes: Dict[str, int]
    candidate_revision_count: int


def build_metrics(conn, system_id: int) -> CanonicalMetrics:
    """Derived, never stored: premise mismatch, stale, and promotion conflicts.

    Acceptance condition 「ログまたはメトリクスから premise 不一致、stale 判定、
    昇格競合を観測できる」.  The premise counts are recomputed from the
    persisted digests on every read (#338's rule: a derived event stream
    cannot drift from the rows it describes), while the conflict counts come
    from the append-only event table, because a refused promotion is an action
    somebody took, not a state anybody can re-derive later.
    """
    head = load_head(conn, system_id)
    premise_counts = {state: 0 for state in PREMISE_STATES}
    disposition_counts = {value: 0 for value in PREMISE_DISPOSITIONS}
    rows = conn.execute(
        "SELECT * FROM interview_session WHERE system_id = ?", (system_id,)
    ).fetchall()
    for row in rows:
        verdict = evaluate_session_premise(conn, row)
        premise_counts[verdict.state] += 1
        disposition_counts[verdict.disposition] += 1

    event_counts = {kind: 0 for kind in CANONICAL_EVENT_KINDS}
    conflict_codes: Dict[str, int] = {}
    for row in conn.execute(
        """SELECT event_kind, rejection_code FROM understanding_canonical_event
           WHERE system_id = ?""",
        (system_id,),
    ).fetchall():
        kind = row["event_kind"]
        if kind in event_counts:
            event_counts[kind] += 1
        if kind == "promotion_conflict" and row["rejection_code"]:
            conflict_codes[row["rejection_code"]] = (
                conflict_codes.get(row["rejection_code"], 0) + 1
            )

    candidates = conn.execute(
        """SELECT COUNT(*) AS n FROM understanding_revision
           WHERE system_id = ? AND COALESCE(status, 'candidate') = 'candidate'""",
        (system_id,),
    ).fetchone()

    return CanonicalMetrics(
        system_id=system_id,
        head_revision_id=head.revision_id if head else None,
        head_version=head.head_version if head else None,
        head_updated_at=head.updated_at if head else None,
        session_premise_counts=premise_counts,
        disposition_counts=disposition_counts,
        event_counts=event_counts,
        promotion_conflict_codes=conflict_codes,
        candidate_revision_count=int(candidates["n"] if candidates else 0),
    )


# --- Retention guard ----------------------------------------------------------

#: Revisions the per-session retention sweep must never delete. Written as a
#: SQL fragment so every pruning site shares one definition.
#:
#: Deliberately NOT "anything referenced as a parent": inside a session every
#: revision but the newest is the parent of the next one, so that clause would
#: protect the entire history and switch retention off. What must survive is
#: the CANONICAL lineage and anything a session's premise points at -- a
#: pruned head is a System that cannot say what it understands, and a pruned
#: baseline turns a healthy session into `missing` for no reason. The promoted
#: chain is already fully covered by `status`, because each promotion marks
#: the previous head `superseded`.
#:
#: Takes three ``system_id`` parameters, in this order.
PROTECTED_REVISION_SQL = """
    id IN (
        SELECT revision_id FROM system_understanding_head WHERE system_id = ?
    )
    OR COALESCE(status, 'candidate') IN ('canonical', 'superseded')
    OR id IN (
        SELECT base_understanding_revision_id FROM interview_session
        WHERE system_id = ? AND base_understanding_revision_id IS NOT NULL
    )
    OR id IN (
        SELECT result_understanding_revision_id FROM interview_session
        WHERE system_id = ? AND result_understanding_revision_id IS NOT NULL
    )
"""
