"""AI Candidate Studio API (Issue #252).

A conversation + versioning layer over the EXISTING isolated-Replay stack.
Nothing here adds a new judgement/execution/comparison path:

  * candidate-proposal generation is the reasoning-model structured proposal +
    deterministic splice->diff in ``app/candidate_studio.py`` (fail-closed);
  * replaying a version reuses ``POST /replay-variant-runs`` VERBATIM (its
    human replay-approval gate, network-off worktree sandbox, always-cleanup,
    and finite diff matrix) by calling the route function directly;
  * promotion reuses the variant experiment-payload shape and never creates an
    experiment, merges, deploys, or enables live shadow (Principle 7).

Every intelligence run is audited in ``intelligence_runs``
(run_type='candidate_studio_proposal'). Only a SUCCESSFUL patch generation
creates an immutable ``candidate_versions`` row (Issue #252: "実際に patch が
生成された場合のみ新規バージョンを作る"); a failed attempt is still audited in
``intelligence_runs`` and surfaced as an assistant message, then fails closed
with a 502.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..candidate_studio import (
    MAX_BASELINE_SOURCE_CHARS,
    PROMPT_VERSION,
    PROPOSAL_PROPOSED,
    SCHEMA_VERSION,
    generate_candidate_proposal,
)
from ..db import get_conn
from ..git_ops import GitError, read_file_at_commit
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    CandidateEventOut,
    CandidateEventsOut,
    CandidateGenerateCreate,
    CandidateMessageCreate,
    CandidatePromotionOut,
    CandidateReplayCreate,
    CandidateSessionCreate,
    CandidateSessionOut,
    CandidateVersionOut,
    ReplaySetCreate,
    ReplayVariantCreate,
    ReplayVariantRunCreate,
)
from ..replay_runner import replay_workspace_base
from ..workspace_context import _redact_and_truncate
from .replay import (
    MAX_REPLAY_SET_SIZE,
    _active_approval,
    _resolve_component_symbol,
    _resolve_snapshot,
    create_replay_set,
    create_replay_variant_run,
    get_replay_variant_experiment_payload,
)
from .replay_readiness import gather_readiness

router = APIRouter()

MAX_EXAMPLE_TRACES = 5
MAX_CONVERSATION_MESSAGES = 20
MAX_CONTEXT_FIELD_CHARS = 2_000


# --- serialization helpers ----------------------------------------------------


def _json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _version_out(conn, row) -> CandidateVersionOut:
    run_row = conn.execute(
        "SELECT provider, model, prompt_version, schema_version, "
        "decision_method, is_mock FROM intelligence_runs WHERE id = ?",
        (row["intelligence_run_id"],),
    ).fetchone()
    return CandidateVersionOut(
        id=row["id"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        parent_version_id=row["parent_version_id"],
        version_number=row["version_number"],
        instruction=row["instruction"] or "",
        status=row["status"],
        summary=row["summary"] or "",
        assumptions=_json_list(row["assumptions_json"]),
        changed_symbols=_json_list(row["changed_symbols_json"]),
        risks=_json_list(row["risks_json"]),
        suggested_tests=_json_list(row["suggested_tests_json"]),
        generated_code=row["generated_code"] or "",
        patch_text=row["patch_text"] or "",
        patch_hash=row["patch_hash"] or "",
        error=row["error"],
        replay_status=row["replay_status"],
        replay_run_id=row["replay_run_id"],
        replay_variant_id=row["replay_variant_id"],
        promoted_at=row["promoted_at"],
        provider=run_row["provider"] if run_row is not None else None,
        model=run_row["model"] if run_row is not None else None,
        prompt_version=run_row["prompt_version"] if run_row is not None else None,
        schema_version=run_row["schema_version"] if run_row is not None else None,
        decision_method=run_row["decision_method"] if run_row is not None else None,
        is_mock=bool(run_row["is_mock"]) if run_row is not None else False,
        created_at=row["created_at"],
    )


def _messages(conn, session_id: int):
    return conn.execute(
        "SELECT * FROM candidate_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


def _versions(conn, session_id: int):
    return conn.execute(
        "SELECT * FROM candidate_versions WHERE session_id = ? "
        "ORDER BY version_number",
        (session_id,),
    ).fetchall()


def _session_out(conn, row, *, include_children: bool = True) -> CandidateSessionOut:
    messages = []
    versions = []
    if include_children:
        messages = [
            {
                "id": m["id"],
                "session_id": m["session_id"],
                "role": m["role"],
                "content": m["content"] or "",
                "version_id": m["version_id"],
                "created_at": m["created_at"],
            }
            for m in _messages(conn, row["id"])
        ]
        versions = [_version_out(conn, v) for v in _versions(conn, row["id"])]
    return CandidateSessionOut(
        id=row["id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        symbol_path=row["symbol_path"],
        symbol_qualified_name=row["symbol_qualified_name"],
        replay_set_id=row["replay_set_id"],
        objective=row["objective"] or "",
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        messages=messages,
        versions=versions,
    )


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM candidate_sessions WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate session not found")
    return row


def _get_version_or_404(conn, version_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM candidate_versions WHERE id = ? AND system_id = ?",
        (version_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate version not found")
    return row


def _add_message(conn, system_id: int, session_id: int, role: str, content: str,
                 version_id: Optional[int], now: float) -> None:
    conn.execute(
        """
        INSERT INTO candidate_messages
            (system_id, session_id, role, content, version_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (system_id, session_id, role, content, version_id, now),
    )


