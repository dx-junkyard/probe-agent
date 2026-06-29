import fnmatch
import json
import os
import re
import time
import hashlib
from dataclasses import replace
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..auth import Principal, get_system_id, require_user
from ..capability_hierarchy import (
    EntrypointRecord as HierarchyEntrypointRecord,
    SymbolRecord as HierarchySymbolRecord,
    build_hierarchy,
    propose_capability_grouping,
)
from ..capability_hierarchy import PROMPT_VERSION as HIERARCHY_PROMPT_VERSION
from ..capability_hierarchy import SCHEMA_VERSION as HIERARCHY_SCHEMA_VERSION
from ..code_indexer import index_snapshot_files
from .. import drift as drift_service
from ..code_mapper import (
    FeatureContext,
    generate_code_mapping,
)
from ..code_mapper import PROMPT_VERSION as MAPPING_PROMPT_VERSION
from ..code_mapper import SCHEMA_VERSION as MAPPING_SCHEMA_VERSION
from ..db import get_conn
from ..draft_generator import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    GenerationResult,
    generate_drafts,
)
from ..git_ops import (
    GitError,
    create_snapshot,
    discover_repository_candidates,
    read_file_at_commit,
)
from ..llm import LLMConfig, LLMError, create_llm_client, is_reasoning_model
from ..refresh_proposal import (
    REVIEW_REQUIRED_NOTE,
    RefreshContext,
    propose_refresh,
)
from ..refresh_proposal import PROMPT_VERSION as REFRESH_PROMPT_VERSION
from ..refresh_proposal import SCHEMA_VERSION as REFRESH_SCHEMA_VERSION
from ..models import (
    ApiScanPatternOut,
    ApiScanRequest,
    ApiScanResultOut,
    ExplanationRefreshListOut,
    ExplanationRefreshOut,
    ExplanationRefreshProposalOut,
    RefreshProposalRequest,
    CandidateFlowOut,
    CodeSymbolOut,
    DraftGenerationResult,
    EntrypointCountsOut,
    EvidenceRefOut,
    FlowEdgeOut,
    FlowEntrypointOut,
    FlowEntrypointsOut,
    FlowGraphOut,
    FlowGraphRequest,
    FlowNodeOut,
    ProbePlanFromFlowRequest,
    ProbePreviewOut,
    SourceMetadataOut,
    ExplanationAnchorOut,
    ExplanationAnchorsOut,
    CapabilityHierarchyOut,
    CapabilityOut,
    CapabilityElementOut,
    CapabilityPurposeOut,
    SupportingElementOut,
    HierarchyProvenanceOut,
    AnchorDriftOut,
    CapabilityDriftOut,
    CapabilityHierarchyDriftOut,
    DriftCountsOut,
    ApiRoleCardOut,
    ApiRoleCardsOut,
    FeatureCodeLinkOut,
    FeatureCodeLinksOut,
    FeatureDraftOut,
    FeatureEvidence,
    IntelligenceRunOut,
    LatestDraftsOut,
    LinkReviewUpdate,
    ProbePatchApplyRequest,
    ProbePatchOut,
    ProbePlanOut,
    ProbePlansListOut,
    ProbePointOut,
    ProbePointStatusUpdate,
    RepositoryCandidateOut,
    RepositoryConfigOut,
    SystemUnderstandingOut,
    RepositoryConfigUpdate,
    SnapshotFileOut,
    SnapshotOut,
    SymbolIndexOut,
    SymbolIndexWarningOut,
    SystemProfileDraftOut,
    ValidationCommandOut,
    ValidationRunOut,
)

router = APIRouter()


