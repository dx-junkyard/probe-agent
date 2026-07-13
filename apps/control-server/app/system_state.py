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
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import state_facts
from .db import get_conn
from .git_ops import GitError
from .models import SMOKE_CHECK_COMPONENT_ID

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

# User phase (Issue #237): which of the three phases a state item belongs to.
# ``setup`` gates repository/environment readiness, ``preparation`` gates
# analysis + instrumentation readiness, ``diagnosis`` is the terminal
# ongoing-operation phase. See ``derive_user_phase`` below for how the
# *current* phase is computed; this tuple is only the display/suppression
# ordering.
PHASE_ORDER = ("setup", "preparation", "diagnosis")
_PHASE_RANK = {phase: index for index, phase in enumerate(PHASE_ORDER)}

# Default state_group -> phase mapping (Issue #237 issue body, table under
# "Scope (含む)" item 3). Applied to every StateItem unless overridden below.
STATE_GROUP_PHASE: Dict[str, str] = {
    "repository": "setup",
    "configuration": "setup",
    "snapshot": "preparation",
    "pipeline": "preparation",
    "understanding": "preparation",
    "interview": "preparation",
    "runtime": "diagnosis",
    "proposal": "diagnosis",
}

# Explicit, finite per-state_id overrides where the state_group default above
# would misclassify an item's role in the phase model. Kept as a small,
# hand-enumerated map (Principle 6) -- never inferred from free text.
STATE_ID_PHASE_OVERRIDES: Dict[str, str] = {
    # SDK connectivity is one of the two OR'd preparation-completion signals
    # (an approved probe plan OR non-"no_signal" connectivity -- see
    # derive_user_phase), so tagging it "diagnosis" (the state_group=
    # "runtime" default) would suppress it from notification_items/
    # page_items during preparation and leave no guidance on how to finish
    # that phase.
    "runtime.connectivity.no_signal": "preparation",
    # _diagnostic_state_item collapses every diagnostic category outside
    # auth/database/llm/configuration into state_group="runtime" (Issue
    # #193), so repository/pipeline/understanding diagnostics would
    # otherwise default to "diagnosis" here too. Their real phase follows
    # the same category grouping derive_user_phase uses for the setup and
    # preparation completion gates (repository -> setup;
    # pipeline/understanding -> preparation) -- otherwise a genuine setup
    # blocker (e.g. an unset PROBE_REPOSITORY_ROOTS) would be silently
    # suppressed during setup.
    "diagnostic.repository_roots": "setup",
    "diagnostic.repository_config": "setup",
    "diagnostic.snapshot_status": "setup",
    "diagnostic.pipeline_symbol_index": "preparation",
    "diagnostic.pipeline_entrypoint_index": "preparation",
    "diagnostic.pipeline_documentation_index": "preparation",
    "diagnostic.pipeline_understanding_graph": "preparation",
    "diagnostic.pipeline_capability_hierarchy": "preparation",
    "diagnostic.system_purpose": "preparation",
    "diagnostic.system_capabilities": "preparation",
    # Issue #238: reviewing/approving a proposed probe plan is one of the two
    # ways to satisfy derive_user_phase's "an approved probe plan OR
    # non-no_signal connectivity" preparation-completion signal -- see
    # _probe_plan_proposed_state_item's docstring. Its sibling
    # proposal.probe_plans.approved_without_patch is deliberately NOT listed
    # here: an approved plan already satisfies that signal regardless of
    # patch validation, so it stays at the state_group="proposal" default
    # ("diagnosis").
    "proposal.probe_plans.proposed": "preparation",
}

# Diagnostic categories (system_diagnostics.DiagnosticCheck.category) that
# gate the "setup" phase completion condition (Issue #237): a blocking
# repository/database/auth/llm diagnostic means the environment itself is
# not ready yet, independent of any snapshot/pipeline/understanding work.
SETUP_DIAGNOSTIC_CATEGORIES = ("repository", "database", "auth", "llm")

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
    # Pages where this state is observed.  ``target_ui`` remains the place
    # where the user performs the remediation; it is deliberately not
    # overloaded with presentation routing.
    display_routes: List[str] = field(default_factory=list)
    related_checks: List[str] = field(default_factory=list)
    related_pipeline_steps: List[str] = field(default_factory=list)
    source: str = "system_state"
    dedupe_key: str = ""
    scope: str = "global"
    # User phase (Issue #237): one of PHASE_ORDER. Assigned centrally by
    # build_system_state via _phase_for_item after every item is collected,
    # so individual constructors above do not need to pass it explicitly.
    # Items built directly in tests (bypassing build_system_state) keep the
    # "" default -- callers that care about phase go through
    # build_system_state or call _phase_for_item themselves.
    phase: str = ""


