"""Decision Workspace Context Pack Builder (Issue #36).

Builds a `WorkspaceContextPack` by reading already-persisted rows (system
profile, repository snapshots, feature drafts, code links, component
profiles, traces, evaluations, probe plans, experiments) and compressing
them into source-traceable digests. This module never calls an LLM, never
reads repository file content directly, and never decides or proposes
anything -- it only gathers and compresses data the caller has explicitly
pinned via `workspace_context_items`.

Only data relevant to the workspace's pinned context items is collected:
an empty context-item list yields an (almost) empty pack, never "all data
for the system".
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import List, Optional

from .models import (
    WorkspaceComponentDigest,
    WorkspaceContextPack,
    WorkspaceEvaluationDigest,
    WorkspaceEvidenceRef,
    WorkspaceExperimentDigest,
    WorkspaceExperimentVariantSummary,
    WorkspaceFeatureDigest,
    WorkspaceFocusSummary,
    WorkspaceHumanDecisionDigest,
    WorkspaceProbePlanSummary,
    WorkspaceProbePointSummary,
    WorkspaceRepositorySummary,
    WorkspaceSystemSummary,
    WorkspaceTraceDigest,
)

MAX_TEXT_CHARS = 400
MAX_ITEMS_PER_CATEGORY = 10
MAX_EVIDENCE_PER_DIGEST = 3
MAX_EVIDENCE_TOTAL = 50
MAX_PROBE_POINTS_PER_PLAN = 10
MAX_VARIANTS_PER_EXPERIMENT = 10
MAX_TRACES_SCANNED_PER_COMPONENT = 200
MAX_FAILURE_REASONS = 3
MAX_CATEGORY_CHARS = int(os.getenv("WORKSPACE_CONTEXT_MAX_CATEGORY_CHARS", "12000"))
MAX_CONTEXT_CHARS = int(os.getenv("WORKSPACE_CONTEXT_MAX_CHARS", "50000"))

_SENSITIVE_VALUE_PATTERNS = [
    re.compile(
        r'(?i)(["\']?(?:api[_-]?key|access[_-]?token|password|passwd|secret)'
        r'["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)'
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,\"'}]+)"),
]


def _truncate(text: Optional[str], limit: int = MAX_TEXT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _redact_and_truncate(text: Optional[str], limit: int = MAX_TEXT_CHARS) -> str:
    redacted = text or ""
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return _truncate(redacted, limit)


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


class _PackBuilder:
    """Accumulates evidence/missing_information while respecting budgets."""

    def __init__(self) -> None:
        self.evidence: List[WorkspaceEvidenceRef] = []
        self.missing: List[str] = []
        self._evidence_total = 0

    def note(self, message: str) -> None:
        if message not in self.missing:
            self.missing.append(message)

    def add_evidence(self, refs: List[WorkspaceEvidenceRef]) -> List[WorkspaceEvidenceRef]:
        """Add up to MAX_EVIDENCE_PER_DIGEST refs to the global pool, returning
        the subset accepted (used as the per-digest evidence list)."""
        accepted: List[WorkspaceEvidenceRef] = []
        for ref in refs[:MAX_EVIDENCE_PER_DIGEST]:
            if self._evidence_total >= MAX_EVIDENCE_TOTAL:
                self.note("evidence budget exceeded; remaining references omitted")
                break
            self.evidence.append(ref)
            accepted.append(ref)
            self._evidence_total += 1
        return accepted

    def select_ids(self, item_type: str, ids: List[str]) -> List[str]:
        """Apply the per-category item-count budget, recording an omission
        note for any ids dropped. Order is the caller's (already-deterministic)
        order, so dropped ids are always the tail."""
        if len(ids) <= MAX_ITEMS_PER_CATEGORY:
            return ids
        self.note(
            f"{item_type}: {len(ids) - MAX_ITEMS_PER_CATEGORY} pinned reference(s) "
            "omitted due to the per-category item budget"
        )
        return ids[:MAX_ITEMS_PER_CATEGORY]


def _sorted_unique_item_ids(context_items, item_type: str) -> List[str]:
    ids = {row["item_id"] for row in context_items if row["item_type"] == item_type}
    return sorted(ids)


def _apply_character_budgets(pack: WorkspaceContextPack) -> WorkspaceContextPack:
    """Deterministically trim category tails before an LLM sees the pack.

    Snapshot persistence is unaffected. Budgets are independently configurable
    for Decision Workspace context construction and omissions are recorded.
    """
    category_names = (
        "features",
        "components",
        "traces",
        "evaluations",
        "probe_plans",
        "experiments",
        "human_decisions",
    )
    omitted: List[str] = []

    for name in category_names:
        values = getattr(pack, name)
        removed = 0
        while values and len(
            json.dumps(
                [item.model_dump(mode="json") for item in values],
                ensure_ascii=False,
                sort_keys=True,
            )
        ) > MAX_CATEGORY_CHARS:
            values.pop()
            removed += 1
        if removed:
            omitted.append(
                f"{name}: {removed} item(s) omitted due to the category character budget"
            )

    # Trim lower-priority category tails in a stable order until the complete
    # serialized pack fits. System/focus/repository summaries are retained.
    while len(pack.model_dump_json()) > MAX_CONTEXT_CHARS:
        for name in reversed(category_names):
            values = getattr(pack, name)
            if values:
                values.pop()
                omitted.append(
                    f"{name}: 1 item omitted due to the total context character budget"
                )
                break
        else:
            if pack.evidence:
                pack.evidence.pop()
                if not any("evidence omitted due to" in item for item in omitted):
                    omitted.append(
                        "evidence omitted due to the total context character budget"
                    )
                continue
            break

    for message in omitted:
        if message not in pack.missing_information:
            pack.missing_information.append(message)
    return pack


def _system_summary(conn: sqlite3.Connection, system_id: int) -> WorkspaceSystemSummary:
    row = conn.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
    profile = conn.execute(
        "SELECT * FROM system_profile WHERE system_id = ?", (system_id,)
    ).fetchone()
    return WorkspaceSystemSummary(
        system_id=system_id,
        name=row["name"] if row else "",
        environment=row["environment"] if row else "",
        purpose=_truncate(profile["purpose"]) if profile else "",
        target_users=_truncate(profile["target_users"]) if profile else "",
    )


def _focus_summary(workspace) -> Optional[WorkspaceFocusSummary]:
    focus = (workspace["focus"] or "").strip()
    summary = (workspace["summary"] or "").strip()
    if not focus and not summary:
        return None
    return WorkspaceFocusSummary(
        title=workspace["title"] or "",
        focus=_truncate(focus),
        summary=_truncate(summary),
    )


def _latest_ready_snapshot(conn: sqlite3.Connection, system_id: int):
    return conn.execute(
        """SELECT * FROM repository_snapshots
           WHERE system_id = ? AND status = 'ready'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()