def _probe_patch_out(conn, row) -> ProbePatchOut:
    val_rows = conn.execute(
        "SELECT * FROM validation_runs WHERE patch_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    validation_runs = []
    for vr in val_rows:
        cmd_rows = conn.execute(
            "SELECT * FROM validation_commands WHERE run_id = ? ORDER BY id",
            (vr["id"],),
        ).fetchall()
        validation_runs.append(ValidationRunOut(
            id=vr["id"],
            patch_id=vr["patch_id"],
            system_id=vr["system_id"],
            variant=vr["variant"],
            worktree_path=vr["worktree_path"],
            overall_success=bool(vr["overall_success"]),
            total_duration_ms=vr["total_duration_ms"],
            trace_received=(
                None if vr["trace_received"] is None
                else bool(vr["trace_received"])
            ),
            trace_status=vr["trace_status"],
            network_isolation=vr["network_isolation"],
            cleanup_state=vr["cleanup_state"],
            cleanup_error=vr["cleanup_error"],
            commands=[
                ValidationCommandOut(
                    id=cr["id"],
                    command=cr["command"],
                    exit_code=cr["exit_code"],
                    duration_ms=cr["duration_ms"],
                    stdout=cr["stdout"],
                    stderr=cr["stderr"],
                    stdout_truncated=bool(cr["stdout_truncated"]),
                    stderr_truncated=bool(cr["stderr_truncated"]),
                    timed_out=bool(cr["timed_out"]),
                )
                for cr in cmd_rows
            ],
            error=vr["error"],
            created_at=vr["created_at"],
        ))
    return ProbePatchOut(
        id=row["id"],
        plan_id=row["plan_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        diff=row["diff"],
        worktree_path=row["worktree_path"],
        skipped=json.loads(row["skipped"]),
        status=row["status"],
        error=row["error"],
        cleanup_state=row["cleanup_state"],
        cleanup_error=row["cleanup_error"],
        apply_status=row["apply_status"],
        apply_error=row["apply_error"],
        applied_at=row["applied_at"],
        applied_by_user_id=row["applied_by_user_id"],
        validation_runs=validation_runs,
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# System Understanding (Issue #86)
# ---------------------------------------------------------------------------


@router.get(
    "/repository/system-understanding",
    response_model=SystemUnderstandingOut,
)
def get_system_understanding_endpoint(
    system_id: int = Depends(get_system_id),
) -> SystemUnderstandingOut:
    from ..system_understanding_service import get_system_understanding
    summary = get_system_understanding(system_id)
    return _system_understanding_to_out(summary)


@router.post(
    "/repository/system-understanding/build",
    response_model=SystemUnderstandingOut,
)
def build_system_understanding_endpoint(
    system_id: int = Depends(get_system_id),
) -> SystemUnderstandingOut:
    from ..system_understanding_service import build_system_understanding
    summary = build_system_understanding(system_id)
    return _system_understanding_to_out(summary)


def _system_understanding_to_out(summary) -> SystemUnderstandingOut:
    from ..models import (
        SystemUnderstandingOut,
        SystemUnderstandingPipelineStepOut,
        SystemUnderstandingNextActionOut,
        SystemUnderstandingGapSummaryOut,
        SystemUnderstandingMetadataCoverageOut,
        SystemUnderstandingCapabilitySummaryOut,
        SystemUnderstandingEntrypointSummaryOut,
        SystemUnderstandingSymbolSummaryOut,
        SystemUnderstandingPurposeOut,
        SystemUnderstandingGapOut,
    )
    pipeline = [
        SystemUnderstandingPipelineStepOut(step=s.step, status=s.status, detail=s.detail)
        for s in summary.pipeline
    ]
    purpose = None
    if summary.purpose:
        purpose = SystemUnderstandingPurposeOut(**summary.purpose)
    capabilities = [
        SystemUnderstandingCapabilitySummaryOut(**c) for c in summary.capabilities
    ]
    entrypoints = [
        SystemUnderstandingEntrypointSummaryOut(**e) for e in summary.entrypoints
    ]
    major_symbols = [
        SystemUnderstandingSymbolSummaryOut(**s) for s in summary.major_symbols
    ]
    gaps = [
        SystemUnderstandingGapOut(**g) for g in summary.gaps
    ]
    gap_summary = [
        SystemUnderstandingGapSummaryOut(gap_type=gs.gap_type, count=gs.count)
        for gs in summary.gap_summary
    ]
    metadata_coverage = None
    if summary.metadata_coverage:
        mc = summary.metadata_coverage
        metadata_coverage = SystemUnderstandingMetadataCoverageOut(
            symbol_count=mc.symbol_count,
            symbols_with_source_metadata=mc.symbols_with_source_metadata,
            entrypoint_count=mc.entrypoint_count,
            entrypoints_with_capability_link=mc.entrypoints_with_capability_link,
        )
    next_actions = [
        SystemUnderstandingNextActionOut(action=a.action, reason=a.reason, link=a.link)
        for a in summary.next_actions
    ]
    return SystemUnderstandingOut(
        system_id=summary.system_id,
        snapshot_id=summary.snapshot_id,
        commit_sha=summary.commit_sha,
        pipeline=pipeline,
        purpose=purpose,
        capabilities=capabilities,
        entrypoints=entrypoints,
        major_symbols=major_symbols,
        gaps=gaps,
        gap_summary=gap_summary,
        metadata_coverage=metadata_coverage,
        next_actions=next_actions,
    )


# ---------------------------------------------------------------------------
# Repository configuration
# ---------------------------------------------------------------------------


@router.get(
    "/repository-candidates",
    response_model=List[RepositoryCandidateOut],
)
def list_repository_candidates() -> List[RepositoryCandidateOut]:
    try:
        return [
            RepositoryCandidateOut(name=name, path=path)
            for name, path in discover_repository_candidates()
        ]
    except GitError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/repository", response_model=Optional[RepositoryConfigOut])
def get_repository_config(
    system_id: int = Depends(get_system_id),
) -> Optional[RepositoryConfigOut]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
    if row is None:
        return None
    return RepositoryConfigOut(
        system_id=row["system_id"],
        repo_path=row["repo_path"],
        include_patterns=json.loads(row["include_patterns"]),
        exclude_patterns=json.loads(row["exclude_patterns"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.put("/repository", response_model=RepositoryConfigOut)
def put_repository_config(
    payload: RepositoryConfigUpdate,
    system_id: int = Depends(get_system_id),
) -> RepositoryConfigOut:
    now = time.time()
    include_json = json.dumps(payload.include_patterns)
    exclude_json = json.dumps(payload.exclude_patterns)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE repository_configs
                SET repo_path = ?, include_patterns = ?, exclude_patterns = ?,
                    updated_at = ?
                WHERE system_id = ?
                """,
                (payload.repo_path, include_json, exclude_json, now, system_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO repository_configs
                    (system_id, repo_path, include_patterns, exclude_patterns,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (system_id, payload.repo_path, include_json, exclude_json, now, now),
            )
        row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
    return RepositoryConfigOut(
        system_id=row["system_id"],
        repo_path=row["repo_path"],
        include_patterns=json.loads(row["include_patterns"]),
        exclude_patterns=json.loads(row["exclude_patterns"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _snapshot_out(conn, snapshot_row, include_files: bool = False) -> SnapshotOut:
    files = []
    if include_files:
        file_rows = conn.execute(
            "SELECT path, source_type, size_bytes, inclusion_status, exclusion_reason "
            "FROM snapshot_files WHERE snapshot_id = ?",
            (snapshot_row["id"],),
        ).fetchall()
        files = [
            SnapshotFileOut(
                path=fr["path"],
                source_type=fr["source_type"],
                size_bytes=fr["size_bytes"],
                inclusion_status=fr["inclusion_status"],
                exclusion_reason=fr["exclusion_reason"],
            )
            for fr in file_rows
        ]
    return SnapshotOut(
        id=snapshot_row["id"],
        system_id=snapshot_row["system_id"],
        repo_path=snapshot_row["repo_path"],
        commit_sha=snapshot_row["commit_sha"],
        status=snapshot_row["status"],
        file_count=snapshot_row["file_count"],
        total_size=snapshot_row["total_size"],
        indexed_size=snapshot_row["indexed_size"],
        metadata_only_count=snapshot_row["metadata_only_count"],
        warnings=json.loads(snapshot_row["warnings"] or "[]"),
        error_summary=snapshot_row["error_summary"],
        created_at=snapshot_row["created_at"],
        completed_at=snapshot_row["completed_at"],
        files=files,
    )


@router.post("/repository/snapshots", response_model=SnapshotOut, status_code=201)
def create_snapshot_endpoint(
    system_id: int = Depends(get_system_id),
) -> SnapshotOut:
    with get_conn() as conn:
        config_row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
    if config_row is None:
        raise HTTPException(
            status_code=400,
            detail="Repository is not configured. PUT /repository first.",
        )

    repo_path = config_row["repo_path"]
    include_patterns = json.loads(config_row["include_patterns"])
    exclude_patterns = json.loads(config_row["exclude_patterns"])

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at)
            VALUES (?, ?, '', 'indexing', ?)
            """,
            (system_id, repo_path, now),
        )
        snapshot_id = cur.lastrowid

    try:
        commit_sha, files = create_snapshot(
            repo_path, include_patterns, exclude_patterns
        )
    except GitError as exc:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE repository_snapshots
                SET status = 'failed', error_summary = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc), time.time(), snapshot_id),
            )
            row = conn.execute(
                "SELECT * FROM repository_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return _snapshot_out(conn, row)

    total_size = sum(f.size_bytes for f in files)
    indexed_size = sum(
        f.size_bytes for f in files if f.inclusion_status == "indexed"
    )
    metadata_only_count = sum(
        1 for f in files if f.inclusion_status != "indexed"
    )
    warnings = []
    too_large_files = [f for f in files if f.inclusion_status == "too_large"]
    binary_files = [f for f in files if f.inclusion_status == "binary"]
    excluded_files = [f for f in files if f.inclusion_status == "excluded"]
    unsupported_files = [f for f in files if f.inclusion_status == "unsupported"]
    if too_large_files:
        warnings.append(
            f"{len(too_large_files)} file(s) exceeded the per-file size limit "
            f"and were recorded without content"
        )
    if binary_files:
        warnings.append(
            f"{len(binary_files)} binary file(s) were recorded without content"
        )
    if excluded_files:
        warnings.append(
            f"{len(excluded_files)} file(s) were excluded by repository policy"
        )
    if unsupported_files:
        warnings.append(
            f"{len(unsupported_files)} symlink or unsupported Git object(s) "
            "were recorded without content"
        )
    completed_at = time.time()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                UPDATE repository_snapshots
                SET commit_sha = ?, status = 'ready', file_count = ?,
                    total_size = ?, indexed_size = ?,
                    metadata_only_count = ?, warnings = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    commit_sha, len(files), total_size,
                    indexed_size, metadata_only_count,
                    json.dumps(warnings), completed_at, snapshot_id,
                ),
            )
            for f in files:
                conn.execute(
                    """
                    INSERT INTO snapshot_files
                        (snapshot_id, path, source_type, size_bytes,
                         content_hash, content, inclusion_status,
                         exclusion_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        f.path,
                        f.source_type,
                        f.size_bytes,
                        f.content_hash,
                        f.content,
                        f.inclusion_status,
                        f.exclusion_reason,
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        row = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return _snapshot_out(conn, row, include_files=True)


@router.get("/repository/snapshots/latest", response_model=Optional[SnapshotOut])
def get_latest_snapshot(
    system_id: int = Depends(get_system_id),
) -> Optional[SnapshotOut]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if row is None:
            return None
        return _snapshot_out(conn, row, include_files=True)


@router.get("/repository/snapshots", response_model=List[SnapshotOut])
def list_snapshots(
    system_id: int = Depends(get_system_id),
) -> List[SnapshotOut]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC
            """,
            (system_id,),
        ).fetchall()
        return [_snapshot_out(conn, row) for row in rows]


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


def _evidence_out(conn, draft_type: str, draft_id: int) -> List[FeatureEvidence]:
    rows = conn.execute(
        """
        SELECT path, start_line, end_line, summary
        FROM draft_evidence
        WHERE draft_type = ? AND draft_id = ?
        """,
        (draft_type, draft_id),
    ).fetchall()
    return [
        FeatureEvidence(
            path=r["path"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            summary=r["summary"],
        )
        for r in rows
    ]


def _intelligence_run_out(row) -> IntelligenceRunOut:
    return IntelligenceRunOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        run_type=row["run_type"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        status=row["status"],
        error_details=row["error_details"],
        is_mock=bool(row["is_mock"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _sp_draft_out(conn, row) -> SystemProfileDraftOut:
    return SystemProfileDraftOut(
        id=row["id"],
        system_id=row["system_id"],
        intelligence_run_id=row["intelligence_run_id"],
        snapshot_id=row["snapshot_id"],
        name=row["name"],
        purpose=row["purpose"],
        target_users=json.loads(row["target_users"]),
        stakeholder_value=row["stakeholder_value"],
        constraints=json.loads(row["constraints"]),
        success_criteria=json.loads(row["success_criteria"]),
        evidence=_evidence_out(conn, "system_profile", row["id"]),
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
    )


def _feature_draft_out(conn, row) -> FeatureDraftOut:
    return FeatureDraftOut(
        id=row["id"],
        system_id=row["system_id"],
        intelligence_run_id=row["intelligence_run_id"],
        snapshot_id=row["snapshot_id"],
        feature_id=row["feature_id"],
        name=row["name"],
        summary=row["summary"],
        user_value=row["user_value"],
        success_criteria=json.loads(row["success_criteria"]),
        risks=json.loads(row["risks"]),
        evidence=_evidence_out(conn, "feature", row["id"]),
        decision_method=row["decision_method"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
    )


@router.post(
    "/repository/drafts/generate",
    response_model=DraftGenerationResult,
    status_code=201,
)
def generate_drafts_endpoint(
    system_id: int = Depends(get_system_id),
) -> DraftGenerationResult:
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(
            status_code=400,
            detail="Latest snapshot is not ready. Create a successful snapshot first.",
        )

    snapshot_id = snapshot_row["id"]

    with get_conn() as conn:
        file_rows = conn.execute(
            """
            SELECT path, source_type, size_bytes, content_hash, content
            FROM snapshot_files
            WHERE snapshot_id = ? AND inclusion_status = 'indexed'
            ORDER BY path
            """,
            (snapshot_id,),
        ).fetchall()

    from ..git_ops import IndexedFile

    indexed_files = []
    for fr in file_rows:
        content = bytes(fr["content"] or b"")
        if len(content) != fr["size_bytes"]:
            raise HTTPException(
                status_code=409,
                detail=f"Snapshot content is unavailable or corrupt: {fr['path']}",
            )
        if hashlib.sha256(content).hexdigest() != (fr["content_hash"] or ""):
            raise HTTPException(
                status_code=409,
                detail=f"Snapshot content hash mismatch: {fr['path']}",
            )
        indexed_files.append(
            IndexedFile(
                path=fr["path"],
                source_type=fr["source_type"],
                size_bytes=fr["size_bytes"],
                content_hash=fr["content_hash"] or "",
                content=content,
            )
        )

    llm_config = LLMConfig.from_env()
    intelligence_provider = os.getenv("INTELLIGENCE_LLM_PROVIDER", "").strip()
    intelligence_model = os.getenv("INTELLIGENCE_LLM_MODEL", "").strip()
    if intelligence_provider or intelligence_model:
        llm_config = replace(
            llm_config,
            provider=intelligence_provider or llm_config.provider,
            model=intelligence_model or llm_config.model,
        )
    try:
        intelligence_timeout = float(os.getenv("INTELLIGENCE_LLM_TIMEOUT", "120"))
    except ValueError:
        intelligence_timeout = 120.0
    if llm_config.provider != "mock":
        llm_config = replace(
            llm_config,
            timeout=max(llm_config.timeout, intelligence_timeout),
        )

    started_at = time.time()
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Repository draft generation requires a configured reasoning model"
            )
        if not indexed_files:
            raise LLMError("Snapshot contains no files")
        llm_client = create_llm_client(llm_config)
        result = generate_drafts(llm_client, llm_config, indexed_files)
    except LLMError as exc:
        result = GenerationResult(
            provider=llm_config.provider,
            model=llm_config.model,
            is_mock=llm_config.provider == "mock",
            system_profile=None,
            features=[],
            error=str(exc),
        )
    completed_at = time.time()

    status = "completed" if result.error is None else "failed"
    decision_method = "reasoning_llm"

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'repository_drafts', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    system_id,
                    snapshot_id,
                    result.provider,
                    result.model,
                    PROMPT_VERSION,
                    SCHEMA_VERSION,
                    decision_method,
                    status,
                    result.error,
                    1 if result.is_mock else 0,
                    started_at,
                    completed_at,
                ),
            )
            run_id = cur.lastrowid

            sp_draft_out = None
            if result.system_profile:
                sp = result.system_profile
                now = time.time()
                cur = conn.execute(
                    """
                    INSERT INTO system_profile_drafts
                        (system_id, intelligence_run_id, snapshot_id,
                         name, purpose, target_users, stakeholder_value,
                         constraints, success_criteria, is_mock, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        run_id,
                        snapshot_id,
                        sp.name,
                        sp.purpose,
                        json.dumps(sp.target_users),
                        sp.stakeholder_value,
                        json.dumps(sp.constraints),
                        json.dumps(sp.success_criteria),
                        1 if result.is_mock else 0,
                        now,
                    ),
                )
                sp_draft_id = cur.lastrowid
                for ev in sp.evidence:
                    conn.execute(
                        """
                        INSERT INTO draft_evidence
                            (system_id, draft_type, draft_id, path,
                             start_line, end_line, summary)
                        VALUES (?, 'system_profile', ?, ?, ?, ?, ?)
                        """,
                        (
                            system_id,
                            sp_draft_id,
                            ev.path,
                            ev.start_line,
                            ev.end_line,
                            ev.summary,
                        ),
                    )
                sp_row = conn.execute(
                    "SELECT * FROM system_profile_drafts WHERE id = ?",
                    (sp_draft_id,),
                ).fetchone()
                sp_draft_out = _sp_draft_out(conn, sp_row)

            feature_drafts_out = []
            for fd in result.features:
                now = time.time()
                cur = conn.execute(
                    """
                    INSERT INTO feature_drafts
                        (system_id, intelligence_run_id, snapshot_id,
                         feature_id, name, summary, user_value,
                         success_criteria, risks, decision_method,
                         is_mock, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        run_id,
                        snapshot_id,
                        fd.feature_id,
                        fd.name,
                        fd.summary,
                        fd.user_value,
                        json.dumps(fd.success_criteria),
                        json.dumps(fd.risks),
                        fd.decision_method,
                        1 if result.is_mock else 0,
                        now,
                    ),
                )
                fd_id = cur.lastrowid
                for ev in fd.evidence:
                    conn.execute(
                        """
                        INSERT INTO draft_evidence
                            (system_id, draft_type, draft_id, path,
                             start_line, end_line, summary)
                        VALUES (?, 'feature', ?, ?, ?, ?, ?)
                        """,
                        (
                            system_id,
                            fd_id,
                            ev.path,
                            ev.start_line,
                            ev.end_line,
                            ev.summary,
                        ),
                    )
                fd_row = conn.execute(
                    "SELECT * FROM feature_drafts WHERE id = ?", (fd_id,)
                ).fetchone()
                feature_drafts_out.append(_feature_draft_out(conn, fd_row))

            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return DraftGenerationResult(
        intelligence_run=_intelligence_run_out(run_row),
        system_profile_draft=sp_draft_out,
        feature_drafts=feature_drafts_out,
    )


@router.get("/repository/drafts/latest", response_model=LatestDraftsOut)
def get_latest_drafts(
    system_id: int = Depends(get_system_id),
) -> LatestDraftsOut:
    with get_conn() as conn:
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND run_type = 'repository_drafts'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()

        if run_row:
            snapshot_row = conn.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE system_id = ? AND id = ?
                """,
                (system_id, run_row["snapshot_id"]),
            ).fetchone()
        else:
            snapshot_row = conn.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE system_id = ? AND status = 'ready'
                ORDER BY id DESC LIMIT 1
                """,
                (system_id,),
            ).fetchone()

        snapshot_out = (
            _snapshot_out(conn, snapshot_row, include_files=True)
            if snapshot_row
            else None
        )
        run_out = None
        sp_draft_out = None
        feature_drafts_out = []

        if run_row:
            run_out = _intelligence_run_out(run_row)
            if run_row["status"] == "completed":
                sp_row = conn.execute(
                    """
                    SELECT * FROM system_profile_drafts
                    WHERE system_id = ? AND intelligence_run_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (system_id, run_row["id"]),
                ).fetchone()
                if sp_row:
                    sp_draft_out = _sp_draft_out(conn, sp_row)

                fd_rows = conn.execute(
                    """
                    SELECT * FROM feature_drafts
                    WHERE system_id = ? AND intelligence_run_id = ?
                    ORDER BY id
                    """,
                    (system_id, run_row["id"]),
                ).fetchall()
                feature_drafts_out = [
                    _feature_draft_out(conn, row) for row in fd_rows
                ]

    return LatestDraftsOut(
        system_id=system_id,
        snapshot=snapshot_out,
        intelligence_run=run_out,
        system_profile_draft=sp_draft_out,
        feature_drafts=feature_drafts_out,
    )


# ---------------------------------------------------------------------------
# Symbol indexing (deterministic AST extraction)
# ---------------------------------------------------------------------------


# Bumped when the deterministic symbol index gains new extracted facts so that
# snapshots indexed by an older version can be deterministically upgraded
# without re-creating code_symbols (which would cascade-delete feature links).
# metadata-v1: #54 source metadata. provenance-v1: #55 source-hash provenance.
SYMBOL_INDEX_SCHEMA_VERSION = "provenance-v1"


def _metadata_out(row) -> SourceMetadataOut:
    return SourceMetadataOut(
        start_line=row["start_line"],
        end_line=row["end_line"],
        raw_block=row["raw_block"],
        role=row["role"],
        capability=row["capability"],
        element_type=row["element_type"],
        system_purpose=row["system_purpose"],
        operation_kind=row["operation_kind"],
        consumers=json.loads(row["consumers"]),
        state_effects=json.loads(row["state_effects"]),
        probe_value=row["probe_value"],
        origin=row["origin"],
        explanation_hash=row["explanation_hash"],
    )


def _load_metadata_map(conn, snapshot_id: int) -> dict:
    """Return ``symbol_id -> SourceMetadataOut`` for a snapshot."""
    rows = conn.execute(
        "SELECT * FROM symbol_source_metadata WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {r["symbol_id"]: _metadata_out(r) for r in rows}


def _load_file_hash_map(conn, snapshot_id: int) -> dict:
    """Return ``path -> file_content_hash`` for an indexed snapshot."""
    rows = conn.execute(
        "SELECT path, content_hash FROM snapshot_files WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {r["path"]: r["content_hash"] for r in rows}


def _insert_explanation_anchor(
    conn, snapshot_id: int, system_id: int, metadata_id: int, symbol_id: int,
    sym, meta, file_content_hash,
) -> None:
    """Persist the deterministic source anchor an explanation depends on."""
    conn.execute(
        """
        INSERT INTO explanation_source_anchors
            (snapshot_id, system_id, metadata_id, symbol_id, path,
             qualified_name, start_line, end_line, file_content_hash,
             symbol_source_hash, symbol_body_hash, explanation_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            system_id,
            metadata_id,
            symbol_id,
            sym.path,
            sym.qualified_name,
            meta.start_line,
            meta.end_line,
            file_content_hash,
            sym.symbol_source_hash,
            sym.symbol_body_hash,
            meta.explanation_hash,
        ),
    )


def _backfill_source_metadata(conn, system_id: int, snapshot_id: int, run_id: int) -> None:
    """Deterministically upgrade a pre-existing symbol index in place.

    Snapshots indexed by an older version keep their ``code_symbols`` rows (so
    feature-code links are preserved) but may lack #54 source metadata and #55
    source-hash provenance.  This re-parses the pinned snapshot files and
    additively backfills, matching existing symbols by ``(path,
    qualified_name)``:

    - symbol source/body hashes on ``code_symbols`` (idempotent UPDATE),
    - missing ``symbol_source_metadata`` rows (with explanation hash),
    - missing explanation hashes on existing metadata rows,
    - missing ``explanation_source_anchors``,
    - metadata index warnings.

    It runs once, gated by the run's ``schema_version``.
    """
    file_rows = conn.execute(
        """
        SELECT path, content FROM snapshot_files
        WHERE snapshot_id = ? AND inclusion_status = 'indexed'
        ORDER BY path
        """,
        (snapshot_id,),
    ).fetchall()
    files = [(fr["path"], bytes(fr["content"] or b"")) for fr in file_rows]
    result = index_snapshot_files(files)
    file_hash_map = _load_file_hash_map(conn, snapshot_id)

    sym_rows = conn.execute(
        "SELECT id, path, qualified_name FROM code_symbols WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    id_by_key = {(r["path"], r["qualified_name"]): r["id"] for r in sym_rows}
    # symbol_id -> (metadata_id, explanation_hash)
    existing_meta = {
        r["symbol_id"]: (r["id"], r["explanation_hash"])
        for r in conn.execute(
            "SELECT id, symbol_id, explanation_hash FROM symbol_source_metadata "
            "WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    }
    existing_anchor_meta_ids = {
        r["metadata_id"]
        for r in conn.execute(
            "SELECT metadata_id FROM explanation_source_anchors WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    }
    existing_warnings = {
        (w["path"], w["message"])
        for w in conn.execute(
            "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    }

    conn.execute("BEGIN")
    try:
        for sym in result.symbols:
            symbol_id = id_by_key.get((sym.path, sym.qualified_name))
            if symbol_id is None:
                continue
            # Idempotently set the deterministic source hashes.
            conn.execute(
                "UPDATE code_symbols SET symbol_source_hash = ?, symbol_body_hash = ? "
                "WHERE id = ?",
                (sym.symbol_source_hash, sym.symbol_body_hash, symbol_id),
            )

            meta = sym.source_metadata
            if meta is None:
                continue

            if symbol_id not in existing_meta:
                cur = conn.execute(
                    """
                    INSERT INTO symbol_source_metadata
                        (snapshot_id, system_id, symbol_id, path, qualified_name,
                         start_line, end_line, role, capability, element_type,
                         system_purpose, operation_kind, consumers, state_effects,
                         probe_value, raw_block, origin, explanation_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        system_id,
                        symbol_id,
                        sym.path,
                        sym.qualified_name,
                        meta.start_line,
                        meta.end_line,
                        meta.role,
                        meta.capability,
                        meta.element_type,
                        meta.system_purpose,
                        meta.operation_kind,
                        json.dumps(meta.consumers),
                        json.dumps(meta.state_effects),
                        meta.probe_value,
                        meta.raw_block,
                        meta.origin,
                        meta.explanation_hash,
                    ),
                )
                metadata_id = cur.lastrowid
            else:
                metadata_id, existing_hash = existing_meta[symbol_id]
                if existing_hash is None:
                    conn.execute(
                        "UPDATE symbol_source_metadata SET explanation_hash = ? "
                        "WHERE id = ?",
                        (meta.explanation_hash, metadata_id),
                    )

            if metadata_id not in existing_anchor_meta_ids:
                _insert_explanation_anchor(
                    conn, snapshot_id, system_id, metadata_id, symbol_id, sym,
                    meta, file_hash_map.get(sym.path),
                )
                existing_anchor_meta_ids.add(metadata_id)

        # Add only metadata warnings (syntax/decode warnings already exist from
        # the original index); guard against duplicates so backfill is idempotent.
        for warn in result.warnings:
            if "probe-agent metadata:" not in warn.message:
                continue
            if (warn.path, warn.message) in existing_warnings:
                continue
            conn.execute(
                """
                INSERT INTO symbol_index_warnings
                    (snapshot_id, system_id, path, message)
                VALUES (?, ?, ?, ?)
                """,
                (snapshot_id, system_id, warn.path, warn.message),
            )

        conn.execute(
            "UPDATE intelligence_runs SET schema_version = ? WHERE id = ?",
            (SYMBOL_INDEX_SCHEMA_VERSION, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _upgrade_index_if_stale(conn, system_id: int, snapshot_id: int):
    """Deterministically upgrade a stale symbol index in place, if needed.

    Returns the latest symbol_index run row (refreshed after any upgrade), or
    ``None`` when the snapshot has not been indexed.  Idempotent and gated by
    the run's ``schema_version`` so it is safe to call from read paths: existing
    snapshots indexed by an older version are upgraded the first time they are
    read instead of requiring an explicit re-index.  Mirrors the deterministic
    INSERT-on-read pattern already used by flow-entrypoint discovery.
    """
    run_row = conn.execute(
        """
        SELECT * FROM intelligence_runs
        WHERE system_id = ? AND snapshot_id = ? AND run_type = 'symbol_index'
        ORDER BY id DESC LIMIT 1
        """,
        (system_id, snapshot_id),
    ).fetchone()
    if run_row is None or run_row["schema_version"] == SYMBOL_INDEX_SCHEMA_VERSION:
        return run_row
    has_symbols = conn.execute(
        "SELECT 1 FROM code_symbols WHERE snapshot_id = ? LIMIT 1",
        (snapshot_id,),
    ).fetchone()
    if has_symbols is None:
        return run_row
    _backfill_source_metadata(conn, system_id, snapshot_id, run_row["id"])
    return conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ?",
        (run_row["id"],),
    ).fetchone()


def _symbol_out(
    row,
    metadata: Optional[SourceMetadataOut] = None,
    file_content_hash: Optional[str] = None,
) -> CodeSymbolOut:
    return CodeSymbolOut(
        id=row["id"],
        snapshot_id=row["snapshot_id"],
        system_id=row["system_id"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        kind=row["kind"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        decorators=json.loads(row["decorators"]),
        imports=json.loads(row["imports"]),
        docstring=row["docstring"],
        is_test=bool(row["is_test"]),
        is_pydantic_model=bool(row["is_pydantic_model"]),
        route_path=row["route_path"],
        route_method=row["route_method"],
        component_id=row["component_id"],
        source_metadata=metadata,
        file_content_hash=file_content_hash,
        symbol_source_hash=row["symbol_source_hash"],
        symbol_body_hash=row["symbol_body_hash"],
    )


@router.post(
    "/repository/symbols/index",
    response_model=SymbolIndexOut,
    status_code=201,
)
def index_symbols_endpoint(
    system_id: int = Depends(get_system_id),
) -> SymbolIndexOut:
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(
            status_code=400,
            detail="Latest snapshot is not ready. Create a successful snapshot first.",
        )

    snapshot_id = snapshot_row["id"]

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS cnt FROM code_symbols WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if existing["cnt"] > 0:
            # Deterministically upgrade indexes created before #54/#55 so their
            # source metadata and hashes are populated without re-creating
            # symbols (which would cascade-delete feature links).
            run_row = _upgrade_index_if_stale(conn, system_id, snapshot_id)
            sym_rows = conn.execute(
                "SELECT * FROM code_symbols WHERE snapshot_id = ? ORDER BY path, start_line",
                (snapshot_id,),
            ).fetchall()
            warn_rows = conn.execute(
                "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            meta_map = _load_metadata_map(conn, snapshot_id)
            file_hash_map = _load_file_hash_map(conn, snapshot_id)
            return SymbolIndexOut(
                snapshot_id=snapshot_id,
                system_id=system_id,
                symbol_count=len(sym_rows),
                warning_count=len(warn_rows),
                symbols=[
                    _symbol_out(r, meta_map.get(r["id"]), file_hash_map.get(r["path"]))
                    for r in sym_rows
                ],
                warnings=[
                    SymbolIndexWarningOut(path=w["path"], message=w["message"])
                    for w in warn_rows
                ],
                intelligence_run=_intelligence_run_out(run_row) if run_row else None,
            )

    with get_conn() as conn:
        file_rows = conn.execute(
            """
            SELECT path, content, content_hash FROM snapshot_files
            WHERE snapshot_id = ? AND inclusion_status = 'indexed'
            ORDER BY path
            """,
            (snapshot_id,),
        ).fetchall()

    files = [(fr["path"], bytes(fr["content"] or b"")) for fr in file_rows]
    file_hash_map = {fr["path"]: fr["content_hash"] for fr in file_rows}
    started_at = time.time()
    result = index_snapshot_files(files)
    completed_at = time.time()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, is_mock, started_at, completed_at)
                VALUES (?, ?, 'symbol_index', 'deterministic', 'ast',
                        'n/a', ?, 'deterministic',
                        'completed', 0, ?, ?)
                """,
                (system_id, snapshot_id, SYMBOL_INDEX_SCHEMA_VERSION, started_at, completed_at),
            )
            run_id = cur.lastrowid

            for sym in result.symbols:
                sym_cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO code_symbols
                        (snapshot_id, system_id, path, qualified_name, kind,
                         start_line, end_line, decorators, imports, docstring,
                         is_test, is_pydantic_model, route_path, route_method,
                         component_id, symbol_source_hash, symbol_body_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        system_id,
                        sym.path,
                        sym.qualified_name,
                        sym.kind,
                        sym.start_line,
                        sym.end_line,
                        json.dumps(sym.decorators),
                        json.dumps(sym.imports),
                        sym.docstring,
                        1 if sym.is_test else 0,
                        1 if sym.is_pydantic_model else 0,
                        sym.route_path,
                        sym.route_method,
                        sym.component_id,
                        sym.symbol_source_hash,
                        sym.symbol_body_hash,
                    ),
                )
                # INSERT OR IGNORE skips duplicate (snapshot_id, qualified_name,
                # path) rows instead of raising a UNIQUE constraint error. When a
                # row is ignored, lastrowid is stale, so skip the dependent
                # source-metadata/anchor inserts to avoid mis-attributing them.
                if sym_cur.rowcount == 0:
                    continue
                symbol_id = sym_cur.lastrowid
                meta = sym.source_metadata
                if meta is not None:
                    meta_cur = conn.execute(
                        """
                        INSERT INTO symbol_source_metadata
                            (snapshot_id, system_id, symbol_id, path,
                             qualified_name, start_line, end_line, role,
                             capability, element_type, system_purpose,
                             operation_kind, consumers, state_effects,
                             probe_value, raw_block, origin, explanation_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            system_id,
                            symbol_id,
                            sym.path,
                            sym.qualified_name,
                            meta.start_line,
                            meta.end_line,
                            meta.role,
                            meta.capability,
                            meta.element_type,
                            meta.system_purpose,
                            meta.operation_kind,
                            json.dumps(meta.consumers),
                            json.dumps(meta.state_effects),
                            meta.probe_value,
                            meta.raw_block,
                            meta.origin,
                            meta.explanation_hash,
                        ),
                    )
                    _insert_explanation_anchor(
                        conn, snapshot_id, system_id, meta_cur.lastrowid,
                        symbol_id, sym, meta, file_hash_map.get(sym.path),
                    )

            for warn in result.warnings:
                conn.execute(
                    """
                    INSERT INTO symbol_index_warnings
                        (snapshot_id, system_id, path, message)
                    VALUES (?, ?, ?, ?)
                    """,
                    (snapshot_id, system_id, warn.path, warn.message),
                )

            conn.execute("COMMIT")

            sym_rows = conn.execute(
                "SELECT * FROM code_symbols WHERE snapshot_id = ? ORDER BY path, start_line",
                (snapshot_id,),
            ).fetchall()
            warn_rows = conn.execute(
                "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            meta_map = _load_metadata_map(conn, snapshot_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return SymbolIndexOut(
        snapshot_id=snapshot_id,
        system_id=system_id,
        symbol_count=len(sym_rows),
        warning_count=len(warn_rows),
        symbols=[
            _symbol_out(r, meta_map.get(r["id"]), file_hash_map.get(r["path"]))
            for r in sym_rows
        ],
        warnings=[
            SymbolIndexWarningOut(path=w["path"], message=w["message"])
            for w in warn_rows
        ],
        intelligence_run=_intelligence_run_out(run_row),
    )


@router.get("/repository/symbols", response_model=SymbolIndexOut)
def get_symbols(
    system_id: int = Depends(get_system_id),
) -> SymbolIndexOut:
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if snapshot_row is None:
            return SymbolIndexOut(
                snapshot_id=0,
                system_id=system_id,
                symbol_count=0,
                warning_count=0,
            )

        snapshot_id = snapshot_row["id"]
        # Upgrade pre-#55 indexes on read so Dashboard sees hashes/anchors
        # without requiring an explicit re-index (deterministic, idempotent).
        run_row = _upgrade_index_if_stale(conn, system_id, snapshot_id)
        sym_rows = conn.execute(
            "SELECT * FROM code_symbols WHERE snapshot_id = ? ORDER BY path, start_line",
            (snapshot_id,),
        ).fetchall()
        warn_rows = conn.execute(
            "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        meta_map = _load_metadata_map(conn, snapshot_id)
        file_hash_map = _load_file_hash_map(conn, snapshot_id)

    return SymbolIndexOut(
        snapshot_id=snapshot_id,
        system_id=system_id,
        symbol_count=len(sym_rows),
        warning_count=len(warn_rows),
        symbols=[
            _symbol_out(r, meta_map.get(r["id"]), file_hash_map.get(r["path"]))
            for r in sym_rows
        ],
        warnings=[
            SymbolIndexWarningOut(path=w["path"], message=w["message"])
            for w in warn_rows
        ],
        intelligence_run=_intelligence_run_out(run_row) if run_row else None,
    )


def _anchor_out(row) -> ExplanationAnchorOut:
    return ExplanationAnchorOut(
        id=row["id"],
        snapshot_id=row["snapshot_id"],
        system_id=row["system_id"],
        metadata_id=row["metadata_id"],
        symbol_id=row["symbol_id"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        file_content_hash=row["file_content_hash"],
        symbol_source_hash=row["symbol_source_hash"],
        symbol_body_hash=row["symbol_body_hash"],
        explanation_hash=row["explanation_hash"],
    )


@router.get(
    "/repository/explanation-anchors",
    response_model=ExplanationAnchorsOut,
)
def get_explanation_anchors(
    system_id: int = Depends(get_system_id),
) -> ExplanationAnchorsOut:
    """Return the deterministic source anchors each explanation depends on.

    Downstream hierarchy/drift features compare these hashes against a newer
    snapshot.  Hash equality is only a change signal, never proof of semantic
    equality.
    """
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if snapshot_row is None:
            return ExplanationAnchorsOut(
                system_id=system_id, snapshot_id=0, anchor_count=0,
            )
        snapshot_id = snapshot_row["id"]
        # Upgrade pre-#55 indexes on read so anchors exist without an explicit
        # re-index (deterministic, idempotent).
        _upgrade_index_if_stale(conn, system_id, snapshot_id)
        rows = conn.execute(
            """
            SELECT * FROM explanation_source_anchors
            WHERE snapshot_id = ? AND system_id = ?
            ORDER BY path, start_line
            """,
            (snapshot_id, system_id),
        ).fetchall()

    return ExplanationAnchorsOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        anchor_count=len(rows),
        anchors=[_anchor_out(r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Source-backed capability hierarchy (Issue #56)
# ---------------------------------------------------------------------------


def _hierarchy_symbol_records(conn, snapshot_id: int, system_id: int):
    rows = conn.execute(
        """
        SELECT cs.id AS symbol_id, cs.path, cs.qualified_name, cs.kind,
               cs.start_line, cs.end_line, cs.symbol_source_hash,
               ssm.id AS metadata_id, ssm.role, ssm.capability, ssm.element_type,
               ssm.system_purpose, ssm.operation_kind, ssm.consumers,
               ssm.state_effects, ssm.probe_value, ssm.explanation_hash
        FROM code_symbols cs
        LEFT JOIN symbol_source_metadata ssm ON ssm.symbol_id = cs.id
        WHERE cs.snapshot_id = ? AND cs.system_id = ?
        ORDER BY cs.path, cs.start_line
        """,
        (snapshot_id, system_id),
    ).fetchall()
    # code_symbols has no file_content_hash column; resolve it from the snapshot.
    file_hash_map = _load_file_hash_map(conn, snapshot_id)
    records = []
    for r in rows:
        has_meta = r["metadata_id"] is not None
        records.append(HierarchySymbolRecord(
            symbol_id=r["symbol_id"],
            path=r["path"],
            qualified_name=r["qualified_name"],
            kind=r["kind"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            file_content_hash=file_hash_map.get(r["path"]),
            symbol_source_hash=r["symbol_source_hash"],
            has_metadata=has_meta,
            role=r["role"],
            capability=r["capability"],
            element_type=r["element_type"],
            system_purpose=r["system_purpose"],
            operation_kind=r["operation_kind"],
            consumers=json.loads(r["consumers"]) if r["consumers"] else [],
            state_effects=json.loads(r["state_effects"]) if r["state_effects"] else [],
            probe_value=r["probe_value"],
            explanation_hash=r["explanation_hash"],
        ))
    return records


def _hierarchy_entrypoint_records(conn, snapshot_id: int, system_id: int):
    rows = conn.execute(
        "SELECT * FROM code_entrypoints WHERE snapshot_id = ? AND system_id = ?",
        (snapshot_id, system_id),
    ).fetchall()
    return [
        HierarchyEntrypointRecord(
            entrypoint_id=r["id"],
            category=r["category"],
            label=r["label"],
            operation=r["operation"],
            route_method=r["route_method"],
            route_path=r["route_path"],
            handler_symbol_id=r["handler_symbol_id"],
            handler_path=r["handler_path"],
            handler_qualified_name=r["handler_qualified_name"],
            line_start=r["line_start"],
            line_end=r["line_end"],
        )
        for r in rows
    ]


def _accepted_feature_links_by_symbol(conn, system_id: int, snapshot_id: int) -> dict:
    """Map ``symbol_id -> feature_id`` for accepted Feature-to-Code links (#24).

    Connects hierarchy nodes back to the existing Feature Map. When a symbol has
    several accepted links, the highest-confidence one wins (deterministic).
    """
    rows = conn.execute(
        """
        SELECT symbol_id, feature_id FROM feature_code_links
        WHERE system_id = ? AND snapshot_id = ? AND review_status = 'accepted'
        ORDER BY confidence DESC, id ASC
        """,
        (system_id, snapshot_id),
    ).fetchall()
    mapping: dict = {}
    for r in rows:
        mapping.setdefault(r["symbol_id"], r["feature_id"])
    return mapping


def _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, node, parent_id, now):
    cur = conn.execute(
        """
        INSERT INTO capability_hierarchy_nodes
            (system_id, snapshot_id, intelligence_run_id, parent_id, node_type,
             name, summary, capability_key, element_role, operation_kind,
             probe_value, supporting_kind, classification, symbol_id,
             entrypoint_id, feature_id, system_profile_draft_id, path,
             qualified_name, start_line, end_line, file_content_hash,
             symbol_source_hash, explanation_hash, provenance_kind,
             decision_method, provider, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            system_id, snapshot_id, run_id, parent_id, node.node_type,
            node.name, node.summary, node.capability_key, node.element_role,
            node.operation_kind, node.probe_value, node.supporting_kind,
            node.classification, node.symbol_id, node.entrypoint_id,
            node.feature_id, node.system_profile_draft_id, node.path,
            node.qualified_name, node.start_line, node.end_line,
            node.file_content_hash, node.symbol_source_hash, node.explanation_hash,
            node.provenance_kind, node.decision_method, node.provider, node.model,
            now,
        ),
    )
    node_id = cur.lastrowid
    for child in node.children:
        _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, child, node_id, now)
    return node_id


def _apply_grouping_assignments(built, grouping):
    """Move successfully-assigned unclassified entrypoints into capabilities.

    Reasoning-derived placements are marked provenance_kind='reasoning_llm' so
    they stay distinct from source-authored facts. Unassigned entrypoints remain
    unclassified (no heuristic fallback).
    """
    if grouping is None or grouping.error or not grouping.assignments:
        return
    by_ep = {n.entrypoint_id: n for n in built.unclassified_elements}
    assigned_ids = set()
    for assignment in grouping.assignments:
        node = by_ep.get(assignment.entrypoint_id)
        cap = built.capability_by_key(assignment.capability_key)
        if node is None or cap is None:
            continue
        node.classification = "classified"
        node.capability_key = assignment.capability_key
        node.summary = assignment.reason or node.summary
        node.provenance_kind = "reasoning_llm"
        node.decision_method = "reasoning_llm"
        node.provider = grouping.provider
        node.model = grouping.model
        cap.children.append(node)
        assigned_ids.add(assignment.entrypoint_id)
    built.unclassified_elements = [
        n for n in built.unclassified_elements if n.entrypoint_id not in assigned_ids
    ]


def _provenance_out(row, logical_by_dbid=None) -> HierarchyProvenanceOut:
    entrypoint_type = None
    entrypoint_ref = None
    if logical_by_dbid is not None and row["entrypoint_id"] is not None:
        logical = logical_by_dbid.get(row["entrypoint_id"])
        if logical is not None:
            entrypoint_type, entrypoint_ref = logical
    return HierarchyProvenanceOut(
        provenance_kind=row["provenance_kind"],
        decision_method=row["decision_method"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        file_content_hash=row["file_content_hash"],
        symbol_source_hash=row["symbol_source_hash"],
        explanation_hash=row["explanation_hash"],
        symbol_id=row["symbol_id"],
        entrypoint_id=row["entrypoint_id"],
        entrypoint_type=entrypoint_type,
        entrypoint_ref=entrypoint_ref,
        feature_id=row["feature_id"],
        system_profile_draft_id=row["system_profile_draft_id"],
        provider=row["provider"],
        model=row["model"],
    )


def _element_out(row, logical_by_dbid=None) -> CapabilityElementOut:
    return CapabilityElementOut(
        id=row["id"],
        name=row["name"],
        summary=row["summary"],
        element_role=row["element_role"],
        operation_kind=row["operation_kind"],
        probe_value=row["probe_value"],
        classification=row["classification"],
        provenance=_provenance_out(row, logical_by_dbid),
    )


def _supporting_out(row, logical_by_dbid=None) -> SupportingElementOut:
    return SupportingElementOut(
        id=row["id"],
        name=row["name"],
        summary=row["summary"],
        supporting_kind=row["supporting_kind"],
        provenance=_provenance_out(row, logical_by_dbid),
    )


def _logical_entrypoint_map(conn, snapshot_id, system_id) -> dict:
    """Map a snapshot's code_entrypoints DB row id -> logical (type, id).

    Persisted hierarchy nodes store the snapshot-local DB row id, which is not
    stable across snapshots. The logical pair lets the dashboard link a node to
    Flow Explorer (#62) without re-resolving the DB id.
    """
    rows = conn.execute(
        "SELECT id, entrypoint_type, entrypoint_id FROM code_entrypoints "
        "WHERE snapshot_id = ? AND system_id = ?",
        (snapshot_id, system_id),
    ).fetchall()
    return {r["id"]: (r["entrypoint_type"], r["entrypoint_id"]) for r in rows}


def _load_hierarchy_out(conn, system_id, snapshot_id, run_row) -> CapabilityHierarchyOut:
    rows = conn.execute(
        "SELECT * FROM capability_hierarchy_nodes WHERE intelligence_run_id = ? "
        "ORDER BY id",
        (run_row["id"],),
    ).fetchall()
    logical_by_dbid = _logical_entrypoint_map(conn, run_row["snapshot_id"], system_id)
    by_parent: dict = {}
    purpose_row = None
    capability_rows = []
    unclassified_rows = []
    unattached_supporting_rows = []
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r)
        if r["node_type"] == "purpose":
            purpose_row = r
        elif r["node_type"] == "capability":
            capability_rows.append(r)
        elif r["parent_id"] is None and r["node_type"] == "element":
            unclassified_rows.append(r)
        elif r["parent_id"] is None and r["node_type"] == "supporting":
            unattached_supporting_rows.append(r)

    capabilities = []
    for cap in capability_rows:
        children = by_parent.get(cap["id"], [])
        capabilities.append(CapabilityOut(
            id=cap["id"],
            capability_key=cap["capability_key"],
            name=cap["name"],
            summary=cap["summary"],
            provenance=_provenance_out(cap, logical_by_dbid),
            elements=[
                _element_out(c, logical_by_dbid)
                for c in children if c["node_type"] == "element"
            ],
            supporting_elements=[
                _supporting_out(c, logical_by_dbid)
                for c in children if c["node_type"] == "supporting"
            ],
        ))

    purpose = None
    if purpose_row is not None:
        purpose = CapabilityPurposeOut(
            id=purpose_row["id"],
            name=purpose_row["name"],
            summary=purpose_row["summary"],
            provenance=_provenance_out(purpose_row, logical_by_dbid),
        )

    return CapabilityHierarchyOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        intelligence_run=_intelligence_run_out(run_row),
        purpose=purpose,
        capabilities=capabilities,
        unclassified_elements=[
            _element_out(r, logical_by_dbid) for r in unclassified_rows
        ],
        unattached_supporting=[
            _supporting_out(r, logical_by_dbid) for r in unattached_supporting_rows
        ],
        is_mock=bool(run_row["is_mock"]),
    )


@router.post(
    "/repository/capability-hierarchy/generate",
    response_model=CapabilityHierarchyOut,
    status_code=201,
)
def generate_capability_hierarchy(
    use_reasoning: bool = Query(
        default=False,
        description="Use a reasoning model to group unclassified API entrypoints "
        "under existing source-authored capabilities. Fails closed (no heuristic "
        "fallback); deterministic source-authored facts are persisted regardless.",
    ),
    system_id: int = Depends(get_system_id),
) -> CapabilityHierarchyOut:
    # Deterministic structural inputs (committed snapshot only).
    snapshot_row, flow_symbols, files = _load_flow_inputs(system_id)
    snapshot_id = snapshot_row["id"]
    _ensure_entrypoints_indexed(system_id, snapshot_row, flow_symbols, files)

    with get_conn() as conn:
        symbols = _hierarchy_symbol_records(conn, snapshot_id, system_id)
        entrypoints = _hierarchy_entrypoint_records(conn, snapshot_id, system_id)
        draft_row = conn.execute(
            """
            SELECT id, name, purpose FROM system_profile_drafts
            WHERE system_id = ? AND snapshot_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (system_id, snapshot_id),
        ).fetchone()
        feature_links = _accepted_feature_links_by_symbol(conn, system_id, snapshot_id)

    sp_draft = (
        {"id": draft_row["id"], "name": draft_row["name"], "purpose": draft_row["purpose"]}
        if draft_row is not None
        else None
    )
    built = build_hierarchy(symbols, entrypoints, sp_draft, feature_links=feature_links)

    # Optional reasoning-assisted grouping of unclassified API entrypoints.
    grouping = None
    error = None
    run_provider, run_model, run_is_mock = "deterministic", "none", False
    run_decision = "deterministic"
    if use_reasoning:
        run_decision = "reasoning_llm"
        if built.unclassified_elements and built.capabilities:
            llm_config = _resolve_intelligence_config()
            try:
                if llm_config.provider != "mock" and not is_reasoning_model(
                    llm_config.provider, llm_config.model
                ):
                    raise LLMError(
                        "Capability grouping requires a configured reasoning model"
                    )
                client = create_llm_client(llm_config)
                grouping = propose_capability_grouping(
                    client, llm_config, built.capabilities, built.unclassified_elements
                )
            except LLMError as exc:
                grouping = type("R", (), {
                    "provider": llm_config.provider,
                    "model": llm_config.model,
                    "is_mock": llm_config.provider == "mock",
                    "assignments": [],
                    "error": str(exc),
                })()
            run_provider, run_model = grouping.provider, grouping.model
            run_is_mock = grouping.is_mock
            error = grouping.error
            _apply_grouping_assignments(built, grouping)
        else:
            # Nothing to group; the reasoning run is a no-op but still recorded.
            llm_config = _resolve_intelligence_config()
            run_provider, run_model = llm_config.provider, llm_config.model
            run_is_mock = llm_config.provider == "mock"

    status = "failed" if error else "completed"
    started_at = time.time()
    completed_at = started_at

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'capability_hierarchy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    system_id, snapshot_id, run_provider, run_model,
                    HIERARCHY_PROMPT_VERSION, HIERARCHY_SCHEMA_VERSION,
                    run_decision, status, error, 1 if run_is_mock else 0,
                    started_at, completed_at,
                ),
            )
            run_id = cur.lastrowid

            # Persist deterministic results even when the reasoning step failed
            # (fail closed only suppresses guessed groupings, not source facts).
            now = time.time()
            if built.purpose is not None:
                purpose_id = _persist_hierarchy_node(
                    conn, system_id, snapshot_id, run_id, built.purpose, None, now
                )
            else:
                purpose_id = None
            for cap in built.capabilities:
                _persist_hierarchy_node(
                    conn, system_id, snapshot_id, run_id, cap, purpose_id, now
                )
            for node in built.unclassified_elements:
                _persist_hierarchy_node(
                    conn, system_id, snapshot_id, run_id, node, None, now
                )
            for node in built.unattached_supporting:
                _persist_hierarchy_node(
                    conn, system_id, snapshot_id, run_id, node, None, now
                )

            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
            ).fetchone()
            out = _load_hierarchy_out(conn, system_id, snapshot_id, run_row)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return out


@router.get(
    "/repository/capability-hierarchy",
    response_model=CapabilityHierarchyOut,
)
def get_capability_hierarchy(
    system_id: int = Depends(get_system_id),
) -> CapabilityHierarchyOut:
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if snapshot_row is None:
            return CapabilityHierarchyOut(system_id=system_id, snapshot_id=0)
        snapshot_id = snapshot_row["id"]
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND snapshot_id = ? AND run_type = 'capability_hierarchy'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id, snapshot_id),
        ).fetchone()
        if run_row is None:
            return CapabilityHierarchyOut(
                system_id=system_id, snapshot_id=snapshot_id,
            )
        return _load_hierarchy_out(conn, system_id, snapshot_id, run_row)


# ---------------------------------------------------------------------------
# Explanation drift (Issue #57)
# ---------------------------------------------------------------------------


def _snapshot_is_indexed(conn, system_id: int, snapshot_id: int) -> bool:
    """True when a snapshot has a completed symbol index (so drift is meaningful)."""
    return conn.execute(
        """
        SELECT 1 FROM intelligence_runs
        WHERE system_id = ? AND snapshot_id = ?
          AND run_type = 'symbol_index' AND status = 'completed'
        LIMIT 1
        """,
        (system_id, snapshot_id),
    ).fetchone() is not None


def _latest_indexed_ready_snapshot(conn, system_id: int):
    """Latest ready snapshot that also has a completed symbol index."""
    return conn.execute(
        """
        SELECT rs.* FROM repository_snapshots rs
        WHERE rs.system_id = ? AND rs.status = 'ready'
          AND EXISTS (
              SELECT 1 FROM intelligence_runs ir
              WHERE ir.system_id = rs.system_id AND ir.snapshot_id = rs.id
                AND ir.run_type = 'symbol_index' AND ir.status = 'completed'
          )
        ORDER BY rs.id DESC LIMIT 1
        """,
        (system_id,),
    ).fetchone()


def _snapshot_facts(conn, snapshot_id: int, system_id: int) -> drift_service.SnapshotFacts:
    """Deterministic hash facts of a pinned snapshot for drift comparison."""
    file_rows = conn.execute(
        "SELECT path, content_hash FROM snapshot_files WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    sym_rows = conn.execute(
        """
        SELECT cs.path, cs.qualified_name, cs.symbol_source_hash,
               ssm.explanation_hash
        FROM code_symbols cs
        LEFT JOIN symbol_source_metadata ssm ON ssm.symbol_id = cs.id
        WHERE cs.snapshot_id = ? AND cs.system_id = ?
        """,
        (snapshot_id, system_id),
    ).fetchall()
    return drift_service.SnapshotFacts(
        file_hash_by_path={r["path"]: r["content_hash"] for r in file_rows},
        symbol_by_key={
            (r["path"], r["qualified_name"]): (
                r["symbol_source_hash"], r["explanation_hash"]
            )
            for r in sym_rows
        },
    )


def _node_anchor(row) -> drift_service.NodeAnchor:
    return drift_service.NodeAnchor(
        node_id=row["id"],
        node_type=row["node_type"],
        name=row["name"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        entrypoint_id=row["entrypoint_id"],
        file_content_hash=row["file_content_hash"],
        symbol_source_hash=row["symbol_source_hash"],
        explanation_hash=row["explanation_hash"],
    )


def _anchor_drift_out(d: drift_service.AnchorDrift) -> AnchorDriftOut:
    return AnchorDriftOut(
        node_id=d.node_id,
        node_type=d.node_type,
        name=d.name,
        path=d.path,
        qualified_name=d.qualified_name,
        entrypoint_id=d.entrypoint_id,
        status=d.status,
        changed_hashes=d.changed_hashes,
        captured_file_content_hash=d.captured_file_content_hash,
        captured_symbol_source_hash=d.captured_symbol_source_hash,
        captured_explanation_hash=d.captured_explanation_hash,
        current_file_content_hash=d.current_file_content_hash,
        current_symbol_source_hash=d.current_symbol_source_hash,
        current_explanation_hash=d.current_explanation_hash,
    )


def _counts_out(counts: drift_service.DriftCounts) -> DriftCountsOut:
    return DriftCountsOut(**counts.__dict__)


@router.get(
    "/repository/capability-hierarchy/drift",
    response_model=CapabilityHierarchyDriftOut,
)
def get_capability_hierarchy_drift(
    target_snapshot_id: Optional[int] = Query(
        default=None,
        description="Snapshot to compare against. Defaults to the latest ready "
        "snapshot. The hierarchy's own snapshot is the base.",
    ),
    system_id: int = Depends(get_system_id),
) -> CapabilityHierarchyDriftOut:
    """Report deterministic explanation drift for the latest capability hierarchy.

    Compares the source hashes captured when the hierarchy was generated against
    a newer pinned snapshot. Hash drift is a review trigger, not a correctness
    verdict.
    """
    with get_conn() as conn:
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND run_type = 'capability_hierarchy'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if run_row is None:
            raise HTTPException(
                status_code=400,
                detail="No capability hierarchy generated. Generate one first.",
            )
        base_snapshot_id = run_row["snapshot_id"]

        if target_snapshot_id is None:
            # Compare only against a symbol-indexed snapshot: an un-indexed
            # snapshot has no code_symbols, which would make every symbol anchor
            # look like a deleted source (false-positive review). Fall back to
            # the hierarchy's own (always indexed) base snapshot.
            target_row = _latest_indexed_ready_snapshot(conn, system_id)
            target = target_row["id"] if target_row else base_snapshot_id
        else:
            target = target_snapshot_id
            owned = conn.execute(
                "SELECT 1 FROM repository_snapshots WHERE id = ? AND system_id = ?",
                (target, system_id),
            ).fetchone()
            if owned is None:
                raise HTTPException(
                    status_code=404,
                    detail="Target snapshot not found for this system.",
                )
            if not _snapshot_is_indexed(conn, system_id, target):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Target snapshot has no symbol index. Run symbol "
                        "indexing on it before computing drift."
                    ),
                )

        node_rows = conn.execute(
            "SELECT * FROM capability_hierarchy_nodes WHERE intelligence_run_id = ? "
            "ORDER BY id",
            (run_row["id"],),
        ).fetchall()
        facts = _snapshot_facts(conn, target, system_id)
        target_indexed = _snapshot_is_indexed(conn, system_id, target)

    # Compute per-node drift deterministically.
    drift_by_node: dict = {}
    by_parent: dict = {}
    purpose_row = None
    capability_rows = []
    unclassified_rows = []
    unattached_supporting_rows = []
    for r in node_rows:
        drift_by_node[r["id"]] = drift_service.compute_anchor_drift(
            _node_anchor(r), facts
        )
        by_parent.setdefault(r["parent_id"], []).append(r)
        if r["node_type"] == "purpose":
            purpose_row = r
        elif r["node_type"] == "capability":
            capability_rows.append(r)
        elif r["parent_id"] is None and r["node_type"] == "element":
            unclassified_rows.append(r)
        elif r["parent_id"] is None and r["node_type"] == "supporting":
            unattached_supporting_rows.append(r)

    all_drifts = list(drift_by_node.values())

    capabilities_out = []
    for cap in capability_rows:
        children = by_parent.get(cap["id"], [])
        element_drifts = [
            drift_by_node[c["id"]] for c in children if c["node_type"] == "element"
        ]
        supporting_drifts = [
            drift_by_node[c["id"]] for c in children if c["node_type"] == "supporting"
        ]
        cap_self = drift_by_node[cap["id"]]
        cap_status, cap_counts = drift_service.aggregate_drift(
            [cap_self] + element_drifts + supporting_drifts
        )
        capabilities_out.append(CapabilityDriftOut(
            capability_id=cap["id"],
            capability_key=cap["capability_key"],
            name=cap["name"],
            status=cap_status,
            counts=_counts_out(cap_counts),
            elements=[_anchor_drift_out(d) for d in element_drifts],
            supporting_elements=[_anchor_drift_out(d) for d in supporting_drifts],
        ))

    system_status, system_counts = drift_service.aggregate_drift(all_drifts)

    return CapabilityHierarchyDriftOut(
        system_id=system_id,
        base_snapshot_id=base_snapshot_id,
        target_snapshot_id=target,
        intelligence_run=_intelligence_run_out(run_row),
        status=system_status,
        counts=_counts_out(system_counts),
        target_indexed=target_indexed,
        purpose=(
            _anchor_drift_out(drift_by_node[purpose_row["id"]])
            if purpose_row is not None else None
        ),
        capabilities=capabilities_out,
        unclassified_elements=[
            _anchor_drift_out(drift_by_node[r["id"]]) for r in unclassified_rows
        ],
        unattached_supporting=[
            _anchor_drift_out(drift_by_node[r["id"]]) for r in unattached_supporting_rows
        ],
        is_review_recommended=drift_service.is_review_recommended(system_status),
        review_note=(
            drift_service.REVIEW_NOTE
            if drift_service.is_review_recommended(system_status) else None
        ),
    )


# ---------------------------------------------------------------------------
# API role cards (Issue #58) — Flow Explorer developer context
# ---------------------------------------------------------------------------

_BACKEND_CATEGORIES = ("api", "message_queue", "scheduled_job", "cli")

_BOUNDARY_LABELS = {
    "database-read": "database",
    "database-write": "database",
    "network": "external HTTP",
    "external-api": "external HTTP",
    "filesystem": "filesystem",
    "cache": "cache",
    "queue": "queue",
}


def _boundaries_for(state_effects: List[str]) -> List[str]:
    out: List[str] = []
    for effect in state_effects:
        label = _BOUNDARY_LABELS.get(effect)
        if label and label not in out:
            out.append(label)
    return out


@router.get(
    "/repository/api-role-cards",
    response_model=ApiRoleCardsOut,
)
def get_api_role_cards(
    system_id: int = Depends(get_system_id),
) -> ApiRoleCardsOut:
    """Role cards for backend entrypoints, for Flow Explorer.

    Aggregates each backend entrypoint's source-authored explanation (#54), its
    place in the capability hierarchy (#56), and explanation drift (#57) into a
    developer-facing card. Invents no new hierarchy semantics; degrades to a
    clear unclassified/unknown state when context is missing.
    """
    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            return ApiRoleCardsOut(system_id=system_id)
        snapshot_id = snapshot_row["id"]
        has_symbols = conn.execute(
            "SELECT 1 FROM code_symbols WHERE snapshot_id = ? AND system_id = ? LIMIT 1",
            (snapshot_id, system_id),
        ).fetchone()
    if has_symbols is None:
        return ApiRoleCardsOut(system_id=system_id, snapshot_id=snapshot_id)

    # Deterministic entrypoint discovery (idempotent persistence).
    snapshot_row, flow_symbols, files = _load_flow_inputs(system_id)
    snapshot_id = snapshot_row["id"]
    _ensure_entrypoints_indexed(system_id, snapshot_row, flow_symbols, files)

    with get_conn() as conn:
        ep_rows = conn.execute(
            "SELECT * FROM code_entrypoints WHERE snapshot_id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchall()
        meta_rows = conn.execute(
            """
            SELECT symbol_id, role, capability, element_type, operation_kind,
                   consumers, state_effects, probe_value
            FROM symbol_source_metadata WHERE snapshot_id = ? AND system_id = ?
            """,
            (snapshot_id, system_id),
        ).fetchall()
        meta_by_symbol = {r["symbol_id"]: r for r in meta_rows}

        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND run_type = 'capability_hierarchy'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()

        node_by_logical: dict = {}
        cap_by_id: dict = {}
        children_by_cap: dict = {}
        drift_by_node: dict = {}
        cap_drift: dict = {}
        base_snapshot_id = None
        target_snapshot_id = None
        drift_available = False

        if run_row is not None:
            base_snapshot_id = run_row["snapshot_id"]
            node_rows = conn.execute(
                "SELECT * FROM capability_hierarchy_nodes WHERE intelligence_run_id = ? "
                "ORDER BY id",
                (run_row["id"],),
            ).fetchall()
            # Hierarchy nodes reference the base snapshot's code_entrypoints row
            # ids, which are not stable across snapshots. Translate them to the
            # logical (entrypoint_type, entrypoint_id) so the current snapshot's
            # entrypoints can be joined to their hierarchy node.
            base_ep_rows = conn.execute(
                "SELECT id, entrypoint_type, entrypoint_id FROM code_entrypoints "
                "WHERE snapshot_id = ? AND system_id = ?",
                (base_snapshot_id, system_id),
            ).fetchall()
            logical_by_dbid = {
                r["id"]: (r["entrypoint_type"], r["entrypoint_id"])
                for r in base_ep_rows
            }
            for r in node_rows:
                if r["node_type"] == "capability":
                    cap_by_id[r["id"]] = r
                if r["parent_id"] is not None:
                    children_by_cap.setdefault(r["parent_id"], []).append(r)
                if r["entrypoint_id"] is not None:
                    logical = logical_by_dbid.get(r["entrypoint_id"])
                    if logical is not None:
                        node_by_logical[logical] = r

            # Drift against the latest symbol-indexed snapshot (#57 semantics).
            target_row = _latest_indexed_ready_snapshot(conn, system_id)
            if target_row is not None:
                target_snapshot_id = target_row["id"]
                drift_available = True
                facts = _snapshot_facts(conn, target_snapshot_id, system_id)
                for r in node_rows:
                    drift_by_node[r["id"]] = drift_service.compute_anchor_drift(
                        _node_anchor(r), facts
                    )
                for cap_id, cap_row in cap_by_id.items():
                    members = [drift_by_node[cap_id]] + [
                        drift_by_node[c["id"]] for c in children_by_cap.get(cap_id, [])
                    ]
                    cap_drift[cap_id] = drift_service.aggregate_drift(members)

    cards: List[ApiRoleCardOut] = []
    for ep in ep_rows:
        if ep["category"] not in _BACKEND_CATEGORIES:
            continue
        handler_resolved = ep["handler_symbol_id"] is not None
        meta = meta_by_symbol.get(ep["handler_symbol_id"]) if handler_resolved else None
        node = node_by_logical.get((ep["entrypoint_type"], ep["entrypoint_id"]))

        # Classification: prefer the hierarchy node (reflects reasoning grouping);
        # fall back to handler metadata.
        classification = "unclassified"
        capability_key = None
        provenance_kinds: List[str] = ["structural"]
        if node is not None:
            classification = node["classification"] or "unclassified"
            capability_key = node["capability_key"]
            if node["provenance_kind"] == "reasoning_llm":
                provenance_kinds = ["reasoning_llm", "structural"]
        elif meta is not None and meta["capability"]:
            classification = "classified"
            capability_key = meta["capability"]
        if meta is not None and meta["capability"]:
            if "source_authored" not in provenance_kinds:
                provenance_kinds = ["source_authored"] + provenance_kinds
        if classification != "classified":
            capability_key = None

        capability_name = None
        flows_through: List[str] = []
        if capability_key is not None and node is not None and node["parent_id"]:
            cap_row = cap_by_id.get(node["parent_id"])
            if cap_row is not None:
                capability_name = cap_row["name"]
                flows_through = [
                    c["name"] for c in children_by_cap.get(cap_row["id"], [])
                    if c["node_type"] == "element" and c["id"] != node["id"]
                ]
        if capability_name is None and capability_key is not None:
            capability_name = capability_key

        state_effects = (
            json.loads(meta["state_effects"]) if meta and meta["state_effects"] else []
        )
        consumers = json.loads(meta["consumers"]) if meta and meta["consumers"] else []

        # Drift: capability aggregate for classified, node-level otherwise.
        drift_status = None
        changed = 0
        total = 0
        if drift_available and node is not None:
            if classification == "classified" and node["parent_id"] in cap_drift:
                status, counts = cap_drift[node["parent_id"]]
                drift_status = status
                changed = counts.stale + counts.missing
                total = counts.fresh + counts.stale + counts.missing
            elif node["id"] in drift_by_node:
                d = drift_by_node[node["id"]]
                drift_status = d.status
                changed = 1 if d.status in ("stale", "missing_source") else 0
                total = 1 if d.status != "unknown" else 0

        # The card needs attention itself when an LLM-scan entry has no handler:
        # an executable flow graph cannot be built and the role is unverified.
        review_needed = ep["source"] == "reasoning_llm" and not handler_resolved
        review_reason = (
            "LLM-derived API definition without a resolved handler; an executable "
            "flow graph is not supported and the role is unverified."
            if review_needed else None
        )

        cards.append(ApiRoleCardOut(
            entrypoint_type=ep["entrypoint_type"],
            entrypoint_id=ep["entrypoint_id"],
            label=ep["label"],
            category=ep["category"],
            route_method=ep["route_method"],
            route_path=ep["route_path"],
            operation=ep["operation"],
            framework=ep["framework"],
            source=ep["source"],
            handler_resolved=handler_resolved,
            classification=classification,
            capability_key=capability_key,
            capability_name=capability_name,
            element_type=meta["element_type"] if meta else None,
            role=meta["role"] if meta else None,
            operation_kind=meta["operation_kind"] if meta else None,
            probe_value=meta["probe_value"] if meta else None,
            consumers=consumers,
            state_effects=state_effects,
            boundaries=_boundaries_for(state_effects),
            flows_through=flows_through,
            provenance_kinds=provenance_kinds,
            drift_status=drift_status,
            drift_changed_anchors=changed,
            drift_total_anchors=total,
            drift_review_recommended=drift_service.is_review_recommended(drift_status or ""),
            review_needed=review_needed,
            review_reason=review_reason,
            node_id=node["id"] if node is not None else None,
        ))

    cards.sort(key=lambda c: (c.category, c.label))
    return ApiRoleCardsOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        hierarchy_run=_intelligence_run_out(run_row) if run_row else None,
        base_snapshot_id=base_snapshot_id,
        target_snapshot_id=target_snapshot_id,
        drift_available=drift_available,
        cards=cards,
    )


# ---------------------------------------------------------------------------
# Explanation refresh proposals (Issue #59) — reasoning model, suggestion only
# ---------------------------------------------------------------------------


def _read_source_snippet(
    conn, snapshot_id: int, system_id: int, path: str, qualified_name: Optional[str]
) -> str:
    """Return the current source span for a symbol from the pinned snapshot.

    Reads only committed snapshot content (never the working tree). Slices to the
    symbol's line range when the symbol still exists in the target snapshot;
    otherwise returns '' so the proposal can state the source is gone.
    """
    file_row = conn.execute(
        "SELECT content FROM snapshot_files WHERE snapshot_id = ? AND path = ? LIMIT 1",
        (snapshot_id, path),
    ).fetchone()
    if file_row is None:
        return ""
    raw = file_row["content"]
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if qualified_name is None:
        return "\n".join(text.splitlines()[:60])
    sym = conn.execute(
        """
        SELECT start_line, end_line FROM code_symbols
        WHERE snapshot_id = ? AND system_id = ? AND path = ? AND qualified_name = ?
        LIMIT 1
        """,
        (snapshot_id, system_id, path, qualified_name),
    ).fetchone()
    if sym is None:
        return ""
    lines = text.splitlines()
    start = max(0, (sym["start_line"] or 1) - 1)
    end = min(len(lines), sym["end_line"] or len(lines))
    span = lines[start:end]
    # Cap the snippet so the context pack stays bounded.
    return "\n".join(span[:200])


def _old_metadata_row(conn, snapshot_id: int, system_id: int, path: str, qualified_name: str):
    return conn.execute(
        """
        SELECT raw_block, role, capability, element_type, operation_kind,
               system_purpose, consumers, state_effects, probe_value
        FROM symbol_source_metadata
        WHERE snapshot_id = ? AND system_id = ? AND path = ? AND qualified_name = ?
        LIMIT 1
        """,
        (snapshot_id, system_id, path, qualified_name),
    ).fetchone()


def _refresh_proposal_out(row) -> ExplanationRefreshProposalOut:
    return ExplanationRefreshProposalOut(
        id=row["id"],
        node_id=row["node_id"],
        node_type=row["node_type"],
        name=row["name"],
        entrypoint_type=row["entrypoint_type"],
        entrypoint_id=row["entrypoint_id"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        drift_status=row["drift_status"] or "unknown",
        drift_reason=row["drift_reason"],
        changed_hashes=json.loads(row["changed_hashes"] or "[]"),
        old_explanation=row["old_explanation"],
        proposed_explanation=row["proposed_explanation"],
        proposed_metadata=(
            json.loads(row["proposed_metadata"]) if row["proposed_metadata"] else None
        ),
        summary_of_changes=row["summary_of_changes"],
        confidence=row["confidence"],
        captured_file_content_hash=row["captured_file_content_hash"],
        captured_symbol_source_hash=row["captured_symbol_source_hash"],
        captured_explanation_hash=row["captured_explanation_hash"],
        current_file_content_hash=row["current_file_content_hash"],
        current_symbol_source_hash=row["current_symbol_source_hash"],
        current_explanation_hash=row["current_explanation_hash"],
        status=row["status"],
        is_mock=bool(row["is_mock"]),
        provider=row["provider"],
        model=row["model"],
        decision_method=row["decision_method"],
        created_at=row["created_at"],
    )


@router.post(
    "/repository/explanation-refresh",
    response_model=ExplanationRefreshOut,
    status_code=201,
)
def create_explanation_refresh(
    payload: RefreshProposalRequest,
    system_id: int = Depends(get_system_id),
) -> ExplanationRefreshOut:
    """Propose a reviewable explanation refresh for a stale node / role card.

    Builds a deterministic context pack (old explanation, changed anchors, old
    and new hashes, current source snippet from the pinned snapshot, structural
    facts) and asks a reasoning model to propose updated wording/metadata. The
    proposal is a SUGGESTION only: probe-agent never edits the target source
    repository. Fails closed (no heuristic fallback) for mock/non-reasoning
    models, persisting the failed run so it is visible.
    """
    if payload.node_id is None and not (
        payload.entrypoint_type and payload.entrypoint_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Provide node_id or (entrypoint_type, entrypoint_id).",
        )

    with get_conn() as conn:
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND run_type = 'capability_hierarchy'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if run_row is None:
            raise HTTPException(
                status_code=400,
                detail="No capability hierarchy generated. Generate one first.",
            )
        base_snapshot_id = run_row["snapshot_id"]

        # Resolve the requested hierarchy node.
        node_row = None
        if payload.node_id is not None:
            node_row = conn.execute(
                """
                SELECT * FROM capability_hierarchy_nodes
                WHERE id = ? AND system_id = ? AND intelligence_run_id = ?
                """,
                (payload.node_id, system_id, run_row["id"]),
            ).fetchone()
        else:
            # Translate the stable logical entrypoint key to the base snapshot's
            # transient code_entrypoints id, then to its hierarchy node.
            base_ep = conn.execute(
                """
                SELECT id FROM code_entrypoints
                WHERE snapshot_id = ? AND system_id = ?
                  AND entrypoint_type = ? AND entrypoint_id = ?
                LIMIT 1
                """,
                (base_snapshot_id, system_id, payload.entrypoint_type,
                 payload.entrypoint_id),
            ).fetchone()
            if base_ep is not None:
                node_row = conn.execute(
                    """
                    SELECT * FROM capability_hierarchy_nodes
                    WHERE intelligence_run_id = ? AND entrypoint_id = ?
                    LIMIT 1
                    """,
                    (run_row["id"], base_ep["id"]),
                ).fetchone()
        if node_row is None:
            raise HTTPException(
                status_code=404,
                detail="Hierarchy node not found for this system.",
            )
        if node_row["path"] is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This node has no source anchor (e.g. a draft-linked purpose) "
                    "and cannot be drift-checked or refreshed."
                ),
            )

        # Resolve the target snapshot (must be symbol-indexed; #57 semantics).
        if payload.target_snapshot_id is None:
            target_row = _latest_indexed_ready_snapshot(conn, system_id)
            target = target_row["id"] if target_row else base_snapshot_id
        else:
            target = payload.target_snapshot_id
            owned = conn.execute(
                "SELECT 1 FROM repository_snapshots WHERE id = ? AND system_id = ?",
                (target, system_id),
            ).fetchone()
            if owned is None:
                raise HTTPException(
                    status_code=404,
                    detail="Target snapshot not found for this system.",
                )
            if not _snapshot_is_indexed(conn, system_id, target):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Target snapshot has no symbol index. Run symbol "
                        "indexing on it before requesting a refresh."
                    ),
                )

        facts = _snapshot_facts(conn, target, system_id)
        drift = drift_service.compute_anchor_drift(_node_anchor(node_row), facts)
        if not drift_service.is_review_recommended(drift.status):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Node is {drift.status}, not stale. A refresh proposal is "
                    "only generated for drifted explanations."
                ),
            )

        # Old explanation + parsed metadata from the base (captured) snapshot.
        meta_row = _old_metadata_row(
            conn, base_snapshot_id, system_id, node_row["path"],
            node_row["qualified_name"] or "",
        )
        old_explanation = meta_row["raw_block"] if meta_row else ""
        old_metadata = {}
        if meta_row is not None:
            for k in ("role", "capability", "element_type", "operation_kind",
                      "system_purpose", "probe_value"):
                if meta_row[k]:
                    old_metadata[k] = meta_row[k]
            if meta_row["consumers"]:
                old_metadata["consumers"] = json.loads(meta_row["consumers"])
            if meta_row["state_effects"]:
                old_metadata["state_effects"] = json.loads(meta_row["state_effects"])

        snippet = _read_source_snippet(
            conn, target, system_id, node_row["path"], node_row["qualified_name"]
        )

        structural_facts = {
            "capability_key": node_row["capability_key"],
            "classification": node_row["classification"],
            "operation_kind": node_row["operation_kind"],
        }
        ep_logical_type = None
        ep_logical_id = None
        if node_row["entrypoint_id"] is not None:
            ep_row = conn.execute(
                "SELECT * FROM code_entrypoints WHERE id = ? AND system_id = ?",
                (node_row["entrypoint_id"], system_id),
            ).fetchone()
            if ep_row is not None:
                ep_logical_type = ep_row["entrypoint_type"]
                ep_logical_id = ep_row["entrypoint_id"]
                structural_facts.update({
                    "route_method": ep_row["route_method"],
                    "route_path": ep_row["route_path"],
                    "operation": ep_row["operation"],
                    "category": ep_row["category"],
                    "framework": ep_row["framework"],
                })

    ctx = RefreshContext(
        node_id=node_row["id"],
        node_type=node_row["node_type"],
        name=node_row["name"],
        path=node_row["path"],
        qualified_name=node_row["qualified_name"],
        drift_status=drift.status,
        changed_hashes=drift.changed_hashes,
        old_explanation=old_explanation,
        old_metadata=old_metadata,
        captured_hashes={
            "file_content_hash": node_row["file_content_hash"],
            "symbol_source_hash": node_row["symbol_source_hash"],
            "explanation_hash": node_row["explanation_hash"],
        },
        current_hashes={
            "file_content_hash": drift.current_file_content_hash,
            "symbol_source_hash": drift.current_symbol_source_hash,
            "explanation_hash": drift.current_explanation_hash,
        },
        source_snippet=snippet,
        structural_facts={k: v for k, v in structural_facts.items() if v},
    )

    # Run the reasoning model (fails closed for mock/non-reasoning).
    llm_config = _resolve_intelligence_config()
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Explanation refresh proposals require a configured reasoning model"
            )
        client = create_llm_client(llm_config)
        proposal = propose_refresh(client, llm_config, ctx)
    except LLMError as exc:
        proposal = type("R", (), {
            "provider": llm_config.provider,
            "model": llm_config.model,
            "is_mock": llm_config.provider == "mock",
            "proposed_explanation": None,
            "proposed_metadata": None,
            "summary_of_changes": None,
            "confidence": None,
            "error": str(exc),
        })()

    status = "failed" if proposal.error else "proposed"
    run_status = "failed" if proposal.error else "completed"
    drift_reason = (
        "Source is gone (deleted or renamed)."
        if drift.status == "missing_source"
        else f"Changed source hashes: {', '.join(drift.changed_hashes) or 'unknown'}."
    )
    now = time.time()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'explanation_refresh', ?, ?, ?, ?, 'reasoning_llm',
                        ?, ?, ?, ?, ?)
                """,
                (
                    system_id, base_snapshot_id, proposal.provider, proposal.model,
                    REFRESH_PROMPT_VERSION, REFRESH_SCHEMA_VERSION,
                    run_status, proposal.error, 1 if proposal.is_mock else 0,
                    now, now,
                ),
            )
            run_id = cur.lastrowid

            cur2 = conn.execute(
                """
                INSERT INTO explanation_refresh_proposals
                    (system_id, intelligence_run_id, base_snapshot_id,
                     target_snapshot_id, node_id, node_type, name,
                     entrypoint_type, entrypoint_id, path, qualified_name,
                     drift_status, drift_reason, changed_hashes, old_explanation,
                     proposed_explanation, proposed_metadata, summary_of_changes,
                     confidence, captured_file_content_hash,
                     captured_symbol_source_hash, captured_explanation_hash,
                     current_file_content_hash, current_symbol_source_hash,
                     current_explanation_hash, status, is_mock, provider, model,
                     decision_method, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', ?)
                """,
                (
                    system_id, run_id, base_snapshot_id, target,
                    node_row["id"], node_row["node_type"], node_row["name"],
                    ep_logical_type, ep_logical_id, node_row["path"],
                    node_row["qualified_name"], drift.status, drift_reason,
                    json.dumps(drift.changed_hashes), old_explanation,
                    proposal.proposed_explanation,
                    json.dumps(proposal.proposed_metadata)
                    if proposal.proposed_metadata else None,
                    proposal.summary_of_changes, proposal.confidence,
                    node_row["file_content_hash"], node_row["symbol_source_hash"],
                    node_row["explanation_hash"],
                    drift.current_file_content_hash,
                    drift.current_symbol_source_hash,
                    drift.current_explanation_hash,
                    status, 1 if proposal.is_mock else 0,
                    proposal.provider, proposal.model, now,
                ),
            )
            proposal_id = cur2.lastrowid
            run_out_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
            ).fetchone()
            proposal_row = conn.execute(
                "SELECT * FROM explanation_refresh_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return ExplanationRefreshOut(
        system_id=system_id,
        base_snapshot_id=base_snapshot_id,
        target_snapshot_id=target,
        intelligence_run=_intelligence_run_out(run_out_row),
        status=status,
        error=proposal.error,
        review_required=True,
        review_note=REVIEW_REQUIRED_NOTE,
        proposal=_refresh_proposal_out(proposal_row),
    )


@router.get(
    "/repository/explanation-refresh",
    response_model=ExplanationRefreshListOut,
)
def list_explanation_refresh(
    system_id: int = Depends(get_system_id),
) -> ExplanationRefreshListOut:
    """List the most recent explanation refresh proposals for the system.

    Every proposal is a suggestion only; the review note states that a developer
    must apply it to the source by hand.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM explanation_refresh_proposals
            WHERE system_id = ? ORDER BY id DESC LIMIT 50
            """,
            (system_id,),
        ).fetchall()
    return ExplanationRefreshListOut(
        system_id=system_id,
        review_note=REVIEW_REQUIRED_NOTE,
        proposals=[_refresh_proposal_out(r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Feature-to-Code mapping (reasoning model)
# ---------------------------------------------------------------------------


def _link_out(conn, row) -> FeatureCodeLinkOut:
    sym_row = conn.execute(
        "SELECT * FROM code_symbols WHERE id = ?",
        (row["symbol_id"],),
    ).fetchone()
    run_row = conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ?",
        (row["intelligence_run_id"],),
    ).fetchone()
    latest_snapshot = conn.execute(
        """
        SELECT id FROM repository_snapshots
        WHERE system_id = ? AND status = 'ready'
        ORDER BY id DESC LIMIT 1
        """,
        (row["system_id"],),
    ).fetchone()
    return FeatureCodeLinkOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        intelligence_run_id=row["intelligence_run_id"],
        feature_id=row["feature_id"],
        symbol=_symbol_out(sym_row),
        relation_reason=row["relation_reason"],
        confidence=row["confidence"],
        source=row["source"],
        review_status=row["review_status"],
        provider=run_row["provider"],
        model=run_row["model"],
        prompt_version=run_row["prompt_version"],
        schema_version=run_row["schema_version"],
        is_stale=(
            latest_snapshot is None
            or latest_snapshot["id"] != row["snapshot_id"]
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post(
    "/repository/code-links/generate",
    response_model=FeatureCodeLinksOut,
    status_code=201,
)
def generate_code_links_endpoint(
    system_id: int = Depends(get_system_id),
) -> FeatureCodeLinksOut:
    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(
            status_code=400,
            detail="Latest snapshot is not ready.",
        )
    snapshot_id = snapshot_row["id"]

    with get_conn() as conn:
        sym_rows = conn.execute(
            "SELECT * FROM code_symbols WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    if not sym_rows:
        raise HTTPException(
            status_code=400,
            detail="No symbols indexed for the latest snapshot. Run symbol indexing first.",
        )

    with get_conn() as conn:
        draft_run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND snapshot_id = ?
              AND run_type = 'repository_drafts' AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id, snapshot_id),
        ).fetchone()
        if draft_run_row is None:
            raise HTTPException(
                status_code=400,
                detail="No completed draft generation found. Generate drafts first.",
            )

        fd_rows = conn.execute(
            """
            SELECT * FROM feature_drafts
            WHERE system_id = ? AND intelligence_run_id = ?
            ORDER BY id
            """,
            (system_id, draft_run_row["id"]),
        ).fetchall()

    if not fd_rows:
        raise HTTPException(
            status_code=400,
            detail="No feature drafts found. Generate drafts first.",
        )

    features = []
    for fd in fd_rows:
        with get_conn() as conn2:
            evidence_rows = conn2.execute(
                "SELECT summary FROM draft_evidence WHERE draft_type = 'feature' AND draft_id = ?",
                (fd["id"],),
            ).fetchall()

        keywords = [fd["name"]]
        for ev in evidence_rows:
            keywords.extend(ev["summary"].split()[:5])

        features.append(FeatureContext(
            feature_id=fd["feature_id"],
            name=fd["name"],
            summary=fd["summary"],
            user_value=fd["user_value"],
            success_criteria=json.loads(fd["success_criteria"]),
            risks=json.loads(fd["risks"]),
            evidence_keywords=keywords,
        ))

    from ..code_indexer import CodeSymbol as CodeSymbolData

    symbols = []
    for sr in sym_rows:
        symbols.append(CodeSymbolData(
            path=sr["path"],
            qualified_name=sr["qualified_name"],
            kind=sr["kind"],
            start_line=sr["start_line"],
            end_line=sr["end_line"],
            decorators=json.loads(sr["decorators"]),
            imports=json.loads(sr["imports"]),
            docstring=sr["docstring"],
            is_test=bool(sr["is_test"]),
            is_pydantic_model=bool(sr["is_pydantic_model"]),
            route_path=sr["route_path"],
            route_method=sr["route_method"],
            component_id=sr["component_id"],
        ))

    llm_config = LLMConfig.from_env()
    intelligence_provider = os.getenv("INTELLIGENCE_LLM_PROVIDER", "").strip()
    intelligence_model = os.getenv("INTELLIGENCE_LLM_MODEL", "").strip()
    if intelligence_provider or intelligence_model:
        llm_config = replace(
            llm_config,
            provider=intelligence_provider or llm_config.provider,
            model=intelligence_model or llm_config.model,
        )

    started_at = time.time()
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Feature-to-code mapping requires a configured reasoning model"
            )
        llm_client = create_llm_client(llm_config)
        mapping_result = generate_code_mapping(llm_client, llm_config, features, symbols)
    except LLMError as exc:
        mapping_result = type("R", (), {
            "provider": llm_config.provider,
            "model": llm_config.model,
            "is_mock": llm_config.provider == "mock",
            "links": [],
            "error": str(exc),
        })()
    completed_at = time.time()

    status = "completed" if mapping_result.error is None else "failed"

    symbol_key_to_id = {}
    for sr in sym_rows:
        symbol_key_to_id[(sr["path"], sr["qualified_name"])] = sr["id"]

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'feature_code_mapping', ?, ?, ?, ?, 'reasoning_llm',
                        ?, ?, ?, ?, ?)
                """,
                (
                    system_id,
                    snapshot_id,
                    mapping_result.provider,
                    mapping_result.model,
                    MAPPING_PROMPT_VERSION,
                    MAPPING_SCHEMA_VERSION,
                    status,
                    mapping_result.error,
                    1 if mapping_result.is_mock else 0,
                    started_at,
                    completed_at,
                ),
            )
            run_id = cur.lastrowid

            now = time.time()
            for link in mapping_result.links:
                symbol_id = symbol_key_to_id.get(
                    (link.symbol_path, link.symbol_qualified_name)
                )
                if symbol_id is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO feature_code_links
                        (system_id, snapshot_id, intelligence_run_id,
                         feature_id, symbol_id, relation_reason,
                         confidence, source, review_status,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                    """,
                    (
                        system_id,
                        snapshot_id,
                        run_id,
                        link.feature_id,
                        symbol_id,
                        link.relation_reason,
                        link.confidence,
                        link.source,
                        now,
                        now,
                    ),
                )

            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

            link_rows = conn.execute(
                """
                SELECT * FROM feature_code_links
                WHERE intelligence_run_id = ?
                ORDER BY feature_id, confidence DESC
                """,
                (run_id,),
            ).fetchall()

            links_out = [_link_out(conn, lr) for lr in link_rows]
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return FeatureCodeLinksOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        intelligence_run=_intelligence_run_out(run_row),
        links=links_out,
        is_mock=mapping_result.is_mock,
    )


@router.get("/repository/code-links", response_model=FeatureCodeLinksOut)
def get_code_links(
    system_id: int = Depends(get_system_id),
) -> FeatureCodeLinksOut:
    with get_conn() as conn:
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND run_type = 'feature_code_mapping'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        if run_row is None:
            return FeatureCodeLinksOut(system_id=system_id)

        link_rows = conn.execute(
            """
            SELECT * FROM feature_code_links
            WHERE intelligence_run_id = ?
            ORDER BY feature_id, confidence DESC
            """,
            (run_row["id"],),
        ).fetchall()

        return FeatureCodeLinksOut(
            system_id=system_id,
            snapshot_id=run_row["snapshot_id"],
            intelligence_run=_intelligence_run_out(run_row),
            links=[_link_out(conn, lr) for lr in link_rows],
            is_mock=bool(run_row["is_mock"]),
        )


@router.put(
    "/repository/code-links/{link_id}/review",
    response_model=FeatureCodeLinkOut,
)
def review_code_link(
    link_id: int,
    payload: LinkReviewUpdate,
    system_id: int = Depends(get_system_id),
) -> FeatureCodeLinkOut:
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM feature_code_links WHERE id = ? AND system_id = ?",
            (link_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Link not found")
        conn.execute(
            """
            UPDATE feature_code_links
            SET review_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.review_status, now, link_id),
        )
        row = conn.execute(
            "SELECT * FROM feature_code_links WHERE id = ?",
            (link_id,),
        ).fetchone()
        return _link_out(conn, row)


# ---------------------------------------------------------------------------
# Probe Plan generation (reasoning model)
# ---------------------------------------------------------------------------


def _probe_point_out(row) -> ProbePointOut:
    return ProbePointOut(
        id=row["id"],
        plan_id=row["plan_id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        feature_id=row["feature_id"],
        path=row["path"],
        symbol=row["symbol"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        reason=row["reason"],
        recommended_mode=row["recommended_mode"],
        side_effect_risk=row["side_effect_risk"],
        replayability=row["replayability"],
        denylist_hit=row["denylist_hit"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _probe_plan_out(conn, plan_row, include_run: bool = True) -> ProbePlanOut:
    point_rows = conn.execute(
        "SELECT * FROM probe_points WHERE plan_id = ? ORDER BY id",
        (plan_row["id"],),
    ).fetchall()

    run_out = None
    is_mock = False
    if include_run:
        run_row = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?",
            (plan_row["intelligence_run_id"],),
        ).fetchone()
        if run_row:
            run_out = _intelligence_run_out(run_row)
            is_mock = bool(run_row["is_mock"])

    avoid_rows = conn.execute(
        """SELECT summary FROM draft_evidence
           WHERE draft_type = 'probe_plan_avoid' AND draft_id = ?""",
        (plan_row["id"],),
    ).fetchall()

    return ProbePlanOut(
        id=plan_row["id"],
        system_id=plan_row["system_id"],
        snapshot_id=plan_row["snapshot_id"],
        intelligence_run_id=plan_row["intelligence_run_id"],
        feature_id=plan_row["feature_id"],
        objective=plan_row["objective"],
        status=plan_row["status"],
        avoid_reasons=[r["summary"] for r in avoid_rows],
        probe_points=[_probe_point_out(r) for r in point_rows],
        intelligence_run=run_out,
        is_mock=is_mock,
        created_at=plan_row["created_at"],
        updated_at=plan_row["updated_at"],
    )


@router.post(
    "/repository/probe-plans/generate",
    response_model=ProbePlanOut,
    status_code=201,
)
def generate_probe_plan_endpoint(
    feature_id: str,
    objective: str = "",
    system_id: int = Depends(get_system_id),
) -> ProbePlanOut:
    from ..probe_planner import AcceptedLink, generate_probe_plan
    from ..probe_planner import PROMPT_VERSION as PLAN_PROMPT_VERSION
    from ..probe_planner import SCHEMA_VERSION as PLAN_SCHEMA_VERSION

    with get_conn() as conn:
        snapshot_row = conn.execute(
            """SELECT * FROM repository_snapshots
               WHERE system_id = ? ORDER BY id DESC LIMIT 1""",
            (system_id,),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(
            status_code=400,
            detail="Latest snapshot is not ready.",
        )
    snapshot_id = snapshot_row["id"]

    with get_conn() as conn:
        fd_row = conn.execute(
            """SELECT fd.* FROM feature_drafts fd
               JOIN intelligence_runs ir ON fd.intelligence_run_id = ir.id
               WHERE fd.system_id = ? AND fd.feature_id = ?
                 AND fd.snapshot_id = ?
                 AND ir.status = 'completed'
               ORDER BY fd.id DESC LIMIT 1""",
            (system_id, feature_id, snapshot_id),
        ).fetchone()
    if fd_row is None:
        raise HTTPException(
            status_code=400,
            detail=f"No completed feature draft found for feature_id: {feature_id}",
        )

    with get_conn() as conn:
        link_rows = conn.execute(
            """SELECT fcl.*, cs.path AS sym_path, cs.qualified_name, cs.kind,
                      cs.start_line, cs.end_line, cs.decorators, cs.component_id,
                      cs.is_test, cs.docstring
               FROM feature_code_links fcl
               JOIN code_symbols cs ON fcl.symbol_id = cs.id
               WHERE fcl.system_id = ? AND fcl.feature_id = ?
                 AND fcl.snapshot_id = ?
                 AND fcl.review_status = 'accepted'
               ORDER BY fcl.confidence DESC""",
            (system_id, feature_id, snapshot_id),
        ).fetchall()
    if not link_rows:
        raise HTTPException(
            status_code=400,
            detail=f"No accepted code links for feature: {feature_id}. Accept links first.",
        )

    accepted_links = []
    for lr in link_rows:
        accepted_links.append(AcceptedLink(
            feature_id=lr["feature_id"],
            symbol_qualified_name=lr["qualified_name"],
            symbol_path=lr["sym_path"],
            symbol_kind=lr["kind"],
            start_line=lr["start_line"],
            end_line=lr["end_line"],
            decorators=json.loads(lr["decorators"]),
            component_id=lr["component_id"],
            is_test=bool(lr["is_test"]),
            docstring=lr["docstring"],
            relation_reason=lr["relation_reason"],
        ))

    llm_config = LLMConfig.from_env()
    intelligence_provider = os.getenv("INTELLIGENCE_LLM_PROVIDER", "").strip()
    intelligence_model = os.getenv("INTELLIGENCE_LLM_MODEL", "").strip()
    if intelligence_provider or intelligence_model:
        llm_config = replace(
            llm_config,
            provider=intelligence_provider or llm_config.provider,
            model=intelligence_model or llm_config.model,
        )

    started_at = time.time()
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Probe plan generation requires a configured reasoning model"
            )
        llm_client = create_llm_client(llm_config)
        plan_result = generate_probe_plan(
            llm_client,
            llm_config,
            feature_id=feature_id,
            feature_name=fd_row["name"],
            feature_summary=fd_row["summary"],
            feature_user_value=fd_row["user_value"],
            feature_success_criteria=json.loads(fd_row["success_criteria"]),
            feature_risks=json.loads(fd_row["risks"]),
            accepted_links=accepted_links,
            objective_hint=objective,
        )
    except LLMError as exc:
        from ..probe_planner import PlanResult
        plan_result = PlanResult(
            provider=llm_config.provider,
            model=llm_config.model,
            is_mock=llm_config.provider == "mock",
            feature_id=feature_id,
            objective="",
            probe_points=[],
            avoid_reasons=[],
            error=str(exc),
        )
    completed_at = time.time()

    status = "completed" if plan_result.error is None else "failed"
    now = time.time()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, error_details, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan', ?, ?, ?, ?, 'reasoning_llm',
                           ?, ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id,
                    plan_result.provider, plan_result.model,
                    PLAN_PROMPT_VERSION, PLAN_SCHEMA_VERSION,
                    status, plan_result.error,
                    1 if plan_result.is_mock else 0,
                    started_at, completed_at,
                ),
            )
            run_id = cur.lastrowid

            plan_status = "proposed" if plan_result.error is None else "rejected"
            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id,
                        feature_id, objective, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id, run_id,
                    feature_id, plan_result.objective,
                    plan_status, now, now,
                ),
            )
            plan_id = cur.lastrowid

            for point in plan_result.probe_points:
                conn.execute(
                    """INSERT INTO probe_points
                           (plan_id, system_id, component_id, feature_id,
                            path, symbol, line_start, line_end, reason,
                            recommended_mode, side_effect_risk, replayability,
                            denylist_hit, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                    (
                        plan_id, system_id, point.component_id, point.feature_id,
                        point.path, point.symbol, point.line_start, point.line_end,
                        point.reason, point.recommended_mode, point.side_effect_risk,
                        point.replayability, point.denylist_hit,
                        now, now,
                    ),
                )

            for avoid_reason in plan_result.avoid_reasons:
                conn.execute(
                    """INSERT INTO draft_evidence
                           (system_id, draft_type, draft_id, path,
                            start_line, end_line, summary)
                       VALUES (?, 'probe_plan_avoid', ?, '', 0, 0, ?)""",
                    (system_id, plan_id, avoid_reason),
                )

            plan_row = conn.execute(
                "SELECT * FROM probe_plans WHERE id = ?", (plan_id,),
            ).fetchone()
            result = _probe_plan_out(conn, plan_row)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return result


@router.get("/repository/probe-plans", response_model=ProbePlansListOut)
def get_probe_plans(
    system_id: int = Depends(get_system_id),
) -> ProbePlansListOut:
    with get_conn() as conn:
        plan_rows = conn.execute(
            "SELECT * FROM probe_plans WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        plans = [_probe_plan_out(conn, row) for row in plan_rows]
        is_mock = any(p.is_mock for p in plans)
    return ProbePlansListOut(system_id=system_id, plans=plans, is_mock=is_mock)


@router.get("/repository/probe-plans/{plan_id}", response_model=ProbePlanOut)
def get_probe_plan(
    plan_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePlanOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM probe_plans WHERE id = ? AND system_id = ?",
            (plan_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Probe plan not found")
        return _probe_plan_out(conn, row)


@router.put(
    "/repository/probe-points/{point_id}/status",
    response_model=ProbePointOut,
)
def update_probe_point_status(
    point_id: int,
    payload: ProbePointStatusUpdate,
    system_id: int = Depends(get_system_id),
) -> ProbePointOut:
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM probe_points WHERE id = ? AND system_id = ?",
            (point_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Probe point not found")
        if payload.status == "approved" and row["denylist_hit"]:
            raise HTTPException(
                status_code=409,
                detail="Safety-denylisted probe points cannot be approved",
            )
        conn.execute(
            """UPDATE probe_points SET status = ?, updated_at = ? WHERE id = ?""",
            (payload.status, now, point_id),
        )
        row = conn.execute(
            "SELECT * FROM probe_points WHERE id = ?", (point_id,),
        ).fetchone()
        return _probe_point_out(row)


# ---------------------------------------------------------------------------
# Flow graph explorer (Issue #43, Phase 1)
# ---------------------------------------------------------------------------


def _latest_ready_snapshot(conn, system_id: int):
    return conn.execute(
        """SELECT * FROM repository_snapshots
           WHERE system_id = ? AND status = 'ready'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()


def _load_flow_inputs(
    system_id: int,
    expected_snapshot_id: Optional[int] = None,
    expected_commit_sha: Optional[str] = None,
):
    """Load snapshot symbols and indexed Python sources for flow building.

    Returns ``(snapshot_row, symbol_records, files)`` or raises HTTPException.
    Only committed-snapshot content is read; no working-tree access.

    When ``expected_snapshot_id`` / ``expected_commit_sha`` are supplied they
    must match the current latest ready snapshot; otherwise the caller's graph
    is stale (a newer snapshot exists) and a 409 is raised so the UI reloads.
    """
    from ..flow_graph import SymbolRecord

    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            raise HTTPException(
                status_code=400,
                detail="No ready snapshot. Create a snapshot first.",
            )
        snapshot_id = snapshot_row["id"]

        if (
            expected_snapshot_id is not None
            and expected_snapshot_id != snapshot_id
        ) or (
            expected_commit_sha is not None
            and expected_commit_sha != snapshot_row["commit_sha"]
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Flow graph is stale: a newer snapshot exists "
                    f"(requested snapshot {expected_snapshot_id}/"
                    f"{expected_commit_sha}, current {snapshot_id}/"
                    f"{snapshot_row['commit_sha']}). Reload the flow graph."
                ),
            )
        sym_rows = conn.execute(
            "SELECT * FROM code_symbols WHERE snapshot_id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchall()
        file_rows = conn.execute(
            """SELECT path, content FROM snapshot_files
               WHERE snapshot_id = ? AND inclusion_status = 'indexed'
                 AND path LIKE '%.py'
               ORDER BY path""",
            (snapshot_id,),
        ).fetchall()

    if not sym_rows:
        raise HTTPException(
            status_code=400,
            detail="No symbols indexed for the latest snapshot. Run symbol indexing first.",
        )

    symbols = [
        SymbolRecord(
            symbol_id=r["id"],
            path=r["path"],
            qualified_name=r["qualified_name"],
            kind=r["kind"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            decorators=json.loads(r["decorators"]),
            component_id=r["component_id"],
            route_path=r["route_path"],
            route_method=r["route_method"],
            docstring=r["docstring"],
            is_test=bool(r["is_test"]),
        )
        for r in sym_rows
    ]
    files = [
        (fr["path"], bytes(fr["content"] or b"").decode("utf-8", errors="replace"))
        for fr in file_rows
    ]
    return snapshot_row, symbols, files


def _evidence_refs_out(evidence) -> List[EvidenceRefOut]:
    return [
        EvidenceRefOut(
            path=e.path, start_line=e.start_line, end_line=e.end_line, summary=e.summary,
        )
        for e in evidence
    ]


def _entrypoint_out(e) -> FlowEntrypointOut:
    return FlowEntrypointOut(
        entrypoint_type=e.entrypoint_type,
        entrypoint_id=e.entrypoint_id,
        label=e.label,
        path=e.path,
        qualified_name=e.qualified_name,
        line_start=e.line_start,
        line_end=e.line_end,
        component_id=e.component_id,
        route_method=e.route_method,
        route_path=e.route_path,
        category=e.category,
        framework=e.framework,
        operation=e.operation,
        confidence=e.confidence,
        evidence=_evidence_refs_out(e.evidence),
        source=getattr(e, "source", "deterministic"),
    )


def _load_persisted_api_entrypoints(system_id: int, snapshot_id: int):
    """Load previously-saved LLM-regex API entrypoints for a snapshot.

    These come from "Scan API definitions" (an ``api_scan`` reasoning run) and
    are kept separate from deterministic AST rows by ``source='reasoning_llm'``.
    """
    from ..flow_graph import EvidenceRef, FlowEntrypoint

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ? "
            "AND source = 'reasoning_llm'",
            (system_id, snapshot_id),
        ).fetchall()
    eps = []
    for r in rows:
        evidence = [
            EvidenceRef(
                path=ev["path"], start_line=ev["start_line"],
                end_line=ev["end_line"], summary=ev["summary"],
            )
            for ev in json.loads(r["evidence_json"])
        ]
        eps.append(FlowEntrypoint(
            entrypoint_type=r["entrypoint_type"],
            entrypoint_id=r["entrypoint_id"],
            label=r["label"],
            path=r["handler_path"],
            qualified_name=r["handler_qualified_name"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            route_method=r["route_method"],
            route_path=r["route_path"],
            category=r["category"],
            framework=r["framework"],
            operation=r["operation"],
            confidence=r["confidence"],
            evidence=evidence,
            source="reasoning_llm",
        ))
    return eps


def _ensure_entrypoints_indexed(system_id: int, snapshot_row, symbols, files):
    """Run deterministic entrypoint discovery and persist it once per snapshot.

    Discovery itself is deterministic and recomputed on each call (it is cheap);
    the persisted ``code_entrypoints`` rows and the ``entrypoint_index``
    intelligence run are the audit artefacts required by the issue. Persistence
    is idempotent: it happens only the first time for a given snapshot.
    """
    from ..entrypoint_discovery import DISCOVERY_VERSION, discover_entrypoints

    snapshot_id = snapshot_row["id"]
    llm_eps = _load_persisted_api_entrypoints(system_id, snapshot_id)
    discovery = discover_entrypoints(symbols, files, persisted_api=llm_eps)
    now = time.time()
    with get_conn() as conn:
        run_exists = conn.execute(
            """SELECT 1 FROM intelligence_runs
               WHERE system_id = ? AND snapshot_id = ?
                 AND run_type = 'entrypoint_index' LIMIT 1""",
            (system_id, snapshot_id),
        ).fetchone()
        if run_exists:
            return discovery
        conn.execute("BEGIN")
        try:
            conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'entrypoint_index', 'deterministic', 'ast',
                           ?, ?, 'deterministic', 'completed', 0, ?, ?)""",
                (system_id, snapshot_id, DISCOVERY_VERSION, DISCOVERY_VERSION,
                 now, now),
            )
            sym_rows = conn.execute(
                "SELECT id, path, qualified_name FROM code_symbols "
                "WHERE snapshot_id = ? AND system_id = ?",
                (snapshot_id, system_id),
            ).fetchall()
            sym_id = {(r["path"], r["qualified_name"]): r["id"] for r in sym_rows}
            for e in discovery.entrypoints:
                # LLM-regex entrypoints are persisted by the api_scan endpoint
                # with their own source/pattern_id; only the deterministic AST
                # rows are owned by this entrypoint_index run.
                if getattr(e, "source", "deterministic") != "deterministic":
                    continue
                evidence_json = json.dumps([
                    {"path": ev.path, "start_line": ev.start_line,
                     "end_line": ev.end_line, "summary": ev.summary}
                    for ev in e.evidence
                ])
                conn.execute(
                    """INSERT OR IGNORE INTO code_entrypoints
                           (system_id, snapshot_id, entrypoint_type, entrypoint_id,
                            category, label, operation, framework, handler_symbol_id,
                            handler_path, handler_qualified_name, line_start, line_end,
                            route_method, route_path, confidence, evidence_json,
                            source, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'deterministic', ?)""",
                    (
                        system_id, snapshot_id, e.entrypoint_type, e.entrypoint_id,
                        e.category, e.label, e.operation, e.framework,
                        sym_id.get((e.path, e.qualified_name)),
                        e.path, e.qualified_name, e.line_start, e.line_end,
                        e.route_method, e.route_path, e.confidence, evidence_json,
                        now,
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return discovery


def _ep_matches(e, needle: str) -> bool:
    return (
        needle in e.label.lower()
        or needle in e.path.lower()
        or needle in (e.operation or "").lower()
        or needle in e.qualified_name.lower()
    )


@router.get("/repository/flow-entrypoints", response_model=FlowEntrypointsOut)
def list_flow_entrypoints(
    system_id: int = Depends(get_system_id),
    category: Optional[str] = Query(
        default=None,
        description="Filter backend entrypoints by category: api | message_queue "
        "| scheduled_job | cli. 'function' returns the Advanced fallback; "
        "'all' (or omitted) returns every backend kind.",
    ),
    entrypoint_type: Optional[str] = Query(
        default=None,
        description="Alias of 'category'; also accepts dispatch types "
        "(http_route/public_function) for convenience.",
    ),
    q: Optional[str] = Query(default=None, description="Case-insensitive substring filter."),
    include_functions: bool = Query(
        default=False,
        description="Include the public-function Advanced fallback in 'functions'.",
    ),
) -> FlowEntrypointsOut:
    """List backend entrypoints (Issue #51, backend-entrypoint-first).

    ``entrypoints`` carries only backend entrypoints (api/message_queue/
    scheduled_job/cli) resolved to handler symbols. The public-function fallback
    is demoted to ``functions`` and returned only when explicitly requested
    (``include_functions`` or ``category=function``). ``counts`` and
    ``diagnostics`` let the UI explain thin discovery instead of dumping the raw
    function index.
    """
    from ..flow_graph import category_for_type

    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            return FlowEntrypointsOut(system_id=system_id)
    snapshot_row, symbols, files = _load_flow_inputs(system_id)
    discovery = _ensure_entrypoints_indexed(system_id, snapshot_row, symbols, files)

    backend = list(discovery.entrypoints)
    functions = list(discovery.functions)

    wanted = category or entrypoint_type
    wanted_category = None
    if wanted and wanted.lower() not in ("all", ""):
        wanted_category = category_for_type(wanted.lower())

    if wanted_category == "function":
        backend = []
    elif wanted_category is not None:
        backend = [e for e in backend if e.category == wanted_category]

    want_functions = include_functions or wanted_category == "function"
    funcs_out = functions if want_functions else []

    if q and q.strip():
        needle = q.strip().lower()
        backend = [e for e in backend if _ep_matches(e, needle)]
        funcs_out = [e for e in funcs_out if _ep_matches(e, needle)]

    return FlowEntrypointsOut(
        system_id=system_id,
        snapshot_id=snapshot_row["id"],
        commit_sha=snapshot_row["commit_sha"],
        total=discovery.backend_total,
        entrypoints=[_entrypoint_out(e) for e in backend],
        functions=[_entrypoint_out(e) for e in funcs_out],
        counts=EntrypointCountsOut(**discovery.counts),
        indexed_function_count=discovery.indexed_function_count,
        has_backend_entrypoints=discovery.backend_total > 0,
        frameworks=discovery.frameworks,
        diagnostics=discovery.diagnostics,
    )


def _load_scan_files(system_id: int, expected_snapshot_id=None, expected_commit_sha=None):
    """Load all indexed text files for the latest ready snapshot (any language).

    Unlike ``_load_flow_inputs`` this is not restricted to ``.py`` and does not
    require indexed symbols, because the LLM scan operates on raw source across
    frameworks/languages. Only committed-snapshot content is read.
    """
    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            raise HTTPException(
                status_code=400, detail="No ready snapshot. Create a snapshot first.",
            )
        snapshot_id = snapshot_row["id"]
        if (
            expected_snapshot_id is not None and expected_snapshot_id != snapshot_id
        ) or (
            expected_commit_sha is not None
            and expected_commit_sha != snapshot_row["commit_sha"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Snapshot is stale: a newer snapshot exists. Reload and retry.",
            )
        file_rows = conn.execute(
            """SELECT path, content FROM snapshot_files
               WHERE snapshot_id = ? AND inclusion_status = 'indexed'
               ORDER BY path""",
            (snapshot_id,),
        ).fetchall()
    files = [
        (fr["path"], bytes(fr["content"] or b"").decode("utf-8", errors="replace"))
        for fr in file_rows
    ]
    return snapshot_row, files


def _resolve_intelligence_config():
    """Return an LLMConfig with the intelligence-model override applied."""
    llm_config = LLMConfig.from_env()
    provider = os.getenv("INTELLIGENCE_LLM_PROVIDER", "").strip()
    model = os.getenv("INTELLIGENCE_LLM_MODEL", "").strip()
    if provider or model:
        llm_config = replace(
            llm_config,
            provider=provider or llm_config.provider,
            model=model or llm_config.model,
        )
    return llm_config


def _snapshot_config_stale_reason(system_id: int, snapshot_row) -> Optional[str]:
    """Return a clear reason when the latest snapshot predates repo settings.

    Repository include/exclude patterns are applied only when a snapshot is
    created. If the user adds e.g. ``backend/**`` after the latest snapshot, API
    discovery will still inspect the old file set and silently miss routes.
    """
    with get_conn() as conn:
        config_row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
    if config_row is None:
        return None

    snapshot_time = snapshot_row["completed_at"] or snapshot_row["created_at"] or 0
    if (config_row["updated_at"] or 0) <= snapshot_time:
        return None

    include_patterns = json.loads(config_row["include_patterns"] or "[]")
    exclude_patterns = json.loads(config_row["exclude_patterns"] or "[]")
    return (
        "Repository settings were updated after the latest snapshot was created. "
        "Create a new repository snapshot and re-run symbol indexing/API scan. "
        f"Current include_patterns={include_patterns}, "
        f"exclude_patterns={exclude_patterns}."
    )


def _api_scan_pattern_out(row) -> ApiScanPatternOut:
    examples = [
        EvidenceRefOut(path=ex.get("path", ""), start_line=int(ex.get("line", 0)),
                       end_line=int(ex.get("line", 0)), summary="example match")
        for ex in json.loads(row["examples_json"])
    ]
    return ApiScanPatternOut(
        id=row["id"],
        file_glob=row["file_glob"],
        regex=row["regex"],
        method_group=row["method_group"],
        path_group=row["path_group"],
        method_constant=row["method_constant"],
        framework=row["framework"],
        language=row["language"],
        reason=row["reason"],
        confidence=row["confidence"],
        match_count=row["match_count"],
        examples=examples,
    )


@router.post("/repository/api-scan", response_model=ApiScanResultOut)
def run_api_scan(
    payload: Optional[ApiScanRequest] = None,
    system_id: int = Depends(get_system_id),
) -> ApiScanResultOut:
    """Scan the snapshot with a reasoning model to discover API definitions.

    The model returns regular expressions that filter API definitions; the
    regexes are applied deterministically to the pinned snapshot to extract
    concrete entrypoints. Fails closed (no heuristic fallback) for mock or
    non-reasoning models. Persists an ``api_scan`` audit run, the reviewable
    regex patterns, and the extracted entrypoints (source='reasoning_llm').
    """
    from ..api_scan import (
        PROMPT_VERSION as SCAN_PROMPT_VERSION,
        SCHEMA_VERSION as SCAN_SCHEMA_VERSION,
        apply_patterns,
        build_snapshot_digest,
        generate_api_scan,
    )

    payload = payload or ApiScanRequest()
    snapshot_row, files = _load_scan_files(
        system_id, payload.snapshot_id, payload.commit_sha,
    )
    snapshot_id = snapshot_row["id"]
    llm_config = _resolve_intelligence_config()

    if payload.snapshot_id is None and payload.commit_sha is None:
        stale_reason = _snapshot_config_stale_reason(system_id, snapshot_row)
        if stale_reason:
            return ApiScanResultOut(
                system_id=system_id,
                snapshot_id=snapshot_id,
                commit_sha=snapshot_row["commit_sha"],
                status="failed",
                provider=llm_config.provider,
                model=llm_config.model,
                is_mock=llm_config.provider == "mock",
                error=stale_reason,
                diagnostics=[
                    "The API scanner only reads files stored in the selected "
                    "repository snapshot.",
                    "Repository include/exclude changes do not modify existing "
                    "snapshots.",
                ],
            )

    started_at = time.time()
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "API definition scanning requires a configured reasoning model"
            )
        client = create_llm_client(llm_config)
        digest = build_snapshot_digest(files)
        scan = generate_api_scan(client, llm_config, digest)
    except LLMError as exc:
        scan = type("R", (), {
            "provider": llm_config.provider, "model": llm_config.model,
            "is_mock": llm_config.provider == "mock", "patterns": [],
            "error": str(exc),
        })()

    extraction_diags: List[str] = []
    extracted = []
    if scan.error is None:
        extracted, extraction_diags = apply_patterns(scan.patterns, files)

    status = "completed" if scan.error is None else "failed"
    now = time.time()

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, error_details, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'api_scan', ?, ?, ?, ?, 'reasoning_llm',
                           ?, ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id, scan.provider, scan.model,
                    SCAN_PROMPT_VERSION, SCAN_SCHEMA_VERSION, status,
                    scan.error, 1 if scan.is_mock else 0, started_at, now,
                ),
            )
            run_id = cur.lastrowid

            if status == "completed":
                # Replace any prior LLM-regex discovery for this snapshot so a
                # re-scan is authoritative; deterministic rows are untouched.
                conn.execute(
                    "DELETE FROM code_entrypoints WHERE system_id = ? "
                    "AND snapshot_id = ? AND source = 'reasoning_llm'",
                    (system_id, snapshot_id),
                )
                pattern_ids = {}
                for pat in scan.patterns:
                    pcur = conn.execute(
                        """INSERT INTO code_entrypoint_patterns
                               (system_id, snapshot_id, intelligence_run_id,
                                file_glob, regex, method_group, path_group,
                                method_constant, framework, language, reason,
                                confidence, match_count, examples_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            system_id, snapshot_id, run_id, pat.file_glob,
                            pat.regex, pat.method_group, pat.path_group,
                            pat.method_constant, pat.framework, pat.language,
                            pat.reason, pat.confidence, 0,
                            json.dumps([
                                {"path": p, "line": ln} for p, ln in pat.examples
                            ]),
                            now,
                        ),
                    )
                    pattern_ids[(pat.file_glob, pat.regex)] = (pcur.lastrowid, pat)

                # Attribute each extracted entrypoint to the first pattern whose
                # glob+framework matches, for the per-pattern match_count.
                match_counts = {pid: 0 for pid, _ in pattern_ids.values()}
                for ep in extracted:
                    evidence_json = json.dumps([
                        {"path": ev.path, "start_line": ev.start_line,
                         "end_line": ev.end_line, "summary": ev.summary}
                        for ev in ep.evidence
                    ])
                    matched_pid = None
                    for pid, pat in pattern_ids.values():
                        if ep.framework == pat.framework and fnmatch.fnmatch(
                            ep.path, pat.file_glob
                        ):
                            matched_pid = pid
                            break
                    if matched_pid is not None:
                        match_counts[matched_pid] = match_counts.get(matched_pid, 0) + 1
                    conn.execute(
                        """INSERT OR IGNORE INTO code_entrypoints
                               (system_id, snapshot_id, entrypoint_type, entrypoint_id,
                                category, label, operation, framework, handler_symbol_id,
                                handler_path, handler_qualified_name, line_start, line_end,
                                route_method, route_path, confidence, evidence_json,
                                source, pattern_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?,
                                   'reasoning_llm', ?, ?)""",
                        (
                            system_id, snapshot_id, ep.entrypoint_type,
                            ep.entrypoint_id, ep.category, ep.label, ep.operation,
                            ep.framework, ep.path, ep.qualified_name, ep.line_start,
                            ep.line_end, ep.route_method, ep.route_path,
                            ep.confidence, evidence_json, matched_pid, now,
                        ),
                    )
                for pid, count in match_counts.items():
                    conn.execute(
                        "UPDATE code_entrypoint_patterns SET match_count = ? WHERE id = ?",
                        (count, pid),
                    )

            pattern_rows = conn.execute(
                "SELECT * FROM code_entrypoint_patterns WHERE intelligence_run_id = ? "
                "ORDER BY confidence DESC, id",
                (run_id,),
            ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    frameworks = sorted({p["framework"] for p in pattern_rows if p["framework"]})
    return ApiScanResultOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        commit_sha=snapshot_row["commit_sha"],
        run_id=run_id,
        status=status,
        provider=scan.provider,
        model=scan.model,
        is_mock=scan.is_mock,
        error=scan.error,
        patterns=[_api_scan_pattern_out(p) for p in pattern_rows],
        extracted_count=len(extracted) if status == "completed" else 0,
        frameworks=frameworks,
        diagnostics=extraction_diags,
    )


@router.get("/repository/api-scan", response_model=ApiScanResultOut)
def get_api_scan(system_id: int = Depends(get_system_id)) -> ApiScanResultOut:
    """Return the latest API scan (patterns + extracted count) for the snapshot."""
    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            return ApiScanResultOut(system_id=system_id, status="none")
        snapshot_id = snapshot_row["id"]
        run = conn.execute(
            """SELECT * FROM intelligence_runs
               WHERE system_id = ? AND snapshot_id = ? AND run_type = 'api_scan'
               ORDER BY id DESC LIMIT 1""",
            (system_id, snapshot_id),
        ).fetchone()
        if run is None:
            return ApiScanResultOut(
                system_id=system_id, snapshot_id=snapshot_id,
                commit_sha=snapshot_row["commit_sha"], status="none",
            )
        pattern_rows = conn.execute(
            "SELECT * FROM code_entrypoint_patterns WHERE intelligence_run_id = ? "
            "ORDER BY confidence DESC, id",
            (run["id"],),
        ).fetchall()
        extracted_count = conn.execute(
            "SELECT COUNT(*) AS c FROM code_entrypoints WHERE system_id = ? "
            "AND snapshot_id = ? AND source = 'reasoning_llm'",
            (system_id, snapshot_id),
        ).fetchone()["c"]

    frameworks = sorted({p["framework"] for p in pattern_rows if p["framework"]})
    return ApiScanResultOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        commit_sha=snapshot_row["commit_sha"],
        run_id=run["id"],
        status=run["status"],
        provider=run["provider"],
        model=run["model"],
        is_mock=bool(run["is_mock"]),
        error=run["error_details"],
        patterns=[_api_scan_pattern_out(p) for p in pattern_rows],
        extracted_count=extracted_count,
        frameworks=frameworks,
        diagnostics=[],
    )


def _preview_out(preview) -> ProbePreviewOut:
    return ProbePreviewOut(
        recommended_mode=preview.recommended_mode,
        captured_data=preview.captured_data,
        redaction=preview.redaction,
        replayability=preview.replayability,
        estimated_event_volume=preview.estimated_event_volume,
        side_effect_risk=preview.side_effect_risk,
        denylist_hit=preview.denylist_hit,
    )


def _flow_graph_out(system_id: int, graph) -> FlowGraphOut:
    from ..flow_graph import build_edge_preview, build_node_preview

    nodes_by_id = {n.node_id: n for n in graph.nodes}
    return FlowGraphOut(
        system_id=system_id,
        snapshot_id=graph.snapshot_id,
        commit_sha=graph.commit_sha,
        entrypoint=_entrypoint_out(graph.entrypoint),
        nodes=[
            FlowNodeOut(
                node_id=n.node_id,
                node_type=n.node_type,
                symbol_id=n.symbol_id,
                qualified_name=n.qualified_name,
                path=n.path,
                line_start=n.line_start,
                line_end=n.line_end,
                component_id=n.component_id,
                probe_capabilities=n.probe_capabilities,
                risk=n.risk,
                denylist_hit=n.denylist_hit,
                evidence=_evidence_refs_out(n.evidence),
                boundary_kind=n.boundary_kind,
                is_external=n.is_external,
                trace_count=n.trace_count,
                error_count=n.error_count,
                evaluation_pass=n.evaluation_pass,
                evaluation_fail=n.evaluation_fail,
                observed=n.observed,
                preview=(
                    None if n.is_external
                    else _preview_out(build_node_preview(n))
                ),
            )
            for n in graph.nodes
        ],
        edges=[
            FlowEdgeOut(
                edge_id=e.edge_id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                edge_type=e.edge_type,
                confidence=e.confidence,
                resolution=e.resolution,
                callee_name=e.callee_name,
                line=e.line,
                evidence=_evidence_refs_out(e.evidence),
                preview=_preview_out(build_edge_preview(
                    e,
                    nodes_by_id.get(e.source_node_id),
                    nodes_by_id.get(e.target_node_id) if e.target_node_id else None,
                )),
            )
            for e in graph.edges
        ],
        candidate_paths=[
            CandidateFlowOut(
                flow_id=c.flow_id,
                title=c.title,
                summary=c.summary,
                entrypoint_node_id=c.entrypoint_node_id,
                node_ids=c.node_ids,
                node_count=c.node_count,
                max_depth=c.max_depth,
                confidence=c.confidence,
                unresolved_edge_count=c.unresolved_edge_count,
                external_boundary_count=c.external_boundary_count,
                observed_node_count=c.observed_node_count,
                unobserved_node_ids=c.unobserved_node_ids,
            )
            for c in graph.candidate_paths
        ],
        diagnostics=graph.diagnostics,
        truncated=graph.truncated,
    )


def _overlay_runtime(system_id: int, graph) -> None:
    """Overlay real trace/evaluation data onto in-repo nodes (Phase 2/3).

    Only nodes that carry a component_id (i.e. already instrumented symbols)
    can have runtime observations. Boundary nodes and uninstrumented symbols
    stay at zero. This reads aggregate counts only; no payloads are exposed.
    """
    from ..flow_graph import apply_observed_overlay

    component_ids = sorted({
        n.component_id for n in graph.nodes if n.component_id
    })
    if component_ids:
        placeholders = ",".join("?" for _ in component_ids)
        with get_conn() as conn:
            trace_rows = conn.execute(
                f"""SELECT component_id,
                           COUNT(*) AS total,
                           SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) AS errors
                    FROM traces
                    WHERE system_id = ? AND component_id IN ({placeholders})
                    GROUP BY component_id""",
                (system_id, *component_ids),
            ).fetchall()
            eval_rows = conn.execute(
                f"""SELECT component_id,
                           SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS passed,
                           SUM(CASE WHEN status = 'ng' THEN 1 ELSE 0 END) AS failed
                    FROM evaluation_results
                    WHERE system_id = ? AND component_id IN ({placeholders})
                    GROUP BY component_id""",
                (system_id, *component_ids),
            ).fetchall()
        traces_by_comp = {r["component_id"]: r for r in trace_rows}
        evals_by_comp = {r["component_id"]: r for r in eval_rows}
        for node in graph.nodes:
            if not node.component_id:
                continue
            tr = traces_by_comp.get(node.component_id)
            if tr:
                node.trace_count = tr["total"] or 0
                node.error_count = tr["errors"] or 0
                node.observed = node.trace_count > 0
            ev = evals_by_comp.get(node.component_id)
            if ev:
                node.evaluation_pass = ev["passed"] or 0
                node.evaluation_fail = ev["failed"] or 0
    apply_observed_overlay(graph)


@router.post("/repository/flow-graphs", response_model=FlowGraphOut)
def build_flow_graph_endpoint(
    payload: FlowGraphRequest,
    system_id: int = Depends(get_system_id),
) -> FlowGraphOut:
    from ..entrypoint_discovery import discover_entrypoints
    from ..flow_graph import build_flow_graph

    snapshot_row, symbols, files = _load_flow_inputs(
        system_id, payload.snapshot_id, payload.commit_sha,
    )
    llm_eps = _load_persisted_api_entrypoints(system_id, snapshot_row["id"])
    discovery = discover_entrypoints(symbols, files, persisted_api=llm_eps)
    graph = build_flow_graph(
        symbols=symbols,
        files=files,
        snapshot_id=snapshot_row["id"],
        commit_sha=snapshot_row["commit_sha"],
        entrypoint_type=payload.entrypoint_type,
        entrypoint_id=payload.entrypoint_id,
        max_depth=payload.max_depth,
        max_nodes=payload.max_nodes,
        entrypoints=discovery.entrypoints + discovery.functions,
    )
    if graph is None:
        _raise_entrypoint_not_found(payload.entrypoint_id, llm_eps)
    _overlay_runtime(system_id, graph)
    return _flow_graph_out(system_id, graph)


def _raise_entrypoint_not_found(entrypoint_id: str, llm_eps) -> None:
    """Raise a 404, or a clearer 422 for LLM-discovered API entrypoints.

    Entrypoints recovered from LLM-generated regexes have no resolved handler
    symbol, so a deterministic call tree cannot be rooted from them yet.
    """
    if any(e.entrypoint_id == entrypoint_id for e in llm_eps):
        raise HTTPException(
            status_code=422,
            detail=(
                "This API was detected by an LLM-generated regex and is not yet "
                "mapped to a handler symbol, so a process tree cannot be built "
                "from it. It is listed for visibility only."
            ),
        )
    raise HTTPException(
        status_code=404,
        detail=f"Entrypoint not found in snapshot: {entrypoint_id}",
    )


def _component_id_for(qualified_name: str) -> str:
    base = qualified_name.rsplit(".", 1)[-1]
    kebab = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"{kebab}-observer" if kebab else "flow-observer"


@router.post(
    "/repository/probe-plans/from-flow",
    response_model=ProbePlanOut,
    status_code=201,
)
def create_probe_plan_from_flow(
    payload: ProbePlanFromFlowRequest,
    system_id: int = Depends(get_system_id),
) -> ProbePlanOut:
    """Convert user-selected flow nodes into a manual Probe Plan draft.

    This is an explicit, user-driven selection (decision_method=manual). It
    only records a draft; it does not generate, apply, or run any patch.
    """
    from ..entrypoint_discovery import discover_entrypoints
    from ..flow_graph import (
        build_edge_preview,
        build_flow_graph,
        build_node_preview,
    )

    snapshot_row, symbols, files = _load_flow_inputs(
        system_id, payload.snapshot_id, payload.commit_sha,
    )
    llm_eps = _load_persisted_api_entrypoints(system_id, snapshot_row["id"])
    discovery = discover_entrypoints(symbols, files, persisted_api=llm_eps)
    graph = build_flow_graph(
        symbols=symbols,
        files=files,
        snapshot_id=snapshot_row["id"],
        commit_sha=snapshot_row["commit_sha"],
        entrypoint_type=payload.entrypoint_type,
        entrypoint_id=payload.entrypoint_id,
        max_depth=payload.max_depth,
        max_nodes=payload.max_nodes,
        entrypoints=discovery.entrypoints + discovery.functions,
    )
    if graph is None:
        _raise_entrypoint_not_found(payload.entrypoint_id, llm_eps)

    nodes_by_id = {n.node_id: n for n in graph.nodes}
    edges_by_id = {e.edge_id: e for e in graph.edges}

    # Resolve each selection to (the in-repo symbol node to instrument, a probe
    # reason, and a deterministic preview). Edge/boundary selections always map
    # to the in-repo caller; external nodes are never instrumented directly.
    resolved_selections = []
    for sel in payload.selections:
        if sel.target_type == "edge":
            edge = edges_by_id.get(sel.edge_id)
            if edge is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Selected edge is not part of this flow graph: {sel.edge_id}",
                )
            # Unresolved dynamic calls are not a confirmed boundary on the graph
            # and must not be turned into a Probe Point. The dashboard already
            # blocks selecting them; enforce the same safety boundary in the API
            # so it does not depend on the UI alone (issue #47).
            if edge.resolution == "unresolved":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Unresolved edge cannot be selected as a probe boundary: "
                        f"{sel.edge_id}"
                    ),
                )
            caller = nodes_by_id.get(edge.source_node_id)
            if caller is None or caller.is_external:
                raise HTTPException(
                    status_code=400,
                    detail=f"Edge source is not an instrumentable node: {sel.edge_id}",
                )
            target = (
                nodes_by_id.get(edge.target_node_id)
                if edge.target_node_id else None
            )
            preview = build_edge_preview(edge, caller, target)
            callee = target.qualified_name if target else edge.callee_name
            reason = (
                f"User-selected call boundary in flow '{graph.entrypoint.label}': "
                f"observe before/after {edge.callee_name}() "
                f"({edge.edge_type}) at {caller.path}:{edge.line}. Target: {callee}."
            )
            resolved_selections.append((sel, caller, "boundary", reason, preview))
        else:
            node = nodes_by_id.get(sel.node_id)
            if node is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Selected node is not part of this flow graph: {sel.node_id}",
                )
            if node.is_external:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "External boundary nodes cannot be instrumented directly. "
                        "Select the in-repo caller and observe its call boundary instead: "
                        f"{sel.node_id}"
                    ),
                )
            preview = build_node_preview(node)
            observation = {
                "input": "function input",
                "output": "function output / return / error",
                "boundary": "call boundary (before/after)",
            }[sel.observation]
            reason = (
                f"User-selected from execution flow '{graph.entrypoint.label}'. "
                f"Observe {observation}."
            )
            resolved_selections.append((sel, node, sel.observation, reason, preview))

    snapshot_id = snapshot_row["id"]
    now = time.time()
    feature_id = payload.entrypoint_id

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan_from_flow', 'manual', 'n/a',
                           'flow-v1', 'flow-v1', 'manual',
                           'completed', 0, ?, ?)""",
                (system_id, snapshot_id, now, now),
            )
            run_id = cur.lastrowid

            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id,
                        feature_id, objective, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                (
                    system_id, snapshot_id, run_id, feature_id,
                    payload.objective.strip(), now, now,
                ),
            )
            plan_id = cur.lastrowid

            for sel, node, _obs, reason, preview in resolved_selections:
                # The preview carries the deterministic risk/replayability/
                # denylist assessment for the instrumented (in-repo) symbol or
                # its observed boundary.
                denylist_hit = preview.denylist_hit
                risk = preview.side_effect_risk
                conn.execute(
                    """INSERT INTO probe_points
                           (plan_id, system_id, component_id, feature_id,
                            path, symbol, line_start, line_end, reason,
                            recommended_mode, side_effect_risk, replayability,
                            denylist_hit, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                    (
                        plan_id, system_id,
                        node.component_id or _component_id_for(node.qualified_name),
                        feature_id, node.path, node.qualified_name,
                        node.line_start, node.line_end, reason,
                        sel.mode_preference, risk, preview.replayability,
                        denylist_hit, now, now,
                    ),
                )

            plan_row = conn.execute(
                "SELECT * FROM probe_plans WHERE id = ?", (plan_id,),
            ).fetchone()
            result = _probe_plan_out(conn, plan_row)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


# ---------------------------------------------------------------------------
# Patch generation
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-plans/{plan_id}/patch",
    response_model=ProbePatchOut,
    status_code=201,
)
def generate_patch_endpoint(
    plan_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePatchOut:
    from ..patch_generator import ApprovedPoint, generate_patch

    with get_conn() as conn:
        plan_row = conn.execute(
            "SELECT * FROM probe_plans WHERE id = ? AND system_id = ?",
            (plan_id, system_id),
        ).fetchone()
    if plan_row is None:
        raise HTTPException(status_code=404, detail="Probe plan not found")

    snapshot_id = plan_row["snapshot_id"]
    with get_conn() as conn:
        snapshot_row = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(status_code=400, detail="Snapshot is not ready")
    if not snapshot_row["repo_path"]:
        raise HTTPException(status_code=409, detail="Snapshot repository path is unavailable")

    with get_conn() as conn:
        point_rows = conn.execute(
            """SELECT * FROM probe_points
               WHERE plan_id = ? AND status = 'approved'
                 AND denylist_hit IS NULL
               ORDER BY path, line_start""",
            (plan_id,),
        ).fetchall()
    if not point_rows:
        raise HTTPException(
            status_code=400,
            detail="No approved probe points. Approve points before generating a patch.",
        )

    approved = [
        ApprovedPoint(
            component_id=r["component_id"],
            path=r["path"],
            symbol=r["symbol"],
            recommended_mode=r["recommended_mode"],
            line_start=r["line_start"],
            line_end=r["line_end"],
        )
        for r in point_rows
    ]

    worktree_base = os.getenv("PROBE_WORKTREE_BASE", "/tmp/probe-worktrees")
    os.makedirs(worktree_base, exist_ok=True)

    patch_result = generate_patch(
        repo_path=snapshot_row["repo_path"],
        commit_sha=snapshot_row["commit_sha"],
        approved_points=approved,
        worktree_base=worktree_base,
    )

    now = time.time()
    patch_status = "generated" if patch_result.error is None else "failed"

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO probe_patches
                   (plan_id, system_id, snapshot_id, commit_sha, diff,
                    worktree_path, skipped, status, error,
                    cleanup_state, cleanup_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan_id, system_id, snapshot_id,
                snapshot_row["commit_sha"], patch_result.diff,
                patch_result.worktree_path,
                json.dumps(patch_result.skipped),
                patch_status, patch_result.error,
                patch_result.cleanup_state, patch_result.cleanup_error, now,
            ),
        )
        patch_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ?", (patch_id,),
        ).fetchone()

    with get_conn() as conn:
        return _probe_patch_out(conn, row)


@router.get("/repository/probe-patches", response_model=List[ProbePatchOut])
def list_probe_patches(
    system_id: int = Depends(get_system_id),
) -> List[ProbePatchOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM probe_patches WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_probe_patch_out(conn, row) for row in rows]


@router.get("/repository/probe-patches/{patch_id}", response_model=ProbePatchOut)
def get_probe_patch(
    patch_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePatchOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Patch not found")

        return _probe_patch_out(conn, row)


@router.get("/repository/probe-patches/{patch_id}/download")
def download_probe_patch(
    patch_id: int,
    system_id: int = Depends(get_system_id),
) -> Response:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT diff FROM probe_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Patch not found")
    return Response(
        content=row["diff"],
        media_type="text/x-diff",
        headers={
            "Content-Disposition": f'attachment; filename="probe-patch-{patch_id}.diff"'
        },
    )


# ---------------------------------------------------------------------------
# Explicit application to the source repository
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-patches/{patch_id}/apply",
    response_model=ProbePatchOut,
)
def apply_probe_patch_endpoint(
    patch_id: int,
    payload: ProbePatchApplyRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProbePatchOut:
    from ..patch_generator import apply_patch_to_repository

    with get_conn() as conn:
        patch_row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
        if patch_row is None:
            raise HTTPException(status_code=404, detail="Patch not found")
        snapshot_row = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (patch_row["snapshot_id"], system_id),
        ).fetchone()
        validation_rows = conn.execute(
            """
            SELECT variant, overall_success
            FROM validation_runs
            WHERE patch_id = ?
            ORDER BY id DESC
            """,
            (patch_id,),
        ).fetchall()

    if patch_row["apply_status"] == "applied":
        raise HTTPException(status_code=409, detail="Patch has already been applied")
    if patch_row["status"] == "failed" or not patch_row["diff"].strip():
        raise HTTPException(status_code=400, detail="Patch is not applicable")
    if payload.expected_commit_sha != patch_row["commit_sha"]:
        raise HTTPException(
            status_code=409,
            detail="Confirmation commit does not match the patch snapshot",
        )
    if snapshot_row is None or not snapshot_row["repo_path"]:
        raise HTTPException(
            status_code=409,
            detail="Snapshot repository path is unavailable",
        )

    latest_validation = {}
    for row in validation_rows:
        latest_validation.setdefault(row["variant"], bool(row["overall_success"]))
    if not (
        latest_validation.get("baseline") is True
        and latest_validation.get("probed") is True
    ):
        raise HTTPException(
            status_code=409,
            detail="A successful baseline and probed validation is required",
        )

    apply_error = apply_patch_to_repository(
        snapshot_row["repo_path"],
        patch_row["commit_sha"],
        patch_row["diff"],
    )
    now = time.time()
    with get_conn() as conn:
        if apply_error:
            conn.execute(
                """
                UPDATE probe_patches
                SET apply_status = 'failed', apply_error = ?
                WHERE id = ?
                """,
                (apply_error, patch_id),
            )
            raise HTTPException(status_code=409, detail=apply_error)
        conn.execute(
            """
            UPDATE probe_patches
            SET apply_status = 'applied', apply_error = NULL,
                applied_at = ?, applied_by_user_id = ?
            WHERE id = ?
            """,
            (now, principal.user_id, patch_id),
        )
        row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ?", (patch_id,)
        ).fetchone()
        return _probe_patch_out(conn, row)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-patches/{patch_id}/validate",
    response_model=ProbePatchOut,
    status_code=201,
)
def validate_patch_endpoint(
    patch_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePatchOut:
    from ..patch_generator import (
        apply_unified_diff,
        cleanup_worktree,
        create_worktree,
    )
    from ..validation_runner import load_validation_config_text, run_validation

    with get_conn() as conn:
        patch_row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
    if patch_row is None:
        raise HTTPException(status_code=404, detail="Patch not found")
    if patch_row["status"] == "failed":
        raise HTTPException(status_code=400, detail="Cannot validate a failed patch")

    with get_conn() as conn:
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE id = ? AND system_id = ?
            """,
            (patch_row["snapshot_id"], system_id),
        ).fetchone()
        config_file = conn.execute(
            """
            SELECT path, content FROM snapshot_files
            WHERE snapshot_id = ? AND path IN ('probe-agent.yml', 'probe-agent.example.yml')
            ORDER BY CASE path WHEN 'probe-agent.yml' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (patch_row["snapshot_id"],),
        ).fetchone()
    if snapshot_row is None or not snapshot_row["repo_path"]:
        raise HTTPException(status_code=409, detail="Snapshot repository path is unavailable")

    repo_path = snapshot_row["repo_path"]
    commit_sha = patch_row["commit_sha"]

    try:
        if config_file is not None:
            config_bytes = bytes(config_file["content"] or b"")
        else:
            config_bytes = None
            for config_path in ("probe-agent.yml", "probe-agent.example.yml"):
                try:
                    config_bytes = read_file_at_commit(
                        repo_path, commit_sha, config_path
                    )
                    break
                except GitError:
                    continue
            if config_bytes is None:
                raise GitError(
                    "probe-agent.yml is not present at the pinned commit"
                )
        val_config = load_validation_config_text(
            config_bytes.decode("utf-8", errors="strict")
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or missing pinned probe-agent.yml: {exc}",
        )

    worktree_base = os.getenv("PROBE_WORKTREE_BASE", "/tmp/probe-worktrees")
    os.makedirs(worktree_base, exist_ok=True)

    baseline_worktree = None
    probed_worktree = None
    now = time.time()
    cleanup_results = {}

    try:
        baseline_worktree = create_worktree(
            repo_path, commit_sha, worktree_base + "/baseline",
        )
        probed_worktree = create_worktree(
            repo_path, commit_sha, worktree_base + "/probed",
        )
        patch_error = apply_unified_diff(probed_worktree, patch_row["diff"])
        if patch_error:
            raise GitError(f"Failed to apply stored patch: {patch_error}")
    except GitError as exc:
        if baseline_worktree:
            cleanup_results["baseline"] = cleanup_worktree(
                repo_path, baseline_worktree
            )
        if probed_worktree:
            cleanup_results["probed"] = cleanup_worktree(repo_path, probed_worktree)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to prepare validation worktrees: {exc}",
        )

    try:
        baseline_result = run_validation("baseline", baseline_worktree, val_config)
        with get_conn() as conn:
            trace_count_before = conn.execute(
                "SELECT COUNT(*) AS count FROM traces WHERE system_id = ?",
                (system_id,),
            ).fetchone()["count"]
        probed_result = run_validation("probed", probed_worktree, val_config)
        with get_conn() as conn:
            trace_count_after = conn.execute(
                "SELECT COUNT(*) AS count FROM traces WHERE system_id = ?",
                (system_id,),
            ).fetchone()["count"]
        probed_result.trace_received = trace_count_after > trace_count_before
        probed_result.trace_status = (
            "received" if probed_result.trace_received else "missing"
        )
    finally:
        if baseline_worktree:
            cleanup_results["baseline"] = cleanup_worktree(
                repo_path, baseline_worktree
            )
        if probed_worktree:
            cleanup_results["probed"] = cleanup_worktree(repo_path, probed_worktree)

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            for result in [baseline_result, probed_result]:
                cleanup_result = cleanup_results.get(result.variant)
                cleanup_state = (
                    cleanup_result.state if cleanup_result else "not_attempted"
                )
                cleanup_error = cleanup_result.error if cleanup_result else None
                cur = conn.execute(
                    """INSERT INTO validation_runs
                           (patch_id, system_id, variant, worktree_path,
                            overall_success, total_duration_ms, trace_received,
                            trace_status, network_isolation, cleanup_state,
                            cleanup_error, error, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        patch_id, system_id, result.variant,
                        result.worktree_path,
                        1 if result.overall_success else 0,
                        result.total_duration_ms,
                        (
                            None if getattr(result, "trace_received", None) is None
                            else (1 if result.trace_received else 0)
                        ),
                        getattr(result, "trace_status", "not_checked"),
                        result.network_isolation,
                        cleanup_state,
                        cleanup_error,
                        result.error, now,
                    ),
                )
                run_id = cur.lastrowid
                for cmd in result.results:
                    conn.execute(
                        """INSERT INTO validation_commands
                               (run_id, command, exit_code, duration_ms,
                                stdout, stderr, stdout_truncated,
                                stderr_truncated, timed_out)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            run_id, cmd.command, cmd.exit_code,
                            cmd.duration_ms, cmd.stdout, cmd.stderr,
                            1 if cmd.stdout_truncated else 0,
                            1 if cmd.stderr_truncated else 0,
                            1 if cmd.timed_out else 0,
                        ),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return get_probe_patch(patch_id, system_id)
