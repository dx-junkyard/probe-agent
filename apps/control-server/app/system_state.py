"""System State Assessment layer (Issue #193).

Normalizes "what state is this system's understanding/pipeline in" into a
single deterministic model instead of leaving it scattered across
``system_diagnostics.py``, the assistant screen-context endpoint, System
Understanding ``next_actions``, and page-local Dashboard logic.

This module is read-only and LLM-free (Principle 6): every state item is
derived from the database and persisted run/build-step records for the
system's latest ready snapshot. It never calls a reasoning model and never
approximates a state when evidence is missing — a missing/blocked state is
represented explicitly instead of guessed.

Phase 1 (this module) covers System Understanding (System Purpose / Core
Capabilities), snapshot readiness, and the pipeline steps that gate them.
``system_diagnostics.py`` consumes ``evaluate_understanding`` from here so
the Diagnostics dialog and the Assistant screen context share the same
understanding-state evaluation (Phase 2/3 projection); it does not yet
replace the pipeline checks in ``system_diagnostics.py``.

probe-agent:
  role: Deterministic System State Assessment service
  capability: system-state-assessment
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify System Understanding / snapshot / pipeline state is derived once, deterministically, from persisted records, and reused by diagnostics and assistant projections instead of being recomputed per surface.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .db import get_conn
from .git_ops import GitError, resolve_head, working_tree_status

# Severity vocabulary shared with system_diagnostics.py. Order = worst first.
SEVERITY_ORDER = ["error", "blocked", "warning", "info", "ok"]

STATE_GROUPS = (
    "repository",
    "snapshot",
    "pipeline",
    "understanding",
    "interview",
    "runtime",
    "proposal",
    "configuration",
)

USER_ACTION_KINDS = (
    "none", "configure", "create_snapshot", "build", "confirm",
    "review", "rerun", "inspect", "wait",
)

INTERVENTION_TIMINGS = ("now", "before_next_step", "optional", "after_build", "none")

# These rankings are deliberately data-free.  Keeping selection separate from
# state collection makes the "what should I do first?" decision deterministic
# and directly unit-testable.
_SEVERITY_RANK = {value: index for index, value in enumerate(SEVERITY_ORDER)}
_TIMING_RANK = {value: index for index, value in enumerate(INTERVENTION_TIMINGS)}
_ACTION_RANK = {
    "configure": 0, "create_snapshot": 1, "build": 2, "confirm": 3,
    "review": 4, "rerun": 5, "inspect": 6, "wait": 7, "none": 8,
}

STATUS_VALUES = (
    "satisfied", "missing", "unconfirmed", "stale", "impacted",
    "blocked", "running", "failed", "ready",
)

PAGE_REPOSITORY = "/repository"
PAGE_SYSTEM_UNDERSTANDING = "/system-understanding"
PAGE_INTERVIEW = "/interview"

ANCHOR_SNAPSHOT_CREATE = "snapshot-create"
ANCHOR_BUILD = "build"
ANCHOR_INTERVIEW_PURPOSE = "interview-purpose"
ANCHOR_INTERVIEW_CAPABILITIES = "interview-capabilities"

DOC_PATH_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")
DOC_PATH_MARKERS = ("readme", "architecture", "design", "spec", "docs/", "doc/")


def _worst_severity(severities: List[str]) -> str:
    for level in SEVERITY_ORDER:
        if level in severities:
            return level
    return "ok"


@dataclass
class TargetUi:
    route: str
    anchor: Optional[str] = None
    action_label: str = ""


@dataclass
class StateItem:
    state_id: str
    state_group: str  # one of STATE_GROUPS
    severity: str  # ok | info | warning | blocked | error
    status: str  # one of STATUS_VALUES
    user_action_kind: str  # one of USER_ACTION_KINDS
    intervention_timing: str  # one of INTERVENTION_TIMINGS
    subject: str
    summary: str
    detail: str
    impact: str = ""
    remediation: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    target_ui: Optional[TargetUi] = None
    related_checks: List[str] = field(default_factory=list)
    related_pipeline_steps: List[str] = field(default_factory=list)
    source: str = "system_state"
    dedupe_key: str = ""
    scope: str = "global"


@dataclass
class SystemStateAssessment:
    system_id: int
    generated_at: float
    overall_severity: str
    severity_counts: Dict[str, int]
    items: List[StateItem] = field(default_factory=list)
    primary_item: Optional[StateItem] = None
    notification_items: List[StateItem] = field(default_factory=list)
    page_items: Dict[str, List[StateItem]] = field(default_factory=dict)


# --- Understanding baseline / diff-impact evaluation ------------------------
#
# Moved here (rather than duplicated) from system_diagnostics.py so the
# understanding-state evaluation has one implementation; system_diagnostics
# imports it to build its System Purpose / Core Capabilities checks.


@dataclass
class UnderstandingBaseline:
    snapshot_id: int
    source: str  # interview | hierarchy | draft
    purpose_count: int = 0
    capability_count: int = 0


@dataclass
class UnderstandingDiffImpact:
    status: str  # unchanged | directly_impacted | possibly_impacted
    reasons: List[str] = field(default_factory=list)


@dataclass
class UnderstandingStatus:
    """Which branch a purpose/capabilities evaluation falls into."""

    kind: str  # satisfied_current | baseline_reusable | diff_impacted | unconfirmed | missing_baseline
    baseline: Optional[UnderstandingBaseline] = None
    impact: Optional[UnderstandingDiffImpact] = None
    count: int = 0


def _is_doc_path(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(DOC_PATH_SUFFIXES) or any(m in lower for m in DOC_PATH_MARKERS)


def _load_json_obj(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _understanding_item_count(understanding: Any, key: str) -> int:
    if not isinstance(understanding, dict):
        return 0
    items = understanding.get(key)
    return len(items) if isinstance(items, list) else 0


def latest_ready_snapshot_id(conn, system_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def _latest_interview_baseline(
    conn, system_id: int, *, needs_purpose: bool, needs_capabilities: bool,
) -> Optional[UnderstandingBaseline]:
    rows = conn.execute(
        """
        SELECT snapshot_id, current_understanding
          FROM interview_session
         WHERE system_id = ?
           AND understanding_confirmed_at IS NOT NULL
           AND current_understanding IS NOT NULL
         ORDER BY understanding_confirmed_at DESC, id DESC
        """,
        (system_id,),
    ).fetchall()
    for row in rows:
        understanding = _load_json_obj(row["current_understanding"])
        purpose_count = _understanding_item_count(understanding, "system_purpose")
        capability_count = _understanding_item_count(understanding, "core_capabilities")
        if needs_purpose and purpose_count == 0:
            continue
        if needs_capabilities and capability_count == 0:
            continue
        return UnderstandingBaseline(
            snapshot_id=row["snapshot_id"],
            source="interview",
            purpose_count=purpose_count,
            capability_count=capability_count,
        )
    return None


def latest_unconfirmed_interview_understanding(
    conn, system_id: int, *, needs_purpose: bool, needs_capabilities: bool,
) -> Optional[UnderstandingBaseline]:
    rows = conn.execute(
        """
        SELECT snapshot_id, current_understanding
          FROM interview_session
         WHERE system_id = ?
           AND understanding_confirmed_at IS NULL
           AND current_understanding IS NOT NULL
         ORDER BY updated_at DESC, id DESC
        """,
        (system_id,),
    ).fetchall()
    for row in rows:
        understanding = _load_json_obj(row["current_understanding"])
        purpose_count = _understanding_item_count(understanding, "system_purpose")
        capability_count = _understanding_item_count(understanding, "core_capabilities")
        if needs_purpose and purpose_count == 0:
            continue
        if needs_capabilities and capability_count == 0:
            continue
        return UnderstandingBaseline(
            snapshot_id=row["snapshot_id"],
            source="interview_unconfirmed",
            purpose_count=purpose_count,
            capability_count=capability_count,
        )
    return None


def _latest_hierarchy_baseline(
    conn, system_id: int, *, needs_purpose: bool, needs_capabilities: bool,
) -> Optional[UnderstandingBaseline]:
    rows = conn.execute(
        """
        SELECT snapshot_id,
               SUM(CASE WHEN node_type = 'purpose' THEN 1 ELSE 0 END) AS purpose_count,
               SUM(CASE WHEN node_type = 'capability' THEN 1 ELSE 0 END) AS capability_count
          FROM capability_hierarchy_nodes
         WHERE system_id = ?
         GROUP BY snapshot_id
         ORDER BY snapshot_id DESC
        """,
        (system_id,),
    ).fetchall()
    for row in rows:
        purpose_count = int(row["purpose_count"] or 0)
        capability_count = int(row["capability_count"] or 0)
        if needs_purpose and purpose_count == 0:
            continue
        if needs_capabilities and capability_count == 0:
            continue
        return UnderstandingBaseline(
            snapshot_id=row["snapshot_id"],
            source="hierarchy",
            purpose_count=purpose_count,
            capability_count=capability_count,
        )
    return None


def _latest_draft_baseline(conn, system_id: int) -> Optional[UnderstandingBaseline]:
    row = conn.execute(
        """
        SELECT snapshot_id
          FROM system_profile_drafts
         WHERE system_id = ? AND (name <> '' OR purpose <> '')
         ORDER BY id DESC
         LIMIT 1
        """,
        (system_id,),
    ).fetchone()
    if not row:
        return None
    return UnderstandingBaseline(
        snapshot_id=row["snapshot_id"], source="draft", purpose_count=1, capability_count=0,
    )


def understanding_baseline(
    conn, system_id: int, *, needs_purpose: bool = False, needs_capabilities: bool = False,
) -> Optional[UnderstandingBaseline]:
    """Latest confirmed or generated understanding reusable across snapshots."""
    baseline = _latest_interview_baseline(
        conn, system_id, needs_purpose=needs_purpose, needs_capabilities=needs_capabilities,
    )
    if baseline:
        return baseline
    baseline = _latest_hierarchy_baseline(
        conn, system_id, needs_purpose=needs_purpose, needs_capabilities=needs_capabilities,
    )
    if baseline:
        return baseline
    if needs_purpose and not needs_capabilities:
        return _latest_draft_baseline(conn, system_id)
    return None


def _snapshot_file_hashes(conn, snapshot_id: int) -> Dict[str, Optional[str]]:
    rows = conn.execute(
        "SELECT path, content_hash FROM snapshot_files WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {row["path"]: row["content_hash"] for row in rows}


def _metadata_facts(conn, snapshot_id: int) -> Dict[tuple, tuple]:
    rows = conn.execute(
        """
        SELECT path, qualified_name, role, capability, system_purpose,
               consumers, raw_block, explanation_hash
          FROM symbol_source_metadata
         WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    facts: Dict[tuple, tuple] = {}
    for row in rows:
        facts[(row["path"], row["qualified_name"])] = (
            row["role"] or "",
            row["capability"] or "",
            row["system_purpose"] or "",
            row["consumers"] or "[]",
            row["raw_block"] or "",
            row["explanation_hash"] or "",
        )
    return facts