@dataclass
class PhaseCompletion:
    phase: str  # one of PHASE_ORDER
    complete: bool


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
    # User phase (Issue #237).
    user_phase: str = "setup"
    phases: List[PhaseCompletion] = field(default_factory=list)


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
    return state_facts.get_latest_ready_snapshot_id(conn, system_id)


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
    return state_facts.purpose_defined_in_snapshot(conn, system_id, snapshot_id)


def _capability_count_in_current_snapshot(conn, system_id: int, snapshot_id: int) -> int:
    return state_facts.capability_count_in_snapshot(conn, system_id, snapshot_id)


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
            display_routes=[PAGE_SYSTEM_UNDERSTANDING],
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
            display_routes=[PAGE_SYSTEM_UNDERSTANDING],
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
        display_routes=[PAGE_SYSTEM_UNDERSTANDING],
        related_checks=["system_purpose" if purpose else "system_capabilities"],
    )


def _snapshot_missing_item(conn, system_id: int) -> Optional[StateItem]:
    latest = state_facts.get_latest_snapshot(conn, system_id)
    ready = state_facts.get_latest_ready_snapshot(conn, system_id)
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
        display_routes=[PAGE_SYSTEM_UNDERSTANDING],
    )


def _active_build(conn, system_id: int, snapshot_id: int):
    return state_facts.get_active_build(conn, system_id, snapshot_id)


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
    row = state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, run_types)
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
    row = state_facts.get_latest_build_step(conn, system_id, snapshot_id, step)
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
    row = state_facts.get_latest_intelligence_run(
        conn, system_id, snapshot_id, ["capability_hierarchy"]
    )
    if row is not None and row["status"] == "completed":
        # Issue #210: a completed run with zero capability nodes is not
        # "done" from the user's perspective (no probe-agent: docstring
        # metadata was found to group). Report a distinct warning item
        # instead of silently returning None, so the SystemStateBanner and
        # the "complete" pipeline checklist agree, and so remediation points
        # at Interview/metadata instead of re-running the already-completed
        # Build / Refresh.
        if _capability_count_in_current_snapshot(conn, system_id, snapshot_id) == 0:
            proposal_state = conn.execute(
                """SELECT s.id AS session_id, s.materialized_at,
                          SUM(CASE WHEN p.approval_state = 'needs_review' THEN 1 ELSE 0 END) AS needs_review_count,
                          SUM(CASE WHEN p.approval_state IN ('approved', 'edited') THEN 1 ELSE 0 END) AS approved_count
                   FROM interview_session s
                   LEFT JOIN interview_proposal p ON p.session_id = s.id AND p.system_id = s.system_id
                   WHERE s.system_id = ?
                   GROUP BY s.id
                   HAVING needs_review_count > 0 OR approved_count > 0 OR s.materialized_at IS NOT NULL
                   ORDER BY s.updated_at DESC, s.id DESC LIMIT 1""",
                (system_id,),
            ).fetchone()
            if proposal_state is not None and proposal_state["needs_review_count"]:
                session_id = proposal_state["session_id"]
                needs_review = proposal_state["needs_review_count"]
                detail = (
                    f"capability_hierarchy の実行（#{row['id']}）は完了していますが、現在の snapshot に "
                    "`probe-agent:` docstring メタデータがありません。"
                    f"Interview session #{session_id} の提案 {needs_review} 件は再レビュー待ちです。"
                )
                remediation = (
                    f"Interview session #{session_id} で提案を再レビューして承認し、レビュー用差分を生成してください。"
                    "差分を対象リポジトリへ適用して新しい snapshot を作成した後、Build / Refresh を実行してください。"
                )
                target_route = f"{PAGE_INTERVIEW}?session={session_id}"
                action_label = "Interview の提案を再レビュー"
            elif proposal_state is not None and proposal_state["approved_count"]:
                session_id = proposal_state["session_id"]
                detail = (
                    f"capability_hierarchy の実行（#{row['id']}）は完了していますが、現在の snapshot に "
                    "`probe-agent:` docstring メタデータがありません。承認済み Interview 提案はまだソースに反映されていません。"
                )
                remediation = (
                    f"Interview session #{session_id} でレビュー用差分を生成し、対象リポジトリへ適用してください。"
                    "その後、新しい snapshot を作成して Build / Refresh を実行してください。"
                )
                target_route = f"{PAGE_INTERVIEW}?session={session_id}"
                action_label = "Interview で差分を生成"
            else:
                detail = (
                    f"capability_hierarchy の実行（#{row['id']}）は完了していますが、"
                    "現在の snapshot に capability ノードが存在しません。対象リポジトリに"
                    " `probe-agent:` docstring メタデータが見つからなかったことが原因です。"
                )
                remediation = (
                    "Interview で Core Capabilities を確認して提案を生成・承認し、レビュー用差分を対象リポジトリへ適用してください。"
                    "新しい snapshot を作成した後、Build / Refresh を実行してください。"
                )
                target_route = PAGE_INTERVIEW
                action_label = "Interview で Core Capabilities を確認"
            return StateItem(
                state_id="pipeline.capability_hierarchy.empty",
                state_group="pipeline",
                severity="warning",
                status="missing",
                user_action_kind="confirm",
                intervention_timing="before_next_step",
                subject="Capability 階層",
                summary="Capability 階層は実行済みですが capability が 0 件です。",
                detail=detail,
                impact="Core Capabilities が未定義のため、probe 設計・flow 探索・改善提案の前提が欠けています。",
                remediation=remediation,
                evidence={"snapshot_id": snapshot_id, "run_id": row["id"], "capability_count": 0},
                target_ui=TargetUi(
                    route=target_route,
                    anchor=ANCHOR_INTERVIEW_CAPABILITIES,
                    action_label=action_label,
                ),
                display_routes=[PAGE_SYSTEM_UNDERSTANDING],
                related_pipeline_steps=["capability_hierarchy_ready"],
            )
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