def _context_echo(session_row) -> str:
    """DETERMINISTIC (Principle 6) assistant echo of the prepared context —
    display text, never an inference. The LLM's actual understanding of the
    request is surfaced later inside a CandidateVersion's proposal
    (summary / assumptions)."""
    return (
        "準備できました。次の条件で候補を生成します:\n"
        f"- component: {session_row['component_id']}\n"
        f"- baseline commit: {session_row['commit_sha'][:12]}\n"
        f"- 対象シンボル: {session_row['symbol_path']}:"
        f"{session_row['symbol_qualified_name']}\n"
        f"- Replay Set: #{session_row['replay_set_id']}\n"
        "改善の目的と制約を入力し、「候補を生成」してください。"
    )


# --- sessions -----------------------------------------------------------------


@router.post("/candidate-sessions", response_model=CandidateSessionOut, status_code=201)
def create_candidate_session(
    payload: CandidateSessionCreate,
    system_id: int = Depends(get_system_id),
) -> CandidateSessionOut:
    selections = [
        payload.replay_set_id is not None,
        payload.trace_ids is not None,
        payload.trace_id is not None,
    ]
    if sum(1 for s in selections if s) > 1:
        raise HTTPException(
            status_code=422,
            detail="Provide at most one of replay_set_id, trace_ids, or trace_id",
        )

    now = time.time()
    # Phase 1: resolve snapshot + symbol and validate any given Replay Set,
    # then release the connection. create_replay_set (Phase 2) opens its own
    # get_conn() and the shared connection lock is non-reentrant, so it must
    # not be called while we hold a connection. Resolving first also avoids
    # creating an orphan Replay Set when symbol resolution fails.
    with get_conn() as conn:
        snapshot = _resolve_snapshot(conn, system_id, payload.snapshot_id)
        # Deterministic component->symbol resolution (409 if unresolved) so the
        # session is only ever anchored to a concrete, indexed function symbol.
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], payload.component_id
        )
        snapshot_id = snapshot["id"]
        commit_sha = snapshot["commit_sha"]
        symbol_path = symbol["path"]
        symbol_qualified_name = symbol["qualified_name"]
        replay_set_id: Optional[int] = None
        trace_ids: Optional[List[str]] = None
        if payload.replay_set_id is not None:
            replay_set = conn.execute(
                "SELECT * FROM replay_sets WHERE id = ? AND system_id = ?",
                (payload.replay_set_id, system_id),
            ).fetchone()
            if replay_set is None:
                raise HTTPException(status_code=404, detail="Replay set not found")
            if replay_set["component_id"] != payload.component_id:
                raise HTTPException(
                    status_code=422,
                    detail="Replay set belongs to a different component",
                )
            replay_set_id = replay_set["id"]
        elif payload.trace_ids is not None:
            trace_ids = [str(trace_id) for trace_id in payload.trace_ids]
        elif payload.trace_id is not None:
            trace_ids = [str(payload.trace_id)]
        else:
            # The component-detail entry point deliberately supplies only a
            # component.  Build a deterministic evaluation set from its most
            # recent captured inputs so that entry point is actually usable.
            recent = conn.execute(
                """
                SELECT trace_id FROM traces
                WHERE system_id = ? AND component_id = ?
                ORDER BY timestamp DESC, trace_id
                LIMIT ?
                """,
                (system_id, payload.component_id, MAX_REPLAY_SET_SIZE),
            ).fetchall()
            trace_ids = [row["trace_id"] for row in recent]
            if not trace_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Component '{payload.component_id}' has no captured traces; "
                        "provide a Replay Set or capture a trace first"
                    ),
                )

    # Issue #372: refuse a session whose evaluation set cannot be replayed at
    # all, BEFORE any reasoning-model call. The audited component had 11
    # traces and zero usable captures, so the failure only surfaced after the
    # cost had been paid. Evaluated outside the connection above -- it opens
    # its own, and the lock is non-reentrant.
    readiness = gather_readiness(
        system_id, payload.component_id, trace_ids if trace_ids else None
    )
    if readiness.selected.usable == 0:
        blocking = [c for c in readiness.checks if c.status == "blocking"]
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_replayable_traces",
                "message": (
                    blocking[0].detail if blocking
                    else "Replayに使えるTraceがありません"
                ),
                "remediation": blocking[0].remediation if blocking else "",
                "counts": {
                    "total": readiness.counts.total,
                    "replayable": readiness.counts.replayable,
                    "partial": readiness.counts.partial,
                    "unreplayable": readiness.counts.unreplayable,
                    "not_captured": readiness.counts.not_captured,
                },
            },
        )

    # Phase 2: create the Replay Set from trace ids (its own connection),
    # reusing POST /replay-sets validation (existence, dedupe, size cap).
    if replay_set_id is None:
        assert trace_ids is not None
        replay_set_out = create_replay_set(
            ReplaySetCreate(
                component_id=payload.component_id,
                name=f"Candidate Studio ({payload.component_id})",
                trace_ids=trace_ids,
            ),
            system_id=system_id,
        )
        replay_set_id = replay_set_out.id

    # Phase 3: persist the session and its opening conversation turns.
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO candidate_sessions
                (system_id, component_id, snapshot_id, commit_sha, symbol_path,
                 symbol_qualified_name, replay_set_id, objective, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                system_id,
                payload.component_id,
                snapshot_id,
                commit_sha,
                symbol_path,
                symbol_qualified_name,
                replay_set_id,
                payload.objective,
                now,
                now,
            ),
        )
        session_id = cur.lastrowid
        session_row = _get_session_or_404(conn, session_id, system_id)
        if payload.objective.strip():
            _add_message(conn, system_id, session_id, "user", payload.objective, None, now)
        _add_message(
            conn, system_id, session_id, "assistant",
            _context_echo(session_row), None, now,
        )
        return _session_out(conn, session_row)


