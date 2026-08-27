"""Shared repository snapshot and symbol-index services (Issue #277).

Both the individual API endpoints and persisted resync jobs call these
route-independent functions. Git access is committed-object read-only; derived
artifacts are persisted only in probe-agent's database.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from .code_indexer import index_snapshot_files
from .db import get_conn
from .git_ops import GitError, create_snapshot
from .models import (
    CodeSymbolOut,
    IntelligenceRunOut,
    SnapshotFileOut,
    SnapshotOut,
    SourceMetadataOut,
    SymbolIndexOut,
    SymbolIndexWarningOut,
)


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


class RepositoryRefreshServiceError(Exception):
    """Expected precondition failure from reusable refresh services."""


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


def create_snapshot_service(system_id: int) -> SnapshotOut:
    """Create and persist one committed-files-only repository snapshot.

    This route-independent service is shared by the individual snapshot API
    and the explicit resync job. It never fetches, checks out, or writes to the
    configured target repository.
    """
    with get_conn() as conn:
        config_row = conn.execute(
            "SELECT * FROM repository_configs WHERE system_id = ?", (system_id,)
        ).fetchone()
    if config_row is None:
        raise RepositoryRefreshServiceError(
            "Repository is not configured. PUT /repository first."
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
                WHERE id = ? AND system_id = ?
                """,
                (str(exc), time.time(), snapshot_id, system_id),
            )
            row = conn.execute(
                "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
                (snapshot_id, system_id),
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
                WHERE id = ? AND system_id = ?
                """,
                (
                    commit_sha, len(files), total_size,
                    indexed_size, metadata_only_count,
                    json.dumps(warnings), completed_at, snapshot_id, system_id,
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
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        return _snapshot_out(conn, row, include_files=True)


# Deterministic symbol-index persistence and upgrade helpers.
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


def index_symbols_service(
    system_id: int,
    snapshot_id: Optional[int] = None,
) -> SymbolIndexOut:
    """Persist a deterministic symbol index for one ready snapshot.

    ``snapshot_id`` pins resync jobs to the snapshot they created. Individual
    index requests omit it and retain the existing latest-snapshot behavior.
    """
    with get_conn() as conn:
        if snapshot_id is None:
            snapshot_row = conn.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE system_id = ? ORDER BY id DESC LIMIT 1
                """,
                (system_id,),
            ).fetchone()
        else:
            snapshot_row = conn.execute(
                "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
                (snapshot_id, system_id),
            ).fetchone()
    if snapshot_row is None or snapshot_row["status"] != "ready":
        raise RepositoryRefreshServiceError(
            "Requested snapshot is not ready. Create a successful snapshot first."
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