def _repository_summary(
    conn: sqlite3.Connection, system_id: int, builder: _PackBuilder
) -> Optional[WorkspaceRepositorySummary]:
    row = _latest_ready_snapshot(conn, system_id)
    if row is None:
        builder.note("repository: no ready snapshot is available for this system")
        return None
    return WorkspaceRepositorySummary(
        snapshot_id=row["id"],
        commit_sha=row["commit_sha"],
        repo_path=row["repo_path"],
        file_count=row["file_count"],
        status=row["status"],
    )


def _feature_digest(
    conn: sqlite3.Connection,
    system_id: int,
    feature_id: str,
    snapshot_id: Optional[int],
    builder: _PackBuilder,
) -> Optional[WorkspaceFeatureDigest]:
    if snapshot_id is None:
        builder.note(f"feature {feature_id}: no ready snapshot to read drafts from")
        return None
    fd_row = conn.execute(
        """SELECT fd.* FROM feature_drafts fd
           JOIN intelligence_runs ir ON fd.intelligence_run_id = ir.id
           WHERE fd.system_id = ? AND fd.feature_id = ? AND fd.snapshot_id = ?
             AND ir.status = 'completed'
           ORDER BY fd.id DESC LIMIT 1""",
        (system_id, feature_id, snapshot_id),
    ).fetchone()
    if fd_row is None:
        builder.note(f"feature {feature_id}: no completed feature draft found")
        return None

    evidence_rows = conn.execute(
        """SELECT * FROM draft_evidence
           WHERE system_id = ? AND draft_type = 'feature' AND draft_id = ?
           ORDER BY id""",
        (system_id, fd_row["id"]),
    ).fetchall()
    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="feature_draft",
            source_id=str(fd_row["id"]),
            snapshot_id=snapshot_id,
            path=ev["path"],
            start_line=ev["start_line"],
            end_line=ev["end_line"],
            summary=_truncate(ev["summary"]),
        )
        for ev in evidence_rows
    ]

    link_count_row = conn.execute(
        """SELECT COUNT(*) AS n FROM feature_code_links
           WHERE system_id = ? AND feature_id = ? AND snapshot_id = ?
             AND review_status = 'accepted'""",
        (system_id, feature_id, snapshot_id),
    ).fetchone()

    return WorkspaceFeatureDigest(
        feature_id=feature_id,
        name=fd_row["name"] or "",
        summary=_truncate(fd_row["summary"]),
        user_value=_truncate(fd_row["user_value"]),
        success_criteria=_json_list(fd_row["success_criteria"])[:MAX_ITEMS_PER_CATEGORY],
        risks=_json_list(fd_row["risks"])[:MAX_ITEMS_PER_CATEGORY],
        accepted_code_link_count=link_count_row["n"] if link_count_row else 0,
        decision_method=fd_row["decision_method"] or "reasoning_llm",
        evidence=builder.add_evidence(evidence_refs),
    )


