"""Issue #459: the single, server-owned "全体 next_action" projection for the
Gap discussion workflow (docs/01-specifications/ux/decision-discussion-workflow.md §3, DD-UX-01).

This module owns exactly one thing: the PRIORITY ORDER that turns several
already-decided facts -- read from #444/#453/#456/#458/#455/#452's own owner
modules, never re-derived -- into ONE primary action for the whole
Gap-discussion screen. It never re-decides a domain lifecycle, a JU outcome,
or a Proposal item's eligibility; those stay exactly where #337/#338/#349/
#455 already put them. It adds no persistence of its own.

`evaluate_gap_discussion_next_action` is pure (dataclass in, dataclass out) so
it can be unit-tested directly without a database -- the same discipline
`app/interview_workflow.py` (#349) and `app/overview_projection.py` (#380)
already use for their own first-match tables. The Dashboard renders whatever
this returns and MUST NOT reimplement the priority order itself (DD-UX-01:
"clientは同じ判定表を本番コードへ複製しない").

Priority order (Issue #459 Decisions, verbatim): 対象/権限エラー -> 必要根拠
のstale/取得失敗 -> 処理中 -> 保存結果不明 -> 未保存編集 -> 提案レビュー ->
調査結果 -> 仮説レビュー -> 照合. Rows 1/2/4/5/9 are always determinable from
data this module's caller reads directly on every request (the thread's own
resolved target state, its self-context read, and two client-reported
ephemeral flags -- see below); rows 3/6/7/8 depend on reading the Proposal/
Joint-Understanding tables, and a failure reading THOSE must not block an
earlier or later row that IS determinable (DD-INT: "任意sectionの失敗だけで
無関係な操作まで止めない。理由と復旧を併記する") -- such a row is skipped and
recorded in `degraded_sections` instead of forcing the whole projection to a
generic "unavailable" (unlike `overview_projection.decide_next_action`, which
intentionally fails the WHOLE table closed on an unreadable earlier fact: here
the fact groups are independent of each other, so the failure mode differs on
purpose).

Rows 4 ("save_unknown") and 5 ("unsaved_edit") are the two rows this module
cannot determine on its own: a lost HTTP response to an already-committed
save, and an open form's own dirty state, are facts only the CLIENT currently
holds (the existing `useIdempotentSaveRequest` / `useDirtyGuard` machinery).
The caller passes them in as plain booleans; this does not reintroduce a
client-side judgement table -- the PRIORITY ORDER these two facts are slotted
into still lives here, and only here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

#: The finite `kind` vocabulary, in the exact priority order the Issue #459
#: Decisions specify. Mirrored as `models.GapDiscussionNextActionKind`
#: (Python Literal), `src/api/types.ts`'s `GapDiscussionNextActionKind`, and
#: `shared/schemas/assistant_discussion.schema.json`'s
#: `gap_discussion_next_action_kind` -- keep all four in sync (see
#: `tests/test_discussion_contract_parity.py`).
NEXT_ACTION_KINDS: Tuple[str, ...] = (
    "target_error",
    "evidence_stale",
    "processing",
    "save_unknown",
    "unsaved_edit",
    "review_proposal",
    "investigation_result",
    "review_hypothesis",
    "match",
)


@dataclass(frozen=True)
class GapDiscussionFacts:
    """Every field defaults to "nothing pending" -- the conservative value
    for a projection whose fallback (`match`) is itself always safe to
    surface (unlike `overview_projection.NextActionFacts`, whose fallback
    is a step the developer has not earned yet)."""

    # 1: target / permission error. Always determinable from the thread's own
    # `assistant_discussion.evaluate_target_state` result -- `unresolvable`
    # means the target was deleted or is not visible to this System/actor.
    target_error: bool = False
    target_error_reason: str = ""

    # 2: required evidence stale / fetch failure. Always determinable from
    # data already read fresh on every request (the same target_state, plus
    # the root's own single-adapter context read) -- never the full related
    # bundle, which #458 already owns and this module must not duplicate.
    bundle_stale: bool = False
    bundle_has_unavailable_section: bool = False
    bundle_unavailable_reason: str = ""

    # 3: processing -- an already-promoted hypothesis's Joint Understanding
    # session is still open with no findings to review yet.
    investigation_running: bool = False

    # 4: save result unknown. Client-reported only (see module docstring).
    save_result_unknown: bool = False
    save_reference_id: Optional[str] = None

    # 5: unsaved edit. Client-reported only (see module docstring).
    has_unsaved_ui_draft: bool = False

    # 6: proposal review -- this thread has a Proposal with at least one
    # item still `status == "proposed"` (not yet applied/rejected).
    open_proposal_exists: bool = False
    open_proposal_id: Optional[int] = None

    # 7: investigation result -- a promoted hypothesis's Joint Understanding
    # session has a `current`-premise finding eligible for reflux
    # (`joint_understanding.can_reflux`), ready to fold into a new Proposal.
    investigation_result_ready: bool = False
    ju_session_id: Optional[int] = None

    # 8: hypothesis review -- this thread has a Proposal hypothesis still
    # `status == "proposed"` (not yet promoted or rejected).
    hypothesis_pending_promotion: bool = False
    hypothesis_id: Optional[int] = None

    # --- Availability of the two DB-backed fact groups above (rows 3/6/7/8) --
    # A `False` here means "could not read this group just now", which SKIPS
    # only the rows that depend on it -- never the whole projection.
    proposal_status_available: bool = True
    ju_status_available: bool = True


@dataclass(frozen=True)
class NextActionResult:
    kind: str  # one of NEXT_ACTION_KINDS
    reason: str
    target_ref: Optional[str] = None
    #: Which DB-backed fact groups could not be read for THIS evaluation
    #: (`"proposal"` / `"investigation"`), so the UI can show "理由と復旧を
    #: 併記する" for those specific sections while still acting on whatever
    #: row DID match.
    degraded_sections: Tuple[str, ...] = ()


def evaluate_gap_discussion_next_action(facts: GapDiscussionFacts) -> NextActionResult:
    """The Issue #459 first-match table. Returns exactly one action -- the
    fallback (`match`) is always reachable, so this never returns a generic
    "unavailable" the way `overview_projection.decide_next_action` can: every
    row here is either always determinable or independently skippable."""
    degraded: list[str] = []

    # 1: target / permission error.
    if facts.target_error:
        return NextActionResult(
            kind="target_error",
            reason=facts.target_error_reason or "対象が見つからないか、参照する権限がありません。",
        )

    # 2: required evidence stale / fetch failure.
    if facts.bundle_stale:
        return NextActionResult(
            kind="evidence_stale",
            reason="起点の参照が更新されています。最新の内容で再照合してください。",
        )
    if facts.bundle_has_unavailable_section:
        return NextActionResult(
            kind="evidence_stale",
            reason=facts.bundle_unavailable_reason or "一部の根拠を取得できませんでした。再取得してください。",
        )

    # 3: processing.
    if not facts.ju_status_available:
        degraded.append("investigation")
    elif facts.investigation_running:
        return NextActionResult(
            kind="processing",
            reason="調査が進行中です。結果が出るまで開いて確認できます。",
            target_ref=str(facts.ju_session_id) if facts.ju_session_id else None,
            degraded_sections=tuple(degraded),
        )

    # 4: save result unknown.
    if facts.save_result_unknown:
        return NextActionResult(
            kind="save_unknown",
            reason="保存結果を確認できませんでした。結果を照会してから再試行してください。",
            target_ref=facts.save_reference_id,
            degraded_sections=tuple(degraded),
        )

    # 5: unsaved edit.
    if facts.has_unsaved_ui_draft:
        return NextActionResult(
            kind="unsaved_edit",
            reason="未保存の編集があります。保存または破棄してください。",
            degraded_sections=tuple(degraded),
        )

    # 6: proposal review.
    if not facts.proposal_status_available:
        degraded.append("proposal")
    elif facts.open_proposal_exists:
        return NextActionResult(
            kind="review_proposal",
            reason="変更候補があります。項目ごとに確認してください。",
            target_ref=str(facts.open_proposal_id) if facts.open_proposal_id else None,
            degraded_sections=tuple(degraded),
        )

    # 7: investigation result.
    if facts.ju_status_available and facts.investigation_result_ready:
        return NextActionResult(
            kind="investigation_result",
            reason="調査結果があります。変更候補を整理できます。",
            target_ref=str(facts.ju_session_id) if facts.ju_session_id else None,
            degraded_sections=tuple(degraded),
        )

    # 8: hypothesis review.
    if facts.proposal_status_available and facts.hypothesis_pending_promotion:
        return NextActionResult(
            kind="review_hypothesis",
            reason="調査へ送る仮説の候補があります。競合説明・反証条件を確認してください。",
            target_ref=str(facts.hypothesis_id) if facts.hypothesis_id else None,
            degraded_sections=tuple(degraded),
        )

    # 9: match -- the always-safe default (docs/01-specifications/ux/decision-discussion-workflow.md §3's
    # 「対象選択」/「照合結果」 stages): nothing pending elsewhere means the
    # next productive step is to (re)run or review the target/UX/機能 matching.
    return NextActionResult(
        kind="match",
        reason="目的・UX・機能を照合してください。",
        degraded_sections=tuple(degraded),
    )


# --- fact assembly -------------------------------------------------------------
#
# Reads existing owner tables directly (Proposal items/hypotheses,
# `assistant_discussion_hypothesis_promotion` + its Joint Understanding
# session) -- the SAME rows `routes/assistant.py`'s `list_discussion_proposals`
# / `get_discussion_joint_understanding` already expose, never a new
# "aggregate progress" table (Issue #459 Decisions: "別の総合進捗DBやclient
# 判定表を作らない"). Each fact GROUP is read inside its own try/except so a
# failure in one never prevents reading the other (see module docstring).


def gather_gap_discussion_facts(
    conn, system_id: int, thread_id: int, *,
    target_state: str,
    self_operation_state: str,
    self_unavailable_reason: str = "",
    has_unsaved_ui_draft: bool = False,
    save_result_unknown: bool = False,
    save_reference_id: Optional[str] = None,
) -> GapDiscussionFacts:
    from . import joint_understanding as joint_understanding_domain
    from .routes import joint_understanding as joint_understanding_routes

    open_proposal_id: Optional[int] = None
    hypothesis_pending_id: Optional[int] = None
    proposal_status_available = True
    try:
        proposal_rows = conn.execute(
            "SELECT id FROM assistant_discussion_proposal "
            "WHERE system_id = ? AND thread_id = ? ORDER BY id DESC",
            (system_id, thread_id),
        ).fetchall()
        for prow in proposal_rows:
            pid = prow["id"]
            if open_proposal_id is None:
                item_row = conn.execute(
                    "SELECT id FROM assistant_discussion_proposal_item "
                    "WHERE proposal_id = ? AND status = 'proposed' LIMIT 1",
                    (pid,),
                ).fetchone()
                if item_row is not None:
                    open_proposal_id = pid
            if hypothesis_pending_id is None:
                hyp_row = conn.execute(
                    "SELECT id FROM assistant_discussion_proposal_hypothesis "
                    "WHERE proposal_id = ? AND status = 'proposed' LIMIT 1",
                    (pid,),
                ).fetchone()
                if hyp_row is not None:
                    hypothesis_pending_id = hyp_row["id"]
            if open_proposal_id is not None and hypothesis_pending_id is not None:
                break
    except Exception:
        proposal_status_available = False

    investigation_running = False
    investigation_result_ready = False
    ju_session_id: Optional[int] = None
    ju_status_available = True
    try:
        promotions = conn.execute(
            "SELECT * FROM assistant_discussion_hypothesis_promotion "
            "WHERE thread_id = ? AND system_id = ? ORDER BY id DESC",
            (thread_id, system_id),
        ).fetchall()
        for promo in promotions:
            ju_row = conn.execute(
                "SELECT * FROM joint_understanding_session WHERE id = ? AND system_id = ?",
                (promo["joint_understanding_session_id"], system_id),
            ).fetchone()
            if ju_row is None:
                continue
            verdict = joint_understanding_routes._premise_verdict(conn, ju_row)  # noqa: SLF001
            findings = conn.execute(
                "SELECT * FROM joint_understanding_finding "
                "WHERE joint_understanding_id = ? ORDER BY id",
                (ju_row["id"],),
            ).fetchall()
            superseded = joint_understanding_routes._superseded_finding_ids(findings)  # noqa: SLF001
            has_current_findings = verdict.state == "current" and any(
                f["id"] not in superseded
                and joint_understanding_domain.can_reflux(f["origin_role"], f["claim_kind"])
                for f in findings
            )
            if has_current_findings:
                investigation_result_ready = True
                ju_session_id = ju_row["id"]
                break
            if ju_row["status"] == "open" and ju_session_id is None:
                investigation_running = True
                ju_session_id = ju_row["id"]
    except Exception:
        ju_status_available = False

    return GapDiscussionFacts(
        target_error=(target_state == "unresolvable"),
        bundle_stale=(target_state == "stale"),
        bundle_has_unavailable_section=(self_operation_state == "unavailable"),
        bundle_unavailable_reason=self_unavailable_reason,
        investigation_running=investigation_running,
        save_result_unknown=save_result_unknown,
        save_reference_id=save_reference_id,
        has_unsaved_ui_draft=has_unsaved_ui_draft,
        open_proposal_exists=open_proposal_id is not None,
        open_proposal_id=open_proposal_id,
        investigation_result_ready=investigation_result_ready,
        ju_session_id=ju_session_id,
        hypothesis_pending_promotion=hypothesis_pending_id is not None,
        hypothesis_id=hypothesis_pending_id,
        proposal_status_available=proposal_status_available,
        ju_status_available=ju_status_available,
    )
