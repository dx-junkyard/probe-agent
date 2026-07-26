"""Deterministic Interview UX metric aggregation (Issue #309).

Only persisted facts are used.  A metric is ``measured`` only when every
term in its fixed formula is available and its denominator is non-zero.
Missing lineage is not approximated: the affected metric is returned as
``unmeasured`` with a stable reason instead of a numeric zero.

This module performs no LLM calls and no network or external analytics I/O.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from typing import Optional, Sequence

from .models import InterviewMetricOut, InterviewMetricsOut


def _measured(
    *,
    key: str,
    category: str,
    guardrail: bool,
    description: str,
    formula: str,
    sources: Sequence[str],
    unit: str,
    numerator: int,
    denominator: int,
) -> InterviewMetricOut:
    if denominator <= 0:
        return _unmeasured(
            key=key,
            category=category,
            guardrail=guardrail,
            description=description,
            formula=formula,
            sources=sources,
            unit=unit,
            numerator=numerator,
            denominator=denominator,
            reason="no_observations",
        )
    return InterviewMetricOut(
        key=key,
        category=category,
        guardrail=guardrail,
        description=description,
        formula=formula,
        sources=list(sources),
        status="measured",
        value=round(numerator / denominator, 6),
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator,
        unmeasured_reason=None,
    )


def _unmeasured(
    *,
    key: str,
    category: str,
    guardrail: bool,
    description: str,
    formula: str,
    sources: Sequence[str],
    unit: str,
    reason: str,
    numerator: Optional[int] = None,
    denominator: Optional[int] = None,
) -> InterviewMetricOut:
    return InterviewMetricOut(
        key=key,
        category=category,
        guardrail=guardrail,
        description=description,
        formula=formula,
        sources=list(sources),
        status="unmeasured",
        value=None,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator or 0,
        unmeasured_reason=reason,
    )


def _ratio_metric_from_events(
    conn: sqlite3.Connection,
    system_id: int,
    *,
    key: str,
    category: str,
    guardrail: bool,
    description: str,
    formula: str,
    sources: Sequence[str],
    numerator_event: str,
    denominator_event: str,
) -> InterviewMetricOut:
    # Count semantic observation opportunities, not physical event rows.
    # Numerators are the subset with the same (session, target kind, target)
    # as a denominator fact. This keeps ratios bounded even for legacy rows
    # created before semantic idempotency/prerequisite enforcement.
    row = conn.execute(
        """WITH denominator_facts AS (
               SELECT DISTINCT session_id, target_kind, target_id
               FROM interview_metric_event
               WHERE system_id = ? AND event_type = ?
           ),
           numerator_facts AS (
               SELECT DISTINCT session_id, target_kind, target_id
               FROM interview_metric_event
               WHERE system_id = ? AND event_type = ?
           )
           SELECT COUNT(*) AS denominator_n,
                  COUNT(n.target_id) AS numerator_n
           FROM denominator_facts d
           LEFT JOIN numerator_facts n
             ON n.session_id = d.session_id
            AND n.target_kind = d.target_kind
            AND n.target_id = d.target_id""",
        (system_id, denominator_event, system_id, numerator_event),
    ).fetchone()
    numerator = int(row["numerator_n"])
    denominator = int(row["denominator_n"])
    return _measured(
        key=key,
        category=category,
        guardrail=guardrail,
        description=description,
        formula=formula,
        sources=sources,
        unit="ratio",
        numerator=numerator,
        denominator=denominator,
    )


def _origin_was_answered_after_resolution(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    origin_kind: str,
    origin_id: int,
    resolved_at: float,
) -> bool:
    if origin_kind == "qa":
        current_id: Optional[int] = origin_id
        seen: set[int] = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            row = conn.execute(
                """SELECT answered_at, superseded_by_id
                   FROM interview_qa
                   WHERE id = ? AND system_id = ?""",
                (current_id, system_id),
            ).fetchone()
            if row is None:
                return False
            if row["answered_at"] is not None and row["answered_at"] > resolved_at:
                return True
            current_id = row["superseded_by_id"]
        return False

    if origin_kind == "intent":
        current_id = origin_id
        seen = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            row = conn.execute(
                """SELECT status, updated_at, superseded_by_id
                   FROM interview_intent_item
                   WHERE id = ? AND system_id = ?""",
                (current_id, system_id),
            ).fetchone()
            if row is None:
                return False
            if row["status"] in ("confirmed", "undecided", "not_applicable") and (
                row["updated_at"] > resolved_at
            ):
                return True
            current_id = row["superseded_by_id"]
        return False

    if origin_kind == "review_item":
        row = conn.execute(
            """SELECT user_decision
               FROM alignment_item
               WHERE id = ? AND system_id = ?""",
            (origin_id, system_id),
        ).fetchone()
        if row is None or not row["user_decision"]:
            return False
        try:
            decision = json.loads(row["user_decision"])
        except (TypeError, ValueError):
            return False
        decided_at = decision.get("decided_at")
        return isinstance(decided_at, (int, float)) and decided_at > resolved_at

    return False


def _origin_return_metric(
    conn: sqlite3.Connection,
    system_id: int,
) -> InterviewMetricOut:
    resolved = conn.execute(
        """SELECT i.origin_kind, i.origin_id, MIN(t.created_at) AS resolved_at
           FROM interview_inquiry i
           JOIN interview_inquiry_transition t
             ON t.inquiry_id = i.id
            AND t.system_id = i.system_id
            AND t.to_status = 'resolved'
           WHERE i.system_id = ?
           GROUP BY i.id, i.origin_kind, i.origin_id""",
        (system_id,),
    ).fetchall()
    numerator = sum(
        1
        for row in resolved
        if _origin_was_answered_after_resolution(
            conn,
            system_id=system_id,
            origin_kind=row["origin_kind"],
            origin_id=row["origin_id"],
            resolved_at=row["resolved_at"],
        )
    )
    return _measured(
        key="post_inquiry_confirmation_rate",
        category="ux_quality",
        guardrail=False,
        description=(
            "疑問を resolved にした後、同じ起点項目に明示的な回答・確認が"
            "記録された割合。UI上で元カードへ復帰できたかは表さない。"
        ),
        formula="resolved inquiries with a later origin decision / resolved inquiries",
        sources=[
            "interview_inquiry_transition.to_status",
            "interview_qa.answered_at",
            "interview_intent_item.updated_at",
            "alignment_item.user_decision.decided_at",
        ],
        unit="ratio",
        numerator=numerator,
        denominator=len(resolved),
    )


def _implementation_question_transfer_metric(
    conn: sqlite3.Connection,
    system_id: int,
) -> InterviewMetricOut:
    denominator_row = conn.execute(
        """SELECT COUNT(*) AS n
           FROM interview_metric_event
           WHERE system_id = ? AND event_type = 'question_presented'
             AND target_kind = 'qa'""",
        (system_id,),
    ).fetchone()
    numerator_row = conn.execute(
        """SELECT COUNT(*) AS n
           FROM interview_metric_event e
           JOIN interview_qa q
             ON q.id = e.target_id
            AND q.system_id = e.system_id
            AND q.session_id = e.session_id
           WHERE e.system_id = ?
             AND e.event_type = 'question_presented'
             AND e.target_kind = 'qa'
             AND q.route_category = 'system_researchable'""",
        (system_id,),
    ).fetchone()
    return _measured(
        key="implementation_question_transfer_rate",
        category="ux_quality",
        guardrail=True,
        description=(
            "ユーザーに提示されたQ&Aのうち、Question Routerが"
            "system_researchable（実装から調査可能）と記録したものの割合。"
        ),
        formula=(
            "presented QA events whose QA route_category is system_researchable"
            " / all presented QA events"
        ),
        sources=[
            "interview_metric_event.question_presented",
            "interview_qa.route_category",
        ],
        unit="ratio",
        numerator=int(numerator_row["n"]),
        denominator=int(denominator_row["n"]),
    )


def build_interview_metrics(
    conn: sqlite3.Connection,
    system_id: int,
    *,
    generated_at: Optional[float] = None,
) -> InterviewMetricsOut:
    """Build the fixed v1 metric catalog for exactly one System."""

    session_count = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM interview_session WHERE system_id = ?",
            (system_id,),
        ).fetchone()["n"]
    )
    event_count = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM interview_metric_event WHERE system_id = ?",
            (system_id,),
        ).fetchone()["n"]
    )

    metrics: list[InterviewMetricOut] = []

    # There is no durable answer-id -> result_revision_id linkage. A global
    # answer/revision quotient would be an estimate and is intentionally
    # forbidden by the Issue #309 acceptance criteria.
    metrics.append(
        _unmeasured(
            key="answers_per_understanding_update",
            category="user_burden",
            guardrail=False,
            description="1回の理解更新へ実際に取り込まれた回答数。",
            formula="answer actions linked to update / completed understanding updates",
            sources=["interview_refresh_job (missing incorporated answer IDs)"],
            unit="answers_per_update",
            reason="answer_to_revision_lineage_not_recorded",
        )
    )

    unknown_row = conn.execute(
        """SELECT
               SUM(CASE WHEN answer_unknown = 1 THEN 1 ELSE 0 END) AS unknown_n,
               COUNT(*) AS total_n
           FROM interview_qa
           WHERE system_id = ?
             AND answered_at IS NOT NULL
             AND answer_unknown IS NOT NULL""",
        (system_id,),
    ).fetchone()
    metrics.append(
        _measured(
            key="unknown_answer_rate",
            category="user_burden",
            guardrail=False,
            description=(
                "unknown/known が明示保存されたQ&A回答操作における"
                "「わからない」選択率。判別不能な旧 revised 行は除外する。"
            ),
            formula="QA answer actions with answer_unknown=1 / QA answer actions with answer_unknown recorded",
            sources=["interview_qa.answer_unknown", "interview_qa.answered_at"],
            unit="ratio",
            numerator=int(unknown_row["unknown_n"] or 0),
            denominator=int(unknown_row["total_n"]),
        )
    )

    metrics.append(
        _ratio_metric_from_events(
            conn,
            system_id,
            key="review_abandonment_rate",
            category="user_burden",
            guardrail=False,
            description="明示的に開始記録されたレビューに対する明示的な途中離脱記録の割合。",
            formula="review_abandoned events / review_started events",
            sources=["interview_metric_event"],
            numerator_event="review_abandoned",
            denominator_event="review_started",
        )
    )
    metrics.append(
        _ratio_metric_from_events(
            conn,
            system_id,
            key="evidence_detail_expansion_rate",
            category="user_burden",
            guardrail=False,
            description="根拠詳細を展開可能として提示した機会のうち、実際に展開された割合。",
            formula="evidence_expanded events / evidence_available events",
            sources=["interview_metric_event"],
            numerator_event="evidence_expanded",
            denominator_event="evidence_available",
        )
    )

    inquiry_count = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM interview_inquiry WHERE system_id = ?",
            (system_id,),
        ).fetchone()["n"]
    )
    metrics.append(
        _unmeasured(
            key="operations_per_inquiry",
            category="user_burden",
            guardrail=False,
            description=(
                "疑問1件あたりの追加UI操作数。DBにはメッセージ・状態遷移"
                "しかなく、展開や画面復帰などを含む全操作数は復元できない。"
            ),
            formula="all additional UI operations / inquiries",
            sources=["missing complete UI operation event stream"],
            unit="operations_per_inquiry",
            reason="complete_ui_operation_stream_not_recorded",
        )
    )

    corrected_intent_row = conn.execute(
        """SELECT
               SUM(CASE WHEN superseded_by_id IS NOT NULL THEN 1 ELSE 0 END) AS corrected_n,
               COUNT(*) AS confirmed_n
           FROM interview_intent_item
           WHERE system_id = ?
             AND status = 'confirmed'
             AND decision_method = 'manual'""",
        (system_id,),
    ).fetchone()
    metrics.append(
        _measured(
            key="corrected_confirmed_intent_rate",
            category="accuracy",
            guardrail=False,
            description=(
                "人間確認済みIntent項目のうち、後続Intent行で訂正された割合。"
                "stableな superseded_by_id があるIntentに限定する。"
            ),
            formula="confirmed manual intent rows with superseded_by_id / confirmed manual intent rows",
            sources=[
                "interview_intent_item.status",
                "interview_intent_item.decision_method",
                "interview_intent_item.superseded_by_id",
            ],
            unit="ratio",
            numerator=int(corrected_intent_row["corrected_n"] or 0),
            denominator=int(corrected_intent_row["confirmed_n"]),
        )
    )
    metrics.append(
        _unmeasured(
            key="incorrect_answer_confirmation_rate",
            category="accuracy",
            guardrail=True,
            description=(
                "誤った回答を確定した割合。後日訂正だけでは意味的な誤りや"
                "因果を確定できないため、訂正率をこのguardrailへ流用しない。"
            ),
            formula="semantically incorrect confirmed answers / confirmed answers",
            sources=["missing semantic-error audit with stable confirmation identity"],
            unit="ratio",
            reason="semantic_error_outcome_not_recorded",
        )
    )

    runtime_row = conn.execute(
        """SELECT
               SUM(CASE WHEN runtime_check = 'mismatch' THEN 1 ELSE 0 END) AS mismatch_n,
               COUNT(*) AS observed_n
           FROM alignment_item
           WHERE system_id = ?
             AND runtime_check IN ('match', 'mismatch')
             AND superseded = 0
             AND is_mock = 0""",
        (system_id,),
    ).fetchone()
    metrics.append(
        _measured(
            key="runtime_contradiction_rate",
            category="accuracy",
            guardrail=False,
            description="比較可能なRuntime Reality Checkのうち mismatch が記録された割合。",
            formula="alignment items with runtime_check=mismatch / items with runtime_check in (match,mismatch)",
            sources=["alignment_item.runtime_check"],
            unit="ratio",
            numerator=int(runtime_row["mismatch_n"] or 0),
            denominator=int(runtime_row["observed_n"]),
        )
    )

    metrics.append(
        _unmeasured(
            key="understanding_revision_recorrection_rate",
            category="accuracy",
            guardrail=False,
            description="Understanding Revisionが後続Revisionで再修正された割合。",
            formula="revisions later corrected again / revisions eligible for correction",
            sources=["understanding_revision (missing field-level correction lineage)"],
            unit="ratio",
            reason="revision_correction_lineage_not_recorded",
        )
    )
    approved_count = int(
        conn.execute(
            """SELECT COUNT(DISTINCT proposal_id) AS n
               FROM interview_proposal_decision
               WHERE system_id = ? AND decision IN ('approved', 'edited')""",
            (system_id,),
        ).fetchone()["n"]
    )
    rejected_after_approval_count = int(
        conn.execute(
            """SELECT COUNT(DISTINCT approved.proposal_id) AS n
               FROM interview_proposal_decision approved
               JOIN interview_proposal_decision rejected
                 ON rejected.proposal_id = approved.proposal_id
                AND rejected.system_id = approved.system_id
                AND rejected.id > approved.id
                AND rejected.decision = 'rejected'
               WHERE approved.system_id = ?
                 AND approved.decision IN ('approved', 'edited')""",
            (system_id,),
        ).fetchone()["n"]
    )
    metrics.append(
        _measured(
            key="post_approval_rejection_rate",
            category="accuracy",
            guardrail=False,
            description="承認または編集承認を記録した提案のうち、後続履歴で却下された割合。",
            formula="proposals with a later rejected decision / proposals ever approved or edited",
            sources=["interview_proposal_decision.id", "interview_proposal_decision.decision"],
            unit="ratio",
            numerator=rejected_after_approval_count,
            denominator=approved_count,
        )
    )
    metrics.append(
        _unmeasured(
            key="post_approval_rollback_rate",
            category="accuracy",
            guardrail=False,
            description="承認済み提案が後からロールバックされた割合。",
            formula="approved proposals later rolled back / approved proposals",
            sources=["missing proposal rollback audit event"],
            unit="ratio",
            reason="proposal_rollback_not_recorded",
        )
    )
    metrics.append(
        _unmeasured(
            key="post_approval_rejection_or_rollback_rate",
            category="accuracy",
            guardrail=False,
            description=(
                "承認済み提案が後から却下またはロールバックされた割合。"
                "却下だけは別指標で計測できるが、合成指標はrollback監査が"
                "無いため未計測とする。"
            ),
            formula="approved proposals later rejected or rolled back / approved proposals",
            sources=[
                "interview_proposal_decision",
                "missing proposal rollback audit event",
            ],
            unit="ratio",
            reason="proposal_rollback_not_recorded",
        )
    )

    root_question_rows = conn.execute(
        """SELECT q.session_id, q.question_text
           FROM interview_qa q
           WHERE q.system_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM interview_qa previous
                 WHERE previous.system_id = q.system_id
                   AND previous.superseded_by_id = q.id
             )""",
        (system_id,),
    ).fetchall()
    question_keys = [
        (row["session_id"], row["question_text"])
        for row in root_question_rows
        if row["question_text"]
    ]
    counts = Counter(question_keys)
    repeated_occurrences = sum(count - 1 for count in counts.values() if count > 1)
    metrics.append(
        _measured(
            key="repeated_question_rate",
            category="ux_quality",
            guardrail=False,
            description=(
                "訂正チェーンを除くQ&A起点のうち、同一session内で完全に"
                "同一の question_text が再出現した割合。"
                "空白差・大小文字差・意味的類似は同一視しない。"
            ),
            formula="root question occurrences after the first identical text in the same session / root question occurrences",
            sources=["interview_qa.question_text", "interview_qa.superseded_by_id"],
            unit="ratio",
            numerator=repeated_occurrences,
            denominator=len(question_keys),
        )
    )
    metrics.append(
        _ratio_metric_from_events(
            conn,
            system_id,
            key="unchanged_item_reconfirmation_rate",
            category="ux_quality",
            guardrail=False,
            description="変更なし項目の提示記録のうち、ユーザーへ再確認を求めた記録の割合。",
            formula="unchanged_item_reconfirmed events / unchanged_item_presented events",
            sources=["interview_metric_event"],
            numerator_event="unchanged_item_reconfirmed",
            denominator_event="unchanged_item_presented",
        )
    )

    resolved_count = int(
        conn.execute(
            """SELECT COUNT(DISTINCT i.id) AS n
               FROM interview_inquiry i
               JOIN interview_inquiry_transition t
                 ON t.inquiry_id = i.id AND t.system_id = i.system_id
               WHERE i.system_id = ? AND t.to_status = 'resolved'""",
            (system_id,),
        ).fetchone()["n"]
    )
    metrics.append(
        _measured(
            key="inquiry_resolution_rate",
            category="ux_quality",
            guardrail=True,
            description="作成されたInquiryのうちユーザーが resolved へ遷移させた割合。",
            formula="inquiries with a resolved transition / inquiries",
            sources=["interview_inquiry", "interview_inquiry_transition.to_status"],
            unit="ratio",
            numerator=resolved_count,
            denominator=inquiry_count,
        )
    )
    metrics.append(_origin_return_metric(conn, system_id))
    metrics.append(_implementation_question_transfer_metric(conn, system_id))

    return InterviewMetricsOut(
        system_id=system_id,
        generated_at=generated_at if generated_at is not None else time.time(),
        sessions_observed=session_count,
        events_observed=event_count,
        metrics=metrics,
    )
