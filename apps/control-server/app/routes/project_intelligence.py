import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..draft_generator import generate_drafts
from ..git_ops import GitError, create_snapshot
from ..llm import LLMConfig, get_llm_client
from ..models import (
    DraftGenerationResult,
    ExperimentSummary,
    ExperimentVariant,
    FeatureCodeLink,
    FeatureDraftOut,
    FeatureEvidence,
    FeatureProfile,
    IntelligenceRunOut,
    LatestDraftsOut,
    ProbePlan,
    ProbePoint,
    ProjectIntelligenceMock,
    RepositoryConfigOut,
    RepositoryConfigUpdate,
    RepositorySnapshot,
    SnapshotFileOut,
    SnapshotOut,
    SystemProfile,
    SystemProfileDraftOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Repository configuration
# ---------------------------------------------------------------------------


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
            "SELECT path, source_type, size_bytes FROM snapshot_files WHERE snapshot_id = ?",
            (snapshot_row["id"],),
        ).fetchall()
        files = [
            SnapshotFileOut(
                path=fr["path"],
                source_type=fr["source_type"],
                size_bytes=fr["size_bytes"],
            )
            for fr in file_rows
        ]
    return SnapshotOut(
        id=snapshot_row["id"],
        system_id=snapshot_row["system_id"],
        commit_sha=snapshot_row["commit_sha"],
        status=snapshot_row["status"],
        file_count=snapshot_row["file_count"],
        total_size=snapshot_row["total_size"],
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
                (system_id, commit_sha, status, created_at)
            VALUES (?, '', 'indexing', ?)
            """,
            (system_id, now),
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
    completed_at = time.time()

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE repository_snapshots
            SET commit_sha = ?, status = 'ready', file_count = ?,
                total_size = ?, completed_at = ?
            WHERE id = ?
            """,
            (commit_sha, len(files), total_size, completed_at, snapshot_id),
        )
        for f in files:
            conn.execute(
                """
                INSERT INTO snapshot_files
                    (snapshot_id, path, source_type, size_bytes, content_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (snapshot_id, f.path, f.source_type, f.size_bytes, f.content_hash),
            )
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
            WHERE system_id = ? AND status = 'ready'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()
    if snapshot_row is None:
        raise HTTPException(
            status_code=400,
            detail="No ready snapshot. Create a snapshot first.",
        )

    snapshot_id = snapshot_row["id"]

    with get_conn() as conn:
        file_rows = conn.execute(
            "SELECT path, source_type, size_bytes, content_hash FROM snapshot_files WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()

    from ..git_ops import IndexedFile, read_file_at_commit

    config_row = None
    with get_conn() as conn:
        config_row = conn.execute(
            "SELECT repo_path FROM repository_configs WHERE system_id = ?",
            (system_id,),
        ).fetchone()

    indexed_files = []
    for fr in file_rows:
        content = b""
        if config_row:
            try:
                content = read_file_at_commit(
                    config_row["repo_path"],
                    snapshot_row["commit_sha"],
                    fr["path"],
                )
            except Exception:
                pass
        indexed_files.append(IndexedFile(
            path=fr["path"],
            source_type=fr["source_type"],
            size_bytes=fr["size_bytes"],
            content_hash=fr["content_hash"] or "",
            content=content,
        ))

    llm_config = LLMConfig.from_env()
    llm_client = get_llm_client()

    started_at = time.time()
    result = generate_drafts(llm_client, llm_config, indexed_files)
    completed_at = time.time()

    status = "completed" if result.error is None else "failed"
    decision_method = "reasoning_llm"

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method,
                 status, error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'system_profile_draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id, snapshot_id,
                result.provider, result.model,
                "v1", "v1", decision_method,
                status, result.error,
                1 if result.is_mock else 0,
                started_at, completed_at,
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
                    system_id, run_id, snapshot_id,
                    sp.name, sp.purpose,
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
                    (system_id, sp_draft_id, ev.path,
                     ev.start_line, ev.end_line, ev.summary),
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
                    system_id, run_id, snapshot_id,
                    fd.feature_id, fd.name, fd.summary, fd.user_value,
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
                    (system_id, fd_id, ev.path,
                     ev.start_line, ev.end_line, ev.summary),
                )
            fd_row = conn.execute(
                "SELECT * FROM feature_drafts WHERE id = ?", (fd_id,)
            ).fetchone()
            feature_drafts_out.append(_feature_draft_out(conn, fd_row))

        run_row = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
        ).fetchone()

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
        snapshot_row = conn.execute(
            """
            SELECT * FROM repository_snapshots
            WHERE system_id = ? AND status = 'ready'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()

        snapshot_out = None
        if snapshot_row:
            snapshot_out = _snapshot_out(conn, snapshot_row, include_files=True)

        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
        ).fetchone()

        run_out = None
        sp_draft_out = None
        feature_drafts_out = []

        if run_row:
            run_out = _intelligence_run_out(run_row)
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
            feature_drafts_out = [_feature_draft_out(conn, r) for r in fd_rows]

    return LatestDraftsOut(
        system_id=system_id,
        snapshot=snapshot_out,
        intelligence_run=run_out,
        system_profile_draft=sp_draft_out,
        feature_drafts=feature_drafts_out,
    )


