"""Experiment Workspace Runner API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import flow_orchestration
from ..auth import get_system_id
from ..db import get_conn
from ..experiment_runner import comparison_payload, execute_variant, patch_hash
from ..git_ops import GitError, read_file_at_commit
from ..intelligence_payloads import recorded_calls, store_payloads, wrap_for_recording
from ..llm import LLMConfig, create_llm_client, is_reasoning_model
from ..models import (
    ExperimentAnalysisOut,
    ExperimentCommandOut,
    ExperimentCreate,
    ExperimentDecisionUpdate,
    ExperimentOut,
    ExperimentRecordDetailOut,
    ExperimentRecordOut,
    ExperimentRecordVariantOut,
    ExperimentVariantResultOut,
)
from ..validation_runner import load_validation_config_text
from .execution_modes import gate_execution_target, record_attested_execution_observation
from .snapshot_preflight import require_snapshot_preflight

router = APIRouter()
PROMPT_VERSION = "experiment-interpretation-v2"
SCHEMA_VERSION = "experiment-analysis-v1"


def _materialize_improvement_publish_artifact(
    conn, experiment, variant, *, now: float
) -> int:
    """Persist the exact adopted-variant -> publish-artifact lineage.

    The GitHub publisher already has a hardened, approval-gated transport for
    unified diffs (``probe_patches``).  An improvement artifact deliberately
    reuses that transport, but its semantic identity lives in
    ``improvement_publish_artifacts`` so an instrumentation patch can never be
    mistaken for an adopted improvement.
    """
    existing = conn.execute(
        """SELECT id FROM improvement_publish_artifacts
            WHERE experiment_id = ? AND variant_id = ?""",
        (experiment["id"], variant["id"]),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    # The selected non-baseline variant and the baseline must both have
    # completed successfully; this is the experiment's validation gate.
    baseline = conn.execute(
        """SELECT id FROM experiment_variants
            WHERE experiment_id = ? AND is_baseline = 1 AND status = 'completed'
            ORDER BY id LIMIT 1""",
        (experiment["id"],),
    ).fetchone()
    if baseline is None:
        raise HTTPException(
            status_code=409,
            detail="Adoption requires a completed baseline before a publish artifact can be created",
        )

    run_id = conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at)
           VALUES (?, ?, 'probe_plan', 'deterministic', 'experiment-adoption',
                   'experiment-adoption-v1', 'improvement-publish-artifact-v1',
                   'deterministic', 'completed', 0, ?, ?)""",
        (experiment["system_id"], experiment["snapshot_id"], now, now),
    ).lastrowid
    plan_id = conn.execute(
        """INSERT INTO probe_plans
               (system_id, snapshot_id, intelligence_run_id, feature_id,
                objective, status, origin, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'approved', 'experiment_adoption', ?, ?)""",
        (
            experiment["system_id"],
            experiment["snapshot_id"],
            run_id,
            experiment["feature_id"],
            experiment["objective"],
            now,
            now,
        ),
    ).lastrowid
    patch_id = conn.execute(
        """INSERT INTO probe_patches
               (plan_id, system_id, snapshot_id, commit_sha, diff, status,
                cleanup_state, created_at)
           VALUES (?, ?, ?, ?, ?, 'generated', 'not_attempted', ?)""",
        (
            plan_id,
            experiment["system_id"],
            experiment["snapshot_id"],
            experiment["baseline_commit"],
            variant["patch_text"],
            now,
        ),
    ).lastrowid

    # The publisher re-checks these immediately before approval.  They are a
    # structural projection of the already completed Experiment, not a second
    # evaluation or an inferred success.
    for validation_variant in ("baseline", "probed"):
        conn.execute(
            """INSERT INTO validation_runs
                   (patch_id, system_id, variant, worktree_path,
                    overall_success, trace_status, network_isolation,
                    cleanup_state, created_at)
               VALUES (?, ?, ?, '', 1, 'not_checked', 'not_requested',
                       'not_attempted', ?)""",
            (patch_id, experiment["system_id"], validation_variant, now),
        )

    return conn.execute(
        """INSERT INTO improvement_publish_artifacts
               (system_id, experiment_id, variant_id, patch_id, status, created_at)
           VALUES (?, ?, ?, ?, 'ready', ?)""",
        (
            experiment["system_id"],
            experiment["id"],
            variant["id"],
            patch_id,
            now,
        ),
    ).lastrowid


def _json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("analysis response must be a JSON object")
    return value