def _component_digest(
    conn: sqlite3.Connection, system_id: int, component_id: str, builder: _PackBuilder
) -> Optional[WorkspaceComponentDigest]:
    row = conn.execute(
        "SELECT * FROM component_profiles WHERE system_id = ? AND component_id = ?",
        (system_id, component_id),
    ).fetchone()
    if row is None:
        builder.note(f"component {component_id}: no component profile recorded")
        return None
    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="component_profile",
            source_id=component_id,
            summary=_truncate(row["responsibility"]),
        )
    ]
    return WorkspaceComponentDigest(
        component_id=component_id,
        purpose=_truncate(row["purpose"]),
        responsibility=_truncate(row["responsibility"]),
        expected_input=_truncate(row["expected_input"]),
        expected_output=_truncate(row["expected_output"]),
        failure_impact=_truncate(row["failure_impact"]),
        evidence=builder.add_evidence(evidence_refs),
    )


def _trace_digest(
    conn: sqlite3.Connection, system_id: int, component_id: str, builder: _PackBuilder
) -> Optional[WorkspaceTraceDigest]:
    rows = conn.execute(
        """SELECT * FROM traces WHERE system_id = ? AND component_id = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (system_id, component_id, MAX_TRACES_SCANNED_PER_COMPONENT),
    ).fetchall()
    if not rows:
        builder.note(f"trace digest for {component_id}: no traces recorded")
        return None

    error_count = sum(1 for r in rows if r["error"])
    timestamps = [r["timestamp"] for r in rows]
    representative = rows[0]
    representative_input = None
    if representative["input_json"] is not None:
        representative_input = _redact_and_truncate(
            str(representative["input_json"])
        )

    failed_trace_ids = {r["trace_id"] for r in rows}
    eval_failed_count = 0
    if failed_trace_ids:
        placeholders = ",".join("?" for _ in failed_trace_ids)
        eval_row = conn.execute(
            f"""SELECT COUNT(DISTINCT trace_id) AS n FROM evaluation_results
                WHERE system_id = ? AND component_id = ? AND status = 'ng'
                  AND trace_id IN ({placeholders})""",
            (system_id, component_id, *failed_trace_ids),
        ).fetchone()
        eval_failed_count = eval_row["n"] if eval_row else 0

    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="trace",
            source_id=representative["trace_id"],
            summary=f"{len(rows)} trace(s) scanned for {component_id}",
        )
    ]

    return WorkspaceTraceDigest(
        component_id=component_id,
        trace_count=len(rows),
        period_start=min(timestamps),
        period_end=max(timestamps),
        error_count=error_count,
        eval_failed_count=eval_failed_count,
        representative_input=representative_input,
        representative_output=_redact_and_truncate(representative["output_text"])
        if representative["output_text"]
        else None,
        evidence=builder.add_evidence(evidence_refs),
    )


def _evaluation_digest(
    conn: sqlite3.Connection, system_id: int, component_id: str, builder: _PackBuilder
) -> Optional[WorkspaceEvaluationDigest]:
    criteria_row = conn.execute(
        "SELECT COUNT(*) AS n FROM evaluation_criteria WHERE system_id = ? AND component_id = ?",
        (system_id, component_id),
    ).fetchone()
    result_rows = conn.execute(
        """SELECT * FROM evaluation_results WHERE system_id = ? AND component_id = ?
           ORDER BY id DESC LIMIT ?""",
        (system_id, component_id, MAX_TRACES_SCANNED_PER_COMPONENT),
    ).fetchall()
    if not result_rows:
        builder.note(f"evaluation digest for {component_id}: no evaluation results recorded")
        return None

    passed = sum(1 for r in result_rows if r["status"] == "ok")
    failed = sum(1 for r in result_rows if r["status"] == "ng")
    failure_reasons: List[str] = []
    for r in result_rows:
        if r["status"] != "ng" or not r["reason"]:
            continue
        reason = _truncate(r["reason"], 120)
        if reason not in failure_reasons:
            failure_reasons.append(reason)
        if len(failure_reasons) >= MAX_FAILURE_REASONS:
            break

    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="evaluation_result",
            source_id=str(result_rows[0]["id"]),
            summary=f"{passed} pass / {failed} fail for {component_id}",
        )
    ]

    return WorkspaceEvaluationDigest(
        component_id=component_id,
        criterion_count=criteria_row["n"] if criteria_row else 0,
        passed_count=passed,
        failed_count=failed,
        top_failure_reasons=failure_reasons,
        evidence=builder.add_evidence(evidence_refs),
    )


def _probe_plan_summary(
    conn: sqlite3.Connection, system_id: int, plan_id: int, builder: _PackBuilder
) -> Optional[WorkspaceProbePlanSummary]:
    plan_row = conn.execute(
        "SELECT * FROM probe_plans WHERE id = ? AND system_id = ?",
        (plan_id, system_id),
    ).fetchone()
    if plan_row is None:
        builder.note(f"probe plan {plan_id}: not found for this system")
        return None
    point_rows = conn.execute(
        "SELECT * FROM probe_points WHERE plan_id = ? AND system_id = ? ORDER BY id",
        (plan_id, system_id),
    ).fetchall()
    points = [
        WorkspaceProbePointSummary(
            component_id=p["component_id"],
            symbol=p["symbol"],
            path=p["path"],
            recommended_mode=p["recommended_mode"],
            side_effect_risk=p["side_effect_risk"],
            status=p["status"],
        )
        for p in point_rows[:MAX_PROBE_POINTS_PER_PLAN]
    ]
    if len(point_rows) > MAX_PROBE_POINTS_PER_PLAN:
        builder.note(
            f"probe plan {plan_id}: {len(point_rows) - MAX_PROBE_POINTS_PER_PLAN} "
            "probe point(s) omitted due to the per-category item budget"
        )
    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="probe_point",
            source_id=str(p["id"]),
            path=p["path"],
            start_line=p["line_start"],
            end_line=p["line_end"],
            summary=_truncate(p["reason"], 200),
        )
        for p in point_rows
    ]
    return WorkspaceProbePlanSummary(
        plan_id=plan_row["id"],
        feature_id=plan_row["feature_id"],
        objective=_truncate(plan_row["objective"]),
        status=plan_row["status"],
        probe_points=points,
        evidence=builder.add_evidence(evidence_refs),
    )


def _experiment_digest(
    conn: sqlite3.Connection, system_id: int, experiment_id: int, builder: _PackBuilder
):
    """Returns (digest, experiment_row) or None if the experiment isn't found."""
    exp_row = conn.execute(
        "SELECT * FROM experiments WHERE id = ? AND system_id = ?",
        (experiment_id, system_id),
    ).fetchone()
    if exp_row is None:
        builder.note(f"experiment {experiment_id}: not found for this system")
        return None
    variant_rows = conn.execute(
        "SELECT * FROM experiment_variants WHERE experiment_id = ? ORDER BY id",
        (experiment_id,),
    ).fetchall()
    if len(variant_rows) > MAX_VARIANTS_PER_EXPERIMENT:
        builder.note(
            f"experiment {experiment_id}: "
            f"{len(variant_rows) - MAX_VARIANTS_PER_EXPERIMENT} variant(s) omitted "
            "due to the per-category item budget"
        )
    variants = [
        WorkspaceExperimentVariantSummary(
            variant_key=v["variant_key"],
            label=v["label"],
            is_baseline=bool(v["is_baseline"]),
            status=v["status"],
            metrics=json.loads(v["metrics_json"] or "{}"),
        )
        for v in variant_rows[:MAX_VARIANTS_PER_EXPERIMENT]
    ]
    analysis_row = conn.execute(
        "SELECT * FROM experiment_analyses WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    evidence_refs = [
        WorkspaceEvidenceRef(
            source_type="experiment_variant",
            source_id=v["variant_key"],
            summary=f"patch {v['patch_hash'][:12]} ({v['status']})",
        )
        for v in variant_rows
    ]
    return WorkspaceExperimentDigest(
        experiment_id=exp_row["id"],
        feature_id=exp_row["feature_id"],
        objective=_truncate(exp_row["objective"]),
        baseline_commit=exp_row["baseline_commit"],
        status=exp_row["status"],
        variants=variants,
        analysis_status=analysis_row["status"] if analysis_row else "not_requested",
        analysis_narrative=_truncate(analysis_row["narrative"])
        if analysis_row and analysis_row["narrative"]
        else None,
        analysis_recommendation_variant_key=(
            analysis_row["recommendation_variant_key"] if analysis_row else None
        ),
        evidence=builder.add_evidence(evidence_refs),
    ), exp_row


def build_context_pack(conn: sqlite3.Connection, system_id: int, workspace, context_items) -> WorkspaceContextPack:
    """Deterministically build a WorkspaceContextPack for one workspace.

    `context_items` is the list of `workspace_context_items` rows pinned to
    the workspace. Only data referenced by those pinned items is collected;
    system/focus/repository summaries are always small and cheap so they are
    always included.
    """
    builder = _PackBuilder()

    system_summary = _system_summary(conn, system_id)
    focus_summary = _focus_summary(workspace)
    repository_summary = _repository_summary(conn, system_id, builder)
    snapshot_id = repository_summary.snapshot_id if repository_summary else None

    feature_ids = builder.select_ids(
        "feature", _sorted_unique_item_ids(context_items, "feature")
    )
    features = [
        digest
        for fid in feature_ids
        if (digest := _feature_digest(conn, system_id, fid, snapshot_id, builder))
        is not None
    ]

    # "component" and "trace" pins both key on component_id: "component"
    # surfaces the static profile, "trace" surfaces the runtime digest. Either
    # pin type also feeds the evaluation digest for that component.
    component_ids = builder.select_ids(
        "component", _sorted_unique_item_ids(context_items, "component")
    )
    trace_component_ids = builder.select_ids(
        "trace", _sorted_unique_item_ids(context_items, "trace")
    )

    components = [
        digest
        for cid in component_ids
        if (digest := _component_digest(conn, system_id, cid, builder)) is not None
    ]
    traces = [
        digest
        for cid in trace_component_ids
        if (digest := _trace_digest(conn, system_id, cid, builder)) is not None
    ]
    evaluation_component_ids = sorted(set(component_ids) | set(trace_component_ids))
    evaluations = [
        digest
        for cid in evaluation_component_ids
        if (digest := _evaluation_digest(conn, system_id, cid, builder)) is not None
    ]

    plan_ids = builder.select_ids(
        "probe_plan", _sorted_unique_item_ids(context_items, "probe_plan")
    )
    probe_plans = [
        digest
        for pid in plan_ids
        if pid.isdigit()
        and (digest := _probe_plan_summary(conn, system_id, int(pid), builder)) is not None
    ]
    for pid in plan_ids:
        if not pid.isdigit():
            builder.note(f"probe plan reference '{pid}' is not a valid plan id")

    experiment_ids = builder.select_ids(
        "experiment", _sorted_unique_item_ids(context_items, "experiment")
    )
    experiments: List[WorkspaceExperimentDigest] = []
    human_decisions: List[WorkspaceHumanDecisionDigest] = []
    for eid in experiment_ids:
        if not eid.isdigit():
            builder.note(f"experiment reference '{eid}' is not a valid experiment id")
            continue
        result = _experiment_digest(conn, system_id, int(eid), builder)
        if result is None:
            continue
        digest, exp_row = result
        experiments.append(digest)
        if exp_row["human_decision"] != "undecided":
            human_decisions.append(
                WorkspaceHumanDecisionDigest(
                    source_id=str(exp_row["id"]),
                    decision=exp_row["human_decision"],
                    variant_key=exp_row["human_decision_variant_key"],
                    note=_truncate(exp_row["human_decision_note"], 200),
                )
            )

    pack = WorkspaceContextPack(
        system=system_summary,
        focus=focus_summary,
        repository=repository_summary,
        features=features,
        components=components,
        traces=traces,
        evaluations=evaluations,
        probe_plans=probe_plans,
        experiments=experiments,
        human_decisions=human_decisions,
        evidence=builder.evidence,
        missing_information=builder.missing,
    )
    return _apply_character_budgets(pack)
