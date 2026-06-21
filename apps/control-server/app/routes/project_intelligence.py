import json
import os
import time
import hashlib
from dataclasses import replace
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..code_indexer import index_snapshot_files
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
from ..git_ops import GitError, create_snapshot
from ..llm import LLMConfig, LLMError, create_llm_client, is_reasoning_model
from ..models import (
    CodeSymbolOut,
    DraftGenerationResult,
    ExperimentSummary,
    ExperimentVariant,
    FeatureCodeLink,
    FeatureCodeLinkOut,
    FeatureCodeLinksOut,
    FeatureDraftOut,
    FeatureEvidence,
    FeatureProfile,
    IntelligenceRunOut,
    LatestDraftsOut,
    LinkReviewUpdate,
    ProbePatchOut,
    ProbePlan,
    ProbePlanOut,
    ProbePlansListOut,
    ProbePoint,
    ProbePointOut,
    ProbePointStatusUpdate,
    ProjectIntelligenceMock,
    RepositoryConfigOut,
    RepositoryConfigUpdate,
    RepositorySnapshot,
    SnapshotFileOut,
    SnapshotOut,
    SymbolIndexOut,
    SymbolIndexWarningOut,
    SystemProfile,
    SystemProfileDraftOut,
    ValidationCommandOut,
    ValidationRunOut,
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
        conn.execute("BEGIN")
        try:
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
                        (snapshot_id, path, source_type, size_bytes, content_hash, content)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        f.path,
                        f.source_type,
                        f.size_bytes,
                        f.content_hash,
                        f.content,
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
            WHERE snapshot_id = ?
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
            WHERE system_id = ?
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


def _symbol_out(row) -> CodeSymbolOut:
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
            sym_rows = conn.execute(
                "SELECT * FROM code_symbols WHERE snapshot_id = ? ORDER BY path, start_line",
                (snapshot_id,),
            ).fetchall()
            warn_rows = conn.execute(
                "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            run_row = conn.execute(
                """
                SELECT * FROM intelligence_runs
                WHERE system_id = ? AND snapshot_id = ? AND run_type = 'symbol_index'
                ORDER BY id DESC LIMIT 1
                """,
                (system_id, snapshot_id),
            ).fetchone()
            return SymbolIndexOut(
                snapshot_id=snapshot_id,
                system_id=system_id,
                symbol_count=len(sym_rows),
                warning_count=len(warn_rows),
                symbols=[_symbol_out(r) for r in sym_rows],
                warnings=[
                    SymbolIndexWarningOut(path=w["path"], message=w["message"])
                    for w in warn_rows
                ],
                intelligence_run=_intelligence_run_out(run_row) if run_row else None,
            )

    with get_conn() as conn:
        file_rows = conn.execute(
            """
            SELECT path, content FROM snapshot_files
            WHERE snapshot_id = ?
            ORDER BY path
            """,
            (snapshot_id,),
        ).fetchall()

    files = [(fr["path"], bytes(fr["content"] or b"")) for fr in file_rows]
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
                        'n/a', 'n/a', 'deterministic',
                        'completed', 0, ?, ?)
                """,
                (system_id, snapshot_id, started_at, completed_at),
            )
            run_id = cur.lastrowid

            for sym in result.symbols:
                conn.execute(
                    """
                    INSERT INTO code_symbols
                        (snapshot_id, system_id, path, qualified_name, kind,
                         start_line, end_line, decorators, imports, docstring,
                         is_test, is_pydantic_model, route_path, route_method,
                         component_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
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
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return SymbolIndexOut(
        snapshot_id=snapshot_id,
        system_id=system_id,
        symbol_count=len(sym_rows),
        warning_count=len(warn_rows),
        symbols=[_symbol_out(r) for r in sym_rows],
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
        sym_rows = conn.execute(
            "SELECT * FROM code_symbols WHERE snapshot_id = ? ORDER BY path, start_line",
            (snapshot_id,),
        ).fetchall()
        warn_rows = conn.execute(
            "SELECT path, message FROM symbol_index_warnings WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        run_row = conn.execute(
            """
            SELECT * FROM intelligence_runs
            WHERE system_id = ? AND snapshot_id = ? AND run_type = 'symbol_index'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id, snapshot_id),
        ).fetchone()

    return SymbolIndexOut(
        snapshot_id=snapshot_id,
        system_id=system_id,
        symbol_count=len(sym_rows),
        warning_count=len(warn_rows),
        symbols=[_symbol_out(r) for r in sym_rows],
        warnings=[
            SymbolIndexWarningOut(path=w["path"], message=w["message"])
            for w in warn_rows
        ],
        intelligence_run=_intelligence_run_out(run_row) if run_row else None,
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
            WHERE system_id = ? AND run_type = 'repository_drafts' AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (system_id,),
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
                 AND ir.status = 'completed'
               ORDER BY fd.id DESC LIMIT 1""",
            (system_id, feature_id),
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
                 AND fcl.review_status = 'accepted'
               ORDER BY fcl.confidence DESC""",
            (system_id, feature_id),
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

            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id,
                        feature_id, objective, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                (
                    system_id, snapshot_id, run_id,
                    feature_id, plan_result.objective,
                    now, now,
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
        conn.execute(
            """UPDATE probe_points SET status = ?, updated_at = ? WHERE id = ?""",
            (payload.status, now, point_id),
        )
        row = conn.execute(
            "SELECT * FROM probe_points WHERE id = ?", (point_id,),
        ).fetchone()
        return _probe_point_out(row)


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
            "SELECT * FROM repository_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        config_row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?",
            (system_id,),
        ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise HTTPException(status_code=400, detail="Snapshot is not ready")
    if config_row is None:
        raise HTTPException(status_code=400, detail="Repository is not configured")

    with get_conn() as conn:
        point_rows = conn.execute(
            """SELECT * FROM probe_points
               WHERE plan_id = ? AND status = 'approved'
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
        repo_path=config_row["repo_path"],
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
                    worktree_path, skipped, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan_id, system_id, snapshot_id,
                snapshot_row["commit_sha"], patch_result.diff,
                patch_result.worktree_path,
                json.dumps(patch_result.skipped),
                patch_status, patch_result.error, now,
            ),
        )
        patch_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM probe_patches WHERE id = ?", (patch_id,),
        ).fetchone()

    return ProbePatchOut(
        id=row["id"],
        plan_id=row["plan_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        diff=row["diff"],
        skipped=json.loads(row["skipped"]),
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


@router.get("/repository/probe-patches", response_model=List[ProbePatchOut])
def list_probe_patches(
    system_id: int = Depends(get_system_id),
) -> List[ProbePatchOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM probe_patches WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        results = []
        for row in rows:
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
            results.append(ProbePatchOut(
                id=row["id"],
                plan_id=row["plan_id"],
                system_id=row["system_id"],
                snapshot_id=row["snapshot_id"],
                commit_sha=row["commit_sha"],
                diff=row["diff"],
                skipped=json.loads(row["skipped"]),
                status=row["status"],
                error=row["error"],
                validation_runs=validation_runs,
                created_at=row["created_at"],
            ))
    return results


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

        val_rows = conn.execute(
            "SELECT * FROM validation_runs WHERE patch_id = ? ORDER BY id",
            (patch_id,),
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
        skipped=json.loads(row["skipped"]),
        status=row["status"],
        error=row["error"],
        validation_runs=validation_runs,
        created_at=row["created_at"],
    )


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
    from ..patch_generator import ApprovedPoint, create_worktree, cleanup_worktree
    from ..validation_runner import load_validation_config, run_validation

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
        config_row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?",
            (system_id,),
        ).fetchone()
    if config_row is None:
        raise HTTPException(status_code=400, detail="Repository is not configured")

    repo_path = config_row["repo_path"]
    commit_sha = patch_row["commit_sha"]

    config_path = os.path.join(repo_path, "probe-agent.yml")
    if not os.path.isfile(config_path):
        config_path_alt = os.path.join(repo_path, "probe-agent.example.yml")
        if os.path.isfile(config_path_alt):
            config_path = config_path_alt
        else:
            raise HTTPException(
                status_code=400,
                detail="probe-agent.yml not found in repository",
            )

    try:
        val_config = load_validation_config(config_path)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid probe-agent.yml: {exc}",
        )

    worktree_base = os.getenv("PROBE_WORKTREE_BASE", "/tmp/probe-worktrees")
    os.makedirs(worktree_base, exist_ok=True)

    baseline_worktree = None
    probed_worktree = patch_row["worktree_path"]
    now = time.time()

    try:
        baseline_worktree = create_worktree(
            repo_path, commit_sha, worktree_base + "/baseline",
        )
    except GitError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create baseline worktree: {exc}",
        )

    try:
        baseline_result = run_validation("baseline", baseline_worktree, val_config)

        if probed_worktree and os.path.isdir(probed_worktree):
            probed_result = run_validation("probed", probed_worktree, val_config)
        else:
            probed_result = None
    finally:
        if baseline_worktree:
            cleanup_worktree(repo_path, baseline_worktree)

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            for result in [baseline_result] + ([probed_result] if probed_result else []):
                cur = conn.execute(
                    """INSERT INTO validation_runs
                           (patch_id, system_id, variant, worktree_path,
                            overall_success, total_duration_ms, error, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        patch_id, system_id, result.variant,
                        result.worktree_path,
                        1 if result.overall_success else 0,
                        result.total_duration_ms,
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


# ---------------------------------------------------------------------------
# Legacy mock endpoint — kept for Experiments tab
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