# --- docs_code_reconciled pipeline step (Issue #238) -----------------------
#
# system_understanding_service._check_documentation_claims_scanned's
# "documentation_claims_scanned" factor is already represented as a
# StateItem: the existing diagnostics-projected
# ``diagnostic.pipeline_understanding_graph`` check (system_diagnostics.py,
# artifact-backed on ``understanding_graph_snapshots`` presence, requires
# reasoning) tests exactly the same condition and is already phase-tagged
# "preparation" via STATE_ID_PHASE_OVERRIDES below -- no separate native item
# is needed for that factor.
#
# _check_docs_code_reconciled is not fully covered by that diagnostic,
# though: it requires BOTH an understanding graph AND code symbols, while
# the diagnostic only checks the graph. This item reproduces the stricter
# two-fact structural check natively so a symbol-index gap while the graph
# is otherwise ready is not silently missed by StateItem consumers.


def _docs_code_reconcile_state_item(conn, system_id: int, snapshot_id: int) -> Optional[StateItem]:
    has_graph = state_facts.has_understanding_graph_snapshot(conn, system_id, snapshot_id)
    has_symbols = state_facts.has_code_symbols(conn, system_id, snapshot_id)
    if has_graph and has_symbols:
        return None
    evidence = {
        "snapshot_id": snapshot_id,
        "has_understanding_graph": has_graph,
        "has_code_symbols": has_symbols,
    }
    target_ui = TargetUi(route=PAGE_SYSTEM_UNDERSTANDING, anchor=ANCHOR_BUILD, action_label="Build / Refresh を実行")
    if has_graph or has_symbols:
        return StateItem(
            state_id="pipeline.docs_code_reconcile.partial",
            state_group="pipeline",
            severity="warning",
            status="missing",
            user_action_kind="build",
            intervention_timing="before_next_step",
            subject="Docs-code 照合",
            summary="Docs-code 照合に必要なデータの一部のみが揃っています。",
            detail=(
                "ドキュメントの主張とコードシンボルのどちらか一方のみが揃っています。"
                "docs-code 照合には両方が必要です。"
            ),
            impact="System Understanding で docs-code 照合が未完了として表示されます。",
            remediation="System Understanding で Build / Refresh を実行してください。",
            evidence=evidence,
            target_ui=target_ui,
            related_pipeline_steps=["docs_code_reconciled"],
        )
    return StateItem(
        state_id="pipeline.docs_code_reconcile.not_run",
        state_group="pipeline",
        severity="warning",
        status="missing",
        user_action_kind="build",
        intervention_timing="before_next_step",
        subject="Docs-code 照合",
        summary="Docs-code 照合が未実行です。",
        detail=(
            "ドキュメントの主張とコードシンボルのどちらも揃っていないため、"
            "docs-code 照合は未実行です。"
        ),
        impact="System Understanding で docs-code 照合が未完了として表示されます。",
        remediation="System Understanding で Build / Refresh を実行してください。",
        evidence=evidence,
        target_ui=target_ui,
        related_pipeline_steps=["docs_code_reconciled"],
    )