@router.get("/candidate-sessions", response_model=List[CandidateSessionOut])
def list_candidate_sessions(
    component_id: Optional[str] = None,
    limit: int = 50,
    system_id: int = Depends(get_system_id),
) -> List[CandidateSessionOut]:
    limit = max(1, min(limit, 200))
    where = ["system_id = ?"]
    params: List[Any] = [system_id]
    if component_id:
        where.append("component_id = ?")
        params.append(component_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM candidate_sessions WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [_session_out(conn, row, include_children=False) for row in rows]


@router.get("/candidate-sessions/{session_id}", response_model=CandidateSessionOut)
def get_candidate_session(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> CandidateSessionOut:
    with get_conn() as conn:
        row = _get_session_or_404(conn, session_id, system_id)
        return _session_out(conn, row)


@router.post(
    "/candidate-sessions/{session_id}/messages",
    response_model=CandidateSessionOut,
)
def post_candidate_message(
    session_id: int,
    payload: CandidateMessageCreate,
    system_id: int = Depends(get_system_id),
) -> CandidateSessionOut:
    now = time.time()
    with get_conn() as conn:
        row = _get_session_or_404(conn, session_id, system_id)
        _add_message(conn, system_id, session_id, "user", payload.content, None, now)
        # Deterministically confirm the concrete condition and pinned change
        # boundary.  A message never creates a version; actual reasoning and
        # proposal generation still happen only after the explicit Generate
        # action.
        _add_message(
            conn, system_id, session_id, "assistant",
            (
                "理解した条件:\n"
                f"- {payload.content.strip()}\n"
                "変更方針:\n"
                f"- baseline {row['commit_sha'][:12]} の "
                f"{row['symbol_path']}:{row['symbol_qualified_name']} のみを変更\n"
                "- 既存 Replay Set で比較可能な候補として生成\n"
                "内容を確認し、「候補を生成」で patch を作成してください。"
            ),
            None,
            now,
        )
        conn.execute(
            "UPDATE candidate_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        row = _get_session_or_404(conn, session_id, system_id)
        return _session_out(conn, row)


# --- generation ---------------------------------------------------------------


def _build_generation_context(
    conn, system_id: int, session_row, symbol, baseline_source: str
) -> Dict[str, Any]:
    component_id = session_row["component_id"]
    system_profile = conn.execute(
        "SELECT * FROM system_profile WHERE system_id = ?", (system_id,)
    ).fetchone()
    component_profile = conn.execute(
        "SELECT * FROM component_profiles WHERE system_id = ? AND component_id = ?",
        (system_id, component_id),
    ).fetchone()
    criteria = conn.execute(
        """
        SELECT name, description, criterion_type, expected_value, weight
        FROM evaluation_criteria
        WHERE system_id = ? AND component_id = ? AND enabled = 1
        ORDER BY id
        """,
        (system_id, component_id),
    ).fetchall()
    trace_ids = _json_list(
        conn.execute(
            "SELECT trace_ids_json FROM replay_sets WHERE id = ?",
            (session_row["replay_set_id"],),
        ).fetchone()["trace_ids_json"]
    )
    example_traces: List[Dict[str, Any]] = []
    for trace_id in trace_ids[:MAX_EXAMPLE_TRACES]:
        trace = conn.execute(
            """
            SELECT input_json, output_text, error FROM traces
            WHERE system_id = ? AND component_id = ? AND trace_id = ?
            """,
            (system_id, component_id, trace_id),
        ).fetchone()
        if trace is None:
            continue
        example_traces.append(
            {
                "trace_id": trace_id,
                "input": _redact_and_truncate(
                    trace["input_json"], MAX_CONTEXT_FIELD_CHARS
                ),
                "output": _redact_and_truncate(
                    trace["output_text"], MAX_CONTEXT_FIELD_CHARS
                ),
                "error": _redact_and_truncate(
                    trace["error"], MAX_CONTEXT_FIELD_CHARS
                ),
            }
        )
    conversation_rows = conn.execute(
        """
        SELECT content FROM candidate_messages
        WHERE session_id = ? AND system_id = ? AND role = 'user'
        ORDER BY id DESC LIMIT ?
        """,
        (session_row["id"], system_id, MAX_CONVERSATION_MESSAGES),
    ).fetchall()
    conversation_history = [
        _redact_and_truncate(row["content"], MAX_CONTEXT_FIELD_CHARS)
        for row in reversed(conversation_rows)
    ]
    return {
        "objective": _redact_and_truncate(
            session_row["objective"] or "", MAX_CONTEXT_FIELD_CHARS
        ),
        "component_id": component_id,
        "target_symbol": {
            "path": symbol["path"],
            "qualified_name": symbol["qualified_name"],
        },
        "baseline_source": _redact_and_truncate(
            baseline_source, MAX_BASELINE_SOURCE_CHARS
        ),
        "system_profile": dict(system_profile) if system_profile else {},
        "component_profile": dict(component_profile) if component_profile else {},
        "criteria": [dict(c) for c in criteria],
        "example_traces": example_traces,
        "conversation_history": conversation_history,
    }


def _symbol_span_source(repo_path: str, commit_sha: str, symbol) -> str:
    """The pinned-snapshot source of just the resolved symbol span (shown to
    the model as the baseline when there is no parent version to improve on)."""
    raw = read_file_at_commit(repo_path, commit_sha, symbol["path"])
    lines = raw.decode("utf-8").splitlines(keepends=True)
    start = max(0, symbol["start_line"] - 1)
    end = min(len(lines), symbol["end_line"])
    return "".join(lines[start:end])


@router.post(
    "/candidate-sessions/{session_id}/generate",
    response_model=CandidateVersionOut,
    status_code=201,
)
def generate_candidate_version(
    session_id: int,
    payload: CandidateGenerateCreate,
    system_id: int = Depends(get_system_id),
) -> CandidateVersionOut:
    now = time.time()
    with get_conn() as conn:
        session_row = _get_session_or_404(conn, session_id, system_id)
        component_id = session_row["component_id"]
        snapshot = _resolve_snapshot(conn, system_id, session_row["snapshot_id"])
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], component_id
        )

        parent_id = payload.parent_version_id
        baseline_source: str
        if parent_id is not None:
            parent = conn.execute(
                "SELECT * FROM candidate_versions WHERE id = ? AND session_id = ? "
                "AND system_id = ?",
                (parent_id, session_id, system_id),
            ).fetchone()
            if parent is None:
                raise HTTPException(
                    status_code=404, detail="parent_version_id not found in session"
                )
            if parent["status"] != PROPOSAL_PROPOSED:
                raise HTTPException(
                    status_code=422,
                    detail="parent_version_id must reference a proposed version",
                )
            # Improve on the parent's candidate code; the resulting patch is
            # still diffed against the pinned snapshot so every version applies
            # independently.
            baseline_source = parent["generated_code"] or _symbol_span_source(
                snapshot["repo_path"], snapshot["commit_sha"], symbol
            )
        else:
            try:
                baseline_source = _symbol_span_source(
                    snapshot["repo_path"], snapshot["commit_sha"], symbol
                )
            except (GitError, UnicodeDecodeError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"Could not read baseline source: {exc}",
                )

        if len(baseline_source) > 60_000:
            baseline_source = baseline_source[:60_000]

        context = _build_generation_context(
            conn, system_id, session_row, symbol, baseline_source
        )
        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]
        snapshot_id = snapshot["id"]
        start_line = symbol["start_line"]
        end_line = symbol["end_line"]
        symbol_path = symbol["path"]
        symbol_qualified_name = symbol["qualified_name"]
    # Reasoning attempt (outside the DB lock). Fail closed on any config/call/
    # parse/scope/size failure -- no heuristic fallback (Principle 6).
    config = LLMConfig.from_env()
    from ..candidate_studio import ProposalResult, PROPOSAL_FAILED

    try:
        if config.provider not in {"mock", "openai", "anthropic", "gemini"}:
            raise LLMError(f"Unsupported LLM provider: {config.provider}")
        client = create_llm_client(config)
    except LLMError as exc:
        proposal = ProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            status=PROPOSAL_FAILED,
            error=str(exc),
        )
    else:
        proposal = generate_candidate_proposal(
            client,
            config,
            repo_path=repo_path,
            commit_sha=commit_sha,
            symbol_path=symbol_path,
            symbol_qualified_name=symbol_qualified_name,
            start_line=start_line,
            end_line=end_line,
            context=context,
            instruction=payload.instruction,
            worktree_base=os.path.join(replay_workspace_base(), "candidate-studio"),
        )

    completed_at = time.time()
    run_status = "completed" if proposal.status == PROPOSAL_PROPOSED else "failed"

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'candidate_studio_proposal', ?, ?, ?, ?,
                    'reasoning_llm', ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                snapshot_id,
                proposal.provider,
                proposal.model,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                run_status,
                proposal.error,
                1 if proposal.is_mock else 0,
                now,
                completed_at,
            ),
        )
        intelligence_run_id = cur.lastrowid
        # Record the developer's instruction in the conversation regardless of
        # outcome, so a failed attempt still shows what was asked.
        _add_message(
            conn, system_id, session_id, "user", payload.instruction, None, now,
        )

        # Only a SUCCESSFUL patch generation creates an immutable version
        # (Issue #252). A failed attempt is audited above and surfaced as an
        # assistant message, then fails closed.
        if proposal.status != PROPOSAL_PROPOSED:
            _add_message(
                conn, system_id, session_id, "assistant",
                f"候補の生成に失敗しました: {proposal.error}", None, completed_at,
            )
            conn.execute(
                "UPDATE candidate_sessions SET updated_at = ? WHERE id = ?",
                (completed_at, session_id),
            )
            raise HTTPException(status_code=502, detail=proposal.error or "generation failed")

        # Allocate the display number only after the slow LLM operation, while
        # holding the same write transaction that inserts the version.  This
        # prevents concurrent generations from reusing a stale MAX value.
        next_number = (
            conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM candidate_versions "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            + 1
        )
        cur = conn.execute(
            """
            INSERT INTO candidate_versions
                (system_id, session_id, parent_version_id, version_number,
                 intelligence_run_id, instruction, status, summary,
                 assumptions_json, changed_symbols_json, risks_json,
                 suggested_tests_json, generated_code, patch_text, patch_hash,
                 error, replay_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                    'not_run', ?)
            """,
            (
                system_id,
                session_id,
                parent_id,
                next_number,
                intelligence_run_id,
                payload.instruction,
                proposal.summary,
                json.dumps(proposal.assumptions, ensure_ascii=False),
                json.dumps(proposal.changed_symbols, ensure_ascii=False),
                json.dumps(proposal.risks, ensure_ascii=False),
                json.dumps(proposal.suggested_tests, ensure_ascii=False),
                proposal.generated_code,
                proposal.patch_text,
                proposal.patch_hash,
                completed_at,
            ),
        )
        version_id = cur.lastrowid
        summary_note = proposal.summary or "候補を生成しました。"
        mock_note = "（mock 出力）" if proposal.is_mock else ""
        _add_message(
            conn, system_id, session_id, "assistant",
            f"候補 v{next_number} を生成しました{mock_note}: {summary_note}",
            version_id, completed_at,
        )
        conn.execute(
            "UPDATE candidate_sessions SET updated_at = ? WHERE id = ?",
            (completed_at, session_id),
        )
        version_row = _get_version_or_404(conn, version_id, system_id)
        return _version_out(conn, version_row)


