"""Alignment Review / Review Queue API (Issue #287).

Contrasts confirmed/proposed Intent Brief items (Issue #284,
``interview_intent_item``) against the evidence-backed Current System
understanding (Issue #136, the latest ``understanding_revision``) and
produces "alignment items": one row per contrast point, each with a
DETERMINISTIC review classification (``review_category`` / ``reason_code``,
see ``app/alignment.py``'s rule table -- classification itself is never a
reasoning decision, Principle 6). Only ``review_category IN (must_review,
batch_reviewable)``, further restricted to non-terminal, non-superseded rows
(``status NOT IN (answered, corrected)`` and ``superseded = 0``), ever
surfaces as an action-required Review Queue card; the rest
(``no_review_required`` / ``unchanged`` / ``informational``, plus any
answered/corrected/superseded row) are collapsed/informational/history.

Kept in its own module (like Issues #284/#285) rather than growing
``routes/interview.py`` further, per CLAUDE.md's guidance for this
sub-issue.

Rebuild-merge (POST .../alignment/build): a build DELETEs and recreates only
rows with ``status='open' AND user_decision IS NULL`` for the session --
untouched suggestions with no user progress. Any row with a different status
(``answered``/``corrected``/``held``/``inquiry``) or a recorded
``user_decision`` is always kept, regardless of how the base revision
changed (Principle 2 -- a rebuild must never lose a human decision). Of the
kept rows, a rebuild also marks surviving TERMINAL rows
(``answered``/``corrected``) ``superseded=1`` so the fresh replacement row
for the same contrast point is distinguishable from stale history;
``held``/``inquiry`` rows are never marked superseded (still in-flight).

Inquiry integration (Issue #285's ``origin_kind='review_item'``) lives in
``routes/interview_inquiry.py``: opening an Inquiry on an alignment item sets
its ``status='inquiry'``; the Inquiry closing (resolved/unresolved/cancelled)
sets it back to ``'open'`` -- never ``'answered'``, so the developer must
still explicitly call one of this module's answer/correct/hold endpoints
(Principle 2).

probe-agent:
  role: API boundary for the Alignment Review / Review Queue (Intent vs
    Current System contrast, deterministic review classification)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify a rebuild never deletes/overwrites an alignment_item with user progress, that the review-queue endpoint only ever returns must_review/batch_reviewable items in deterministic order, and that build fails closed (no rows written) when every proposed item's evidence fails snapshot validation.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..alignment import (
    AlignmentProposalResult,
    classify_alignment_item,
    compute_content_hash,
    compute_intent_item_digest,
    generate_alignment_proposal,
    review_sort_key,
    user_reason_for,
    validate_evidence_against_snapshot,
)
from ..auth import get_system_id
from ..db import get_conn
from ..interview_language import get_interview_language
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    AlignmentAnswerRequest,
    AlignmentBatchAnswerItemResult,
    AlignmentBatchAnswerOut,
    AlignmentBatchAnswerRequest,
    AlignmentBuildOut,
    AlignmentCorrectRequest,
    AlignmentEvidenceOut,
    AlignmentItemOut,
    AlignmentListOut,
    AlignmentReviewQueueOut,
    AlignmentUserDecisionOut,
)
from ..runtime_alignment import compare_claim_to_runtime, resolve_component_for_evidence
from ..runtime_match_judge import (
    PROMPT_VERSION as RUNTIME_MATCH_PROMPT_VERSION,
    SCHEMA_VERSION as RUNTIME_MATCH_SCHEMA_VERSION,
    RuntimeMatchJudgeInputItem,
    judge_runtime_match,
)
from ..runtime_reality import aggregate_component_facts, build_provenance

router = APIRouter()

# Only these two categories ever surface as action-required Review Queue
# cards (Principle 6 -- a fixed finite subset, not a heuristic filter).
_ACTIONABLE_CATEGORIES = ("must_review", "batch_reviewable")


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_item_or_404(conn, item_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?",
        (item_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Alignment item not found")
    return row


def _item_out(row) -> AlignmentItemOut:
    evidence = json.loads(row["current_evidence"]) if row["current_evidence"] else []
    risk_flags = json.loads(row["risk_flags"]) if row["risk_flags"] else []
    user_decision = json.loads(row["user_decision"]) if row["user_decision"] else None
    return AlignmentItemOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        revision_id=row["revision_id"],
        snapshot_id=row["snapshot_id"],
        intent_item_id=row["intent_item_id"],
        intent_summary=row["intent_summary"],
        current_claim=row["current_claim"],
        current_evidence=[AlignmentEvidenceOut(**e) for e in evidence],
        gap_summary=row["gap_summary"],
        proposed_interpretation=row["proposed_interpretation"],
        alignment_state=row["alignment_state"],
        risk_flags=risk_flags,
        confidence=row["confidence"],
        review_category=row["review_category"],
        reason_code=row["reason_code"],
        user_reason=row["user_reason"],
        runtime_check=row["runtime_check"] if "runtime_check" in row.keys() else None,
        status=row["status"],
        user_decision=AlignmentUserDecisionOut(**user_decision) if user_decision else None,
        handoff_id=row["handoff_id"] if "handoff_id" in row.keys() else None,
        superseded=bool(row["superseded"]) if "superseded" in row.keys() else False,
        content_hash=row["content_hash"] if "content_hash" in row.keys() else None,
        carried_over_from=row["carried_over_from"] if "carried_over_from" in row.keys() else None,
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _sorted_items(rows) -> List[AlignmentItemOut]:
    items = [_item_out(r) for r in rows]
    items.sort(key=lambda it: review_sort_key(
        review_category=it.review_category, reason_code=it.reason_code, item_id=it.id,
    ))
    return items


# --- Runtime Match Judge (Issue #290 Finding 5, Part 2) ------------------------


def _run_runtime_match_judge(
    conn, system_id: int, snapshot_id: int, config: LLMConfig, client, final_items: List[dict],
) -> None:
    """Semantic match/mismatch judge over items whose baseline is 'match'.

    Mutates ``final_items`` in place, replacing each eligible item's
    ``runtime_check`` with the judge's verdict (or ``None`` on judge
    failure -- never falling back to the deterministic 'match' baseline it
    was pre-filtered on). Stale/unobserved/environment-mismatch/no-mapping
    items are untouched (never eligible in the first place).

    Records exactly one ``intelligence_runs`` row
    (``run_type='runtime_match'``) when there is at least one eligible item;
    skips the LLM call and writes no row when there are none. ``client`` is
    the SAME client already created for the alignment proposal above -- by
    the time this runs, ``proposal.error is None`` already proved it is a
    configured reasoning model (a mock/non-reasoning provider would have
    failed the proposal step closed before any item reached this point).
    """
    eligible = [(i, it) for i, it in enumerate(final_items) if it.get("_judge_ctx") is not None]
    if not eligible:
        return

    started_at = time.time()
    try:
        language = get_interview_language()
    except ValueError as exc:
        judge_error: Optional[str] = str(exc)
        judge_items: Optional[list] = None
    else:
        judge_input_items = [
            RuntimeMatchJudgeInputItem(
                index=i,
                claim=it["current_claim"],
                component_id=it["_judge_ctx"]["component_id"],
                call_count=it["_judge_ctx"]["call_count"],
                error_rate=it["_judge_ctx"]["error_rate"],
                duration_p50_ms=it["_judge_ctx"]["duration_p50_ms"],
                duration_p90_ms=it["_judge_ctx"]["duration_p90_ms"],
                duration_p99_ms=it["_judge_ctx"]["duration_p99_ms"],
                freshness=it["_judge_ctx"]["freshness"],
                environment=it["_judge_ctx"]["environment"],
            )
            for i, it in eligible
        ]
        judge_result = judge_runtime_match(client, config, judge_input_items, language=language)
        judge_error = judge_result.error
        judge_items = judge_result.items if judge_result.error is None else None

    judge_status = "failed" if judge_error else "completed"
    conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'runtime_match', ?, ?, ?, ?, 'reasoning_llm', ?, ?, 0, ?, ?)
        """,
        (
            system_id, snapshot_id, config.provider, config.model,
            RUNTIME_MATCH_PROMPT_VERSION, RUNTIME_MATCH_SCHEMA_VERSION, judge_status,
            judge_error, started_at, time.time(),
        ),
    )

    if judge_items is not None:
        by_index = {r.index: r.runtime_check for r in judge_items}
        for i, it in eligible:
            it["runtime_check"] = by_index.get(i)
    else:
        # Fail-closed: no semantic determination -- never guess, never fall
        # back to the deterministic 'match' baseline.
        for _i, it in eligible:
            it["runtime_check"] = None