def _entrypoint_facts(conn, snapshot_id: int) -> Dict[tuple, tuple]:
    rows = conn.execute(
        """
        SELECT entrypoint_type, entrypoint_id, handler_path,
               handler_qualified_name, route_method, route_path
          FROM code_entrypoints
         WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    facts: Dict[tuple, tuple] = {}
    for row in rows:
        facts[(row["entrypoint_type"], row["entrypoint_id"])] = (
            row["handler_path"] or "",
            row["handler_qualified_name"] or "",
            row["route_method"] or "",
            row["route_path"] or "",
        )
    return facts


def _changed_keys(base: Dict[Any, Any], target: Dict[Any, Any]) -> tuple:
    base_keys = set(base)
    target_keys = set(target)
    added = target_keys - base_keys
    removed = base_keys - target_keys
    changed = {k for k in base_keys & target_keys if base[k] != target[k]}
    return added, removed, changed


def baseline_diff_impact(
    conn, baseline: UnderstandingBaseline, snapshot_id: int, *, for_capabilities: bool,
) -> UnderstandingDiffImpact:
    if baseline.snapshot_id == snapshot_id:
        return UnderstandingDiffImpact(status="unchanged")

    base_files = _snapshot_file_hashes(conn, baseline.snapshot_id)
    target_files = _snapshot_file_hashes(conn, snapshot_id)
    added_paths, removed_paths, changed_paths = _changed_keys(base_files, target_files)
    doc_paths = sorted(p for p in added_paths | removed_paths | changed_paths if _is_doc_path(str(p)))
    if doc_paths:
        sample = ", ".join(doc_paths[:3])
        suffix = " など" if len(doc_paths) > 3 else ""
        return UnderstandingDiffImpact(
            status="directly_impacted",
            reasons=[f"理解の根拠になりやすいドキュメントが変更されています: {sample}{suffix}。"],
        )

    base_meta = _metadata_facts(conn, baseline.snapshot_id)
    target_meta = _metadata_facts(conn, snapshot_id)
    added_meta, removed_meta, changed_meta = _changed_keys(base_meta, target_meta)
    metadata_delta = added_meta | removed_meta | changed_meta
    if metadata_delta:
        sample_key = sorted(metadata_delta)[0]
        return UnderstandingDiffImpact(
            status="directly_impacted",
            reasons=[
                "source metadata（system_purpose / capability / role など）が変更されています: "
                f"{sample_key[0]}:{sample_key[1]}。"
            ],
        )

    if for_capabilities:
        base_entrypoints = _entrypoint_facts(conn, baseline.snapshot_id)
        target_entrypoints = _entrypoint_facts(conn, snapshot_id)
        added_eps, removed_eps, changed_eps = _changed_keys(base_entrypoints, target_entrypoints)
        if added_eps or removed_eps or changed_eps:
            parts = []
            if added_eps:
                parts.append(f"追加 {len(added_eps)} 件")
            if removed_eps:
                parts.append(f"削除 {len(removed_eps)} 件")
            if changed_eps:
                parts.append(f"変更 {len(changed_eps)} 件")
            return UnderstandingDiffImpact(
                status="possibly_impacted",
                reasons=[f"entrypoint に差分があります（{', '.join(parts)}）。主要機能に含めるか確認してください。"],
            )

    return UnderstandingDiffImpact(status="unchanged")


def _purpose_defined_in_current_snapshot(conn, system_id: int, snapshot_id: int) -> bool:
    node = conn.execute(
        "SELECT name, summary FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND node_type = 'purpose' LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    draft = conn.execute(
        "SELECT name, purpose FROM system_profile_drafts "
        "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    return (node is not None and bool(node["name"] or node["summary"])) or (
        draft is not None and bool(draft["name"] or draft["purpose"])
    )


def _capability_count_in_current_snapshot(conn, system_id: int, snapshot_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND node_type = 'capability'",
        (system_id, snapshot_id),
    ).fetchone()[0]


def evaluate_understanding(
    conn, system_id: int, snapshot_id: int, *, purpose: bool,
) -> UnderstandingStatus:
    """Classify System Purpose / Core Capabilities state for one snapshot.

    Shared by ``system_diagnostics._check_system_purpose`` /
    ``_check_system_capabilities`` and ``build_system_state`` so both
    surfaces agree on the same branch (Principle 6: finite, deterministic
    classification only).
    """
    if purpose:
        defined = _purpose_defined_in_current_snapshot(conn, system_id, snapshot_id)
        count = 1 if defined else 0
    else:
        count = _capability_count_in_current_snapshot(conn, system_id, snapshot_id)
        defined = count > 0
    if defined:
        return UnderstandingStatus(kind="satisfied_current", count=count)

    baseline = understanding_baseline(
        conn, system_id, needs_purpose=purpose, needs_capabilities=not purpose,
    )
    if baseline:
        impact = baseline_diff_impact(
            conn, baseline, snapshot_id, for_capabilities=not purpose,
        )
        if impact.status == "unchanged":
            return UnderstandingStatus(kind="baseline_reusable", baseline=baseline, impact=impact)
        return UnderstandingStatus(kind="diff_impacted", baseline=baseline, impact=impact)

    unconfirmed = latest_unconfirmed_interview_understanding(
        conn, system_id, needs_purpose=purpose, needs_capabilities=not purpose,
    )
    if unconfirmed:
        return UnderstandingStatus(kind="unconfirmed", baseline=unconfirmed)

    return UnderstandingStatus(kind="missing_baseline")


# --- State item construction -------------------------------------------------


def _understanding_state_item(status: UnderstandingStatus, *, purpose: bool) -> StateItem:
    subject = "System Purpose" if purpose else "Core Capabilities"
    prefix = "understanding.purpose" if purpose else "understanding.capabilities"
    anchor = ANCHOR_INTERVIEW_PURPOSE if purpose else ANCHOR_INTERVIEW_CAPABILITIES

    if status.kind == "satisfied_current":
        return StateItem(
            state_id=f"{prefix}.satisfied",
            state_group="understanding",
            severity="ok",
            status="satisfied",
            user_action_kind="none",
            intervention_timing="none",
            subject=subject,
            summary=f"{subject} は現在の snapshot で確認済みです。",
            detail=f"{subject} は現在の snapshot で定義されています。",
        )
    if status.kind == "baseline_reusable":
        baseline = status.baseline
        return StateItem(
            state_id=f"{prefix}.baseline_reusable",
            state_group="understanding",
            severity="ok",
            status="satisfied",
            user_action_kind="none",
            intervention_timing="none",
            subject=subject,
            summary=f"{subject} は確認済み baseline を再利用できます。",
            detail=(
                f"{subject} は snapshot #{baseline.snapshot_id} の確認済み理解を再利用できます。"
                "今回の差分には再確認が必要な根拠変更は見つかりませんでした。"
            ),
            evidence={"baseline_snapshot_id": baseline.snapshot_id, "source": baseline.source},
        )
    if status.kind == "diff_impacted":
        baseline = status.baseline
        impact = status.impact
        reason = " ".join(impact.reasons) if impact and impact.reasons else "前回確認済み理解に影響しうる差分があります。"
        return StateItem(
            state_id=f"{prefix}.diff_impacted",
            state_group="understanding",
            severity="warning",
            status="impacted",
            user_action_kind="confirm",
            intervention_timing="before_next_step",
            subject=subject,
            summary=f"{subject} は最新 snapshot との差分で再確認が必要です。",
            detail=(
                f"{subject} は snapshot #{baseline.snapshot_id} で確認済みですが、"
                f"最新 snapshot との差分に影響候補があります。{reason}"
            ),
            impact="確認済み理解をそのまま使えるか判断が必要です。",
            remediation=f"Interview で {subject} を再確認してください。",
            evidence={
                "baseline_snapshot_id": baseline.snapshot_id,
                "source": baseline.source,
                "impact_status": impact.status if impact else "",
                "reasons": impact.reasons if impact else [],
            },
            target_ui=TargetUi(
                route=PAGE_INTERVIEW, anchor=anchor, action_label=f"Interview で{subject}を再確認",
            ),
            related_checks=["system_purpose" if purpose else "system_capabilities"],
        )
    if status.kind == "unconfirmed":
        baseline = status.baseline
        count = baseline.purpose_count if purpose else baseline.capability_count
        return StateItem(
            state_id=f"{prefix}.unconfirmed",
            state_group="understanding",
            severity="warning",
            status="unconfirmed",
            user_action_kind="confirm",
            intervention_timing="before_next_step",
            subject=subject,
            summary=f"{subject} に未確認の候補があります。",
            detail=(
                f"Interview に未確認の {subject} 候補が {count} 件あります。"
                "baseline として再利用するには、開発者による明示的な確認が必要です。"
            ),
            impact="未確認の理解は、snapshot 更新後の差分診断や probe 設計の前提としてまだ採用されません。",
            remediation=f"Interview で {subject} を確認済みにしてください。",
            evidence={"unconfirmed_snapshot_id": baseline.snapshot_id, "candidate_count": count},
            target_ui=TargetUi(
                route=PAGE_INTERVIEW, anchor=anchor, action_label=f"Interview で{subject}を確認",
            ),
            related_checks=["system_purpose" if purpose else "system_capabilities"],
        )
    # missing_baseline
    return StateItem(
        state_id=f"{prefix}.missing_baseline",
        state_group="understanding",
        severity="warning",
        status="missing",
        user_action_kind="confirm",
        intervention_timing="before_next_step",
        subject=subject,
        summary=f"{subject} が未定義です。",
        detail=f"{subject} が未定義です。確認済み・未確認いずれの baseline もありません。",
        impact="probe 設計・flow 探索・改善提案・ユーザー意図との整合の前提となる根幹情報が欠けています。",
        remediation=f"Interview で {subject} を定義・確認してください。",
        target_ui=TargetUi(
            route=PAGE_INTERVIEW, anchor=anchor, action_label=f"Interview で{subject}を定義",
        ),
        related_checks=["system_purpose" if purpose else "system_capabilities"],
    )


def _snapshot_missing_item(conn, system_id: int) -> Optional[StateItem]:
    latest = conn.execute(
        "SELECT id, status FROM repository_snapshots WHERE system_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    ready = conn.execute(
        "SELECT id FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    if ready is not None:
        return None
    if latest is not None and latest["status"] == "indexing":
        return StateItem(
            state_id="snapshot.ready.running",
            state_group="snapshot",
            severity="info",
            status="running",
            user_action_kind="wait",
            intervention_timing="none",
            subject="Snapshot",
            summary="Snapshot を作成中です。",
            detail=f"最新の snapshot #{latest['id']} は作成中です。ready になるまで待機してください。",
            impact="snapshot が ready になるまで、snapshot の内容を読むパイプラインステップは開始できません。",
            remediation="Snapshot 作成の完了を待ってください。",
            evidence={"latest_snapshot_id": latest["id"], "latest_snapshot_status": latest["status"]},
            related_checks=["snapshot_status"],
            related_pipeline_steps=["snapshot_ready"],
        )
    severity = "error" if latest is not None and latest["status"] not in ("ready", "indexing") else "warning"
    return StateItem(
        state_id="snapshot.ready.missing",
        state_group="snapshot",
        severity=severity,
        status="missing",
        user_action_kind="create_snapshot",
        intervention_timing="now",
        subject="Snapshot",
        summary="ready な snapshot がありません。",
        detail=(
            "このシステムではまだ snapshot が作成されていません。"
            if latest is None
            else f"ready な snapshot がありません。最新の snapshot #{latest['id']} の状態は '{latest['status']}' です。"
        ),
        impact="snapshot の内容を読むすべてのパイプラインステップがブロックされます。",
        remediation="Repository タブの Snapshots から snapshot を作成してください。",
        evidence={"latest_snapshot_id": latest["id"] if latest else None},
        target_ui=TargetUi(route=PAGE_REPOSITORY, anchor=ANCHOR_SNAPSHOT_CREATE, action_label="Snapshot を作成"),
        related_checks=["snapshot_status"],
        related_pipeline_steps=["snapshot_ready"],
    )


def _snapshot_stale_for_interview_item(conn, system_id: int, snapshot_id: int) -> Optional[StateItem]:
    row = conn.execute(
        """
        SELECT id, snapshot_id, updated_at
          FROM interview_session
         WHERE system_id = ?
           AND understanding_confirmed_at IS NULL
           AND current_understanding IS NOT NULL
           AND snapshot_id IS NOT NULL
           AND snapshot_id != ?
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
        """,
        (system_id, snapshot_id),
    ).fetchone()
    if row is None:
        return None
    return StateItem(
        state_id="snapshot.latest.stale_for_interview",
        state_group="snapshot",
        severity="info",
        status="stale",
        user_action_kind="review",
        intervention_timing="optional",
        subject="Interview snapshot",
        summary="Interview は古い snapshot に基づいています。",
        detail=(
            f"進行中の Interview session #{row['id']} は snapshot #{row['snapshot_id']} を"
            f"基準にしていますが、最新の ready snapshot は #{snapshot_id} です。"
        ),
        impact="Interview の根拠が最新 snapshot と一致していない可能性があります。",
        remediation="最新 snapshot を基準に Interview を見直してください。",
        evidence={"interview_session_id": row["id"], "interview_snapshot_id": row["snapshot_id"], "latest_snapshot_id": snapshot_id},
        target_ui=TargetUi(route=PAGE_INTERVIEW, anchor=None, action_label="Interview を確認"),
    )


def _stuck_after_seconds() -> float:
    try:
        return float(os.getenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", "300"))
    except ValueError:
        return 300.0


def _is_active_build(row) -> bool:
    if row is None or row["status"] not in ("queued", "running"):
        return False
    last = row["heartbeat_at"] or row["started_at"] or row["created_at"]
    return last is not None and (time.time() - last) <= _stuck_after_seconds()


def _active_build(conn, system_id: int, snapshot_id: int):
    rows = conn.execute(
        """SELECT id, status, current_step, heartbeat_at, started_at, created_at
             FROM system_understanding_builds
            WHERE system_id = ? AND snapshot_id = ? AND status IN ('queued', 'running')
            ORDER BY id DESC""",
        (system_id, snapshot_id),
    ).fetchall()
    for row in rows:
        if _is_active_build(row):
            return row
    return None


def _pipeline_state_item(
    *,
    state_prefix: str,
    raw_status: Optional[str],
    subject: str,
    pipeline_steps: List[str],
    remediation: str,
    evidence: Dict[str, Any],
    active_build=None,
    blocked_by_reasoning: bool = False,
) -> Optional[StateItem]:
    if raw_status == "completed":
        return None
    if blocked_by_reasoning:
        return StateItem(
            state_id=f"{state_prefix}.blocked_by_reasoning",
            state_group="pipeline",
            severity="blocked",
            status="blocked",
            user_action_kind="configure",
            intervention_timing="before_next_step",
            subject=subject,
            summary=f"{subject} は reasoning モデル未設定のためブロックされています。",
            detail="このステップには reasoning モデルが必要ですが、現在は利用可能な reasoning モデルが設定されていません。",
            impact="System Understanding でこのステップはブロックとして表示されます。",
            remediation="intelligence 用 reasoning モデル設定を修正してからビルドを実行してください。",
            evidence=evidence,
            target_ui=TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="LLM 設定を確認"),
            related_pipeline_steps=pipeline_steps,
        )

    if raw_status in ("pending", "running") or (raw_status is None and active_build is not None):
        running_evidence = dict(evidence)
        if active_build is not None:
            running_evidence.update({
                "active_build_id": active_build["id"],
                "active_build_status": active_build["status"],
                "active_build_current_step": active_build["current_step"],
            })
        return StateItem(
            state_id=f"{state_prefix}.running",
            state_group="pipeline",
            severity="info",
            status="running",
            user_action_kind="wait",
            intervention_timing="none",
            subject=subject,
            summary=f"{subject} を実行中です。",
            detail="System Understanding build がこの snapshot に対して実行中です。完了まで待機してください。",
            impact="このステップの成果物は build 完了後に利用できます。",
            remediation="現在の build の完了を待ってください。",
            evidence=running_evidence,
            related_pipeline_steps=pipeline_steps,
        )

    if raw_status == "blocked":
        return StateItem(
            state_id=f"{state_prefix}.blocked",
            state_group="pipeline",
            severity="blocked",
            status="blocked",
            user_action_kind="build",
            intervention_timing="before_next_step",
            subject=subject,
            summary=f"{subject} がブロックされています。",
            detail="直近のビルドステップは blocked です。依存ステップやビルドステップのエラーを確認してください。",
            impact="System Understanding でこのステップはブロックとして表示されます。",
            remediation="ブロック原因を解消してから Build / Refresh を再実行してください。",
            evidence=evidence,
            target_ui=TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="Build 状態を確認"),
            related_pipeline_steps=pipeline_steps,
        )

    if raw_status == "failed":
        return StateItem(
            state_id=f"{state_prefix}.failed",
            state_group="pipeline",
            severity="error",
            status="failed",
            user_action_kind="rerun",
            intervention_timing="before_next_step",
            subject=subject,
            summary=f"{subject} が失敗しています。",
            detail="直近の実行が failed です。エラーを確認し、原因を修正してから再実行してください。",
            impact="このステップの成果物が欠落しているか古くなっています。",
            remediation="System Understanding で Build / Refresh を再実行してください。",
            evidence=evidence,
            target_ui=TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="Build / Refresh を再実行"),
            related_pipeline_steps=pipeline_steps,
        )

    cancelled = raw_status == "cancelled"
    return StateItem(
        state_id=f"{state_prefix}.not_run",
        state_group="pipeline",
        severity="warning",
        status="missing",
        user_action_kind="build",
        intervention_timing="before_next_step",
        subject=subject,
        summary=f"{subject} が未実行です。",
        detail=(
            "直近の実行は cancelled です。必要なら Build / Refresh を再実行してください。"
            if cancelled
            else "このステップは現在の snapshot に対して実行されていません。"
        ),
        impact="System Understanding でこのステップは未実行として表示されます。",
        remediation=remediation,
        evidence=evidence,
        target_ui=TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="Build / Refresh を実行"),
        related_pipeline_steps=pipeline_steps,
    )


def _run_not_run_item(
    conn, system_id: int, snapshot_id: int, *, state_prefix: str, run_types: List[str],
    subject: str, pipeline_steps: List[str], remediation: str,
) -> Optional[StateItem]:
    placeholders = ",".join("?" for _ in run_types)
    row = conn.execute(
        f"SELECT id, status FROM intelligence_runs "
        f"WHERE system_id = ? AND snapshot_id = ? AND run_type IN ({placeholders}) "
        f"ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id, *run_types),
    ).fetchone()
    evidence = {"snapshot_id": snapshot_id, "run_id": row["id"] if row else None}
    return _pipeline_state_item(
        state_prefix=state_prefix,
        raw_status=row["status"] if row else None,
        subject=subject,
        pipeline_steps=pipeline_steps,
        remediation=remediation,
        evidence=evidence,
        active_build=_active_build(conn, system_id, snapshot_id) if row is None else None,
    )


def _build_step_not_run_item(
    conn, system_id: int, snapshot_id: int, *, state_prefix: str, step: str,
    subject: str, pipeline_steps: List[str], remediation: str,
) -> Optional[StateItem]:
    row = conn.execute(
        """SELECT id, status, error FROM system_understanding_build_steps
           WHERE system_id = ? AND snapshot_id = ? AND step = ?
           ORDER BY id DESC LIMIT 1""",
        (system_id, snapshot_id, step),
    ).fetchone()
    evidence = {
        "snapshot_id": snapshot_id,
        "step_run_id": row["id"] if row else None,
        "step_error": row["error"] if row else None,
    }
    return _pipeline_state_item(
        state_prefix=state_prefix,
        raw_status=row["status"] if row else None,
        subject=subject,
        pipeline_steps=pipeline_steps,
        remediation=remediation,
        evidence=evidence,
        active_build=_active_build(conn, system_id, snapshot_id) if row is None else None,
    )


def _capability_hierarchy_item(
    conn, system_id: int, snapshot_id: int, *, reasoning_available: bool,
) -> Optional[StateItem]:
    row = conn.execute(
        "SELECT id, status FROM intelligence_runs "
        "WHERE system_id = ? AND snapshot_id = ? AND run_type = 'capability_hierarchy' "
        "ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row is not None and row["status"] == "completed":
        return None
    evidence = {"snapshot_id": snapshot_id, "run_id": row["id"] if row else None}
    return _pipeline_state_item(
        state_prefix="pipeline.capability_hierarchy",
        raw_status=row["status"] if row else None,
        subject="Capability 階層",
        pipeline_steps=["capability_hierarchy_ready"],
        remediation="System Understanding で Build / Refresh を実行して capability 階層を生成してください。",
        evidence=evidence,
        active_build=_active_build(conn, system_id, snapshot_id) if row is None else None,
        blocked_by_reasoning=not reasoning_available,
    )


def _repository_state_items(conn, system_id: int) -> List[StateItem]:
    """Collect repository freshness state once for every consumer projection."""
    config = conn.execute(
        "SELECT repo_path FROM repository_configs WHERE system_id = ?", (system_id,)
    ).fetchone()
    if config is None or not config["repo_path"]:
        return [StateItem(
            state_id="repository.configuration.missing", state_group="repository",
            severity="warning", status="missing", user_action_kind="configure",
            intervention_timing="now", subject="Repository",
            summary="対象 repository が設定されていません。",
            detail="Snapshot と System Understanding を開始するには対象 repository の設定が必要です。",
            remediation="Repository タブで対象 repository を設定してください。",
            target_ui=TargetUi(route=PAGE_REPOSITORY, anchor="repo-config", action_label="Repository を設定"),
            related_checks=["repository_config"], dedupe_key="repository.configuration",
        )]

    latest_ready = conn.execute(
        "SELECT id, commit_sha FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1", (system_id,),
    ).fetchone()
    try:
        current_head = resolve_head(config["repo_path"])
        working_tree = working_tree_status(config["repo_path"])
    except GitError as exc:
        return [StateItem(
            state_id="repository.head.unreadable", state_group="repository",
            severity="error", status="failed", user_action_kind="configure",
            intervention_timing="now", subject="Repository HEAD",
            summary="Repository HEAD を読み取れません。", detail=str(exc),
            remediation="Repository のパスとアクセス権を確認してください。",
            evidence={"repo_path": config["repo_path"]},
            target_ui=TargetUi(route=PAGE_REPOSITORY, anchor="repo-config", action_label="Repository 設定を確認"),
            related_checks=["repository_path"], dedupe_key="repository.head",
        )]

    items: List[StateItem] = []
    if latest_ready is not None and latest_ready["commit_sha"] != current_head:
        items.append(StateItem(
            state_id="repository.snapshot.stale", state_group="repository",
            severity="warning", status="stale", user_action_kind="create_snapshot",
            intervention_timing="now", subject="Repository snapshot",
            summary="HEAD が最新 snapshot より進んでいます。",
            detail=f"HEAD {current_head} は最新 ready snapshot #{latest_ready['id']} ({latest_ready['commit_sha']}) と異なります。",
            remediation="Repository で新しい snapshot を作成してください。",
            evidence={"current_head": current_head, "latest_ready_snapshot_id": latest_ready["id"],
                      "latest_ready_commit": latest_ready["commit_sha"]},
            target_ui=TargetUi(route=PAGE_REPOSITORY, anchor=ANCHOR_SNAPSHOT_CREATE, action_label="Snapshot を作成"),
            related_checks=["snapshot_status"], related_pipeline_steps=["snapshot_ready"],
            dedupe_key="repository.snapshot.freshness",
        ))
    if not working_tree.clean:
        items.append(StateItem(
            state_id="repository.working_tree.dirty", state_group="repository",
            severity="warning", status="impacted", user_action_kind="review",
            intervention_timing="before_next_step", subject="Working tree",
            summary="未コミット差分があります。",
            detail=f"Working tree に {working_tree.dirty_file_count} 件の未コミット変更があります。",
            remediation="patch 適用や snapshot の前に commit、stash、または差分の確認をしてください。",
            evidence={"dirty_file_count": working_tree.dirty_file_count, "dirty_sample": working_tree.sample},
            target_ui=TargetUi(route=PAGE_REPOSITORY, anchor=ANCHOR_SNAPSHOT_CREATE, action_label="Repository を確認"),
            related_checks=["working_tree"], dedupe_key="repository.working_tree",
        ))
    return items


def _priority_key(item: StateItem) -> tuple:
    """Deterministic cross-surface priority ordering for one item.

    Shared by ``select_primary_item`` (which additionally prefers items on
    the current route) and ``notification_items`` (which has no route
    context and simply wants the globally most important item first).
    """
    return (
        _SEVERITY_RANK.get(item.severity, len(_SEVERITY_RANK)),
        _TIMING_RANK.get(item.intervention_timing, len(_TIMING_RANK)),
        _ACTION_RANK.get(item.user_action_kind, len(_ACTION_RANK)),
        item.state_id,
    )


def select_primary_item(items: List[StateItem], *, route: Optional[str] = None) -> Optional[StateItem]:
    """Select the one user-facing item by the documented deterministic order."""
    actionable = [item for item in items if item.severity != "ok" and item.user_action_kind not in ("none", "wait")]
    if not actionable:
        return None
    return min(actionable, key=lambda item: (
        0 if route and item.target_ui and item.target_ui.route == route else 1,
        *_priority_key(item),
    ))


def _build_page_items(items: List[StateItem]) -> Dict[str, List[StateItem]]:
    """Group actionable (non-``ok``) items by their target route.

    ``page_items[route][0]`` renders as a warning-styled action banner in the
    dashboard, so an ``ok`` item must never appear here even if it happens to
    carry a ``target_ui`` (hardening; no current item does both today).
    """
    actionable = [item for item in items if item.severity != "ok" and item.target_ui]
    routes = sorted({item.target_ui.route for item in actionable})
    return {
        route: sorted(
            [item for item in actionable if item.target_ui.route == route],
            key=lambda item: (
                _SEVERITY_RANK.get(item.severity, len(_SEVERITY_RANK)),
                _TIMING_RANK.get(item.intervention_timing, len(_TIMING_RANK)), item.state_id,
            ),
        )
        for route in routes
    }


def _dedupe_items(items: List[StateItem]) -> List[StateItem]:
    """Keep the highest priority representative for a shared root cause."""
    result: Dict[str, StateItem] = {}
    for item in items:
        key = item.dedupe_key or item.state_id
        old = result.get(key)
        if old is None or select_primary_item([item, old]) is item:
            result[key] = item
    return sorted(result.values(), key=lambda item: item.state_id)


def build_system_state(system_id: int) -> SystemStateAssessment:
    """Build the deterministic state assessment for one system.

    Read-only, LLM-free (Principle 6). Every item is derived from persisted
    database records for the latest ready snapshot.
    """
    from .system_understanding_service import (
        _check_understanding_refresh_recommended, _is_reasoning_model_available,
    )

    items: List[StateItem] = []
    with get_conn() as conn:
        items.extend(_repository_state_items(conn, system_id))
        snapshot_id = latest_ready_snapshot_id(conn, system_id)

        missing = _snapshot_missing_item(conn, system_id)
        if missing:
            items.append(missing)

        if snapshot_id is not None:
            if _check_understanding_refresh_recommended(conn, system_id):
                items.append(StateItem(
                    state_id="interview.materialized.rebuild_required", state_group="interview",
                    severity="warning", status="stale", user_action_kind="build",
                    intervention_timing="after_build", subject="Interview materialization",
                    summary="Interview の反映後に System Understanding の再 build が必要です。",
                    detail="最新の Interview materialization が直近の完了済み build より新しいため、理解結果を更新する必要があります。",
                    remediation="System Understanding で Build / Refresh を実行してください。",
                    target_ui=TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="Build / Refresh を実行"),
                    related_pipeline_steps=["capability_hierarchy_ready"], dedupe_key="interview.materialization.build",
                ))
            stale = _snapshot_stale_for_interview_item(conn, system_id, snapshot_id)
            if stale:
                items.append(stale)

            items.append(_understanding_state_item(
                evaluate_understanding(conn, system_id, snapshot_id, purpose=True), purpose=True,
            ))
            items.append(_understanding_state_item(
                evaluate_understanding(conn, system_id, snapshot_id, purpose=False), purpose=False,
            ))

            symbol = _run_not_run_item(
                conn, system_id, snapshot_id,
                state_prefix="pipeline.symbol_index",
                run_types=["symbol_index"],
                subject="シンボル索引",
                pipeline_steps=["symbols_indexed"],
                remediation="System Understanding で Build / Refresh を実行してコードシンボルを索引付けしてください。",
            )
            if symbol:
                items.append(symbol)

            entrypoint = _run_not_run_item(
                conn, system_id, snapshot_id,
                state_prefix="pipeline.entrypoint_index",
                run_types=["entrypoint_index"],
                subject="エントリポイント索引",
                pipeline_steps=["entrypoints_discovered"],
                remediation="System Understanding で Build / Refresh を実行してエントリポイントを検出してください。",
            )
            if entrypoint:
                items.append(entrypoint)

            documentation = _build_step_not_run_item(
                conn, system_id, snapshot_id,
                state_prefix="pipeline.documentation_index",
                step="documentation_index",
                subject="ドキュメント索引",
                pipeline_steps=["documentation_indexed"],
                remediation="System Understanding で Build / Refresh を実行してドキュメントチャンクを索引付けしてください。",
            )
            if documentation:
                items.append(documentation)

            hierarchy = _capability_hierarchy_item(
                conn, system_id, snapshot_id,
                reasoning_available=_is_reasoning_model_available(),
            )
            if hierarchy:
                items.append(hierarchy)

    # Diagnostics remains its own backward-compatible endpoint, but each
    # actionable diagnostic is also a canonical state item with the same fix
    # target.  Import at call time because diagnostics itself reuses this
    # module's understanding evaluator.
    #
    # Several native items above already cover a diagnostic root cause and
    # declare it via related_checks (e.g. snapshot.ready.missing /
    # repository.snapshot.stale -> "snapshot_status", repository config ->
    # "repository_config", understanding items -> "system_purpose" /
    # "system_capabilities"). Skip projecting those checks a second time so
    # the same root cause doesn't appear twice in items/notifications with
    # different dedupe keys (deterministic finite-set skip, Principle 6).
    covered_check_ids = {
        check_id for item in items for check_id in item.related_checks
    }
    from .system_diagnostics import run_system_diagnostics
    for check in run_system_diagnostics(system_id).checks:
        if check.severity == "ok":
            continue
        if check.check_id in covered_check_ids:
            continue
        severity = check.severity if check.severity in SEVERITY_ORDER else "warning"
        action = "configure" if check.fix_kind == "dialog" else "inspect"
        items.append(StateItem(
            state_id=f"diagnostic.{check.check_id}",
            state_group="configuration" if check.category in ("auth", "database", "llm", "configuration") else "runtime",
            severity=severity,
            status="failed" if severity == "error" else ("blocked" if severity == "blocked" else "missing"),
            user_action_kind=action,
            intervention_timing="now" if severity in ("error", "blocked") else "before_next_step",
            subject=check.title, summary=check.title, detail=check.detail,
            impact=check.impact, remediation=check.remediation,
            evidence={"diagnostic_category": check.category, "fix_kind": check.fix_kind},
            target_ui=(TargetUi(route=check.fix_page, anchor=check.fix_anchor, action_label="修正する") if check.fix_page else None),
            related_checks=[check.check_id], related_pipeline_steps=check.related_pipeline_steps,
            source="system_diagnostics", dedupe_key=f"diagnostic.{check.check_id}",
        ))

    items = _dedupe_items(items)

    severity_counts: Dict[str, int] = {level: 0 for level in SEVERITY_ORDER}
    for item in items:
        severity_counts[item.severity] = severity_counts.get(item.severity, 0) + 1
    overall = _worst_severity([i.severity for i in items])
    return SystemStateAssessment(
        system_id=system_id,
        generated_at=time.time(),
        overall_severity=overall,
        severity_counts=severity_counts,
        items=items,
        primary_item=select_primary_item(items),
        notification_items=sorted(
            [item for item in items if item.severity in ("error", "blocked", "warning") and item.scope == "global"],
            key=_priority_key,
        ),
        page_items=_build_page_items(items),
    )
