"""Experiment Workspace Runner API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..experiment_runner import comparison_payload, execute_variant, patch_hash
from ..git_ops import GitError, read_file_at_commit
from ..llm import LLMConfig, create_llm_client, is_reasoning_model
from ..models import (
    ExperimentAnalysisOut,
    ExperimentCommandOut,
    ExperimentCreate,
    ExperimentDecisionUpdate,
    ExperimentOut,
    ExperimentVariantResultOut,
)
from ..validation_runner import load_validation_config_text
from .snapshot_preflight import require_snapshot_preflight

router = APIRouter()
PROMPT_VERSION = "experiment-interpretation-v1"
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


def _analysis_out(row) -> ExperimentAnalysisOut:
    if row is None:
        return ExperimentAnalysisOut(status="pending")
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
    )


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


def _store_analysis_failure(
    experiment_id: int,
    config: LLMConfig,
    error: str,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE experiment_analyses
            SET status = 'analysis_failed', provider = ?, model = ?,
                prompt_version = ?, schema_version = ?,
                decision_method = 'reasoning_llm', narrative = NULL,
                recommendation_variant_key = NULL, recommendation_reason = NULL,
                risks_json = '[]', error = ?, created_at = ?
            WHERE experiment_id = ?
            """,
            (
                config.provider,
                config.model,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                error,
                time.time(),
                experiment_id,
            ),
        )


def _run_reasoning_analysis(experiment: ExperimentOut) -> None:
    config = LLMConfig.intelligence_from_env()
    if config.provider == "mock" or not is_reasoning_model(config.provider, config.model):
        _store_analysis_failure(
            experiment.id,
            config,
            "Experiment interpretation requires a configured reasoning model",
        )
        return
    try:
        client = create_llm_client(config)
        allowed_keys = [
            variant.variant_key
            for variant in experiment.variants
            if variant.status == "completed"
        ]
        if not allowed_keys:
            raise ValueError("No completed variant metrics are available for analysis")
        response = client.generate_text(
            [
                {
                    "role": "system",
                    "content": (
                        "Interpret deterministic experiment metrics. Return JSON only with "
                        "narrative, recommendation_variant_key, recommendation_reason, risks. "
                        "A recommendation is advisory and never a human adoption decision."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": experiment.objective,
                            "baseline_commit": experiment.baseline_commit,
                            "config_revision": experiment.config_revision,
                            "allowed_variant_keys": allowed_keys,
                            "comparison": experiment.comparison,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            max_tokens=1800,
        )
        parsed = _json_object(response)
        narrative = parsed.get("narrative")
        recommendation_reason = parsed.get("recommendation_reason")
        if not isinstance(narrative, str) or not narrative.strip():
            raise ValueError("narrative is required")
        if not isinstance(recommendation_reason, str):
            raise ValueError("recommendation_reason must be a string")
        recommendation = parsed.get("recommendation_variant_key")
        if recommendation is not None and recommendation not in allowed_keys:
            raise ValueError("recommendation_variant_key is not an experiment variant")
        risks = parsed.get("risks") or []
        if not isinstance(risks, list):
            raise ValueError("risks must be an array")
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE experiment_analyses
                SET status = 'completed', provider = ?, model = ?,
                    prompt_version = ?, schema_version = ?,
                    decision_method = 'reasoning_llm', narrative = ?,
                    recommendation_variant_key = ?, recommendation_reason = ?,
                    risks_json = ?, error = NULL, created_at = ?
                WHERE experiment_id = ?
                """,
                (
                    config.provider,
                    config.model,
                    PROMPT_VERSION,
                    SCHEMA_VERSION,
                    narrative.strip(),
                    recommendation,
                    recommendation_reason.strip(),
                    json.dumps([str(item) for item in risks]),
                    time.time(),
                    experiment.id,
                ),
            )
    except Exception as exc:
        _store_analysis_failure(experiment.id, config, str(exc))


@router.post("/experiments/{experiment_id}/run", response_model=ExperimentOut)
def run_experiment(
    experiment_id: int,
    system_id: int = Depends(get_system_id),
) -> ExperimentOut:
    now = time.time()
    with get_conn() as conn:
        row = _get_experiment_or_404(conn, experiment_id, system_id)
        if row["status"] == "running":
            raise HTTPException(status_code=409, detail="Experiment is already running")
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
            SET status = 'running', error = NULL, started_at = ?, completed_at = NULL
            WHERE id = ?
            """,
            (now, experiment_id),
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
        conn.execute(
            """
            UPDATE experiment_analyses
            SET status = 'pending', narrative = NULL,
                recommendation_variant_key = NULL, recommendation_reason = NULL,
                risks_json = '[]', error = NULL, created_at = NULL
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