# --- runtime / proposal representative items (Issue #237) -----------------
#
# Phase 1 (Issue #193) intentionally left the runtime/proposal state_groups
# declared-but-unused. Issue #237 adds one representative item per group --
# not exhaustive coverage -- to give the preparation and diagnosis phases at
# least one concrete, actionable item each.


def _connectivity_state_item(connectivity_state: str, approved_probe_plan_count: int) -> Optional[StateItem]:
    """SDK instrumentation guidance when no trace has ever been received.

    Tagged phase="preparation" via STATE_ID_PHASE_OVERRIDES (not the
    state_group="runtime" default) because non-"no_signal" connectivity is
    one of the two OR'd preparation-completion signals in
    derive_user_phase. Severity/timing soften once an approved probe plan
    already satisfies that OR condition through the other branch.
    """
    if connectivity_state != "no_signal":
        return None
    blocking = approved_probe_plan_count == 0
    return StateItem(
        state_id="runtime.connectivity.no_signal",
        state_group="runtime",
        severity="warning" if blocking else "info",
        status="missing",
        user_action_kind="review",
        intervention_timing="before_next_step" if blocking else "optional",
        subject="SDK connectivity",
        summary="SDK からのトレースをまだ受信していません。",
        detail=(
            "このシステムでは probe SDK からのトレースを一度も受信していません。"
            + (
                "承認済みの probe plan もまだありません。"
                if blocking
                else "承認済みの probe plan は既にありますが、SDK からのトレースはまだ届いていません。"
            )
        ),
        impact="計装経路（承認済み probe plan または実際のトレース受信）が確立していません。",
        remediation=(
            "Probe Planner で probe plan を作成・承認するか、対象アプリケーションに "
            "@probe を組み込んで Control Server に接続してください。"
        ),
        evidence={
            "connectivity_state": connectivity_state,
            "approved_probe_plan_count": approved_probe_plan_count,
        },
        target_ui=TargetUi(route="/probe-planner", anchor=None, action_label="Probe Planner を確認"),
        related_pipeline_steps=["probe_plans_reviewed"],
    )


def _undecided_experiments_item(undecided_completed_experiment_count: int) -> Optional[StateItem]:
    """Diagnosis-phase prompt to record a decision on completed experiments."""
    count = undecided_completed_experiment_count
    if count == 0:
        return None
    return StateItem(
        state_id="proposal.experiments.undecided",
        state_group="proposal",
        severity="warning",
        status="unconfirmed",
        user_action_kind="review",
        intervention_timing="before_next_step",
        subject="Experiment decisions",
        summary=f"未評価の experiment が {count} 件あります。",
        detail=(
            f"完了した experiment のうち {count} 件は、まだ human decision"
            "（adopted / rejected / needs_more_data）が記録されていません。"
        ),
        impact="評価が確定するまで、候補実装の採否判断が完了しません。",
        remediation="Experiments で完了した experiment の decision を記録してください。",
        evidence={"undecided_completed_experiment_count": count},
        target_ui=TargetUi(route="/experiments", anchor=None, action_label="Experiments を確認"),
    )