# ---------------------------------------------------------------------------
# Legacy mock endpoint — kept for Probe Planner / Experiments tabs
# ---------------------------------------------------------------------------


@router.get("/project-intelligence", response_model=ProjectIntelligenceMock)
def get_project_intelligence_mock(
    system_id: int = Depends(get_system_id),
) -> ProjectIntelligenceMock:
    """Development fixture for tabs not yet backed by real data (Probe Planner, Experiments)."""
    repository = RepositorySnapshot(
        repo_path="/path/to/target-repository",
        commit_sha="0000000000000000000000000000000000000000",
        included_paths=["README.md", "docs/**", "src/**", "tests/**"],
        excluded_paths=[".env", "secrets/**", "data/**"],
        status="not_configured",
    )
    features = [
        FeatureProfile(
            feature_id="repository-understanding",
            name="Repository Understanding",
            summary="Committed documentation and source are indexed with evidence.",
            user_value="The agent can explain the system before proposing instrumentation.",
            success_criteria=[
                "Only files from a pinned commit are read",
                "Every generated statement links to repository evidence",
            ],
            risks=["Secrets may be exposed if the committed-files boundary is bypassed"],
            evidence=[
                FeatureEvidence(
                    path="docs/project-intelligence.md",
                    start_line=1,
                    end_line=10,
                    summary="Defines the committed-files-only boundary.",
                )
            ],
            code_links=[
                FeatureCodeLink(
                    path="apps/control-server/app/routes/project_intelligence.py",
                    symbol="get_project_intelligence_mock",
                    kind="route",
                    confidence=1.0,
                    decision_method="manual",
                )
            ],
            decision_method="manual",
        ),
    ]
    probe_plans = [
        ProbePlan(
            feature_id="feature-map",
            objective="Observe feature extraction without modifying the target repository.",
            probe_points=[
                ProbePoint(
                    component_id="feature-map-builder",
                    feature_id="feature-map",
                    path="apps/control-server/app/routes/project_intelligence.py",
                    symbol="get_project_intelligence_mock",
                    reason="Represents the future feature extraction boundary.",
                    recommended_mode="trace",
                    side_effect_risk="low",
                )
            ],
            avoid_probe_points=[
                "Git credential handling",
                "File deletion, persistence, billing, and external side effects",
            ],
            decision_method="manual",
        )
    ]
    experiments = [
        ExperimentSummary(
            experiment_id="mock-experiment-1",
            feature_id="feature-map",
            objective="Compare baseline feature mapping with a code-index-assisted variant.",
            baseline_commit=repository.commit_sha,
            variants=[
                ExperimentVariant(variant_id="baseline", label="Documentation only"),
                ExperimentVariant(
                    variant_id="ast-index",
                    label="Documentation plus Python AST index",
                    patch_summary="Add code-symbol candidates; do not modify the target repo.",
                ),
            ],
            metrics=[
                "test_pass_rate",
                "mapping_precision",
                "mapping_review_rate",
                "duration_ms",
            ],
            interpretation_method="manual",
        )
    ]
    return ProjectIntelligenceMock(
        system_id=system_id,
        deterministic_decision_policy=(
            "Deterministic rules are allowed only when the output belongs to a "
            "small, explicitly enumerated finite set. All open-ended inference "
            "must use an external reasoning-model LLM API."
        ),
        repository=repository,
        system_profile_draft=SystemProfile(
            name="Target system draft",
            purpose="Drafted from committed README and docs with evidence.",
            target_users=["developers", "system improvement owners"],
            stakeholder_value="Safer, evidence-based probe and experiment planning.",
            constraints=[
                "Do not modify the target repository automatically",
                "Do not read untracked or uncommitted files",
                "Do not adopt a variant based on LLM evaluation alone",
            ],
            success_criteria=[
                "A reviewable Feature Map is produced",
                "Probe plans identify side-effect risk",
                "Experiments run in an isolated workspace",
            ],
        ),
        features=features,
        probe_plans=probe_plans,
        experiments=experiments,
    )