@router.get(
    "/candidate-sessions/{session_id}/versions",
    response_model=List[CandidateVersionOut],
)
def list_candidate_versions(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[CandidateVersionOut]:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        return [_version_out(conn, v) for v in _versions(conn, session_id)]


@router.get("/candidate-versions/{version_id}", response_model=CandidateVersionOut)
def get_candidate_version(
    version_id: int,
    system_id: int = Depends(get_system_id),
) -> CandidateVersionOut:
    with get_conn() as conn:
        return _version_out(conn, _get_version_or_404(conn, version_id, system_id))


# --- events (polling) ---------------------------------------------------------


@router.get(
    "/candidate-sessions/{session_id}/events", response_model=CandidateEventsOut
)
def get_candidate_events(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> CandidateEventsOut:
    """Deterministic job/status timeline derived from persisted version state.
    Generate runs synchronously (like the rest of the Replay stack), so a
    persisted version is already at its terminal generate phase; replay
    progress is reflected via ``replay_status``."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        versions = _versions(conn, session_id)
    events: List[CandidateEventOut] = []
    for v in versions:
        replay_status = v["replay_status"]
        if replay_status == "running":
            phase = "replaying"
        elif v["status"] == "failed" or replay_status == "failed":
            phase = "failed"
        else:
            phase = "completed"
        events.append(
            CandidateEventOut(
                version_id=v["id"],
                version_number=v["version_number"],
                phase=phase,
                status=v["status"],
                replay_status=replay_status,
                detail=v["summary"] or "",
                created_at=v["created_at"],
            )
        )
    return CandidateEventsOut(session_id=session_id, events=events)


# --- replay (reuses POST /replay-variant-runs verbatim) -----------------------


@router.post("/candidate-versions/{version_id}/replay", response_model=CandidateVersionOut)
def replay_candidate_version(
    version_id: int,
    payload: CandidateReplayCreate,
    system_id: int = Depends(get_system_id),
) -> CandidateVersionOut:
    with get_conn() as conn:
        version = _get_version_or_404(conn, version_id, system_id)
        if version["status"] != PROPOSAL_PROPOSED or not version["patch_text"]:
            raise HTTPException(
                status_code=422,
                detail="Only a proposed version with a patch can be replayed",
            )
        session_row = _get_session_or_404(conn, version["session_id"], system_id)
        replay_set_id = session_row["replay_set_id"]
        component_id = session_row["component_id"]
        if (
            payload.snapshot_id is not None
            and payload.snapshot_id != session_row["snapshot_id"]
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Candidate replay must use the session's pinned baseline snapshot "
                    f"({session_row['snapshot_id']})"
                ),
            )
        snapshot_id = session_row["snapshot_id"]
        version_number = version["version_number"]
        patch_text = version["patch_text"]
        # Surface the human replay-approval gate here too (fail closed before
        # touching a worktree); create_replay_variant_run enforces it again.
        if _active_approval(conn, system_id, component_id) is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Replay is not approved for component '{component_id}'. A "
                    "human must approve it via POST /components/"
                    f"{component_id}/replay-approval first."
                ),
            )
        conn.execute(
            "UPDATE candidate_versions SET replay_status = 'running' WHERE id = ?",
            (version_id,),
        )

    run_payload = ReplayVariantRunCreate(
        replay_set_id=replay_set_id,
        snapshot_id=snapshot_id,
        variants=[
            ReplayVariantCreate(
                label=f"Candidate v{version_number}",
                patch_text=patch_text,
                source="llm_draft",
            )
        ],
    )
    try:
        run = create_replay_variant_run(run_payload, system_id=system_id)
    except HTTPException:
        with get_conn() as conn:
            conn.execute(
                "UPDATE candidate_versions SET replay_status = 'not_run' WHERE id = ?",
                (version_id,),
            )
        raise
    except Exception:
        # Unexpected runner failures must not strand the immutable candidate in
        # a permanent 'running' state.
        with get_conn() as conn:
            conn.execute(
                "UPDATE candidate_versions SET replay_status = 'failed' WHERE id = ?",
                (version_id,),
            )
        raise

    candidate_variant = next(
        (v for v in run.variants if not v.is_baseline), None
    )
    replay_status = (
        "completed"
        if (
            run.status == "completed"
            and candidate_variant is not None
            and candidate_variant.status == "completed"
            and candidate_variant.apply_status == "applied"
        )
        else "failed"
    )
    completed_at = time.time()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE candidate_versions
            SET replay_status = ?, replay_run_id = ?, replay_variant_id = ?
            WHERE id = ?
            """,
            (
                replay_status,
                run.id,
                candidate_variant.id if candidate_variant else None,
                version_id,
            ),
        )
        conn.execute(
            "UPDATE candidate_sessions SET updated_at = ? WHERE id = ?",
            (completed_at, version["session_id"]),
        )
        return _version_out(conn, _get_version_or_404(conn, version_id, system_id))


# --- promotion (reuses the variant experiment-payload; never auto-adopts) -----


@router.post("/candidate-versions/{version_id}/promote", response_model=CandidatePromotionOut)
def promote_candidate_version(
    version_id: int,
    system_id: int = Depends(get_system_id),
) -> CandidatePromotionOut:
    now = time.time()
    with get_conn() as conn:
        version = _get_version_or_404(conn, version_id, system_id)
        if version["replay_status"] != "completed" or not version["replay_run_id"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Only a candidate version whose replay completed can be "
                    "promoted to an Experiment"
                ),
            )
        if not version["replay_variant_id"]:
            raise HTTPException(
                status_code=422, detail="Candidate version has no evaluated variant"
            )
        session_row = _get_session_or_404(conn, version["session_id"], system_id)
        replay_run_id = version["replay_run_id"]
        replay_variant_id = version["replay_variant_id"]

    # Reuse Issue #245's variant experiment-payload builder (validates the
    # variant is applied + completed). This never creates an experiment.
    payload = get_replay_variant_experiment_payload(
        replay_run_id, replay_variant_id, system_id=system_id
    )

    with get_conn() as conn:
        conn.execute(
            "UPDATE candidate_versions SET promoted_at = ? WHERE id = ?",
            (now, version_id),
        )

    return CandidatePromotionOut(
        candidate_version_id=version_id,
        label=payload.label,
        patch_text=payload.patch_text,
        patch_hash=payload.patch_hash,
        source="candidate_studio",
        risk_note=(
            f"Promoted from Candidate Studio session {session_row['id']}, "
            f"version {version['version_number']} (component "
            f"'{session_row['component_id']}', snapshot {session_row['snapshot_id']})."
        ),
        origin={
            "candidate_session_id": session_row["id"],
            "candidate_version_id": version_id,
            "replay_variant_run_id": replay_run_id,
            "replay_variant_id": replay_variant_id,
            "component_id": session_row["component_id"],
            "snapshot_id": session_row["snapshot_id"],
            "commit_sha": session_row["commit_sha"],
        },
    )