# --- probe plan review / patch state items (Issue #238) --------------------
#
# Absorb the two probe-plan-shaped NextAction sources the now-removed (Issue
# #239) system_understanding_service._build_next_actions used to generate
# from _load_pending_plan_action_ids: "Review probe plan" (proposed_plan_ids)
# and "Generate / validate probe patch"
# (approved_plan_ids_without_validated_patch).
# Both are aggregate/count-based (one item per condition, not one per plan
# id) to match this module's existing style for proposal-group items
# (_undecided_experiments_item above), rather than exploding into an
# unbounded number of items for systems with many pending plans.


def _probe_plan_proposed_state_item(proposed_count: int) -> Optional[StateItem]:
    """A proposed-but-not-yet-approved probe plan awaiting review.

    Tagged phase="preparation" via STATE_ID_PHASE_OVERRIDES (not the
    state_group="proposal" default of "diagnosis"): an approved probe plan
    is one of the two OR'd preparation-completion signals in
    derive_user_phase, and reviewing/approving a proposed plan is how a user
    reaches that signal through this path -- the same rationale
    runtime.connectivity.no_signal already documents for the other path.
    """
    if proposed_count == 0:
        return None
    return StateItem(
        state_id="proposal.probe_plans.proposed",
        state_group="proposal",
        severity="warning",
        status="unconfirmed",
        user_action_kind="review",
        intervention_timing="before_next_step",
        subject="Probe plan review",
        summary=f"レビュー待ちの probe plan が {proposed_count} 件あります。",
        detail=f"{proposed_count} 件の probe plan が提案されたまま、レビュー・承認されていません。",
        impact="承認されるまで、計装（instrumentation）経路の確立に寄与しません。",
        remediation="Probe Planner でレビューして承認してください。",
        evidence={"proposed_probe_plan_count": proposed_count},
        target_ui=TargetUi(route="/probe-planner", anchor=None, action_label="Probe Planner でレビュー"),
        related_pipeline_steps=["probe_plans_reviewed"],
    )


def _probe_plan_patch_pending_state_item(approved_without_patch_count: int) -> Optional[StateItem]:
    """An approved probe plan whose patch has not passed baseline+probed
    validation yet. Stays at the state_group="proposal" default phase
    ("diagnosis"): an approved plan already satisfies the preparation
    instrumentation-path signal regardless of patch validation, so this item
    is refinement work rather than a preparation blocker.
    """
    if approved_without_patch_count == 0:
        return None
    return StateItem(
        state_id="proposal.probe_plans.approved_without_patch",
        state_group="proposal",
        severity="info",
        status="missing",
        user_action_kind="review",
        intervention_timing="optional",
        subject="Probe patch generation",
        summary=f"検証済みパッチが未生成の承認済み probe plan が {approved_without_patch_count} 件あります。",
        detail=f"承認済み probe plan のうち {approved_without_patch_count} 件に、検証済みの patch がまだありません。",
        impact="patch が検証されるまで、対象リポジトリへの計装は完了しません。",
        remediation="Probe Planner で patch を生成・検証してください。",
        evidence={"approved_probe_plan_without_patch_count": approved_without_patch_count},
        target_ui=TargetUi(route="/probe-planner", anchor=None, action_label="Probe Planner で patch を生成"),
        related_pipeline_steps=["probe_plans_reviewed"],
    )


def _repository_state_items(conn, system_id: int) -> List[StateItem]:
    """Collect repository freshness state once for every consumer projection."""
    config = state_facts.get_repository_config(conn, system_id)
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

    latest_ready = state_facts.get_latest_ready_snapshot(conn, system_id)
    try:
        head_state = state_facts.resolve_repository_head_state(config["repo_path"])
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
    current_head = head_state.current_head

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
    if not head_state.working_tree_clean:
        items.append(StateItem(
            state_id="repository.working_tree.dirty", state_group="repository",
            severity="warning", status="impacted", user_action_kind="review",
            intervention_timing="before_next_step", subject="Working tree",
            summary="未コミット差分があります。",
            detail=f"Working tree に {head_state.working_tree_dirty_file_count} 件の未コミット変更があります。",
            remediation="patch 適用や snapshot の前に commit、stash、または差分の確認をしてください。",
            evidence={"dirty_file_count": head_state.working_tree_dirty_file_count,
                      "dirty_sample": head_state.working_tree_dirty_sample},
            target_ui=TargetUi(route=PAGE_REPOSITORY, anchor=ANCHOR_SNAPSHOT_CREATE, action_label="Repository を確認"),
            related_checks=["working_tree"], dedupe_key="repository.working_tree",
        ))
    return items


