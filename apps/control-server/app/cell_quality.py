"""Probe Cell Fabric: 品質サンプリング・独立監査・quality floor
(Issue #302, Sub 6 of the Probe Cell Fabric epic, Issue #297).

This module owns:

- ``select_samples``: DETERMINISTIC stratified selection (Principle 6) of a
  worker Cell's recent traces for quality audit, using a stable hash
  (``sha256(f"{system_id}:{cell_definition_id}:{trace_id}")``) so the same
  inputs always select the same sample set -- re-running selection over an
  unchanged window never duplicates a row (``UNIQUE`` +
  ``INSERT OR IGNORE``). Stratum assignment is finite-field matching only:
  the ``traces`` table has no task-type/tag column, so a stratum whose
  ``risk`` is ``"error"`` matches traces with a recorded error and every
  other stratum falls back to the default (empty-name) stratum. A stratum
  flagged ``rare`` always gets at least one sample from its matching traces
  (the lowest-hash one) even when the sample rate alone would select none.
- ``run_audit``: a 3-phase (read / optional LLM with NO connection held /
  write) audit of one sample. The verdict is ALWAYS deterministic --
  evaluated against the component's ``evaluation_criteria`` via
  ``app/evaluator.py``'s rule engine, excluding ``natural_language``
  criteria exactly as the existing trace-evaluation engine does
  (``no_criteria`` when the component has no deterministic criteria). Only
  a ``fail`` verdict attempts an OPTIONAL reasoning-model explanation of the
  failure pattern (``run_type=cell_quality_audit``, ``decision_method=
  reasoning_llm``, auditor resolved via ``cell_fabric.resolve_model_alias``
  so the auditor model is never the same alias as the worker's by
  construction) -- on ANY LLM failure the audit row is still written with
  its deterministic verdict, ``explanation=''``, and the ``intelligence_runs``
  row marked ``failed`` (deterministic facts survive interpretation
  failure, Principle 7). A blind re-audit (``blind=True``) never reads a
  prior ``cell_quality_audits`` row before computing its verdict -- the
  verdict function only ever reads the sampled trace and the component's
  criteria, never this table. Every audit consumes one unit from the
  System's shared daily ``cell_quality_usage`` budget (gated by the
  audited Cell's own ``daily_audit_budget``); an exhausted budget is a
  deterministic 409 and the audit is not performed at all.
- ``run_parent_blind_audits``: chooses ``floor(audit_rate * sample_count)``
  of a Cell's samples, most-recent-first (a fixed, reproducible order), and
  blind-audits each one via ``run_audit``.
- ``evaluate_quality_floor``: a rolling pass-rate check over the last
  ``floor_window`` audited samples (blind audits included; ``no_criteria``
  excluded from the denominator). Breaching the floor (with at least 5
  audited samples in the window) suspends ONLY the audited Cell's
  ``cell_intake_states`` row and raises a sev1 escalation through the
  EXISTING ``app/cell_tasks.py::submit_report`` path -- this module never
  duplicates that persistence. ``resume_intake`` is the explicit, manual
  recovery operation: it recomputes the same rolling pass rate and refuses
  (409) unless it is back at or above the floor.

Non-goals (Issue #300's scope line applies here too): sampling/audit/floor
evaluation are explicit API calls or callable steps, never invoked from the
trace-ingestion path (``routes/traces.py`` is untouched). The Python Probe
SDK's lineage/projection ``sample_rate`` is a completely different,
SDK-side thinning contract -- this module is Control-Server-side only and
never imports from ``packages/python-probe``.

probe-agent:
  role: Deterministic quality sampling, deterministic-verdict audits with fail-closed explanation, and quality-floor suspend/resume
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify sample selection is byte-for-byte reproducible from the same stable hash with no duplicate rows, a rare stratum's guaranteed sample always lands, the audit verdict never depends on an LLM or on any prior audit row (blind path provably never reads cell_quality_audits before computing the verdict), a failed explanation still leaves the deterministic verdict persisted with a failed intelligence_runs row, the daily audit budget is a hard deterministic gate shared per System, and a quality-floor breach suspends only the offending Cell while raising exactly one sev1 escalation via the existing report path.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import cell_fabric, cell_tasks, evaluator
from .db import get_conn
from .llm import (
    LLMError,
    LLMResourceLimitError,
    create_llm_client,
    is_reasoning_model,
)

CELL_QUALITY_AUDIT_PROMPT_VERSION = "v1"
CELL_QUALITY_AUDIT_SCHEMA_VERSION = "v1"

DEFAULT_AUDITOR_ALIAS = "auditor-default"

DEFAULT_SAMPLE_RATE = 0.05
DEFAULT_STRATA: List[Dict[str, Any]] = []
DEFAULT_AUDIT_RATE = 0.1
DEFAULT_QUALITY_FLOOR = 0.7
DEFAULT_FLOOR_WINDOW = 20
DEFAULT_DAILY_AUDIT_BUDGET = 50

MIN_FLOOR_SAMPLE_COUNT = 5

VERDICTS: Tuple[str, ...] = ("pass", "fail", "no_criteria")
INTAKE_STATUSES: Tuple[str, ...] = ("accepting", "suspended")


class CellQualityError(Exception):
    """Base class for quality-sampling/audit/floor errors."""


class NotFoundError(CellQualityError):
    """The referenced Cell/config/sample/audit does not exist in this
    System -- maps to 404."""


class ConflictError(CellQualityError):
    """A ledger/budget/floor rule refuses the request -- maps to 409."""


class ValidationFailedError(CellQualityError):
    """Structural/contract validation failed -- maps to 422."""


# ---------------------------------------------------------------------------
# Shared row lookups (all take an already-open `conn` -- db.get_conn()'s lock
# is non-reentrant, never open a nested connection inside these).
# ---------------------------------------------------------------------------


def _get_cell_row_by_id(conn, system_id: int, cell_definition_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE id = ? AND system_id = ?",
        (cell_definition_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Cell {cell_definition_id} not found in this System")
    return row


def _worker_model_alias(conn, cell_row) -> Optional[str]:
    """The audited Cell's own worker model alias (from its pinned Role Card),
    used to reject a self-audit. ``None`` when the Cell has no resolvable
    role card."""
    role_card_id = cell_row["role_card_id"]
    if role_card_id is None:
        return None
    card = conn.execute(
        "SELECT model_alias FROM agent_role_cards WHERE id = ?",
        (role_card_id,),
    ).fetchone()
    if card is None:
        return None
    return (card["model_alias"] or "").strip() or None


def _get_config_row(conn, system_id: int, cell_definition_id: int):
    return conn.execute(
        "SELECT * FROM cell_quality_configs WHERE system_id = ? AND cell_definition_id = ?",
        (system_id, cell_definition_id),
    ).fetchone()


def _get_sample_row(conn, system_id: int, sample_id: int):
    row = conn.execute(
        "SELECT * FROM cell_quality_samples WHERE id = ? AND system_id = ?",
        (sample_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Quality sample {sample_id} not found in this System")
    return row


# ---------------------------------------------------------------------------
# Quality config (PUT/GET /cell-fabric/cells/{cell_id}/quality-config)
# ---------------------------------------------------------------------------


def _validate_strata(strata: Any) -> List[Dict[str, Any]]:
    if not isinstance(strata, list):
        raise ValidationFailedError("strata must be a list")
    out: List[Dict[str, Any]] = []
    for item in strata:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise ValidationFailedError(
                "each stratum requires a non-empty 'name'"
            )
        out.append(
            {
                "name": str(item["name"]),
                "task_type": item.get("task_type"),
                "risk": item.get("risk"),
                "rare": 1 if item.get("rare") else 0,
            }
        )
    return out


def _config_out(row) -> Dict[str, Any]:
    return {
        "system_id": row["system_id"],
        "cell_definition_id": row["cell_definition_id"],
        "sample_rate": row["sample_rate"],
        "strata": json.loads(row["strata_json"] or "[]"),
        "audit_rate": row["audit_rate"],
        "quality_floor": row["quality_floor"],
        "floor_window": row["floor_window"],
        "daily_audit_budget": row["daily_audit_budget"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_quality_config(conn, system_id: int, cell_definition_id: int) -> Dict[str, Any]:
    """Return the persisted config, or in-memory defaults when none has been
    saved yet (mirrors the ``system_profile`` GET-before-PUT convention --
    never persists a row just from a read)."""
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    row = _get_config_row(conn, system_id, cell_definition_id)
    if row is None:
        return {
            "system_id": system_id,
            "cell_definition_id": cell_definition_id,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "strata": list(DEFAULT_STRATA),
            "audit_rate": DEFAULT_AUDIT_RATE,
            "quality_floor": DEFAULT_QUALITY_FLOOR,
            "floor_window": DEFAULT_FLOOR_WINDOW,
            "daily_audit_budget": DEFAULT_DAILY_AUDIT_BUDGET,
            "created_at": None,
            "updated_at": None,
        }
    return _config_out(row)


def upsert_quality_config(
    conn,
    *,
    system_id: int,
    cell_definition_id: int,
    sample_rate: Optional[float] = None,
    strata: Optional[List[Dict[str, Any]]] = None,
    audit_rate: Optional[float] = None,
    quality_floor: Optional[float] = None,
    floor_window: Optional[int] = None,
    daily_audit_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate and upsert a Cell's quality config. Any field left ``None``
    keeps its existing persisted value (or the module default, for a
    first-time create)."""
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    existing = _get_config_row(conn, system_id, cell_definition_id)

    def _default(value, key, default):
        if value is not None:
            return value
        if existing is not None:
            return existing[key]
        return default

    sample_rate_v = _default(sample_rate, "sample_rate", DEFAULT_SAMPLE_RATE)
    audit_rate_v = _default(audit_rate, "audit_rate", DEFAULT_AUDIT_RATE)
    quality_floor_v = _default(quality_floor, "quality_floor", DEFAULT_QUALITY_FLOOR)
    floor_window_v = _default(floor_window, "floor_window", DEFAULT_FLOOR_WINDOW)
    daily_audit_budget_v = _default(
        daily_audit_budget, "daily_audit_budget", DEFAULT_DAILY_AUDIT_BUDGET
    )
    if strata is not None:
        strata_v = _validate_strata(strata)
    elif existing is not None:
        strata_v = json.loads(existing["strata_json"] or "[]")
    else:
        strata_v = list(DEFAULT_STRATA)

    for name, value in (
        ("sample_rate", sample_rate_v),
        ("audit_rate", audit_rate_v),
        ("quality_floor", quality_floor_v),
    ):
        if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
            raise ValidationFailedError(f"{name} must be between 0.0 and 1.0")
    if not isinstance(floor_window_v, int) or floor_window_v < 1:
        raise ValidationFailedError("floor_window must be an integer >= 1")
    if not isinstance(daily_audit_budget_v, int) or daily_audit_budget_v < 0:
        raise ValidationFailedError("daily_audit_budget must be an integer >= 0")

    now = time.time()
    created_at = existing["created_at"] if existing is not None else now
    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT INTO cell_quality_configs
                   (system_id, cell_definition_id, sample_rate, strata_json,
                    audit_rate, quality_floor, floor_window, daily_audit_budget,
                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(system_id, cell_definition_id) DO UPDATE SET
                   sample_rate = excluded.sample_rate,
                   strata_json = excluded.strata_json,
                   audit_rate = excluded.audit_rate,
                   quality_floor = excluded.quality_floor,
                   floor_window = excluded.floor_window,
                   daily_audit_budget = excluded.daily_audit_budget,
                   updated_at = excluded.updated_at""",
            (
                system_id, cell_definition_id, float(sample_rate_v),
                json.dumps(strata_v), float(audit_rate_v), float(quality_floor_v),
                int(floor_window_v), int(daily_audit_budget_v), created_at, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cell_quality_configs WHERE system_id = ? AND cell_definition_id = ?",
            (system_id, cell_definition_id),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _config_out(row)


def _ensure_config_row(conn, system_id: int, cell_definition_id: int):
    """Like ``upsert_quality_config`` but returns the raw row (needed
    internally to get ``config_id``), and is a no-op INSERT-if-missing when
    a config already exists."""
    row = _get_config_row(conn, system_id, cell_definition_id)
    if row is not None:
        return row
    upsert_quality_config(conn, system_id=system_id, cell_definition_id=cell_definition_id)
    return _get_config_row(conn, system_id, cell_definition_id)


# ---------------------------------------------------------------------------
# Deterministic stratified sample selection (Principle 6)
# ---------------------------------------------------------------------------


def selection_seed(system_id: int, cell_definition_id: int, trace_id: str) -> str:
    """The stable-hash input string -- public so tests can independently
    recompute the exact same fraction the selector used."""
    return f"{system_id}:{cell_definition_id}:{trace_id}"


def selection_fraction(system_id: int, cell_definition_id: int, trace_id: str) -> float:
    seed = selection_seed(system_id, cell_definition_id, trace_id)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest, 16) / float(1 << 256)


def _match_stratum(strata: List[Dict[str, Any]], trace_row) -> str:
    """Match deterministic task/risk dimensions captured in trace input.

    SDK trace inputs are JSON and may carry ``task_type``, ``risk`` and
    ``rare`` either at the top level or under a ``metadata`` object. The
    special risk ``error`` also matches a recorded trace error. ``rare`` is
    primarily the minimum-sample guarantee; for a rare-only stratum it is
    also the explicit trace dimension. First matching stratum wins.
    """
    try:
        payload = json.loads(trace_row["input_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    task_type = payload.get("task_type", metadata.get("task_type"))
    risk = payload.get("risk", metadata.get("risk"))
    is_rare = bool(payload.get("rare", metadata.get("rare", False)))

    for stratum in strata:
        expected_task_type = stratum.get("task_type")
        expected_risk = stratum.get("risk")
        if expected_task_type is not None and task_type != expected_task_type:
            continue
        if expected_risk is not None:
            risk_matches = risk == expected_risk
            if expected_risk == "error" and trace_row["error"]:
                risk_matches = True
            if not risk_matches:
                continue
        if (
            expected_task_type is None
            and expected_risk is None
            and stratum.get("rare")
            and not is_rare
        ):
            continue
        return stratum["name"]
    return ""


def select_samples(
    conn,
    *,
    system_id: int,
    cell_definition_id: int,
    window_seconds: Optional[float] = None,
):
    """Deterministic stratified selection over this Cell's traces (the
    worker Cell's ``cell_id`` is its ``component_id``). Idempotent: running
    this twice over the same window never inserts a duplicate row and
    always returns the same sample set."""
    cell_row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
    config_row = _ensure_config_row(conn, system_id, cell_definition_id)
    sample_rate = config_row["sample_rate"]
    strata = json.loads(config_row["strata_json"] or "[]")

    query = (
        "SELECT trace_id, error, input_json, timestamp FROM traces "
        "WHERE system_id = ? AND component_id = ?"
    )
    params: List[Any] = [system_id, cell_row["cell_id"]]
    if window_seconds is not None:
        query += " AND timestamp >= ?"
        params.append(time.time() - window_seconds)
    query += " ORDER BY trace_id ASC"
    trace_rows = conn.execute(query, params).fetchall()

    stratum_by_trace: Dict[str, str] = {}
    fraction_by_trace: Dict[str, float] = {}
    traces_by_stratum: Dict[str, List[str]] = {}
    selected: set = set()

    for t in trace_rows:
        trace_id = t["trace_id"]
        stratum_name = _match_stratum(strata, t)
        stratum_by_trace[trace_id] = stratum_name
        fraction = selection_fraction(system_id, cell_definition_id, trace_id)
        fraction_by_trace[trace_id] = fraction
        traces_by_stratum.setdefault(stratum_name, []).append(trace_id)
        if fraction < sample_rate:
            selected.add(trace_id)

    for stratum in strata:
        if not stratum.get("rare"):
            continue
        candidates = traces_by_stratum.get(stratum["name"], [])
        if candidates and not (selected & set(candidates)):
            best = min(candidates, key=lambda tid: fraction_by_trace[tid])
            selected.add(best)

    now = time.time()
    conn.execute("BEGIN")
    try:
        for trace_id in selected:
            conn.execute(
                """INSERT OR IGNORE INTO cell_quality_samples
                       (system_id, cell_definition_id, config_id, stratum,
                        target_kind, target_id, selection_seed, selected_at)
                   VALUES (?, ?, ?, ?, 'trace', ?, ?, ?)""",
                (
                    system_id, cell_definition_id, config_row["id"],
                    stratum_by_trace[trace_id], trace_id,
                    selection_seed(system_id, cell_definition_id, trace_id), now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if not selected:
        return []
    placeholders = ",".join("?" for _ in selected)
    return conn.execute(
        f"""SELECT * FROM cell_quality_samples
            WHERE system_id = ? AND cell_definition_id = ? AND target_kind = 'trace'
              AND target_id IN ({placeholders})
            ORDER BY id""",
        (system_id, cell_definition_id, *selected),
    ).fetchall()


def list_quality_samples(conn, system_id: int, cell_definition_id: int):
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    return conn.execute(
        """SELECT * FROM cell_quality_samples
           WHERE system_id = ? AND cell_definition_id = ?
           ORDER BY id DESC""",
        (system_id, cell_definition_id),
    ).fetchall()


# ---------------------------------------------------------------------------
# Daily audit budget (System-scoped, mirrors resource_limits' daily pattern)
# ---------------------------------------------------------------------------


def _utc_day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(
        now if now is not None else time.time(), timezone.utc
    ).date().isoformat()


def _consume_quality_audit_budget(conn, system_id: int, budget: int) -> None:
    """Deterministic gate (Principle 6): raises :class:`ConflictError`
    fail-closed WITHOUT performing the audit when the System's shared daily
    counter has already reached the audited Cell's ``daily_audit_budget``."""
    day = _utc_day()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT audits_used FROM cell_quality_usage WHERE system_id = ? AND day = ?",
            (system_id, day),
        ).fetchone()
        used = row["audits_used"] if row is not None else 0
        if used >= budget:
            conn.execute("ROLLBACK")
            raise ConflictError(
                f"Daily quality-audit budget ({budget}) exhausted for this System"
            )
        used += 1
        conn.execute(
            """INSERT INTO cell_quality_usage (system_id, day, audits_used)
                   VALUES (?, ?, ?)
               ON CONFLICT(system_id, day) DO UPDATE SET
                   audits_used = excluded.audits_used""",
            (system_id, day, used),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Deterministic verdict (Principle 6) -- shared by blind and non-blind audits
# ---------------------------------------------------------------------------


def _compute_verdict(conn, *, system_id: int, cell_id: str, trace_row) -> Tuple[str, List[int]]:
    """Never reads ``cell_quality_audits`` -- only the sampled trace and the
    component's ``evaluation_criteria`` -- so a blind re-audit provably
    cannot be influenced by a prior audit row."""
    criteria_rows = conn.execute(
        """SELECT * FROM evaluation_criteria
           WHERE system_id = ? AND component_id = ? AND enabled = 1
           ORDER BY id""",
        (system_id, cell_id),
    ).fetchall()
    deterministic_rows = [c for c in criteria_rows if c["criterion_type"] != "natural_language"]
    if not deterministic_rows:
        return "no_criteria", []
    failed_ids: List[int] = []
    for c in deterministic_rows:
        status, _score, _reason = evaluator.evaluate(
            c["criterion_type"], c["expected_value"], trace_row["output_text"],
        )
        if status != "ok":
            failed_ids.append(c["id"])
    return ("fail" if failed_ids else "pass"), failed_ids


def _verbatim_example(trace_row, max_len: int = 1000) -> str:
    input_text = (trace_row["input_json"] or "")[:max_len]
    output_text = (trace_row["output_text"] or "")[:max_len]
    return f"input: {input_text}\noutput: {output_text}"


# ---------------------------------------------------------------------------
# Reasoning explanation (fail-closed; only attempted for a 'fail' verdict)
# ---------------------------------------------------------------------------

_EXPLANATION_SYSTEM_PROMPT = """\
You are explaining a DETERMINISTIC quality-audit failure of a Probe Cell's \
sampled output against its evaluation criteria. The verdict and failed \
criteria are already decided -- your only job is to describe, in plain \
language grounded only in the evidence given, why it likely failed and what \
pattern this might indicate. Never invent facts not present below."""

_EXPLANATION_PROMPT_TEMPLATE = """\
CELL_QUALITY_AUDIT_RESPONSE_JSON

Cell: {cell_id}
Failed criterion ids: {failed_criteria}
Verbatim sampled excerpt:
{verbatim_example}

Respond with ONLY valid JSON:
{{
  "explanation": "string grounded only in the evidence above"
}}"""


def _parse_explanation_response(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValidationFailedError("Explanation response must be a JSON object")
    explanation = str(data.get("explanation", "")).strip()
    if not explanation:
        raise ValidationFailedError("explanation is required")
    return explanation


# ---------------------------------------------------------------------------
# run_audit: 3-phase (read / optional LLM with NO conn held / write)
# ---------------------------------------------------------------------------


def run_audit(
    *,
    system_id: int,
    sample_id: int,
    blind: bool = False,
    auditor_alias: Optional[str] = None,
):
    """Run one audit of ``sample_id``. Returns
    ``(audit_row, intelligence_run_id_or_None, is_mock, error_or_None)``.

    Budget is consumed BEFORE anything else runs: an exhausted daily budget
    raises :class:`ConflictError` and no audit (deterministic or otherwise)
    is performed. The deterministic verdict never depends on ``blind`` --
    the same ``_compute_verdict`` path runs either way, and it never queries
    ``cell_quality_audits``. Manages its own connections: the LLM call (only
    attempted for a 'fail' verdict) happens with NO ``get_conn()`` held.
    """
    auditor_alias = (auditor_alias or DEFAULT_AUDITOR_ALIAS).strip() or DEFAULT_AUDITOR_ALIAS

    with get_conn() as conn:
        sample_row = _get_sample_row(conn, system_id, sample_id)
        cell_definition_id = sample_row["cell_definition_id"]
        cell_row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
        config_row = _ensure_config_row(conn, system_id, cell_definition_id)

        # Auditor independence (#302): the auditor model must be SEPARATE from
        # the worker's own execution model. Alias names are configuration
        # indirections, so compare their resolved provider/model identities;
        # two different aliases may point at the same model.
        worker_alias = _worker_model_alias(conn, cell_row)
        try:
            auditor_config = cell_fabric.resolve_model_alias(auditor_alias)
            worker_config = (
                cell_fabric.resolve_model_alias(worker_alias)
                if worker_alias is not None else None
            )
        except cell_fabric.CellContractError as exc:
            raise ValidationFailedError(str(exc))
        if worker_config is not None and (
            auditor_config.provider,
            auditor_config.model,
            auditor_config.base_url,
        ) == (
            worker_config.provider,
            worker_config.model,
            worker_config.base_url,
        ):
            raise ValidationFailedError(
                f"auditor_alias {auditor_alias!r} resolves to the audited "
                "Cell's worker model; an audit requires a separate model"
            )

        _consume_quality_audit_budget(conn, system_id, config_row["daily_audit_budget"])

        trace_row = conn.execute(
            "SELECT * FROM traces WHERE system_id = ? AND trace_id = ?",
            (system_id, sample_row["target_id"]),
        ).fetchone()
        if trace_row is None:
            raise NotFoundError("Sampled trace no longer exists in this System")

        verdict, failed_ids = _compute_verdict(
            conn, system_id=system_id, cell_id=cell_row["cell_id"], trace_row=trace_row,
        )
        verbatim = _verbatim_example(trace_row)

    llm_config = auditor_config
    is_mock = llm_config.provider == "mock"
    attempted_llm = verdict == "fail"
    explanation = ""
    error: Optional[str] = None
    started_at = time.time()
    completed_at = started_at

    if attempted_llm:
        try:
            if llm_config.provider != "mock" and not is_reasoning_model(
                llm_config.provider, llm_config.model
            ):
                raise LLMError(
                    "Cell quality audit explanation requires a configured reasoning model"
                )
            llm_client = create_llm_client(llm_config)
            prompt = _EXPLANATION_PROMPT_TEMPLATE.format(
                cell_id=cell_row["cell_id"],
                failed_criteria=json.dumps(failed_ids),
                verbatim_example=verbatim,
            )
            raw = llm_client.generate_text(
                [
                    {"role": "system", "content": _EXPLANATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            explanation = _parse_explanation_response(raw)
        except (LLMError, LLMResourceLimitError, ValidationFailedError, json.JSONDecodeError) as exc:
            error = str(exc)
        completed_at = time.time()

    run_status = "completed" if error is None else "failed"

    with get_conn() as conn:
        now = time.time()
        conn.execute("BEGIN")
        try:
            run_id = None
            if attempted_llm:
                cur = conn.execute(
                    """INSERT INTO intelligence_runs
                           (system_id, snapshot_id, run_type, provider, model,
                            prompt_version, schema_version, decision_method,
                            status, error_details, is_mock, started_at, completed_at)
                       VALUES (?, NULL, 'cell_quality_audit', ?, ?, ?, ?, 'reasoning_llm',
                               ?, ?, ?, ?, ?)""",
                    (
                        system_id, llm_config.provider, llm_config.model,
                        CELL_QUALITY_AUDIT_PROMPT_VERSION, CELL_QUALITY_AUDIT_SCHEMA_VERSION,
                        run_status, error, 1 if is_mock else 0, started_at, completed_at,
                    ),
                )
                run_id = cur.lastrowid
            cur = conn.execute(
                """INSERT INTO cell_quality_audits
                       (system_id, sample_id, auditor_model_alias, verdict,
                        verdict_decision_method, is_blind, failed_criteria_json,
                        verbatim_example, explanation, explanation_run_id, created_at)
                   VALUES (?, ?, ?, ?, 'deterministic', ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, sample_id, auditor_alias, verdict,
                    1 if blind else 0, json.dumps(failed_ids), verbatim,
                    explanation, run_id, now,
                ),
            )
            audit_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM cell_quality_audits WHERE id = ?", (audit_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return row, run_id, is_mock, error


def run_parent_blind_audits(
    *,
    system_id: int,
    cell_definition_id: int,
    auditor_alias: Optional[str] = None,
):
    """Blind-re-audit ``floor(audit_rate * sample_count)`` of a Cell's
    samples, most-recent-first (a fixed, reproducible tie-break by
    ``id DESC`` -- newer samples have larger autoincrement ids)."""
    with get_conn() as conn:
        _get_cell_row_by_id(conn, system_id, cell_definition_id)
        config_row = _ensure_config_row(conn, system_id, cell_definition_id)
        audit_rate = config_row["audit_rate"]
        sample_rows = conn.execute(
            """SELECT id FROM cell_quality_samples
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY id DESC""",
            (system_id, cell_definition_id),
        ).fetchall()

    n = math.floor(audit_rate * len(sample_rows))
    chosen = sample_rows[:n]
    return [
        run_audit(
            system_id=system_id, sample_id=row["id"], blind=True,
            auditor_alias=auditor_alias,
        )
        for row in chosen
    ]


def list_quality_audits(conn, system_id: int, cell_definition_id: int):
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    rows = conn.execute(
        """SELECT a.* FROM cell_quality_audits a
           JOIN cell_quality_samples s ON a.sample_id = s.id
           WHERE a.system_id = ? AND s.cell_definition_id = ?
           ORDER BY a.id DESC""",
        (system_id, cell_definition_id),
    ).fetchall()
    counts = {"pass": 0, "fail": 0, "no_criteria": 0}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return rows, counts


# ---------------------------------------------------------------------------
# Quality floor (rolling pass rate) and intake suspend/resume
# ---------------------------------------------------------------------------


def _rolling_pass_rate(
    conn, system_id: int, cell_definition_id: int, floor_window: int,
) -> Tuple[Optional[float], int]:
    """Rolling pass rate over the last ``floor_window`` DISTINCT samples,
    counting each sample once by its most recent audit. Deduping by sample
    closes the "re-audit a passing sample repeatedly to inflate the rolling
    window and resume intake" gap: repeating an audit of the same sample can
    never add more than that one sample's latest verdict to the window."""
    rows = conn.execute(
        """SELECT s.id AS sample_id, a.verdict, a.id AS audit_id
           FROM cell_quality_audits a
           JOIN cell_quality_samples s ON a.sample_id = s.id
           WHERE a.system_id = ? AND s.cell_definition_id = ?
           ORDER BY a.id DESC""",
        (system_id, cell_definition_id),
    ).fetchall()
    # Keep the latest audit per sample (rows are newest-first, so the first
    # occurrence of each sample_id is its most recent audit).
    latest_by_sample: Dict[int, str] = {}
    for r in rows:
        if r["sample_id"] not in latest_by_sample:
            latest_by_sample[r["sample_id"]] = r["verdict"]
        if len(latest_by_sample) >= floor_window:
            break
    considered = [v for v in latest_by_sample.values() if v != "no_criteria"]
    denominator = len(considered)
    if denominator == 0:
        return None, 0
    passed = sum(1 for v in considered if v == "pass")
    return passed / denominator, denominator


def get_intake_state(conn, system_id: int, cell_definition_id: int) -> Dict[str, Any]:
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    row = conn.execute(
        "SELECT * FROM cell_intake_states WHERE system_id = ? AND cell_definition_id = ?",
        (system_id, cell_definition_id),
    ).fetchone()
    if row is None:
        return {
            "system_id": system_id,
            "cell_definition_id": cell_definition_id,
            "intake_status": "accepting",
            "reason": "",
            "escalation_id": None,
            "changed_at": None,
        }
    return dict(row)


def _set_intake_state(
    conn, *, system_id: int, cell_definition_id: int, status: str,
    reason: str = "", escalation_id: Optional[int] = None,
) -> None:
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT INTO cell_intake_states
                   (system_id, cell_definition_id, intake_status, reason,
                    escalation_id, changed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(system_id, cell_definition_id) DO UPDATE SET
                   intake_status = excluded.intake_status,
                   reason = excluded.reason,
                   escalation_id = excluded.escalation_id,
                   changed_at = excluded.changed_at""",
            (system_id, cell_definition_id, status, reason, escalation_id, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def evaluate_quality_floor(conn, *, system_id: int, cell_definition_id: int) -> Dict[str, Any]:
    """Rolling pass-rate check over the last ``floor_window`` audited
    samples. Breaching the floor with at least ``MIN_FLOOR_SAMPLE_COUNT``
    considered audits suspends ONLY this Cell's intake and raises a sev1
    escalation through the EXISTING ``cell_tasks.submit_report`` path
    (no nested ``get_conn()`` -- both steps reuse this same open ``conn``,
    each in its own short transaction)."""
    cell_row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
    config_row = _ensure_config_row(conn, system_id, cell_definition_id)
    floor_window = config_row["floor_window"]
    quality_floor = config_row["quality_floor"]

    pass_rate, denominator = _rolling_pass_rate(
        conn, system_id, cell_definition_id, floor_window,
    )

    suspended = False
    escalation_id = None
    if (
        denominator >= MIN_FLOOR_SAMPLE_COUNT
        and pass_rate is not None
        and pass_rate < quality_floor
    ):
        reason = (
            f"quality floor breached: pass_rate={pass_rate:.4f} < "
            f"floor={quality_floor} (n={denominator})"
        )
        _set_intake_state(
            conn, system_id=system_id, cell_definition_id=cell_definition_id,
            status="suspended", reason=reason,
        )
        report_row = cell_tasks.submit_report(
            conn,
            system_id=system_id,
            cell_definition_id=cell_row["cell_id"],
            kind="escalation",
            severity="sev1",
            fact=[{"text": reason, "evidence_refs": []}],
        )
        escalation_id = cell_tasks.get_report_escalation_id(conn, report_row["id"])
        _set_intake_state(
            conn, system_id=system_id, cell_definition_id=cell_definition_id,
            status="suspended", reason=reason, escalation_id=escalation_id,
        )
        suspended = True

    return {
        "pass_rate": pass_rate,
        "floor": quality_floor,
        "denominator": denominator,
        "suspended": suspended,
        "escalation_id": escalation_id,
    }


def resume_intake(
    conn, *, system_id: int, cell_definition_id: int, reason: str = "",
) -> Dict[str, Any]:
    """Explicit, manual recovery. Refuses (409) unless the recomputed
    rolling pass rate is at or above the floor."""
    _get_cell_row_by_id(conn, system_id, cell_definition_id)
    config_row = _ensure_config_row(conn, system_id, cell_definition_id)
    floor_window = config_row["floor_window"]
    quality_floor = config_row["quality_floor"]

    pass_rate, _denominator = _rolling_pass_rate(
        conn, system_id, cell_definition_id, floor_window,
    )
    if pass_rate is None or pass_rate < quality_floor:
        raise ConflictError(
            f"Cannot resume intake: recomputed pass_rate="
            f"{pass_rate!r} is still below floor={quality_floor}"
        )
    _set_intake_state(
        conn, system_id=system_id, cell_definition_id=cell_definition_id,
        status="accepting", reason=reason or "", escalation_id=None,
    )
    return get_intake_state(conn, system_id, cell_definition_id)


# ---------------------------------------------------------------------------
# cell_state.quality block (Sub 1's CellQuality; filled here when data exists)
# ---------------------------------------------------------------------------


def build_cell_quality_state(
    conn, *, system_id: int, cell_definition_id: int,
) -> Optional["cell_fabric.CellQuality"]:
    """Returns ``None`` when no quality-sampling data exists yet for this
    Cell (no config saved and no audits recorded) -- never a guessed
    ``CellQuality`` block."""
    config_row = _get_config_row(conn, system_id, cell_definition_id)
    audited_count = conn.execute(
        """SELECT COUNT(*) AS n FROM cell_quality_audits a
           JOIN cell_quality_samples s ON a.sample_id = s.id
           WHERE a.system_id = ? AND s.cell_definition_id = ?""",
        (system_id, cell_definition_id),
    ).fetchone()["n"]
    if config_row is None and audited_count == 0:
        return None

    floor_window = config_row["floor_window"] if config_row is not None else DEFAULT_FLOOR_WINDOW
    pass_rate, _denominator = _rolling_pass_rate(
        conn, system_id, cell_definition_id, floor_window,
    )
    intake_row = conn.execute(
        "SELECT intake_status FROM cell_intake_states WHERE system_id = ? AND cell_definition_id = ?",
        (system_id, cell_definition_id),
    ).fetchone()
    intake_status = intake_row["intake_status"] if intake_row is not None else "accepting"
    sample_rate = config_row["sample_rate"] if config_row is not None else None

    return cell_fabric.CellQuality(
        pass_rate=pass_rate,
        audited_count=audited_count,
        intake_status=intake_status,
        sample_rate=sample_rate,
    )