def _analysis_out(row, *, version_id: Optional[int] = None) -> ExperimentAnalysisOut:
    if row is None:
        return ExperimentAnalysisOut(status="pending")
    keys = row.keys()
    notes_raw = row["notes_json"] if "notes_json" in keys else None
    return ExperimentAnalysisOut(
        status=row["status"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        narrative=row["narrative"],
        recommendation_variant_key=row["recommendation_variant_key"],
        recommendation_reason=row["recommendation_reason"],
        risks=json.loads(row["risks_json"] or "[]"),
        error=row["error"],
        created_at=row["created_at"],
        version_id=(
            version_id
            if version_id is not None
            else (row["version_id"] if "version_id" in keys else None)
        ),
        intelligence_run_id=(
            row["intelligence_run_id"] if "intelligence_run_id" in keys else None
        ),
        notes=json.loads(notes_raw or "[]"),
    )


def _analysis_history(conn, experiment_id: int) -> List[ExperimentAnalysisOut]:
    rows = conn.execute(
        """
        SELECT * FROM experiment_analysis_versions
        WHERE experiment_id = ? ORDER BY id DESC
        """,
        (experiment_id,),
    ).fetchall()
    return [_analysis_out(row, version_id=row["id"]) for row in rows]


def _run_allowed(row) -> bool:
    """The exact guard set of ``run_experiment`` (UX audit 2026-11-17 D7).

    Kept next to that endpoint's checks so the Dashboard's 実行 / 再実行
    control is offered only when the run would not be refused outright.
    """
    governed = (
        "flow_experiment_proposal_id" in row.keys()
        and row["flow_experiment_proposal_id"] is not None
    )
    return row["status"] != "running" and not governed


def _adopted_improvement_patch_id(conn, row) -> Optional[int]:
    """Resolve the current adoption's publish transport patch (C5).

    Exact lineage only: this experiment -> its adopted variant (by key) ->
    ``improvement_publish_artifacts`` -> ``patch_id``. No System-wide existence
    check and no timestamp ordering across experiments (#380).
    """
    if row["human_decision"] != "adopted" or not row["human_decision_variant_key"]:
        return None
    artifact = conn.execute(
        """SELECT a.patch_id
             FROM improvement_publish_artifacts a
             JOIN experiment_variants v ON v.id = a.variant_id
            WHERE a.experiment_id = ? AND a.system_id = ?
              AND v.experiment_id = a.experiment_id AND v.variant_key = ?
            ORDER BY a.id DESC LIMIT 1""",
        (row["id"], row["system_id"], row["human_decision_variant_key"]),
    ).fetchone()
    return artifact["patch_id"] if artifact is not None else None


def _experiment_out(conn, row) -> ExperimentOut:
    variant_rows = conn.execute(
        "SELECT * FROM experiment_variants WHERE experiment_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    variants = []
    comparison_rows = []
    for variant in variant_rows:
        commands = conn.execute(
            "SELECT * FROM experiment_commands WHERE variant_id = ? ORDER BY id",
            (variant["id"],),
        ).fetchall()
        metrics = json.loads(variant["metrics_json"] or "{}")
        artifacts = json.loads(variant["artifacts_json"] or "{}")
        variants.append(
            ExperimentVariantResultOut(
                id=variant["id"],
                variant_key=variant["variant_key"],
                label=variant["label"],
                is_baseline=bool(variant["is_baseline"]),
                patch_text=variant["patch_text"],
                patch_hash=variant["patch_hash"],
                source=variant["source"],
                risk_note=variant["risk_note"],
                status=variant["status"],
                error=variant["error"],
                workspace_path=variant["workspace_path"],
                cleanup_state=variant["cleanup_state"],
                cleanup_error=variant["cleanup_error"],
                metrics=metrics,
                artifacts=artifacts,
                commands=[
                    ExperimentCommandOut(
                        id=command["id"],
                        phase=command["phase"],
                        command=command["command"],
                        exit_code=command["exit_code"],
                        duration_ms=command["duration_ms"],
                        stdout=command["stdout"],
                        stderr=command["stderr"],
                        stdout_truncated=bool(command["stdout_truncated"]),
                        stderr_truncated=bool(command["stderr_truncated"]),
                        timed_out=bool(command["timed_out"]),
                    )
                    for command in commands
                ],
                started_at=variant["started_at"],
                completed_at=variant["completed_at"],
            )
        )
        comparison_rows.append(
            {
                "variant_key": variant["variant_key"],
                "label": variant["label"],
                "is_baseline": bool(variant["is_baseline"]),
                "status": variant["status"],
                "metrics": metrics,
            }
        )
    analysis = conn.execute(
        "SELECT * FROM experiment_analyses WHERE experiment_id = ?", (row["id"],)
    ).fetchone()
    return ExperimentOut(
        run_allowed=_run_allowed(row),
        adopted_improvement_patch_id=_adopted_improvement_patch_id(conn, row),
        id=row["id"],
        system_id=row["system_id"],
        feature_id=row["feature_id"],
        objective=row["objective"],
        snapshot_id=row["snapshot_id"],
        baseline_commit=row["baseline_commit"],
        config_revision=row["config_revision"],
        execution=json.loads(row["execution_config"]),
        status=row["status"],
        error=row["error"],
        human_decision=row["human_decision"],
        human_decision_variant_key=row["human_decision_variant_key"],
        human_decision_note=row["human_decision_note"],
        variants=variants,
        comparison=comparison_payload(comparison_rows),
        analysis=_analysis_out(analysis),
        analysis_history=_analysis_history(conn, row["id"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _get_experiment_or_404(conn, experiment_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM experiments WHERE id = ? AND system_id = ?",
        (experiment_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return row


@router.post("/experiments", response_model=ExperimentOut, status_code=201)
def create_experiment(
    payload: ExperimentCreate,
    system_id: int = Depends(get_system_id),
) -> ExperimentOut:
    now = time.time()

    # Issue #369: the shared preflight decides freshness, and a snapshot that
    # is definitively behind HEAD may only be used with the developer's stated
    # reason. Evaluated BEFORE the write connection is opened: `gather_preflight`
    # runs `git` subprocesses, and the SQLite lock is process-wide and
    # non-reentrant (see .claude/skills/control-server/SKILL.md).
    freshness, head_sha, stale_reason = require_snapshot_preflight(
        system_id, payload.snapshot_id, payload.stale_snapshot_reason
    )

    with get_conn() as conn:
        snapshot = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE id = ? AND system_id = ?
            """,
            (payload.snapshot_id, system_id),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Repository snapshot not found")
        if snapshot["status"] != "ready":
            raise HTTPException(status_code=400, detail="Repository snapshot is not ready")
        if not snapshot["repo_path"]:
            raise HTTPException(
                status_code=409, detail="Snapshot repository path is unavailable"
            )
        config_file = conn.execute(
            """
            SELECT path, content FROM snapshot_files
            WHERE snapshot_id = ? AND path IN ('probe-agent.yml', 'probe-agent.example.yml')
            ORDER BY CASE path WHEN 'probe-agent.yml' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (payload.snapshot_id,),
        ).fetchone()
        if config_file is not None:
            config_bytes = bytes(config_file["content"] or b"")
        else:
            config_bytes = None
            for config_path in ("probe-agent.yml", "probe-agent.example.yml"):
                try:
                    config_bytes = read_file_at_commit(
                        snapshot["repo_path"], snapshot["commit_sha"], config_path
                    )
                    break
                except GitError:
                    continue
            if config_bytes is None:
                raise HTTPException(
                    status_code=400,
                    detail="probe-agent.yml is not present at the pinned commit",
                )
        try:
            config = load_validation_config_text(
                config_bytes.decode("utf-8", errors="strict")
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid pinned probe-agent.yml: {exc}",
            ) from exc
        execution_config = {
            "install_commands": config.install_commands,
            "test_commands": config.test_commands,
            "smoke_commands": config.smoke_commands,
            "workload_commands": config.workload_commands,
            "timeout_seconds": config.timeout_seconds,
            "network": False,
            "env": config.env_allowlist,
            "result_artifact_path": config.result_artifact_path,
            "artifact_retention_seconds": config.artifact_retention_seconds,
        }
        config_revision = hashlib.sha256(config_bytes).hexdigest()
        cur = conn.execute(
            """
            INSERT INTO experiments
                (system_id, feature_id, objective, snapshot_id, baseline_commit,
                 config_revision, execution_config, status, created_at,
                 snapshot_freshness, head_sha_at_creation, stale_ack_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                system_id,
                payload.feature_id,
                payload.objective,
                payload.snapshot_id,
                snapshot["commit_sha"],
                config_revision,
                json.dumps(execution_config),
                now,
                freshness,
                head_sha,
                stale_reason,
            ),
        )
        experiment_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO experiment_variants
                (experiment_id, variant_key, label, is_baseline, patch_text,
                 patch_hash, source, risk_note, status)
            VALUES (?, 'baseline', 'Baseline', 1, '', ?, 'pinned_commit', '', 'planned')
            """,
            (experiment_id, hashlib.sha256(b"").hexdigest()),
        )
        for index, variant in enumerate(payload.variants, start=1):
            conn.execute(
                """
                INSERT INTO experiment_variants
                    (experiment_id, variant_key, label, is_baseline, patch_text,
                     patch_hash, source, risk_note, status)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, 'planned')
                """,
                (
                    experiment_id,
                    f"variant-{index}",
                    variant.label,
                    variant.patch_text,
                    patch_hash(variant.patch_text),
                    variant.source,
                    variant.risk_note,
                ),
            )
        conn.execute(
            "INSERT INTO experiment_analyses (experiment_id, status) VALUES (?, 'pending')",
            (experiment_id,),
        )
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        return _experiment_out(conn, row)


@router.get("/experiments", response_model=List[ExperimentOut])
def list_experiments(system_id: int = Depends(get_system_id)) -> List[ExperimentOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM experiments WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_experiment_out(conn, row) for row in rows]


@router.post("/experiments/cleanup")
def cleanup_expired_experiment_artifacts(
    system_id: int = Depends(get_system_id),
) -> Dict[str, int]:
    now = time.time()
    cleaned = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, execution_config, completed_at
            FROM experiments
            WHERE system_id = ? AND completed_at IS NOT NULL
            """,
            (system_id,),
        ).fetchall()
        for row in rows:
            config = json.loads(row["execution_config"])
            retention = int(config.get("artifact_retention_seconds", 86400))
            if row["completed_at"] + retention > now:
                continue
            variant_ids = [
                item["id"]
                for item in conn.execute(
                    "SELECT id FROM experiment_variants WHERE experiment_id = ?",
                    (row["id"],),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE experiment_variants SET artifacts_json = '{}' "
                "WHERE experiment_id = ?",
                (row["id"],),
            )
            for variant_id in variant_ids:
                conn.execute(
                    """
                    UPDATE experiment_commands
                    SET stdout = '', stderr = ''
                    WHERE variant_id = ?
                    """,
                    (variant_id,),
                )
            cleaned += 1
    return {"cleaned_experiments": cleaned}


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(
    experiment_id: int,
    system_id: int = Depends(get_system_id),
) -> ExperimentOut:
    with get_conn() as conn:
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        return _experiment_out(conn, row)


def _record_analysis_run(
    conn,
    experiment: ExperimentOut,
    config: LLMConfig,
    *,
    status: str,
    error: Optional[str],
    started_at: float,
    calls,
) -> int:
    """Record the analysis as an intelligence_run with its payloads (IK-0029)."""
    run_id = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method,
             status, error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'experiment_analysis', ?, ?, ?, ?, 'reasoning_llm',
                ?, ?, ?, ?, ?)
        """,
        (
            experiment.system_id,
            experiment.snapshot_id,
            config.provider,
            config.model,
            PROMPT_VERSION,
            SCHEMA_VERSION,
            status,
            error,
            1 if config.provider == "mock" else 0,
            started_at,
            time.time(),
        ),
    ).lastrowid
    store_payloads(conn, run_id, PROMPT_VERSION, calls)
    return run_id


_ANALYSIS_FIELDS = (
    "status",
    "provider",
    "model",
    "prompt_version",
    "schema_version",
    "decision_method",
    "narrative",
    "recommendation_variant_key",
    "recommendation_reason",
    "risks_json",
    "notes_json",
    "error",
    "intelligence_run_id",
    "created_at",
)


def _archive_unversioned_analysis(conn, experiment_id: int) -> None:
    """Keep a pre-versioning (or not yet versioned) current analysis.

    Analyses written before IK-0030 live only in ``experiment_analyses``;
    copy such a row into the version table once, before anything replaces
    it, so a re-run or re-analysis never destroys it. Additive only.
    """
    current = conn.execute(
        "SELECT * FROM experiment_analyses WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    if current is None or current["status"] == "pending":
        return
    if current["version_id"] is not None:
        return
    keys = current.keys()
    values = [
        current[field] if field in keys else None for field in _ANALYSIS_FIELDS
    ]
    values[_ANALYSIS_FIELDS.index("risks_json")] = current["risks_json"] or "[]"
    values[_ANALYSIS_FIELDS.index("notes_json")] = current["notes_json"] or "[]"
    version_id = conn.execute(
        f"""
        INSERT INTO experiment_analysis_versions
            (experiment_id, {", ".join(_ANALYSIS_FIELDS)})
        VALUES (?, {", ".join("?" for _ in _ANALYSIS_FIELDS)})
        """,
        (experiment_id, *values),
    ).lastrowid
    conn.execute(
        "UPDATE experiment_analyses SET version_id = ? WHERE experiment_id = ?",
        (version_id, experiment_id),
    )


def _write_analysis(conn, experiment_id: int, fields: Dict[str, Any]) -> int:
    """Append a version and point the current analysis at it."""
    _archive_unversioned_analysis(conn, experiment_id)
    values = [fields.get(field) for field in _ANALYSIS_FIELDS]
    values[_ANALYSIS_FIELDS.index("risks_json")] = fields.get("risks_json") or "[]"
    values[_ANALYSIS_FIELDS.index("notes_json")] = fields.get("notes_json") or "[]"
    version_id = conn.execute(
        f"""
        INSERT INTO experiment_analysis_versions
            (experiment_id, {", ".join(_ANALYSIS_FIELDS)})
        VALUES (?, {", ".join("?" for _ in _ANALYSIS_FIELDS)})
        """,
        (experiment_id, *values),
    ).lastrowid
    exists = conn.execute(
        "SELECT 1 FROM experiment_analyses WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO experiment_analyses (experiment_id, status) VALUES (?, 'pending')",
            (experiment_id,),
        )
    conn.execute(
        f"""
        UPDATE experiment_analyses
        SET {", ".join(f"{field} = ?" for field in _ANALYSIS_FIELDS)},
            version_id = ?
        WHERE experiment_id = ?
        """,
        (*values, version_id, experiment_id),
    )
    return version_id


def _store_analysis_failure(
    experiment: ExperimentOut,
    config: LLMConfig,
    error: str,
    *,
    started_at: Optional[float] = None,
    calls=(),
) -> None:
    with get_conn() as conn:
        run_id = _record_analysis_run(
            conn,
            experiment,
            config,
            status="failed",
            error=error,
            started_at=started_at or time.time(),
            calls=list(calls),
        )
        _write_analysis(
            conn,
            experiment.id,
            {
                "status": "analysis_failed",
                "provider": config.provider,
                "model": config.model,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "decision_method": "reasoning_llm",
                "risks_json": "[]",
                "notes_json": "[]",
                "error": error,
                "intelligence_run_id": run_id,
                "created_at": time.time(),
            },
        )


_CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿ｦ-ﾟ]")
PATCH_EXCERPT_CHARS = 300


def _output_language(objective: str) -> str:
    """The language the interpretation must be written in (IK-0030).

    The objective is the developer's own words; answer in that language. Only
    Japanese vs. English is distinguished -- a CJK character in the objective
    means Japanese.
    """
    return "Japanese" if _CJK.search(objective or "") else "English"


def _variant_context(experiment: ExperimentOut) -> List[Dict[str, Any]]:
    """What each variant *is*, so the model does not guess from its label.

    A label such as "corr8" was read as "longer correlation" twice when the
    risk note said the opposite (sim-sandbox E-0009, IK-0030).
    """
    context = []
    for variant in experiment.variants:
        patch = variant.patch_text or ""
        excerpt = patch[:PATCH_EXCERPT_CHARS]
        context.append(
            {
                "variant_key": variant.variant_key,
                "label": variant.label,
                "is_baseline": variant.is_baseline,
                "source": variant.source,
                "risk_note": variant.risk_note,
                "patch_excerpt": excerpt,
                "patch_truncated": len(patch) > len(excerpt),
            }
        )
    return context


def _analysis_messages(
    experiment: ExperimentOut, allowed_keys: List[str]
) -> List[Dict[str, str]]:
    language = _output_language(experiment.objective)
    system = (
        "Interpret deterministic experiment metrics. Return JSON only with the keys "
        "narrative (string), recommendation_variant_key (one of allowed_variant_keys, "
        "or null), recommendation_reason (string) and risks (array of strings). "
        "A recommendation is advisory and never a human adoption decision. "
        "Each entry of `variants` says what that variant changes: read its "
        "risk_note, source and patch_excerpt to learn what the variant does, and "
        "do not infer the meaning of a variant from its label alone. "
        f"Write narrative, recommendation_reason and every risk in {language}, "
        "the language of `objective`; keep variant keys, labels and numbers as given."
    )
    user = {
        "objective": experiment.objective,
        "output_language": language,
        "baseline_commit": experiment.baseline_commit,
        "config_revision": experiment.config_revision,
        "allowed_variant_keys": allowed_keys,
        "variants": _variant_context(experiment),
        "comparison": experiment.comparison,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _analysis_max_tokens() -> int:
    value = int(os.getenv("INTELLIGENCE_MAX_OUTPUT_TOKENS", "128000"))
    if value < 1:
        raise ValueError("INTELLIGENCE_MAX_OUTPUT_TOKENS must be positive")
    return value


def _text_of(value: Any) -> Optional[str]:
    """A string field the model may have returned as a list or object."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_text_of(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "summary", "description", "risk", "reason", "detail"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _normalize_analysis(
    parsed: Dict[str, Any],
    allowed_keys: List[str],
    experiment: ExperimentOut,
) -> Dict[str, Any]:
    """Normalise -> resolve -> drop only the bad field (K-0001, IK-0031).

    Only a missing narrative fails the analysis: it is the one field nothing
    else can stand in for. A shape slip (risks as a string, a reason as a
    list) is normalised, and a recommendation that does not resolve to an
    experiment variant is dropped with a note while the narrative is kept.
    Every adjustment is reported in ``notes``.
    """
    notes: List[str] = []

    narrative = _text_of(parsed.get("narrative"))
    if not narrative:
        raise ValueError("narrative is required")
    if not isinstance(parsed.get("narrative"), str):
        notes.append(
            f"narrative was a {type(parsed.get('narrative')).__name__}; joined into text"
        )

    raw_reason = parsed.get("recommendation_reason")
    reason = _text_of(raw_reason) or ""
    if raw_reason is not None and not isinstance(raw_reason, str):
        notes.append(
            f"recommendation_reason was a {type(raw_reason).__name__}; joined into text"
        )

    raw_risks = parsed.get("risks")
    if raw_risks is None:
        risks: List[str] = []
    elif isinstance(raw_risks, list):
        risks = [text for text in (_text_of(item) for item in raw_risks) if text]
        if any(not isinstance(item, str) for item in raw_risks):
            notes.append("risks contained non-string items; converted to text")
    elif isinstance(raw_risks, str):
        risks = [raw_risks.strip()] if raw_risks.strip() else []
        notes.append("risks was a string; normalised to a one-item list")
    else:
        text = _text_of(raw_risks)
        risks = [text] if text else []
        notes.append(
            f"risks was a {type(raw_risks).__name__}; normalised to a one-item list"
        )

    raw_key = parsed.get("recommendation_variant_key")
    recommendation: Optional[str] = None
    if raw_key is not None and not (isinstance(raw_key, str) and not raw_key.strip()):
        candidate = str(raw_key).strip()
        if candidate in allowed_keys:
            recommendation = candidate
        else:
            # Resolve the two slips a model makes with a listed key: case or
            # spacing, and naming the variant by its label instead of its key.
            by_key = {key.lower(): key for key in allowed_keys}
            by_label = {
                variant.label.strip().lower(): variant.variant_key
                for variant in experiment.variants
                if variant.variant_key in allowed_keys
            }
            resolved = by_key.get(candidate.lower()) or by_label.get(candidate.lower())
            if resolved is not None:
                recommendation = resolved
                notes.append(
                    f"recommendation_variant_key {candidate!r} resolved to {resolved!r}"
                )
            else:
                notes.append(
                    f"recommendation_variant_key {candidate!r} is not an experiment "
                    "variant; recommendation dropped, narrative kept"
                )
    return {
        "narrative": narrative,
        "recommendation_variant_key": recommendation,
        "recommendation_reason": reason,
        "risks": risks,
        "notes": notes,
    }


def _run_reasoning_analysis(experiment: ExperimentOut) -> None:
    config = LLMConfig.intelligence_from_env()
    if config.provider == "mock" or not is_reasoning_model(config.provider, config.model):
        _store_analysis_failure(
            experiment,
            config,
            "Experiment interpretation requires a configured reasoning model",
        )
        return
    started_at = time.time()
    client = None
    try:
        client = wrap_for_recording(create_llm_client(config))
        allowed_keys = [
            variant.variant_key
            for variant in experiment.variants
            if variant.status == "completed"
        ]
        if not allowed_keys:
            raise ValueError("No completed variant metrics are available for analysis")
        response = client.generate_text(
            _analysis_messages(experiment, allowed_keys),
            # Same budget as drafts / code-links / probe plans (IK-0028): a
            # reasoning model spends output tokens on reasoning first.
            max_tokens=_analysis_max_tokens(),
        )
        parsed = _json_object(response)
        normalized = _normalize_analysis(parsed, allowed_keys, experiment)
        narrative = normalized["narrative"]
        recommendation = normalized["recommendation_variant_key"]
        recommendation_reason = normalized["recommendation_reason"]
        risks = normalized["risks"]
        notes = normalized["notes"]
        with get_conn() as conn:
            run_id = _record_analysis_run(
                conn,
                experiment,
                config,
                status="completed",
                error=None,
                started_at=started_at,
                calls=recorded_calls(client),
            )
            _write_analysis(
                conn,
                experiment.id,
                {
                    "status": "completed",
                    "provider": config.provider,
                    "model": config.model,
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION,
                    "decision_method": "reasoning_llm",
                    "narrative": narrative,
                    "recommendation_variant_key": recommendation,
                    "recommendation_reason": recommendation_reason,
                    "risks_json": json.dumps(risks, ensure_ascii=False),
                    "notes_json": json.dumps(notes, ensure_ascii=False),
                    "error": None,
                    "intelligence_run_id": run_id,
                    "created_at": time.time(),
                },
            )
    except Exception as exc:
        _store_analysis_failure(
            experiment,
            config,
            str(exc),
            started_at=started_at,
            calls=recorded_calls(client),
        )


@router.post("/experiments/{experiment_id}/analyze", response_model=ExperimentOut)
def analyze_experiment(
    experiment_id: int,
    system_id: int = Depends(get_system_id),
) -> ExperimentOut:
    """Re-run only the reasoning interpretation of a finished Experiment.

    IK-0030: variants are not executed again and metrics are not touched; the
    new interpretation is appended to ``analysis_history`` and becomes the
    current ``analysis``.
    """
    with get_conn() as conn:
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        if row["status"] not in ("completed", "failed"):
            raise HTTPException(
                status_code=409,
                detail="Only a finished (completed or failed) Experiment can be analyzed",
            )
        current = _experiment_out(conn, row)
    _run_reasoning_analysis(current)
    return get_experiment(experiment_id, system_id)


RECORD_TOOL = "probe-agent"
DECIDED = ("adopted", "rejected", "needs_more_data")
_RECORD_TEXT = {
    "Japanese": {
        "objective": "目的",
        "snapshot": "スナップショット",
        "variants": "variant",
        "baseline": "基準",
        "analysis": "解釈",
        "no_analysis": "なし",
        "recommendation": "推奨（助言）",
        "no_recommendation": "なし",
        "decision": "人の判断",
        "note": "判断メモ",
        "open": "（",
        "close": "）",
        "sep": "・",
        "decisions": {
            "adopted": "採用",
            "rejected": "不採用",
            "needs_more_data": "追加データが必要",
            "undecided": "未",
        },
    },
    "English": {
        "objective": "Objective",
        "snapshot": "Snapshot",
        "variants": "Variants",
        "baseline": "baseline variant",
        "analysis": "Interpretation",
        "no_analysis": "none",
        "recommendation": "Recommendation (advisory)",
        "no_recommendation": "none",
        "decision": "Human decision",
        "note": "Decision note",
        "open": " (",
        "close": ")",
        "sep": ", ",
        "decisions": {
            "adopted": "adopted",
            "rejected": "rejected",
            "needs_more_data": "needs more data",
            "undecided": "not yet",
        },
    },
}


def _record_url(experiment_id: int) -> str:
    """dashboard の Experiment 行への URL（IK-0035）。

    control server は dashboard の公開先を知らないので、``PROBE_DASHBOARD_URL``
    が設定されているときだけ組み立てる。未設定なら空文字（台帳では任意項目）。
    """
    base = (os.getenv("PROBE_DASHBOARD_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/experiments?experiment={experiment_id}"


def _iso_utc(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _recorded_analysis(experiment: ExperimentOut) -> Optional[ExperimentAnalysisOut]:
    """台帳に写す解釈: 現在の解釈が完了していればそれ、なければ履歴の最新の完了版。"""
    if experiment.analysis.status == "completed":
        return experiment.analysis
    for version in experiment.analysis_history:  # 新しい順
        if version.status == "completed":
            return version
    return None


def _variant_name(experiment: ExperimentOut, variant_key: Optional[str], text) -> str:
    labels = {v.variant_key: v.label for v in experiment.variants}
    label = labels.get(variant_key or "")
    if label is None or label == variant_key:
        return variant_key or ""
    return f"{label}{text['open']}{variant_key}{text['close']}"


def _record_conditions(experiment: ExperimentOut, text) -> str:
    lines = [
        f"{text['objective']}: {experiment.objective}",
        f"{text['snapshot']}: #{experiment.snapshot_id}"
        f"{text['open']}baseline_commit {experiment.baseline_commit[:7]}{text['close']}",
        f"{text['variants']}:",
    ]
    for variant in experiment.variants:
        tag = variant.variant_key
        if variant.is_baseline:
            tag += text["sep"] + text["baseline"]
        line = f"- {variant.label}{text['open']}{tag}{text['close']}"
        summary = (variant.risk_note or "").strip()
        if summary:
            line += f": {summary}"
        lines.append(line)
    return "\n".join(lines)


def _record_analysis_summary(
    experiment: ExperimentOut, analysis: Optional[ExperimentAnalysisOut], text
) -> str:
    """LLM の解釈と推奨（助言）の文。人の判断とは混ぜない（IK-0036）。"""
    if analysis is None:
        return (
            f"{text['analysis']}: {text['no_analysis']}"
            f"{text['open']}{experiment.analysis.status}{text['close']}"
        )
    heading = text["analysis"]
    if analysis.version_id is not None:
        heading += f"{text['open']}v{analysis.version_id}{text['close']}"
    lines = [f"{heading}: {(analysis.narrative or '').strip()}"]
    if analysis.recommendation_variant_key:
        target = _variant_name(experiment, analysis.recommendation_variant_key, text)
    else:
        target = text["no_recommendation"]
    reason = (analysis.recommendation_reason or "").strip()
    lines.append(f"{text['recommendation']}: {target}" + (f": {reason}" if reason else ""))
    return "\n".join(lines)


def _record_result_summary(experiment: ExperimentOut, text) -> str:
    """台帳の結果の文は人の判断だけで組み立てる（IK-0036）。

    LLM の推奨（助言）を同じ文に入れると、台帳を読む学習者に助言が判断として
    読まれる（Experiment #2 では助言 n600 と判断 baseline が食い違った）。
    判断が無ければ「未」とだけ書き、メモも写さない。
    """
    decision = experiment.human_decision or "undecided"
    if decision not in DECIDED:
        return f"{text['decision']}: {text['decisions']['undecided']}"
    decided = text["decisions"].get(decision, decision)
    if experiment.human_decision_variant_key:
        chosen = _variant_name(experiment, experiment.human_decision_variant_key, text)
        decided += f"{text['open']}{chosen}{text['close']}"
    lines = [f"{text['decision']}: {decided}"]
    note = (experiment.human_decision_note or "").strip()
    if note:
        lines.append(f"{text['note']}: {note}")
    return "\n".join(lines)


def experiment_record(experiment: ExperimentOut) -> ExperimentRecordOut:
    """台帳の外部検証の線に写せる記録を組み立てる（IK-0035）。

    LLM も乱数も時刻も使わない: 同じ Experiment の行からは常に同じ記録になる。
    文の言語は解釈と同じく objective の言語（IK-0030 の ``_output_language``）。
    """
    text = _RECORD_TEXT[_output_language(experiment.objective)]
    analysis = _recorded_analysis(experiment)
    complete = (
        experiment.status == "completed"
        and analysis is not None
        and experiment.human_decision in DECIDED
    )
    return ExperimentRecordOut(
        tool=RECORD_TOOL,
        run_ref=f"experiment:{experiment.id}",
        url=_record_url(experiment.id),
        executed_at=_iso_utc(experiment.completed_at),
        conditions=_record_conditions(experiment, text),
        result_summary=_record_result_summary(experiment, text),
        analysis_summary=_record_analysis_summary(experiment, analysis, text),
        complete=complete,
        detail=ExperimentRecordDetailOut(
            experiment_id=experiment.id,
            feature_id=experiment.feature_id,
            snapshot_id=experiment.snapshot_id,
            baseline_commit=experiment.baseline_commit,
            status=experiment.status,
            variants=[
                ExperimentRecordVariantOut(
                    variant_key=v.variant_key,
                    label=v.label,
                    status=v.status,
                    test_pass_rate=v.metrics.get("test_pass_rate"),
                    criteria_pass_rate=v.metrics.get("criteria_pass_rate"),
                )
                for v in experiment.variants
            ],
            analysis_version=analysis.version_id if analysis is not None else None,
            human_decision=experiment.human_decision,
            human_decision_variant_key=experiment.human_decision_variant_key,
        ),
    )


@router.get("/experiments/{experiment_id}/record", response_model=ExperimentRecordOut)
def get_experiment_record(
    experiment_id: int,
    system_id: int = Depends(get_system_id),
) -> ExperimentRecordOut:
    """台帳に写す記録を返す読み取り専用の口（IK-0035）。何も書き込まない。"""
    with get_conn() as conn:
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        experiment = _experiment_out(conn, row)
    return experiment_record(experiment)


@router.post("/experiments/{experiment_id}/run", response_model=ExperimentOut)
def run_experiment(
    experiment_id: int,
    flow_experiment_proposal_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
    response: Response = None,
) -> ExperimentOut:
    now = time.time()
    with get_conn() as conn:
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        if row["status"] == "running":
            raise HTTPException(status_code=409, detail="Experiment is already running")
        if row["flow_experiment_proposal_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "experiment_execution_immutable",
                    "message": (
                        "A governed Experiment execution is immutable; create a new "
                        "Experiment for another attempt."
                    ),
                },
            )
        candidate_refs = tuple(
            f"patch_sha256:{variant['patch_hash']}"
            for variant in conn.execute(
                "SELECT patch_hash FROM experiment_variants "
                "WHERE experiment_id = ? AND is_baseline = 0 ORDER BY id",
                (experiment_id,),
            ).fetchall()
        )
        conn.execute("BEGIN IMMEDIATE")
        # Execution-mode gate (#412 §4.3). An Experiment runs candidate source
        # variants in isolated worktrees, so it is `candidate_execution`. The
        # subject is the Experiment's `feature_id`, mapped to an Evolution Node
        # by `evolution_node_link(link_kind='feature')`. A Feature no Node links
        # is `unmapped` and runs exactly as before -- migrating existing
        # Features onto Nodes is an explicit Epic non-goal. The gate sits before
        # the status/variant reset below so a refusal leaves the Experiment
        # untouched rather than half-reset.
        execution_gate = gate_execution_target(
            conn,
            system_id=system_id,
            capability="candidate_execution",
            target_kind="feature",
            target_ref=row["feature_id"],
            response=response,
            flow_experiment_proposal_id=flow_experiment_proposal_id,
            candidate_refs=candidate_refs,
            candidate_required=True,
            snapshot_id=row["snapshot_id"],
        )
        snapshot = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE id = ? AND system_id = ?
            """,
            (row["snapshot_id"], system_id),
        ).fetchone()
        if snapshot is None or not snapshot["repo_path"]:
            raise HTTPException(
                status_code=409, detail="Snapshot repository path is unavailable"
            )
        conn.execute(
            """
            UPDATE experiments
            SET status = 'running', error = NULL, started_at = ?, completed_at = NULL,
                flow_experiment_proposal_id = ?,
                flow_experiment_candidate_refs_json = ?
            WHERE id = ?
            """,
            (
                now,
                flow_experiment_proposal_id,
                json.dumps(candidate_refs, ensure_ascii=False),
                experiment_id,
            ),
        )
        if flow_experiment_proposal_id is not None:
            flow_orchestration.record_execution(
                conn,
                system_id=system_id,
                proposal_id=flow_experiment_proposal_id,
                execution_kind="experiment",
                execution_ref=str(experiment_id),
                actor="execution-gate",
                actor_kind="system",
                note="Recorded at the governed Experiment execution boundary.",
                now=now,
            )
        record_attested_execution_observation(
            conn,
            gate=execution_gate,
            execution_kind="experiment",
            execution_ref=str(experiment_id),
        )
        conn.execute(
            "DELETE FROM experiment_commands WHERE variant_id IN "
            "(SELECT id FROM experiment_variants WHERE experiment_id = ?)",
            (experiment_id,),
        )
        conn.execute(
            """
            UPDATE experiment_variants
            SET status = 'planned', error = NULL, workspace_path = NULL,
                cleanup_state = 'not_attempted', cleanup_error = NULL,
                metrics_json = '{}', artifacts_json = '{}',
                started_at = NULL, completed_at = NULL
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        )
        # IK-0030: a re-run keeps the previous interpretation in the history.
        _archive_unversioned_analysis(conn, experiment_id)
        conn.execute(
            """
            UPDATE experiment_analyses
            SET status = 'pending', narrative = NULL,
                recommendation_variant_key = NULL, recommendation_reason = NULL,
                risks_json = '[]', error = NULL, created_at = NULL,
                notes_json = NULL, intelligence_run_id = NULL, version_id = NULL
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        )
        variants = conn.execute(
            "SELECT * FROM experiment_variants WHERE experiment_id = ? ORDER BY id",
            (experiment_id,),
        ).fetchall()
        execution_config = json.loads(row["execution_config"])
        repo_path = snapshot["repo_path"]
        commit_sha = row["baseline_commit"]
        conn.execute("COMMIT")

    workspace_root = os.getenv("PROBE_EXPERIMENT_WORKSPACE_BASE", "/tmp/probe-experiments")
    os.makedirs(workspace_root, exist_ok=True)
    failures = []
    for variant in variants:
        started_at = time.time()
        with get_conn() as conn:
            conn.execute(
                "UPDATE experiment_variants SET status = 'running', started_at = ? WHERE id = ?",
                (started_at, variant["id"]),
            )
        execution = execute_variant(
            repo_path=repo_path,
            commit_sha=commit_sha,
            workspace_base=os.path.join(workspace_root, str(experiment_id)),
            patch_text=variant["patch_text"],
            execution_config=execution_config,
        )
        completed_at = time.time()
        if execution.status != "completed":
            failures.append(f"{variant['variant_key']}: {execution.status}")
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE experiment_variants
                SET status = ?, error = ?, workspace_path = ?,
                    cleanup_state = ?, cleanup_error = ?, metrics_json = ?,
                    artifacts_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    execution.status,
                    execution.error,
                    execution.workspace_path,
                    execution.cleanup_state,
                    execution.cleanup_error,
                    json.dumps(execution.metrics),
                    json.dumps(execution.artifacts),
                    completed_at,
                    variant["id"],
                ),
            )
            for command in execution.commands:
                conn.execute(
                    """
                    INSERT INTO experiment_commands
                        (variant_id, phase, command, exit_code, duration_ms,
                         stdout, stderr, stdout_truncated, stderr_truncated, timed_out)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        variant["id"],
                        command.phase,
                        command.command,
                        command.exit_code,
                        command.duration_ms,
                        command.stdout,
                        command.stderr,
                        1 if command.stdout_truncated else 0,
                        1 if command.stderr_truncated else 0,
                        1 if command.timed_out else 0,
                    ),
                )

    with get_conn() as conn:
        final_status = "failed" if len(failures) == len(variants) else "completed"
        conn.execute(
            """
            UPDATE experiments
            SET status = ?, error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                final_status,
                "; ".join(failures) if failures else None,
                time.time(),
                experiment_id,
            ),
        )
        current = _experiment_out(
            conn, _get_experiment_or_404(conn, experiment_id, system_id)
        )
    _run_reasoning_analysis(current)
    return get_experiment(experiment_id, system_id)


@router.put("/experiments/{experiment_id}/decision", response_model=ExperimentOut)
def update_experiment_decision(
    experiment_id: int,
    payload: ExperimentDecisionUpdate,
    system_id: int = Depends(get_system_id),
) -> ExperimentOut:
    with get_conn() as conn:
        experiment = _get_experiment_or_404(conn, experiment_id, system_id)
        decision_variant_key = None
        if payload.decision == "adopted":
            if experiment["status"] != "completed":
                raise HTTPException(
                    status_code=409,
                    detail="Only a completed experiment can be adopted",
                )
            if not payload.note.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Adoption requires a human decision note",
                )
            if not payload.variant_key:
                raise HTTPException(
                    status_code=422,
                    detail="Adoption requires a variant_key",
                )
            variant = conn.execute(
                """
                SELECT *
                FROM experiment_variants
                WHERE experiment_id = ? AND variant_key = ?
                """,
                (experiment_id, payload.variant_key),
            ).fetchone()
            if (
                variant is None
                or bool(variant["is_baseline"])
                or variant["status"] != "completed"
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Adopted variant must be a completed non-baseline variant",
                )
            decision_variant_key = payload.variant_key
            _materialize_improvement_publish_artifact(
                conn, experiment, variant, now=time.time()
            )
        conn.execute(
            """
            UPDATE experiments
            SET human_decision = ?, human_decision_variant_key = ?,
                human_decision_note = ?
            WHERE id = ?
            """,
            (
                payload.decision,
                decision_variant_key,
                payload.note,
                experiment_id,
            ),
        )
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        return _experiment_out(conn, row)