def _phase_for_item(item: StateItem) -> str:
    """Deterministic phase tag for one state item (Issue #237).

    Looks up STATE_ID_PHASE_OVERRIDES first (a small, explicit exception
    list); falls back to the STATE_GROUP_PHASE default for the item's
    ``state_group``. Both maps are finite and hand-authored (Principle 6) --
    nothing here is inferred from item text.
    """
    return STATE_ID_PHASE_OVERRIDES.get(
        item.state_id, STATE_GROUP_PHASE.get(item.state_group, "diagnosis"),
    )


@dataclass(frozen=True)
class UserPhaseFacts:
    """Deterministic inputs to ``derive_user_phase`` (Issue #237).

    Every field is already a finite-set/boolean/count fact -- no reasoning
    model output, no free text -- gathered by ``build_system_state`` from
    ``state_facts`` and ``system_diagnostics`` before calling
    ``derive_user_phase``. Keeping this as a plain frozen dataclass (rather
    than passing a raw dict or individual positional args) makes
    ``derive_user_phase`` directly unit-testable without a database.

    Every field defaults to the conservative ("earlier phase") value, so a
    caller that cannot determine a fact and passes the default does not
    accidentally advance the phase -- matching the "判定に迷う入力は前の
    フェーズに倒す" rule from Issue #235/#237.
    """

    repository_configured: bool = False
    setup_diagnostics_blocking: bool = False
    ready_snapshot_exists: bool = False
    pipeline_all_complete: bool = False
    purpose_satisfied: bool = False
    capabilities_satisfied: bool = False
    approved_probe_plan_count: int = 0
    connectivity_state: str = "no_signal"


@dataclass(frozen=True)
class UserPhaseResult:
    user_phase: str  # one of PHASE_ORDER
    phases: List[PhaseCompletion]


