"""Probe Cell Fabric: 改善仮説・カナリア・shadow実行承認ゲート
(Issue #304, Sub 7 of the Probe Cell Fabric epic, Issue #297).

This module owns the persistence and finite-state logic for:

- ``cell_improvements``: an improvement hypothesis lifecycle
  ``observed -> proposed -> canary_ready -> canary_running ->
  adopted | rejected | blocked`` (``IMPROVEMENT_TRANSITIONS``, a finite table
  in the style of ``app.cell_fabric.TASK_TRANSITIONS``). ``adopted`` and
  ``rejected`` are terminal. Every transition writes exactly one
  ``cell_improvement_events`` row; rejected hypotheses are NEVER deleted --
  there is no DELETE endpoint anywhere in this module, and the append-only
  event history is also the deterministic input to the consecutive-rejection
  auto-suspend circuit breaker below.
- Deterministic gates enforced AT TRANSITION TIME (Principle 6):
  ``observed -> proposed`` requires non-empty hypothesis / expected_effect /
  risk / rollback_plan already stored on the row (from manual creation or a
  successful reasoning draft) -- 422 otherwise. ``proposed -> canary_ready``
  requires at least one canary evidence ref that both matches the restricted
  ``replay_run:<id>`` / ``experiment:<id>`` / ``evaluation:<id>`` ref grammar
  AND resolves via ``app.cell_tasks.resolve_evidence_ref`` -- this reuses the
  EXISTING Replay/offline-shadow/Experiment/Evaluation-Criteria
  infrastructure and adds no new execution path. ``canary_running ->
  adopted`` requires BOTH a recorded parent approval and a recorded human
  approval (separate ``parent_approve`` / ``human_approve`` calls, 409
  otherwise); for ``target_kind == 'role_card'`` it additionally requires
  ``proposed_role_card_version`` to resolve to an existing
  ``agent_role_cards`` row for the SAME ``role_key`` as the improvement's
  originally-pinned card, and pins ``cell_definitions.role_card_id`` to that
  row -- the compatible-vs-major-bump distinction is recorded in the event
  detail (never used to reject: an explicitly proposed major bump is by
  definition intentional). For ``target_kind == 'candidate_patch'``,
  adoption performs NO write outside ``cell_improvements``/events -- it is a
  handoff marker; the real adoption/publish flow goes through the EXISTING
  #25/#216/#242/#252 human gates, never a shortcut here.
- Rubric ownership (parent-owned): whenever a transition call sets/changes
  ``proposed_role_card_version`` for a ``role_card`` improvement, and the
  candidate card's ``rubric_ref`` differs from the pinned card's, the
  improvement's ``parent_cell_id`` must already be set -- otherwise 422
  ("Cellが自己採点基準を変更できない"). A Cell can never grade its own
  rubric change; only a parent-owned improvement may propose one.
- Suspension / auto-suspend circuit breaker: ``suspend_improvements`` /
  ``resume_improvements`` flip the per-row ``suspended`` flag on the Cell's
  currently non-terminal improvements (a suspended improvement refuses every
  transition/approval with 409 until resumed).
  ``record_failure_and_maybe_suspend`` recomputes, from the immutable event
  history, the number of TRAILING consecutive ``rejected`` transitions since
  the last ``adopted`` one (or from the start) across ALL of a Cell's
  improvements; reaching 3 auto-suspends every currently non-terminal
  improvement of that Cell. New-improvement creation
  (``create_improvement_manual`` / ``draft_improvement``) recomputes the
  same count and refuses (409) while it is >= 3 -- this is a live,
  history-derived gate, not a separate persisted flag, so it clears the
  moment an ``adopted`` transition is recorded for that Cell.
- ``rollback_role_card``: only for an ``adopted`` ``role_card`` improvement;
  reads the previous card id recorded in that adoption's own
  ``cell_improvement_events`` row and re-pins ``cell_definitions.role_card_id``
  back to it (no content mutation of any card version), writing a
  ``rolled_back`` event.
- ``draft_improvement``: the ONE reasoning-model boundary in this module
  (``run_type='cell_improvement_draft'``, ``decision_method='reasoning_llm'``).
  Three phases, matching ``app.cell_orchestrator.run_triage`` /
  ``app.cell_quality.run_audit``: a deterministic read phase that resolves
  every ``observed_facts_refs`` entry via ``cell_tasks.resolve_evidence_ref``
  BEFORE any LLM call, a phase-2 LLM call with NO ``get_conn()`` held (the
  quota wrapper around ``generate_text`` opens its own connection; the DB
  lock is non-reentrant), and a phase-3 write. Fail-closed: on ANY failure
  (missing reasoning-model config, LLM error, invalid/incomplete JSON) the
  ``intelligence_runs`` row is persisted ``status='failed'`` and NO
  ``cell_improvements`` row is created at all -- never a heuristic
  hypothesis.
- Shadow contract: ``propose_shadow`` (a ``cell_shadow_decisions`` row,
  ``kind='shadow_proposal'``) and ``request_live_shadow_approval`` (a
  SEPARATE row, ``kind='live_shadow_execution_approval'``, only creatable
  once the improvement's latest ``shadow_proposal`` is itself ``approved``)
  are two independent records with independent statuses -- approving one
  never approves or performs the other. ``decide_shadow`` is always
  ``decision_method='manual'``; approving a
  ``live_shadow_execution_approval`` writes NOTHING but that decision row
  plus one audit event ("live shadow承認だけでは配備やpolicy変更を実行し
  ない") -- no policy write, no candidate deploy, anywhere in this module.

Non-goal (Issue #300's scope line applies here too, extended): this module
never creates an experiment, writes to a target repository, opens/merges a
PR, deploys, or enables live shadow itself -- every one of those stays
behind the existing human gates (#25 probe-plan approval/apply, #216 publish
approval/push, #242 replay approval, #252 candidate promotion).

probe-agent:
  role: Improvement hypothesis lifecycle, canary evidence gate, parent/human approval gates, rubric ownership rule, suspension circuit breaker, rollback, and the fail-closed reasoning hypothesis draft
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify every improvement transition follows the finite table with rejected history never deleted, canary_ready is unreachable without a resolvable replay_run/experiment/evaluation ref, adopted is unreachable without BOTH parent and human approval recorded as separate fields, a role_card adoption only ever pins an existing agent_role_cards row of the same role_key and rollback restores exactly the previous pin, a rubric_ref change on a role_card proposal without parent_cell_id is rejected 422, 3 consecutive rejected improvements (with no intervening adopted) blocks new creation and suspends the Cell's open improvements, and approving a live_shadow_execution_approval never writes a policy or candidate row anywhere.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cell_fabric, cell_tasks
from .db import get_conn
from .llm import (
    LLMConfig,
    LLMError,
    LLMResourceLimitError,
    create_llm_client,
    is_reasoning_model,
)

CELL_IMPROVEMENT_DRAFT_PROMPT_VERSION = "v1"
CELL_IMPROVEMENT_DRAFT_SCHEMA_VERSION = "v1"

TARGET_KINDS: Tuple[str, ...] = ("role_card", "candidate_patch")

IMPROVEMENT_STATUSES: Tuple[str, ...] = (
    "observed", "proposed", "canary_ready", "canary_running",
    "adopted", "rejected", "blocked",
)

# Finite transition table (Principle 6), mirroring cell_fabric.TASK_TRANSITIONS.
IMPROVEMENT_TRANSITIONS: Dict[str, set] = {
    "observed": {"proposed"},
    "proposed": {"canary_ready", "rejected"},
    "canary_ready": {"canary_running", "rejected", "blocked"},
    "canary_running": {"adopted", "rejected", "blocked"},
    "blocked": {"canary_ready", "rejected"},
    "adopted": set(),
    "rejected": set(),
}

SHADOW_KINDS: Tuple[str, ...] = ("shadow_proposal", "live_shadow_execution_approval")
SHADOW_DECISIONS: Tuple[str, ...] = ("approved", "rejected")
_SHADOW_PROPOSAL_ALLOWED_STATUSES = ("proposed", "canary_ready", "canary_running")

_ALLOWED_CANARY_KINDS: Tuple[str, ...] = ("replay_run", "experiment", "evaluation")
_CANARY_REF_RE = re.compile(r"^(replay_run|experiment|evaluation):(.+)$")

_CONSECUTIVE_REJECTION_THRESHOLD = 3


class CellImprovementError(Exception):
    """Base class for improvement-lifecycle errors."""


class NotFoundError(CellImprovementError):
    """The referenced Cell/improvement/shadow decision does not exist in
    this System -- maps to 404."""


class ConflictError(CellImprovementError):
    """A ledger/approval/suspension/shadow-gate rule refuses the request --
    maps to 409."""


class ValidationFailedError(CellImprovementError):
    """Structural/contract validation failed (unknown enum, illegal
    transition, unresolvable/disallowed evidence ref, rubric-ownership
    violation, ...) -- maps to 422."""


# ---------------------------------------------------------------------------
# Shared row lookups (all take an already-open `conn` -- db.get_conn()'s lock
# is non-reentrant, never open a nested connection inside these).
# ---------------------------------------------------------------------------


def _get_cell_row_by_id(conn, system_id: int, row_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE id = ? AND system_id = ?",
        (row_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Cell {row_id} not found in this System")
    return row


def _get_improvement_row(conn, system_id: int, improvement_id: int):
    row = conn.execute(
        "SELECT * FROM cell_improvements WHERE id = ? AND system_id = ?",
        (improvement_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Improvement {improvement_id} not found in this System")
    return row


def _get_shadow_decision_row(conn, system_id: int, decision_id: int):
    row = conn.execute(
        "SELECT * FROM cell_shadow_decisions WHERE id = ? AND system_id = ?",
        (decision_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Shadow decision {decision_id} not found in this System")
    return row


def _card_row(conn, role_card_id: Optional[int]):
    if role_card_id is None:
        return None
    return conn.execute(
        "SELECT * FROM agent_role_cards WHERE id = ?", (role_card_id,),
    ).fetchone()


def _resolve_candidate_card(conn, system_id: int, role_key: str, version: str):
    row = conn.execute(
        """SELECT * FROM agent_role_cards
           WHERE system_id = ? AND role_key = ? AND version = ?""",
        (system_id, role_key, version),
    ).fetchone()
    if row is None:
        raise ValidationFailedError(
            f"proposed_role_card_version {version!r} does not exist for role_key "
            f"{role_key!r} in this System"
        )
    return row


def _resolve_evidence_ref(conn, system_id: int, ref: str) -> None:
    """Wrapper around ``cell_tasks.resolve_evidence_ref`` that translates its
    own ``cell_tasks.ValidationFailedError`` into this module's
    :class:`ValidationFailedError` -- ``cell_tasks``'s error class is a
    distinct hierarchy, so callers here must not let it escape untranslated
    (it would otherwise surface as an unhandled 500 instead of a 422)."""
    try:
        cell_tasks.resolve_evidence_ref(conn, system_id, ref)
    except cell_tasks.ValidationFailedError as exc:
        raise ValidationFailedError(str(exc))


def _write_event(
    conn, *, system_id: int, improvement_id: int, event_type: str,
    from_status: Optional[str] = None, to_status: Optional[str] = None,
    actor: Optional[str] = None, detail: str = "",
) -> None:
    conn.execute(
        """INSERT INTO cell_improvement_events
               (system_id, improvement_id, event_type, from_status, to_status,
                actor, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, improvement_id, event_type, from_status, to_status,
            actor, detail or "", time.time(),
        ),
    )