# --- Build ---------------------------------------------------------------------


def run_alignment_build(conn, session_id: int, system_id: int) -> AlignmentBuildOut:
    """Build alignment items contrasting Intent Brief vs Current System.

    Core of ``POST .../alignment/build`` (below), extracted so the automatic
    refresh job (``app/interview_refresh.py``, Issue #288) can rebuild
    Alignment on the exact same code path right after Understanding is
    rebuilt, instead of duplicating this orchestration.

    Requires at least one ``understanding_revision`` for this session (409
    otherwise -- build/refresh System Understanding first; no reasoning call
    is attempted and no ``intelligence_runs`` row is created for this
    precondition, mirroring how a missing session/item is a plain 404
    elsewhere in this API surface). Once a reasoning attempt is made, every
    outcome (success or failure) is recorded as an ``intelligence_runs``
    row (``run_type='alignment_build'``).

    Fail-closed (Principle 6): LLM/schema failures, or every proposed item's
    evidence failing snapshot validation, both raise ``HTTPException`` (502)
    with no ``alignment_item`` rows written or replaced. See module
    docstring for the rebuild-merge rule. Callers that want to treat a
    failure as non-fatal (the refresh job does, for the Alignment step
    specifically) must catch ``HTTPException`` themselves.

    Once the proposal succeeds, ``_run_runtime_match_judge`` (Issue #290
    Finding 5, Part 2) runs as a SEPARATE reasoning step over just the items
    whose deterministic runtime baseline is 'match', recorded in its own
    ``intelligence_runs`` row (``run_type='runtime_match'``); a judge
    failure never fails this whole build (those items just persist
    ``runtime_check=NULL``) since the Alignment proposal itself already
    succeeded.
    """
    now = time.time()
    _get_session_or_404(conn, session_id, system_id)

    revision = conn.execute(
        """SELECT * FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()
    if revision is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No understanding revision found for this session; "
                "build/refresh System Understanding first."
            ),
        )

    snapshot_row = conn.execute(
        "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
        (revision["snapshot_id"], system_id),
    ).fetchone()
    if snapshot_row is None:
        raise HTTPException(
            status_code=409,
            detail="The pinned snapshot for this session's latest understanding revision no longer exists.",
        )

    # Issue #290: the System's own declared environment (may be '') is the
    # only "expected environment" compare_claim_to_runtime is ever given --
    # never inferred from claim text.
    system_row = conn.execute(
        "SELECT environment FROM systems WHERE id = ?", (system_id,),
    ).fetchone()
    expected_environment = (
        system_row["environment"] if system_row and system_row["environment"] else None
    )

    intent_rows = conn.execute(
        """SELECT * FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
           ORDER BY field, id""",
        (session_id, system_id),
    ).fetchall()
    intent_payload = [
        {
            "id": r["id"], "field": r["field"], "value_text": r["value_text"],
            "status": r["status"],
        }
        for r in intent_rows
    ]
    # Deterministic FK resolution (Principle 6): the reasoning model only
    # proposes a *field name*; the concrete interview_intent_item id is
    # resolved here by an exact structural match against the current
    # (non-superseded) row for that field -- never a fuzzy/text match.
    intent_item_id_by_field: Dict[str, int] = {}
    for r in intent_rows:
        intent_item_id_by_field.setdefault(r["field"], r["id"])
    # 2nd review round (PR #296, Finding 1): a fresh digest of every current
    # (non-superseded) intent row's own field/value_text/status, computed
    # from THIS build's DB read -- fed into compute_content_hash below so a
    # content_hash also reflects the linked Intent Brief entity's current
    # content, not just the field name it belongs to.
    intent_digest_by_id: Dict[int, str] = {
        r["id"]: compute_intent_item_digest(field=r["field"], value_text=r["value_text"], status=r["status"])
        for r in intent_rows
    }

    current_understanding = (
        json.loads(revision["current_understanding"]) if revision["current_understanding"] else None
    )
    gap_analysis = json.loads(revision["gap_analysis"]) if revision["gap_analysis"] else None

    config = LLMConfig.intelligence_from_env()
    client_error: Optional[str] = None
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        client = None
        client_error = str(exc)

    if client_error is not None:
        proposal = AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            error=client_error,
        )
    else:
        proposal = generate_alignment_proposal(
            client, config,
            intent_items=intent_payload,
            current_understanding=current_understanding,
            gap_analysis=gap_analysis,
        )

    completed_at = time.time()
    final_items: List[dict] = []
    if proposal.error is None:
        line_count_cache: Dict[str, Optional[int]] = {}
        had_raw_items = len(proposal.items) > 0
        for item in proposal.items:
            valid_evidence, _pruned = validate_evidence_against_snapshot(
                snapshot_row["repo_path"], snapshot_row["commit_sha"],
                item.evidence, line_count_cache,
            )
            if not valid_evidence:
                # Dropped, not fatal on its own -- see the "fail only if
                # none valid" rule below.
                continue

            # Issue #290: deterministic evidence -> component_id -> runtime
            # facts -> finite match state. None whenever no deterministic
            # mapping exists (ambiguous or no code_symbols component_id
            # match) -- never guessed. Finding 5: provenance now comes only
            # from what was actually observed on traces (never the pinned
            # snapshot/commit).
            runtime_check: Optional[str] = None
            judge_ctx: Optional[Dict[str, object]] = None
            component_id = resolve_component_for_evidence(
                conn, revision["snapshot_id"], valid_evidence,
            )
            if component_id is not None:
                fact = aggregate_component_facts(conn, system_id, component_id)
                provenance = build_provenance(fact, conn=conn, system_id=system_id)
                runtime_check = compare_claim_to_runtime(
                    item.current_claim, fact, provenance,
                    expected_environment=expected_environment,
                )
                if runtime_check == "match":
                    # Finding 5 Part 2: only a fresh, structurally-clean
                    # baseline is eligible for the semantic judge below --
                    # stale/unobserved/environment-mismatch guards are never
                    # second-guessed by the model (Principle 6).
                    judge_ctx = {
                        "component_id": component_id,
                        "call_count": fact.call_count,
                        "error_rate": fact.error_rate,
                        "duration_p50_ms": fact.duration_p50_ms,
                        "duration_p90_ms": fact.duration_p90_ms,
                        "duration_p99_ms": fact.duration_p99_ms,
                        "freshness": provenance.freshness,
                        "environment": provenance.environment,
                    }

            final_items.append({
                "intent_item_id": intent_item_id_by_field.get(item.intent_field)
                    if item.intent_field else None,
                "intent_summary": item.intent_ref_hint,
                "current_claim": item.current_claim,
                "current_evidence": [
                    {"path": e.path, "start_line": e.start_line, "end_line": e.end_line, "summary": e.summary}
                    for e in valid_evidence
                ],
                "gap_summary": item.gap_summary,
                "proposed_interpretation": item.proposed_interpretation,
                "alignment_state": item.alignment_state,
                "risk_flags": item.risk_flags,
                "confidence": item.confidence,
                "intent_field": item.intent_field,
                "runtime_check": runtime_check,
                "_judge_ctx": judge_ctx,
            })
        if had_raw_items and not final_items:
            proposal = AlignmentProposalResult(
                provider=proposal.provider, model=proposal.model, is_mock=proposal.is_mock,
                error="Every proposed alignment item's evidence failed snapshot validation",
            )

        if proposal.error is None:
            _run_runtime_match_judge(conn, system_id, revision["snapshot_id"], config, client, final_items)

            # Issue #295: unchanged-item carry-over. carry_candidates maps a
            # content_hash from the immediately preceding build to the id of
            # the ORIGINAL human-decided (answered/corrected) row for that
            # contrast point -- the only kind of row a fresh item's audit
            # trail may ever point back to.
            #
            # Review fix (PR #296, Finding 2): a prior build's 'unchanged'
            # row (status='open', since it was never itself answered) is now
            # ALSO an eligible candidate, not just terminal answered/
            # corrected rows -- otherwise a 3rd build's carry-over candidate
            # set loses the 2nd build's unchanged row (an 'open' row this
            # rebuild's DELETE ... WHERE status='open' AND user_decision IS
            # NULL is about to remove) and the item incorrectly falls back
            # to fresh reclassification. When the candidate is itself an
            # 'unchanged' row, its OWN carried_over_from (never its own id)
            # is propagated as the origin -- so the audit trail always
            # terminates at the real human decision, no matter how many
            # unchanged generations sit in between. This is exactly why the
            # DELETE above is safe against `carried_over_from ... ON DELETE
            # SET NULL`: the referenced origin id is always an
            # answered/corrected row, which the DELETE's WHERE clause never
            # touches (it only ever deletes status='open' rows), so the FK
            # target never disappears out from under a surviving reference.
            #
            # Read before this build's own DELETE/UPDATE/INSERT touch the
            # table (the write transaction is still below).
            carry_rows = conn.execute(
                """SELECT id, content_hash, carried_over_from, review_category
                   FROM alignment_item
                   WHERE session_id = ? AND system_id = ?
                     AND superseded = 0
                     AND content_hash IS NOT NULL
                     AND (
                       status IN ('answered', 'corrected')
                       OR (review_category = 'unchanged' AND carried_over_from IS NOT NULL)
                     )
                   ORDER BY id""",
                (session_id, system_id),
            ).fetchall()
            carry_candidates: Dict[str, int] = {}
            for r in carry_rows:
                origin_id = (
                    r["carried_over_from"] if r["review_category"] == "unchanged" else r["id"]
                )
                carry_candidates.setdefault(r["content_hash"], origin_id)

            # A rebuild triggered by a batch that confirmed/corrected/
            # declined ANY Intent Brief field never carries anything over.
            #
            # 2nd review round (PR #296, Finding 1): originally this guard
            # covered only the 'goal' field (the rule table's own
            # core_intent special-case). But goal is not the only Intent
            # field a per-item content_hash cannot see through: an item may
            # be linked to (or contrast against) 'constraints' /
            # 'success_criteria' / any other field, and intent_item_id /
            # linked_intent_digest above only catch a change for items that
            # are THEMSELVES linked to the changed field -- an unlinked item
            # whose claim happens to be affected by a different field's
            # change (e.g. a new constraint narrows what "aligned" means for
            # an unrelated claim) would still slip through on hash match
            # alone. So this guard is generalized: ANY confirmed/
            # not_applicable Intent Brief field decided/updated since the
            # immediately preceding build blocks carry-over for the WHOLE
            # build, not just goal-linked items -- the conservative, safe
            # direction (Issue #295 brief). goal remains covered as one case
            # of this general rule.
            #
            # interview_refresh.py's trigger_kind alone cannot tell us this:
            # a batch's later triggers can be swallowed into an
            # already-'pending' job created by an earlier, different
            # trigger_kind (see interview_refresh._enqueue's dedupe), so the
            # job that actually runs may carry a trigger_kind of
            # 'qa_answer' even though the same batch also confirmed an
            # intent field. Instead, compare every current (non-superseded,
            # decided) intent item's updated_at against the immediately
            # preceding build's timestamp -- every row from one build shares
            # exactly one created_at (the build's completed_at, see the
            # INSERT below), so MAX(created_at) over existing rows is
            # exactly that moment.
            #
            # Core Capability (part of understanding_revision's reasoning-
            # model output, not a separately-confirmed row like Intent Brief
            # fields) is deliberately NOT included here: unlike
            # interview_intent_item, there is no per-Core-Capability
            # confirmed/updated_at column to compare deterministically
            # against a previous build's timestamp (only
            # system_purpose_confirmations exists, and only for System
            # Purpose, not the Core Capability list) -- adding one is a
            # persistence/schema change out of this fix's scope. This is a
            # known, reported gap, not a silent omission (see this PR's
            # report).
            previous_build_row = conn.execute(
                "SELECT MAX(created_at) AS ts FROM alignment_item WHERE session_id = ? AND system_id = ?",
                (session_id, system_id),
            ).fetchone()
            previous_build_at = previous_build_row["ts"] if previous_build_row else None
            changed_intent_row = None
            if previous_build_at is not None:
                changed_intent_row = conn.execute(
                    """SELECT 1 FROM interview_intent_item
                       WHERE session_id = ? AND system_id = ?
                         AND superseded_by_id IS NULL AND status IN ('confirmed', 'not_applicable')
                         AND updated_at > ?
                       LIMIT 1""",
                    (session_id, system_id, previous_build_at),
                ).fetchone()
            intent_changed_since_last_build = bool(
                previous_build_at is not None and changed_intent_row is not None
            )

            # Review fix (PR #296, Finding 1): source_digest_cache amortizes
            # the per-evidence-item pinned-commit reads compute_content_hash
            # now performs (one full-file read per distinct path across every
            # item in this build, not per evidence citation).
            source_digest_cache: Dict[str, Optional[List[str]]] = {}
            for it in final_items:
                linked_intent_item_id = it["intent_item_id"]
                it["content_hash"] = compute_content_hash(
                    current_claim=it["current_claim"],
                    current_evidence=it["current_evidence"],
                    alignment_state=it["alignment_state"],
                    risk_flags=it["risk_flags"],
                    confidence=it["confidence"],
                    intent_field=it["intent_field"],
                    runtime_check=it["runtime_check"],
                    intent_summary=it["intent_summary"],
                    gap_summary=it["gap_summary"],
                    proposed_interpretation=it["proposed_interpretation"],
                    intent_item_id=linked_intent_item_id,
                    linked_intent_digest=(
                        intent_digest_by_id.get(linked_intent_item_id)
                        if linked_intent_item_id is not None else None
                    ),
                    repo_path=snapshot_row["repo_path"],
                    commit_sha=snapshot_row["commit_sha"],
                    source_digest_cache=source_digest_cache,
                )
                carried_from = (
                    None if intent_changed_since_last_build
                    else carry_candidates.get(it["content_hash"])
                )
                if carried_from is not None:
                    it["review_category"] = "unchanged"
                    it["reason_code"] = "unchanged_since_confirmation"
                    it["carried_over_from"] = carried_from
                else:
                    review_category, reason_code = classify_alignment_item(
                        alignment_state=it["alignment_state"],
                        risk_flags=it["risk_flags"],
                        confidence=it["confidence"],
                        intent_field=it["intent_field"],
                        runtime_check=it["runtime_check"],
                    )
                    it["review_category"] = review_category
                    it["reason_code"] = reason_code
                    it["carried_over_from"] = None

    run_status = "failed" if proposal.error else "completed"
    run_cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'alignment_build', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
        """,
        (
            system_id, revision["snapshot_id"], proposal.provider, proposal.model,
            proposal.prompt_version, proposal.schema_version, run_status, proposal.error,
            1 if proposal.is_mock else 0, now, completed_at,
        ),
    )
    run_id = run_cur.lastrowid

    if proposal.error:
        raise HTTPException(status_code=502, detail=proposal.error)

    conn.execute("BEGIN")
    try:
        # Rebuild-merge (Principle 2): only rows with no user progress
        # are ever replaced.
        conn.execute(
            """DELETE FROM alignment_item
               WHERE session_id = ? AND system_id = ?
                 AND status = 'open' AND user_decision IS NULL""",
            (session_id, system_id),
        )
        # Finding 4 fix: surviving TERMINAL rows (answered/corrected) become
        # history the moment a fresh row for the same contrast point is
        # about to be inserted. GET .../review-queue also filters on
        # status/superseded directly (belt and braces), but marking these
        # rows here is what lets a full-listing view (GET .../alignment)
        # tell a stale answered/corrected row apart from a current one.
        # held/inquiry rows are intentionally NOT marked superseded -- they
        # are still in-flight and stay the current row (Principle 2's
        # rebuild-must-never-lose-progress rule already preserves them
        # untouched; this only adds the superseded label to the terminal
        # ones).
        conn.execute(
            """UPDATE alignment_item
               SET superseded = 1
               WHERE session_id = ? AND system_id = ?
                 AND status IN ('answered', 'corrected') AND superseded = 0""",
            (session_id, system_id),
        )
        for it in final_items:
            reason_code = it["reason_code"]
            conn.execute(
                """INSERT INTO alignment_item
                    (session_id, system_id, revision_id, snapshot_id, intent_item_id,
                     intent_summary, current_claim, current_evidence, gap_summary,
                     proposed_interpretation, alignment_state, risk_flags, confidence,
                     review_category, reason_code, user_reason, runtime_check, status,
                     user_decision, content_hash, carried_over_from,
                     intelligence_run_id, is_mock, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, system_id, revision["id"], revision["snapshot_id"],
                    it["intent_item_id"], it["intent_summary"], it["current_claim"],
                    json.dumps(it["current_evidence"], ensure_ascii=False),
                    it["gap_summary"], it["proposed_interpretation"], it["alignment_state"],
                    json.dumps(it["risk_flags"]), it["confidence"], it["review_category"],
                    reason_code, user_reason_for(reason_code), it["runtime_check"],
                    it["content_hash"], it["carried_over_from"],
                    run_id, 1 if proposal.is_mock else 0, completed_at, completed_at,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    rows = conn.execute(
        "SELECT * FROM alignment_item WHERE session_id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchall()
    return AlignmentBuildOut(
        session_id=session_id,
        system_id=system_id,
        revision_id=revision["id"],
        intelligence_run_id=run_id,
        is_mock=proposal.is_mock,
        items=_sorted_items(rows),
    )


@router.post(
    "/interview/sessions/{session_id}/alignment/build",
    response_model=AlignmentBuildOut,
)
def build_alignment_items(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentBuildOut:
    """API boundary for ``run_alignment_build`` -- see its docstring."""
    with get_conn() as conn:
        return run_alignment_build(conn, session_id, system_id)


# --- Read ------------------------------------------------------------------


@router.get(
    "/interview/sessions/{session_id}/alignment",
    response_model=AlignmentListOut,
)
def list_alignment_items(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentListOut:
    """Full listing, split into current rows (``items_by_category``/``counts``)
    and history (``superseded_items``).

    Review fix (PR #296, Finding 3): a ``superseded=1`` row is audit history,
    not a live Review Queue signal -- before this fix it was folded into
    ``items_by_category``/``counts`` alongside current rows, so e.g. an
    already-answered-then-superseded ``must_review`` item could inflate
    "要確認 N件" even though the Review Queue itself (which already filters
    ``superseded = 0``, see ``get_review_queue`` above) was empty. History
    stays fully visible via the new additive ``superseded_items`` field --
    nothing is dropped from the response, just moved out of the counts that
    drive the "do I need to act" signal.

    2nd review round (PR #296, Finding 3): ``counts`` still folds in
    current-but-terminal rows (``status IN ('answered', 'corrected')``,
    ``superseded=0``) -- e.g. right after answering a ``must_review`` item
    and before the next rebuild marks it ``superseded=1``, ``counts.
    must_review`` still counts it even though ``GET .../review-queue``
    (which additionally filters ``status NOT IN ('answered', 'corrected')``)
    no longer does, so the two views disagree for that window. ``counts`` is
    left as-is (still "how many current rows of each category exist", for
    backward compatibility); the new additive ``outstanding_counts`` applies
    the EXACT SAME predicate ``get_review_queue`` uses (``superseded = 0 AND
    status NOT IN ('answered', 'corrected')``) to every category
    consistently, so a dashboard that wants "how many of these still need
    action" always matches the Review Queue's own count.
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            "SELECT * FROM alignment_item WHERE session_id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchall()
        current_rows = [r for r in rows if not (("superseded" in r.keys()) and r["superseded"])]
        superseded_rows = [r for r in rows if ("superseded" in r.keys()) and r["superseded"]]
        items = _sorted_items(current_rows)
        categories = ("must_review", "batch_reviewable", "no_review_required", "unchanged", "informational")
        items_by_category: Dict[str, List[AlignmentItemOut]] = {c: [] for c in categories}
        counts: Dict[str, int] = {c: 0 for c in categories}
        outstanding_counts: Dict[str, int] = {c: 0 for c in categories}
        for item in items:
            items_by_category.setdefault(item.review_category, []).append(item)
            counts[item.review_category] = counts.get(item.review_category, 0) + 1
            # Same predicate as get_review_queue below (superseded=0, already
            # true for every row in `items` here, AND status not terminal),
            # applied uniformly across all five categories -- not just the
            # two actionable ones -- per this fix's brief.
            if item.status not in ("answered", "corrected"):
                outstanding_counts[item.review_category] = outstanding_counts.get(item.review_category, 0) + 1
        return AlignmentListOut(
            session_id=session_id, system_id=system_id,
            items_by_category=items_by_category, counts=counts,
            outstanding_counts=outstanding_counts,
            superseded_items=_sorted_items(superseded_rows),
        )


@router.get(
    "/interview/sessions/{session_id}/review-queue",
    response_model=AlignmentReviewQueueOut,
)
def get_review_queue(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentReviewQueueOut:
    """Only action-required items (must_review + batch_reviewable), ordered
    deterministically by category rank, then reason-code rank, then id.

    Finding 4 fix: a terminal-status row (answered/corrected) is history,
    not an action card, even though its review_category was must_review/
    batch_reviewable at creation time -- so it is excluded explicitly here,
    not just left to whatever status the dashboard happens to filter on.
    ``superseded = 0`` is belt-and-braces on top of that: today's flows
    already ensure a superseded row is always terminal-status too, but a
    superseded row must never surface as an action card regardless.
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        placeholders = ",".join("?" for _ in _ACTIONABLE_CATEGORIES)
        rows = conn.execute(
            f"""SELECT * FROM alignment_item
                WHERE session_id = ? AND system_id = ? AND review_category IN ({placeholders})
                  AND status NOT IN ('answered', 'corrected') AND superseded = 0""",
            (session_id, system_id, *_ACTIONABLE_CATEGORIES),
        ).fetchall()
        return AlignmentReviewQueueOut(
            session_id=session_id, system_id=system_id, items=_sorted_items(rows),
        )


# --- Manual decisions --------------------------------------------------------


def _reject_if_inquiry_locked(item) -> None:
    if item["status"] == "inquiry":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "alignment_item_inquiry_open",
                "message": "This item has an open Inquiry; resolve it before answering.",
            },
        )


def _reject_if_superseded(item) -> None:
    """2nd review round (PR #296, Finding 2): the single-item answer/correct/
    hold endpoints had the same history-overwrite hole the batch endpoint
    did -- an already-superseded row (a later rebuild already produced a
    fresh replacement row for the same contrast point) could still be
    answered directly by its stale id (e.g. a stale dashboard tab, or a
    direct API call using an id captured before a rebuild). ``superseded``
    is additive (older DB rows may lack the column), so absence of the
    column is treated as "not superseded" -- same convention as
    ``_item_out``'s ``superseded`` field default.
    """
    if ("superseded" in item.keys()) and item["superseded"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "alignment_item_superseded",
                "message": "この項目は履歴化されています。最新のビルド結果の項目を確認してください。",
            },
        )


def _write_answer_decision(
    conn, item_id: int, *, decision: str, note: Optional[str], now: float,
) -> AlignmentItemOut:
    """Shared write path for a single alignment_item 'answer' decision.

    Used by both the single-item endpoint below and the batch endpoint
    (PR #296 review fix, Finding 5) so the two never drift -- validation
    (item exists / session ownership / not Inquiry-locked) is the caller's
    job, since the single endpoint fails the whole request on a bad item
    (404/409) while the batch endpoint must record a per-item error and
    keep going instead.
    """
    decision_payload = {
        "action": decision, "note": note, "decided_at": now, "decided_by": None,
    }
    conn.execute(
        """UPDATE alignment_item
           SET status = 'answered', user_decision = ?, updated_at = ?
           WHERE id = ?""",
        (json.dumps(decision_payload, ensure_ascii=False), now, item_id),
    )
    row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
    return _item_out(row)


@router.post(
    "/interview/alignment/{item_id}/answer",
    response_model=AlignmentItemOut,
)
def answer_alignment_item(
    item_id: int,
    payload: AlignmentAnswerRequest,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    """Manual decision on an alignment item (Principle 2 -- never automatic).

    Server never auto-sets ``user_decision`` -- this endpoint (plus
    ``/correct`` and ``/hold`` below, and the batch endpoint further down)
    is the only write path for it.
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        _reject_if_superseded(item)
        result = _write_answer_decision(
            conn, item_id, decision=payload.decision, note=payload.note, now=now,
        )

    # Issue #288: refresh Understanding/Alignment/Review Queue now that the
    # decision is durably committed (see interview.answer_interview_qa's
    # comment for why this call sits outside the `with get_conn()` block).
    from ..interview_refresh import request_refresh

    request_refresh(result.session_id, system_id, "alignment_answer")
    return result


@router.post(
    "/interview/alignment/{item_id}/correct",
    response_model=AlignmentItemOut,
)
def correct_alignment_item(
    item_id: int,
    payload: AlignmentCorrectRequest,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    """Record the developer's own corrected interpretation.

    Unlike Intent Brief's /correct, this does not create a new revision row
    -- the alignment item itself stays the audit record, with the
    correction stored as ``user_decision.note`` (the LLM's own
    ``proposed_interpretation`` is left untouched alongside it for
    comparison).
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        _reject_if_superseded(item)
        decision = {
            "action": "corrected", "note": payload.corrected_interpretation,
            "decided_at": now, "decided_by": None,
        }
        conn.execute(
            """UPDATE alignment_item
               SET status = 'corrected', user_decision = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(decision, ensure_ascii=False), now, item_id),
        )
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        result = _item_out(row)

    # Issue #288: see answer_alignment_item's comment above.
    from ..interview_refresh import request_refresh

    request_refresh(result.session_id, system_id, "alignment_answer")
    return result


@router.post(
    "/interview/alignment/{item_id}/hold",
    response_model=AlignmentItemOut,
)
def hold_alignment_item(
    item_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        _reject_if_superseded(item)
        decision = {"action": "held", "note": None, "decided_at": now, "decided_by": None}
        conn.execute(
            """UPDATE alignment_item
               SET status = 'held', user_decision = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(decision, ensure_ascii=False), now, item_id),
        )
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        return _item_out(row)


# --- Batch answer (PR #296 review fix, Finding 5) ----------------------------


# 2nd review round (PR #296, Finding 2): a batch entry could previously
# overwrite an item that had gone stale/locked/out-of-scope by the time the
# batch was actually submitted -- staged answers built from an earlier
# snapshot of the Review Queue could land on a row a different tab, an
# auto-refresh (Issue #288), or another reviewer had since answered,
# superseded, or reclassified out of the actionable set. Every new rejection
# below is a per-item error (batch continues), matching the existing
# not-found/wrong-session/Inquiry-locked pattern. Messages for the NEW checks
# are Japanese (state_messages.py's convention for user-facing copy); the
# pre-existing English messages above are left as-is (not part of this fix).
_BATCH_ERROR_SUPERSEDED = "この項目は履歴化されています。最新のビルド結果の項目を確認してください。"
_BATCH_ERROR_NOT_ACTIONABLE = (
    "この項目は一括回答の対象外です(要確認・一括レビュー可以外は個別に確認してください)。"
)
# 'inquiry' is rejected earlier with its own specific message; the remaining
# terminal/non-answerable statuses ('answered', 'corrected') land here.
_BATCH_ERROR_NOT_ANSWERABLE_STATUS = "この項目はすでに回答済みのため、一括回答の対象外です。"
_BATCH_ERROR_STALE_CONTENT = "項目が更新されています。最新の内容を確認してください。"


def _validate_answer_target_for_batch(
    conn, item_id: int, session_id: int, system_id: int, *, expected_content_hash: Optional[str] = None,
):
    """Non-raising equivalent of ``_get_item_or_404`` + ``_reject_if_inquiry_locked``
    (+ this fix's history/scope/staleness checks) for the batch endpoint: a
    bad item must produce a per-item error, never abort the whole batch.
    Returns ``(row, error)`` -- exactly one is ``None``.

    Checks, in order: not found / wrong session (existing) -> Inquiry-locked
    (existing) -> superseded (Finding 2: a later rebuild already replaced
    this row) -> non-answerable status (Finding 2: only 'open'/'held' may be
    answered via this path -- 'answered'/'corrected' are terminal and
    'inquiry' is handled above) -> non-actionable review_category (Finding
    2: only must_review/batch_reviewable are ever meant to be batch-answered
    -- no_review_required/unchanged/informational rows have no action to
    take) -> stale content_hash (Finding 2: only enforced when the caller
    supplies ``expected_content_hash``, so omitting it stays fully backward
    compatible).
    """
    row = conn.execute(
        "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?",
        (item_id, system_id),
    ).fetchone()
    if row is None:
        return None, "Alignment item not found"
    if row["session_id"] != session_id:
        return None, "Alignment item does not belong to this session"
    if row["status"] == "inquiry":
        return None, "This item has an open Inquiry; resolve it before answering."
    if ("superseded" in row.keys()) and row["superseded"]:
        return None, _BATCH_ERROR_SUPERSEDED
    if row["status"] not in ("open", "held"):
        return None, _BATCH_ERROR_NOT_ANSWERABLE_STATUS
    if row["review_category"] not in _ACTIONABLE_CATEGORIES:
        return None, _BATCH_ERROR_NOT_ACTIONABLE
    if expected_content_hash is not None and row["content_hash"] != expected_content_hash:
        return None, _BATCH_ERROR_STALE_CONTENT
    return row, None


@router.post(
    "/interview/sessions/{session_id}/alignment/answers-batch",
    response_model=AlignmentBatchAnswerOut,
)
def answer_alignment_items_batch(
    session_id: int,
    payload: AlignmentBatchAnswerRequest,
    system_id: int = Depends(get_system_id),
) -> AlignmentBatchAnswerOut:
    """Answer several alignment_item rows in one call, one refresh total.

    Fixes Finding 5: the developer clearing N Review Queue cards at once
    previously triggered up to N (or, with de-duplication, up to 2) separate
    Issue #288 ``request_refresh`` calls -- one full Understanding/Alignment/
    Review Queue rebuild is conceptually enough for one batch of decisions.
    This endpoint reuses ``_write_answer_decision`` (the exact single-item
    write path, so ``decision_method: manual`` provenance is identical per
    item) and calls ``request_refresh`` exactly once, only after this
    ``with get_conn()`` block has committed at least one item (autocommit
    connection -- see ``app/db.py``'s ``isolation_level=None``), never inside
    it (same ordering rule as ``answer_alignment_item`` above).

    Partial failure is fail-closed PER ITEM, never per batch: a bad item_id
    (not found / belongs to another session / Inquiry-locked / superseded /
    non-answerable status / non-actionable category / stale content_hash --
    2nd review round, Finding 2) or a duplicate item_id within the same
    batch records a per-item error and the rest of the batch still proceeds.
    If every item fails, no row changes and no refresh is triggered.

    Each entry may optionally carry ``content_hash`` (Finding 2): when
    given, it must match the item's CURRENT ``content_hash`` column or the
    entry is rejected as stale (the item changed since the client staged
    this answer). Omitting it keeps the pre-fix behavior unchanged.
    """
    now = time.time()
    results: List[AlignmentBatchAnswerItemResult] = []
    any_saved = False
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        seen_item_ids: set = set()
        for entry in payload.answers:
            if entry.item_id in seen_item_ids:
                results.append(AlignmentBatchAnswerItemResult(
                    item_id=entry.item_id, success=False,
                    error="Duplicate item_id in this batch",
                ))
                continue
            seen_item_ids.add(entry.item_id)

            _row, error = _validate_answer_target_for_batch(
                conn, entry.item_id, session_id, system_id,
                expected_content_hash=entry.content_hash,
            )
            if error is not None:
                results.append(AlignmentBatchAnswerItemResult(
                    item_id=entry.item_id, success=False, error=error,
                ))
                continue

            item_out = _write_answer_decision(
                conn, entry.item_id, decision=entry.decision, note=entry.note, now=now,
            )
            results.append(AlignmentBatchAnswerItemResult(
                item_id=entry.item_id, success=True, item=item_out,
            ))
            any_saved = True

    if any_saved:
        # Issue #288: see answer_alignment_item's comment above -- called
        # exactly once for the whole batch, outside the connection block.
        from ..interview_refresh import request_refresh

        request_refresh(session_id, system_id, "alignment_answer")

    return AlignmentBatchAnswerOut(
        session_id=session_id, system_id=system_id, results=results, refreshed=any_saved,
    )