def derive_user_phase(facts: UserPhaseFacts) -> UserPhaseResult:
    """Pure deterministic derivation of the current user phase (Issue #237).

    Phase definitions (fixed by Issue #235, not re-litigated here):

    - ``setup``: the target repository is registered, and no
      repository/database/auth/llm diagnostic is ``error``/``blocked``.
    - ``preparation``: a ready snapshot exists, every deterministic pipeline
      step (symbol index, entrypoint index, documentation index, capability
      hierarchy) has completed for it, System Purpose and Core Capabilities
      are each ``satisfied_current`` or ``baseline_reusable``, and an
      instrumentation path is established -- at least one *approved* probe
      plan, or SDK connectivity that is not ``no_signal``.
    - ``diagnosis``: terminal; no completion condition.

    The current phase is the first phase (in ``PHASE_ORDER``) whose
    completion condition is not met. Because every ``UserPhaseFacts`` field
    defaults to its "not yet satisfied" value, missing/unknown facts fall
    back into an earlier phase rather than a later one.
    """
    setup_complete = facts.repository_configured and not facts.setup_diagnostics_blocking
    preparation_complete = (
        setup_complete
        and facts.ready_snapshot_exists
        and facts.pipeline_all_complete
        and facts.purpose_satisfied
        and facts.capabilities_satisfied
        and (facts.approved_probe_plan_count > 0 or facts.connectivity_state != "no_signal")
    )

    if not setup_complete:
        user_phase = "setup"
    elif not preparation_complete:
        user_phase = "preparation"
    else:
        user_phase = "diagnosis"

    return UserPhaseResult(
        user_phase=user_phase,
        phases=[
            PhaseCompletion(phase="setup", complete=setup_complete),
            PhaseCompletion(phase="preparation", complete=preparation_complete),
            PhaseCompletion(phase="diagnosis", complete=False),
        ],
    )


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
    """Group actionable items by explicit observation routes and fix route.

    ``page_items[route][0]`` renders as a warning-styled action banner in the
    dashboard, so an ``ok`` item must never appear here even if it happens to
    carry a ``target_ui`` (hardening; no current item does both today).
    """
    actionable = [item for item in items if item.severity != "ok" and item.target_ui]
    item_routes = {
        id(item): set(item.display_routes) | {item.target_ui.route}
        for item in actionable
    }
    routes = sorted({route for routes in item_routes.values() for route in routes})
    return {
        route: sorted(
            [item for item in actionable if route in item_routes[id(item)]],
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


def _diagnostic_state_item(check: Any) -> StateItem:
    """Project one diagnostics check without promoting informational state."""
    # Diagnostics uses ``unknown`` for an informational observation (for
    # example, no reasoning run has been recorded yet).  State Assessment has
    # the equivalent explicit vocabulary, ``info``.
    severity = "info" if check.severity == "unknown" else check.severity
    action = (
        "none" if check.severity == "unknown"
        else ("configure" if check.fix_kind == "dialog" else "inspect")
    )
    return StateItem(
        state_id=f"diagnostic.{check.check_id}",
        state_group="configuration" if check.category in ("auth", "database", "llm", "configuration") else "runtime",
        severity=severity,
        status=(
            "failed" if severity == "error"
            else ("blocked" if severity == "blocked" else ("unconfirmed" if severity == "info" else "missing"))
        ),
        user_action_kind=action,
        intervention_timing="now" if severity in ("error", "blocked") else "before_next_step",
        subject=check.title, summary=check.title, detail=check.detail,
        impact=check.impact, remediation=check.remediation,
        evidence={"diagnostic_category": check.category, "fix_kind": check.fix_kind},
        target_ui=(
            TargetUi(route=check.fix_page, anchor=check.fix_anchor,
                     action_label=f"「{check.title}」を修正")
            if check.fix_page and check.severity != "unknown" else None
        ),
        related_checks=[check.check_id], related_pipeline_steps=check.related_pipeline_steps,
        source="system_diagnostics", dedupe_key=f"diagnostic.{check.check_id}",
    )


def build_system_state(system_id: int) -> SystemStateAssessment:
    """Build the deterministic state assessment for one system.

    Read-only, LLM-free (Principle 6). Every item is derived from persisted
    database records for the latest ready snapshot.
    """
    from .system_understanding_service import (
        _check_understanding_refresh_recommended, _is_reasoning_model_available,
    )

    items: List[StateItem] = []
    repository_configured = False
    ready_snapshot_exists = False
    pipeline_all_complete = False
    purpose_satisfied = False
    capabilities_satisfied = False
    approved_probe_plan_count = 0
    connectivity_state = "no_signal"

    with get_conn() as conn:
        items.extend(_repository_state_items(conn, system_id))
        # Same truthiness rule as _repository_state_items (a row with an
        # empty repo_path is "not configured"), so the setup phase and the
        # repository.configuration.missing item never disagree.
        config_row = state_facts.get_repository_config(conn, system_id)
        repository_configured = config_row is not None and bool(config_row["repo_path"])

        snapshot_id = latest_ready_snapshot_id(conn, system_id)
        ready_snapshot_exists = snapshot_id is not None

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

            purpose_status = evaluate_understanding(conn, system_id, snapshot_id, purpose=True)
            capabilities_status = evaluate_understanding(conn, system_id, snapshot_id, purpose=False)
            items.append(_understanding_state_item(purpose_status, purpose=True))
            items.append(_understanding_state_item(capabilities_status, purpose=False))
            purpose_satisfied = purpose_status.kind in ("satisfied_current", "baseline_reusable")
            capabilities_satisfied = capabilities_status.kind in ("satisfied_current", "baseline_reusable")

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

            docs_code_reconcile = _docs_code_reconcile_state_item(conn, system_id, snapshot_id)
            if docs_code_reconcile:
                items.append(docs_code_reconcile)

            # Raw run/step statuses (not "item is None") for the preparation
            # phase's "pipeline 全ステップ complete" fact: the capability
            # hierarchy step can be raw-status "completed" while still
            # producing a StateItem (Issue #210's empty-result warning), so
            # "no item" is not an equivalent signal here.
            pipeline_all_complete = all(
                row is not None and row["status"] == "completed"
                for row in (
                    state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["symbol_index"]),
                    state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["entrypoint_index"]),
                    state_facts.get_latest_build_step(conn, system_id, snapshot_id, "documentation_index"),
                    state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["capability_hierarchy"]),
                )
            )

        approved_probe_plan_count = state_facts.count_approved_probe_plans(conn, system_id)
        undecided_experiment_count = state_facts.count_undecided_completed_experiments(conn, system_id)
        connectivity_facts = state_facts.get_connectivity_facts(conn, system_id, SMOKE_CHECK_COMPONENT_ID)
        connectivity_state = state_facts.classify_connectivity_state(
            real_trace_count=connectivity_facts.real_trace_count,
            smoke_trace_count=connectivity_facts.smoke_trace_count,
        )

        connectivity_item = _connectivity_state_item(connectivity_state, approved_probe_plan_count)
        if connectivity_item:
            items.append(connectivity_item)
        undecided_item = _undecided_experiments_item(undecided_experiment_count)
        if undecided_item:
            items.append(undecided_item)

        proposed_probe_plan_count = state_facts.count_proposed_probe_plans(conn, system_id)
        approved_probe_plan_without_patch_count = (
            state_facts.count_approved_probe_plans_without_validated_patch(conn, system_id)
        )
        proposed_item = _probe_plan_proposed_state_item(proposed_probe_plan_count)
        if proposed_item:
            items.append(proposed_item)
        patch_pending_item = _probe_plan_patch_pending_state_item(approved_probe_plan_without_patch_count)
        if patch_pending_item:
            items.append(patch_pending_item)

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
    diagnostics_report = run_system_diagnostics(system_id)
    for check in diagnostics_report.checks:
        if check.severity == "ok":
            continue
        if check.check_id in covered_check_ids:
            continue
        items.append(_diagnostic_state_item(check))

    setup_diagnostics_blocking = any(
        check.category in SETUP_DIAGNOSTIC_CATEGORIES and check.severity in ("error", "blocked")
        for check in diagnostics_report.checks
    )

    items = _dedupe_items(items)
    for item in items:
        item.phase = _phase_for_item(item)

    phase_result = derive_user_phase(UserPhaseFacts(
        repository_configured=repository_configured,
        setup_diagnostics_blocking=setup_diagnostics_blocking,
        ready_snapshot_exists=ready_snapshot_exists,
        pipeline_all_complete=pipeline_all_complete,
        purpose_satisfied=purpose_satisfied,
        capabilities_satisfied=capabilities_satisfied,
        approved_probe_plan_count=approved_probe_plan_count,
        connectivity_state=connectivity_state,
    ))

    # Phase suppression (Issue #237, parent #235's fixed withdrawal rule):
    # items belonging to a phase after the current one are excluded from
    # every notification projection -- primary_item, notification_items, and
    # page_items -- but never from `items` itself (audit trail stays
    # complete). Phase scope is the OUTERMOST criterion of the fixed
    # priority order (phase scope -> severity -> intervention_timing ->
    # user_action_kind -> state_id), so primary selection also happens
    # within the scoped set. Note this means a setup-phase user (e.g. with
    # LLM diagnostics blocked, including LLM_PROVIDER=mock which is
    # test/local-smoke mode per Principle 7) sees setup guidance on page
    # banners instead of later-phase pipeline warnings -- by design: the
    # later-phase facts remain in `items`, and phase-based prerequisite
    # guidance for such pages is Issue #241's scope.
    current_rank = _PHASE_RANK.get(phase_result.user_phase, len(PHASE_ORDER) - 1)
    phase_scoped_items = [
        item for item in items if _PHASE_RANK.get(item.phase, current_rank) <= current_rank
    ]

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
        primary_item=select_primary_item(phase_scoped_items),
        notification_items=sorted(
            [item for item in phase_scoped_items if item.severity in ("error", "blocked", "warning") and item.scope == "global"],
            key=_priority_key,
        ),
        page_items=_build_page_items(phase_scoped_items),
        user_phase=phase_result.user_phase,
        phases=phase_result.phases,
    )