# ---------------------------------------------------------------------------
# Consecutive-rejection circuit breaker (Principle 6: computed live from the
# immutable event history, never a separately-persisted decaying flag).
# ---------------------------------------------------------------------------


def _consecutive_rejection_count(conn, system_id: int, cell_definition_id: int) -> int:
    """Count trailing consecutive 'rejected' status_transition events across
    ALL of a Cell's improvements, since the last 'adopted' one (or from the
    start). An 'adopted' event resets the streak to zero."""
    rows = conn.execute(
        """SELECT e.to_status FROM cell_improvement_events e
           JOIN cell_improvements i ON e.improvement_id = i.id
           WHERE e.system_id = ? AND i.cell_definition_id = ?
             AND e.event_type = 'status_transition'
             AND e.to_status IN ('rejected', 'adopted')
           ORDER BY e.created_at ASC, e.id ASC""",
        (system_id, cell_definition_id),
    ).fetchall()
    count = 0
    for r in rows:
        if r["to_status"] == "adopted":
            count = 0
        else:
            count += 1
    return count


def _refuse_if_consecutive_rejections(conn, system_id: int, cell_definition_id: int) -> None:
    count = _consecutive_rejection_count(conn, system_id, cell_definition_id)
    if count >= _CONSECUTIVE_REJECTION_THRESHOLD:
        raise ConflictError(
            f"Cell has {count} consecutive rejected improvements without an "
            "intervening adopted improvement; new improvement creation is "
            "refused until this is reviewed"
        )


