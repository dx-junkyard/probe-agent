"""Shared test helpers for Joint Understanding findings (Issue #337).

``POST /joint-understanding/{id}/findings`` is developer-only: an
``investigation`` or ``translation`` finding is written exclusively by its
internal producer (``/investigate``, ``/translate``), which validates every
citation against the pinned snapshot. Tests that need a *pre-existing*
investigation finding in order to exercise something else (reflux, outcome
basis, metrics) therefore insert it the way the producer does, rather than
going through an endpoint that -- correctly -- refuses it.

``insert_producer_finding`` writes exactly what ``_persist_loop`` /
``translate_joint_understanding`` write, including the Issue #337 provenance
columns, so a test fixture can never accidentally create a row shape the
production producers cannot.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

_PRODUCER_BY_ROLE = {
    "investigation": "investigation_loop",
    "translation": "translator",
}


def insert_producer_finding(
    *,
    ju_id: int,
    system_id: int,
    origin_role: str = "investigation",
    claim_kind: str = "fact",
    statement: str = "リトライは3回で打ち切られる",
    evidence: Optional[List[Dict[str, Any]]] = None,
    runtime_evidence: Optional[List[Dict[str, Any]]] = None,
    supports_finding_ids: Optional[List[int]] = None,
    competing_explanations: Optional[List[str]] = None,
    refutation_conditions: Optional[List[str]] = None,
    next_investigation: Optional[str] = None,
    uncertainty: str = "",
    supersedes_finding_id: Optional[int] = None,
    intelligence_run_id: Optional[int] = None,
    is_mock: bool = False,
    created_at: Optional[float] = None,
) -> int:
    """Insert one producer-authored finding directly, as its producer would."""
    from app.db import get_conn

    if origin_role not in _PRODUCER_BY_ROLE:
        raise AssertionError(
            f"origin_role={origin_role!r} is not producer-authored; post a "
            "developer finding through the API instead"
        )
    if evidence is None:
        evidence = (
            [{"path": "app/retry.py", "start_line": 1, "end_line": 5, "summary": ""}]
            if origin_role == "investigation" and claim_kind != "unknown"
            else []
        )
    if claim_kind == "hypothesis" and origin_role == "investigation":
        competing_explanations = competing_explanations or ["設定由来かもしれない"]
        refutation_conditions = refutation_conditions or ["設定値が3以外なら誤り"]
        next_investigation = next_investigation or "競合する説明を追加調査する"
    now = created_at if created_at is not None else time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO joint_understanding_finding
                (joint_understanding_id, system_id, origin_role, claim_kind,
                 statement, evidence_json, runtime_evidence_json,
                 supports_finding_ids, competing_explanations,
                 refutation_conditions, next_investigation, uncertainty,
                 supersedes_finding_id, decision_method, intelligence_run_id,
                 is_mock, producer_kind, actor_kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', ?, ?,
                    ?, 'system', ?)""",
            (
                ju_id, system_id, origin_role, claim_kind, statement,
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(runtime_evidence or [], ensure_ascii=False),
                json.dumps(supports_finding_ids or []),
                json.dumps(competing_explanations or [], ensure_ascii=False),
                json.dumps(refutation_conditions or [], ensure_ascii=False),
                next_investigation, uncertainty, supersedes_finding_id,
                intelligence_run_id, 1 if is_mock else 0,
                _PRODUCER_BY_ROLE[origin_role], now,
            ),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        return cur.lastrowid