def record_failure_and_maybe_suspend(
    conn, *, system_id: int, cell_definition_id: int, actor: Optional[str] = None,
) -> bool:
    """Recompute the consecutive-rejection count and, if it has reached the
    threshold, auto-suspend every currently non-terminal improvement of this
    Cell (via ``suspend_improvements``). Returns whether suspension fired."""
    count = _consecutive_rejection_count(conn, system_id, cell_definition_id)
    if count >= _CONSECUTIVE_REJECTION_THRESHOLD:
        reason = (
            f"auto-suspended: {count} consecutive rejected improvements "
            "without an intervening adopted improvement"
        )
        suspend_improvements(
            conn, system_id=system_id, cell_definition_id=cell_definition_id,
            reason=reason, actor=actor or "system:auto_suspend",
        )
        return True
    return False


def suspend_improvements(
    conn, *, system_id: int, cell_definition_id: int, reason: str, actor: Optional[str] = None,
) -> List[int]:
    """Set ``suspended=1`` on every currently non-suspended, non-terminal
    improvement of this Cell; writes one 'suspended' event per row."""
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    now = time.time()
    rows = conn.execute(
        """SELECT id FROM cell_improvements
           WHERE system_id = ? AND cell_definition_id = ? AND suspended = 0
             AND status NOT IN ('adopted', 'rejected')""",
        (system_id, cell_definition_id),
    ).fetchall()
    conn.execute("BEGIN")
    try:
        for r in rows:
            conn.execute(
                """UPDATE cell_improvements
                       SET suspended = 1, suspension_reason = ?, updated_at = ?
                   WHERE id = ?""",
                (reason or "", now, r["id"]),
            )
            _write_event(
                conn, system_id=system_id, improvement_id=r["id"],
                event_type="suspended", actor=actor, detail=reason or "",
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return [r["id"] for r in rows]


def resume_improvements(
    conn, *, system_id: int, cell_definition_id: int, actor: Optional[str] = None,
) -> List[int]:
    """Clear ``suspended`` on every currently suspended improvement of this
    Cell; writes one 'resumed' event per row. Does not itself unblock NEW
    improvement creation -- that gate is recomputed live from the event
    history and only clears once an 'adopted' improvement is recorded."""
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    now = time.time()
    rows = conn.execute(
        """SELECT id FROM cell_improvements
           WHERE system_id = ? AND cell_definition_id = ? AND suspended = 1""",
        (system_id, cell_definition_id),
    ).fetchall()
    conn.execute("BEGIN")
    try:
        for r in rows:
            conn.execute(
                """UPDATE cell_improvements
                       SET suspended = 0, suspension_reason = '', updated_at = ?
                   WHERE id = ?""",
                (now, r["id"]),
            )
            _write_event(
                conn, system_id=system_id, improvement_id=r["id"],
                event_type="resumed", actor=actor, detail="",
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Manual creation (no LLM)
# ---------------------------------------------------------------------------


def create_improvement_manual(
    conn,
    *,
    system_id: int,
    cell_definition_id: int,
    target_kind: str,
    hypothesis: str = "",
    expected_effect: str = "",
    risk: str = "",
    rollback_plan: str = "",
    observed_facts_refs: Optional[List[str]] = None,
    parent_cell_id: Optional[int] = None,
):
    if target_kind not in TARGET_KINDS:
        raise ValidationFailedError(f"Unknown target_kind: {target_kind!r}")
    cell_row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
    _refuse_if_consecutive_rejections(conn, system_id, cell_definition_id)

    parent_row_id = None
    if parent_cell_id is not None:
        parent_row_id = _get_cell_row_by_id(conn, system_id, parent_cell_id)["id"]

    refs = observed_facts_refs or []
    for ref in refs:
        _resolve_evidence_ref(conn, system_id, ref)

    role_card_id = cell_row["role_card_id"] if target_kind == "role_card" else None

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_improvements
                   (system_id, cell_definition_id, status, target_kind, hypothesis,
                    expected_effect, risk, rollback_plan, observed_facts_json,
                    role_card_id, parent_cell_id, created_at, updated_at)
               VALUES (?, ?, 'observed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, cell_definition_id, target_kind, hypothesis or "",
                expected_effect or "", risk or "", rollback_plan or "",
                json.dumps(refs), role_card_id, parent_row_id, now, now,
            ),
        )
        improvement_id = cur.lastrowid
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="created", from_status=None, to_status="observed", detail="",
        )
        row = conn.execute(
            "SELECT * FROM cell_improvements WHERE id = ?", (improvement_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


# ---------------------------------------------------------------------------
# Reasoning draft (fail-closed; the ONE reasoning-model boundary here)
# ---------------------------------------------------------------------------

_IMPROVEMENT_SYSTEM_PROMPT = """\
You are drafting an improvement HYPOTHESIS for a Probe Cell, grounded only in \
the deterministic observed facts referenced below (failed samples, drift, or \
task failures). Never invent facts not present in the evidence. Your output \
is a suggestion only -- it never approves, adopts, or executes anything; a \
human always reviews and approves it separately."""

_IMPROVEMENT_PROMPT_TEMPLATE = """\
CELL_IMPROVEMENT_RESPONSE_JSON

Cell: {cell_id}
Target kind: {target_kind}
Observed facts (evidence refs): {observed_facts_refs}

Respond with ONLY valid JSON:
{{
  "hypothesis": "string grounded only in the observed facts above",
  "expected_effect": "string",
  "risk": "string",
  "rollback_plan": "string"
}}"""


def _parse_improvement_draft_response(raw: str) -> Dict[str, str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValidationFailedError("Improvement draft response must be a JSON object")
    out: Dict[str, str] = {}
    for field in ("hypothesis", "expected_effect", "risk", "rollback_plan"):
        value = str(data.get(field, "")).strip()
        if not value:
            raise ValidationFailedError(f"{field} is required in the draft response")
        out[field] = value
    return out


def draft_improvement(
    *,
    system_id: int,
    cell_definition_id: int,
    target_kind: str = "role_card",
    observed_facts_refs: Optional[List[str]] = None,
    parent_cell_id: Optional[int] = None,
):
    """Draft an improvement hypothesis via the reasoning model. Returns
    ``(improvement_row_or_None, intelligence_run_id, is_mock, error_or_None)``.

    Phase 1 (deterministic read, this conn): resolve the Cell and every
    observed_facts ref BEFORE any LLM call -- an unresolvable ref fails
    closed immediately, with no run persisted at all. Phase 2 (LLM, NO
    conn held). Phase 3 (write): on ANY LLM/JSON failure the
    ``intelligence_runs`` row is persisted ``failed`` and NO
    ``cell_improvements`` row is created.
    """
    if target_kind not in TARGET_KINDS:
        raise ValidationFailedError(f"Unknown target_kind: {target_kind!r}")

    refs = observed_facts_refs or []
    with get_conn() as conn:
        cell_row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
        _refuse_if_consecutive_rejections(conn, system_id, cell_definition_id)
        for ref in refs:
            _resolve_evidence_ref(conn, system_id, ref)
        parent_row_id = None
        if parent_cell_id is not None:
            parent_row_id = _get_cell_row_by_id(conn, system_id, parent_cell_id)["id"]
        role_card_id = cell_row["role_card_id"] if target_kind == "role_card" else None
        cell_business_id = cell_row["cell_id"]

    llm_config = LLMConfig.intelligence_from_env()
    is_mock = llm_config.provider == "mock"
    started_at = time.time()
    error: Optional[str] = None
    drafted: Optional[Dict[str, str]] = None

    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Cell improvement drafting requires a configured reasoning model"
            )
        llm_client = create_llm_client(llm_config)
        prompt = _IMPROVEMENT_PROMPT_TEMPLATE.format(
            cell_id=cell_business_id, target_kind=target_kind,
            observed_facts_refs=json.dumps(refs),
        )
        raw = llm_client.generate_text(
            [
                {"role": "system", "content": _IMPROVEMENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1024,
        )
        drafted = _parse_improvement_draft_response(raw)
    except (LLMError, LLMResourceLimitError, ValidationFailedError, json.JSONDecodeError) as exc:
        error = str(exc)
    completed_at = time.time()

    run_status = "completed" if error is None else "failed"
    with get_conn() as conn:
        now = time.time()
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, error_details, is_mock, started_at, completed_at)
                   VALUES (?, NULL, 'cell_improvement_draft', ?, ?, ?, ?, 'reasoning_llm',
                           ?, ?, ?, ?, ?)""",
                (
                    system_id, llm_config.provider, llm_config.model,
                    CELL_IMPROVEMENT_DRAFT_PROMPT_VERSION,
                    CELL_IMPROVEMENT_DRAFT_SCHEMA_VERSION,
                    run_status, error, 1 if is_mock else 0, started_at, completed_at,
                ),
            )
            run_id = cur.lastrowid
            improvement_row = None
            if run_status == "completed":
                assert drafted is not None
                cur = conn.execute(
                    """INSERT INTO cell_improvements
                           (system_id, cell_definition_id, status, target_kind, hypothesis,
                            expected_effect, risk, rollback_plan, observed_facts_json,
                            proposal_run_id, role_card_id, parent_cell_id,
                            created_at, updated_at)
                       VALUES (?, ?, 'observed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        system_id, cell_definition_id, target_kind,
                        drafted["hypothesis"], drafted["expected_effect"],
                        drafted["risk"], drafted["rollback_plan"], json.dumps(refs),
                        run_id, role_card_id, parent_row_id, now, now,
                    ),
                )
                improvement_id = cur.lastrowid
                _write_event(
                    conn, system_id=system_id, improvement_id=improvement_id,
                    event_type="created", from_status=None, to_status="observed",
                    detail="reasoning_llm draft",
                )
                improvement_row = conn.execute(
                    "SELECT * FROM cell_improvements WHERE id = ?", (improvement_id,),
                ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return improvement_row, run_id, is_mock, error


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_improvements(conn, system_id: int, cell_definition_id: int):
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    return conn.execute(
        """SELECT * FROM cell_improvements WHERE system_id = ? AND cell_definition_id = ?
           ORDER BY id DESC""",
        (system_id, cell_definition_id),
    ).fetchall()


def get_improvement(conn, system_id: int, improvement_id: int):
    return _get_improvement_row(conn, system_id, improvement_id)


def list_improvement_events(conn, system_id: int, improvement_id: int):
    return conn.execute(
        """SELECT * FROM cell_improvement_events
           WHERE system_id = ? AND improvement_id = ? ORDER BY id ASC""",
        (system_id, improvement_id),
    ).fetchall()


def list_shadow_decisions(conn, system_id: int, improvement_id: int):
    return conn.execute(
        """SELECT * FROM cell_shadow_decisions
           WHERE system_id = ? AND improvement_id = ? ORDER BY id ASC""",
        (system_id, improvement_id),
    ).fetchall()


# ---------------------------------------------------------------------------
# Canary evidence gate (restricted ref kinds; existence resolution reuses
# app.cell_tasks.resolve_evidence_ref -- no new execution path)
# ---------------------------------------------------------------------------


def _validate_canary_ref(conn, system_id: int, ref: str) -> None:
    match = _CANARY_REF_RE.match(ref) if isinstance(ref, str) else None
    if match is None:
        raise ValidationFailedError(
            "canary evidence ref must be replay_run:<id> / experiment:<id> / "
            f"evaluation:<id>: {ref!r}"
        )
    # Existence + System ownership (shared resolver, no new execution path).
    _resolve_evidence_ref(conn, system_id, ref)
    # Beyond existence, canary evidence must come from a run that ACTUALLY
    # completed successfully -- an un-run/failed replay or experiment is not
    # evidence (Principle 6, a deterministic structural gate). An
    # ``evaluation:<id>`` is an already-terminal ``evaluation_results`` row
    # (produced only after an evaluation runs), so its existence is the
    # completion signal.
    kind, raw_id = match.group(1), match.group(2)
    if kind == "replay_run":
        status = conn.execute(
            "SELECT status FROM replay_runs WHERE id = ? AND system_id = ?",
            (raw_id, system_id),
        ).fetchone()
        if status is None or status["status"] != "completed":
            raise ValidationFailedError(
                f"canary evidence {ref!r} is not a completed replay run "
                f"(status={status['status'] if status else 'missing'!r})"
            )
    elif kind == "experiment":
        status = conn.execute(
            "SELECT status FROM experiments WHERE id = ? AND system_id = ?",
            (raw_id, system_id),
        ).fetchone()
        if status is None or status["status"] != "completed":
            raise ValidationFailedError(
                f"canary evidence {ref!r} is not a completed experiment "
                f"(status={status['status'] if status else 'missing'!r})"
            )


# ---------------------------------------------------------------------------
# Rubric ownership (parent-owned; Cell cannot change its own grading rubric)
# ---------------------------------------------------------------------------


def _check_rubric_ownership(conn, system_id: int, row, new_version: str) -> None:
    pinned_card = _card_row(conn, row["role_card_id"])
    if pinned_card is None:
        raise ValidationFailedError(
            "improvement has no pinned role card to compare rubric_ref against"
        )
    candidate_card = _resolve_candidate_card(
        conn, system_id, pinned_card["role_key"], new_version,
    )
    if (candidate_card["rubric_ref"] or "") != (pinned_card["rubric_ref"] or ""):
        if not row["parent_cell_id"]:
            raise ValidationFailedError(
                "Cellが自己採点基準を変更できない: a rubric_ref change requires "
                "parent_cell_id to be set and parent approval"
            )


def _require_hypothesis_fields(row) -> None:
    for field in ("hypothesis", "expected_effect", "risk", "rollback_plan"):
        if not (row[field] or "").strip():
            raise ValidationFailedError(
                f"observed -> proposed requires a non-empty {field}"
            )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def transition_improvement(
    conn,
    *,
    system_id: int,
    improvement_id: int,
    new_status: str,
    canary_evidence_refs: Optional[List[str]] = None,
    proposed_role_card_version: Optional[str] = None,
    allow_major_bump: bool = False,
    detail: str = "",
    actor: Optional[str] = None,
):
    row = _get_improvement_row(conn, system_id, improvement_id)
    if row["suspended"]:
        raise ConflictError(
            f"Improvement {improvement_id} is suspended; resume before transitioning"
        )
    current = row["status"]
    if new_status not in IMPROVEMENT_STATUSES:
        raise ValidationFailedError(f"Unknown target status: {new_status!r}")
    allowed = IMPROVEMENT_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValidationFailedError(
            f"Improvement transition {current!r} -> {new_status!r} is not allowed"
        )

    existing_evidence = json.loads(row["canary_evidence_json"] or "[]")
    updated_evidence = list(existing_evidence)
    if canary_evidence_refs:
        for ref in canary_evidence_refs:
            _validate_canary_ref(conn, system_id, ref)
            if ref not in updated_evidence:
                updated_evidence.append(ref)

    updated_role_card_version = row["proposed_role_card_version"]
    if proposed_role_card_version is not None:
        if row["target_kind"] == "role_card":
            _check_rubric_ownership(conn, system_id, row, proposed_role_card_version)
        updated_role_card_version = proposed_role_card_version

    # An approval is granted against a SPECIFIC proposed version + canary
    # evidence set. If either changes after an approval was recorded, the
    # approval no longer covers what would be adopted, so it is invalidated
    # (must be re-granted). This closes the "swap the content after approval,
    # then adopt" gap (Principle 7: the approval is the manual decision on a
    # concrete proposal, not a blank cheque).
    content_changed = (
        updated_role_card_version != row["proposed_role_card_version"]
        or updated_evidence != existing_evidence
    )
    had_approval = bool(
        row["parent_approved_by"] or row["human_approved_by"]
    )
    invalidate_approvals = content_changed and had_approval

    # Eagerly void the approvals the moment the proposed content changes, in
    # its own committed transaction -- so even if the current transition is
    # then refused (e.g. an adopt that changed the version), the stale
    # approval is gone and must be re-granted against the new proposal.
    if invalidate_approvals:
        conn.execute("BEGIN")
        try:
            conn.execute(
                """UPDATE cell_improvements
                       SET parent_approved_by = NULL, parent_approved_at = NULL,
                           human_approved_by = NULL, human_approved_at = NULL
                   WHERE id = ?""",
                (improvement_id,),
            )
            _write_event(
                conn, system_id=system_id, improvement_id=improvement_id,
                event_type="approvals_invalidated", from_status=current, to_status=current,
                actor=actor,
                detail="proposed role card version or canary evidence changed "
                       "after approval; parent/human approvals invalidated",
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    if new_status == "proposed":
        _require_hypothesis_fields(row)

    if new_status == "canary_ready":
        if not updated_evidence:
            raise ValidationFailedError(
                "canary_ready requires at least one canary evidence ref "
                "(replay_run/experiment/evaluation)"
            )

    new_cell_role_card_id: Optional[int] = None
    event_detail_payload: Optional[Dict[str, Any]] = None

    if new_status == "adopted":
        # An approval about to be invalidated by this same call's content
        # change cannot satisfy the adoption gate.
        parent_ok = bool(row["parent_approved_by"] and row["parent_approved_at"]) and not invalidate_approvals
        human_ok = bool(row["human_approved_by"] and row["human_approved_at"]) and not invalidate_approvals
        if not parent_ok:
            raise ConflictError(
                f"Improvement {improvement_id} cannot be adopted without a "
                "recorded parent approval covering the current proposal"
            )
        if not human_ok:
            raise ConflictError(
                f"Improvement {improvement_id} cannot be adopted without a "
                "recorded human approval covering the current proposal"
            )
        if row["target_kind"] == "role_card":
            if not updated_role_card_version:
                raise ValidationFailedError(
                    "adopted role_card improvement requires proposed_role_card_version"
                )
            pinned_card = _card_row(conn, row["role_card_id"])
            if pinned_card is None:
                raise ValidationFailedError(
                    "improvement has no pinned role card recorded"
                )
            candidate_card = _resolve_candidate_card(
                conn, system_id, pinned_card["role_key"], updated_role_card_version,
            )
            # A draft/deprecated card must never be adopted onto a live Cell
            # (deterministic structural gate).
            if candidate_card["status"] != "active":
                raise ConflictError(
                    "cannot adopt a role card whose status is "
                    f"{candidate_card['status']!r}; only an 'active' card may "
                    "be adopted"
                )
            try:
                compatible = cell_fabric.role_card_versions_compatible(
                    pinned_card["version"], candidate_card["version"],
                )
            except cell_fabric.CellContractError as exc:
                raise ValidationFailedError(str(exc))
            # An incompatible (major) bump is only adopted when the caller
            # explicitly intends it -- otherwise fail closed instead of
            # silently recording 'major_bump' and proceeding.
            if not compatible and not allow_major_bump:
                raise ConflictError(
                    f"proposed role card {candidate_card['version']!r} is not "
                    f"semver-compatible with the pinned {pinned_card['version']!r}; "
                    "pass allow_major_bump=true to adopt an intentional major bump"
                )
            compat_label = "compatible" if compatible else "major_bump"
            new_cell_role_card_id = candidate_card["id"]
            event_detail_payload = {
                "role_card_adoption": {
                    "previous_role_card_id": pinned_card["id"],
                    "new_role_card_id": candidate_card["id"],
                    "compat": compat_label,
                }
            }
        else:
            event_detail_payload = {
                "handoff": (
                    "candidate_patch adoption performs no write outside "
                    "cell_improvements/cell_improvement_events; the actual "
                    "adoption/publish flows through the existing #25/#216/"
                    "#242/#252 human gates"
                )
            }

    now = time.time()
    event_detail = json.dumps(event_detail_payload) if event_detail_payload is not None else (detail or "")

    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cell_improvements
                   SET status = ?, canary_evidence_json = ?,
                       proposed_role_card_version = ?, updated_at = ?
               WHERE id = ?""",
            (new_status, json.dumps(updated_evidence), updated_role_card_version, now, improvement_id),
        )
        if new_cell_role_card_id is not None:
            conn.execute(
                "UPDATE cell_definitions SET role_card_id = ?, updated_at = ? WHERE id = ?",
                (new_cell_role_card_id, now, row["cell_definition_id"]),
            )
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="status_transition", from_status=current, to_status=new_status,
            actor=actor, detail=event_detail,
        )
        updated_row = conn.execute(
            "SELECT * FROM cell_improvements WHERE id = ?", (improvement_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if new_status == "rejected":
        record_failure_and_maybe_suspend(
            conn, system_id=system_id, cell_definition_id=row["cell_definition_id"],
            actor="system:auto_suspend",
        )
        updated_row = _get_improvement_row(conn, system_id, improvement_id)

    return updated_row


# ---------------------------------------------------------------------------
# Parent / human approval (separate records; both required for adoption)
# ---------------------------------------------------------------------------


def _ensure_approvable(row) -> None:
    if row["suspended"]:
        raise ConflictError(
            f"Improvement {row['id']} is suspended; resume before approving"
        )
    if row["status"] in ("adopted", "rejected"):
        raise ConflictError(
            f"Improvement {row['id']} is already terminal ({row['status']!r})"
        )


def parent_approve(conn, *, system_id: int, improvement_id: int, actor: str):
    if not actor or not actor.strip():
        raise ValidationFailedError("actor is required")
    row = _get_improvement_row(conn, system_id, improvement_id)
    _ensure_approvable(row)
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cell_improvements
                   SET parent_approved_by = ?, parent_approved_at = ?, updated_at = ?
               WHERE id = ?""",
            (actor, now, now, improvement_id),
        )
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="parent_approval", from_status=row["status"], to_status=row["status"],
            actor=actor, detail=f"parent approved by {actor}",
        )
        updated = conn.execute(
            "SELECT * FROM cell_improvements WHERE id = ?", (improvement_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return updated


def human_approve(conn, *, system_id: int, improvement_id: int, actor: str):
    if not actor or not actor.strip():
        raise ValidationFailedError("actor is required")
    row = _get_improvement_row(conn, system_id, improvement_id)
    _ensure_approvable(row)
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cell_improvements
                   SET human_approved_by = ?, human_approved_at = ?, updated_at = ?
               WHERE id = ?""",
            (actor, now, now, improvement_id),
        )
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="human_approval", from_status=row["status"], to_status=row["status"],
            actor=actor, detail=f"human approved by {actor}",
        )
        updated = conn.execute(
            "SELECT * FROM cell_improvements WHERE id = ?", (improvement_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return updated


# ---------------------------------------------------------------------------
# Rollback (role_card only, only for an adopted improvement)
# ---------------------------------------------------------------------------


def rollback_role_card(conn, *, system_id: int, improvement_id: int, actor: Optional[str] = None):
    row = _get_improvement_row(conn, system_id, improvement_id)
    if row["status"] != "adopted" or row["target_kind"] != "role_card":
        raise ConflictError(
            "rollback is only allowed for an adopted role_card improvement"
        )
    event = conn.execute(
        """SELECT * FROM cell_improvement_events
           WHERE system_id = ? AND improvement_id = ?
             AND event_type = 'status_transition' AND to_status = 'adopted'
           ORDER BY id DESC LIMIT 1""",
        (system_id, improvement_id),
    ).fetchone()
    if event is None or not event["detail"]:
        raise ConflictError(
            f"Improvement {improvement_id} has no adoption event to roll back"
        )
    try:
        detail_data = json.loads(event["detail"])
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ConflictError("adoption event detail is not valid JSON; cannot roll back")
    adoption = detail_data.get("role_card_adoption") if isinstance(detail_data, dict) else None
    if not adoption or "previous_role_card_id" not in adoption:
        raise ConflictError(
            f"Improvement {improvement_id}'s adoption event has no previous "
            "role card recorded"
        )
    previous_role_card_id = adoption["previous_role_card_id"]
    # Only roll back if this improvement's adopted card is STILL the Cell's
    # pinned card. If a later improvement was adopted after this one, its
    # newer card is live -- blindly restoring this improvement's predecessor
    # would clobber that newer adoption.
    cell_row = conn.execute(
        "SELECT role_card_id FROM cell_definitions WHERE id = ? AND system_id = ?",
        (row["cell_definition_id"], system_id),
    ).fetchone()
    adopted_card_id = adoption.get("new_role_card_id")
    if cell_row is None or cell_row["role_card_id"] != adopted_card_id:
        raise ConflictError(
            f"Improvement {improvement_id} cannot be rolled back: a newer role "
            "card adoption has superseded it (the Cell no longer pins this "
            "improvement's adopted card)"
        )
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE cell_definitions SET role_card_id = ?, updated_at = ? WHERE id = ?",
            (previous_role_card_id, now, row["cell_definition_id"]),
        )
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="rolled_back", from_status="adopted", to_status="adopted",
            actor=actor, detail=json.dumps({"restored_role_card_id": previous_role_card_id}),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"improvement_id": improvement_id, "restored_role_card_id": previous_role_card_id}


# ---------------------------------------------------------------------------
# Shadow contract: proposal and live-shadow execution approval are SEPARATE
# records/statuses (Principle 5/8: shadow *proposal* is never execution).
# ---------------------------------------------------------------------------


def propose_shadow(
    conn, *, system_id: int, improvement_id: int, note: str = "", actor: Optional[str] = None,
):
    row = _get_improvement_row(conn, system_id, improvement_id)
    if row["suspended"]:
        raise ConflictError(
            f"Improvement {improvement_id} is suspended; resume before proposing shadow"
        )
    if row["status"] not in _SHADOW_PROPOSAL_ALLOWED_STATUSES:
        raise ConflictError(
            f"shadow proposal is not allowed from status {row['status']!r}"
        )
    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_shadow_decisions
                   (system_id, improvement_id, kind, status, note, created_at)
               VALUES (?, ?, 'shadow_proposal', 'proposed', ?, ?)""",
            (system_id, improvement_id, note or "", now),
        )
        decision_id = cur.lastrowid
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="shadow_proposed", actor=actor, detail=note or "",
        )
        row_out = conn.execute(
            "SELECT * FROM cell_shadow_decisions WHERE id = ?", (decision_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row_out


def request_live_shadow_approval(
    conn, *, system_id: int, improvement_id: int, note: str = "", actor: Optional[str] = None,
):
    row = _get_improvement_row(conn, system_id, improvement_id)
    if row["suspended"]:
        raise ConflictError(
            f"Improvement {improvement_id} is suspended; resume before requesting "
            "live shadow approval"
        )
    latest_proposal = conn.execute(
        """SELECT * FROM cell_shadow_decisions
           WHERE system_id = ? AND improvement_id = ? AND kind = 'shadow_proposal'
           ORDER BY id DESC LIMIT 1""",
        (system_id, improvement_id),
    ).fetchone()
    if latest_proposal is None or latest_proposal["status"] != "approved":
        raise ConflictError(
            "live shadow execution approval requires an already-approved "
            "shadow_proposal"
        )
    # Replay-first (Principle 5/8, #242): the current SDK in-process live
    # shadow does not isolate candidate side effects, so a live shadow may
    # only be REQUESTED after an isolated Replay / offline shadow has actually
    # run. Deterministic structural gate: the improvement must carry at least
    # one completed replay_run canary ref.
    replay_refs = [
        ref for ref in json.loads(row["canary_evidence_json"] or "[]")
        if isinstance(ref, str) and ref.startswith("replay_run:")
    ]
    has_completed_replay = False
    for ref in replay_refs:
        raw_id = ref.split(":", 1)[1]
        status = conn.execute(
            "SELECT status FROM replay_runs WHERE id = ? AND system_id = ?",
            (raw_id, system_id),
        ).fetchone()
        if status is not None and status["status"] == "completed":
            has_completed_replay = True
            break
    if not has_completed_replay:
        raise ConflictError(
            "live shadow execution approval requires replay-first evidence: "
            "at least one completed isolated Replay run (replay_run:<id>) must "
            "be recorded as canary evidence before live shadow is requested"
        )
    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_shadow_decisions
                   (system_id, improvement_id, kind, status, note, created_at)
               VALUES (?, ?, 'live_shadow_execution_approval', 'proposed', ?, ?)""",
            (system_id, improvement_id, note or "", now),
        )
        decision_id = cur.lastrowid
        _write_event(
            conn, system_id=system_id, improvement_id=improvement_id,
            event_type="live_shadow_approval_requested", actor=actor, detail=note or "",
        )
        row_out = conn.execute(
            "SELECT * FROM cell_shadow_decisions WHERE id = ?", (decision_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row_out


def decide_shadow(
    conn, *, system_id: int, decision_id: int, decision: str, decided_by: str, note: str = "",
):
    """Decide a shadow_proposal or live_shadow_execution_approval row.
    Always ``decision_method='manual'``. Approving a
    ``live_shadow_execution_approval`` writes NOTHING but this row plus one
    audit event -- no policy write, no candidate deploy, anywhere here."""
    if decision not in SHADOW_DECISIONS:
        raise ValidationFailedError(f"Unknown decision: {decision!r}")
    if not decided_by or not decided_by.strip():
        raise ValidationFailedError("decided_by is required")
    row = _get_shadow_decision_row(conn, system_id, decision_id)
    if row["status"] != "proposed":
        raise ConflictError(
            f"Shadow decision {decision_id} is already decided ({row['status']!r})"
        )
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cell_shadow_decisions
                   SET status = ?, decided_by = ?, decided_at = ?,
                       decision_method = 'manual', note = ?
               WHERE id = ?""",
            (decision, decided_by, now, note or "", decision_id),
        )
        if row["kind"] == "live_shadow_execution_approval" and decision == "approved":
            _write_event(
                conn, system_id=system_id, improvement_id=row["improvement_id"],
                event_type="live_shadow_approved", actor=decided_by,
                detail="live shadow承認だけでは配備やpolicy変更を実行しない",
            )
        updated = conn.execute(
            "SELECT * FROM cell_shadow_decisions WHERE id = ?", (decision_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return updated
