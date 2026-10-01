"""Per-ask measurement for ``POST /assistant/ask`` (Issue #468, Epic #467).

Canonical contract: docs/01-specifications/capabilities/assistant-answer-quality.md §1.

What a record holds: timings, counts, sizes, finite codes, and source
ids/revisions/digests.  It NEVER holds the question, the answer, an unsaved
draft's values, a prompt, or a provider response (§0).  A metrics failure must
never break the answer, so ``persist`` swallows and logs everything.

``persist`` opens its own ``get_conn()``; call it only when no connection is
held by the caller.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, get_args

from .models import AskOutcome, AskStage, AssistantFailureClass

logger = logging.getLogger(__name__)

# Finite vocabularies, mirrored from the `Literal` aliases in `app/models.py`.
ASK_STAGES = get_args(AskStage)
ASK_OUTCOMES = get_args(AskOutcome)
ASSISTANT_FAILURE_CLASSES = get_args(AssistantFailureClass)
COUNTER_NAMES = ("diagnostics_runs", "system_state_runs", "llm_calls", "llm_retries")
ESTIMATE_METHOD = "chars_div_4"
DEFAULT_RETENTION_DAYS = 30

# LLMError.kind -> AssistantFailureClass (§5.1).
_LLM_KIND_TO_FAILURE_CLASS = {
    "timeout": "timeout",
    "network": "network",
    "auth": "auth",
    "rate_limited": "rate_limited",
    "provider_error": "provider_error",
    "malformed_response": "malformed_output",
    "config": "provider_not_configured",
}

_TARGET_STATE_TO_FRESHNESS = {
    "current": "current",
    "stale": "stale",
    "not_tracked": "not_tracked",
}


def failure_class_for(
    *,
    error_kind: Optional[str] = None,
    malformed_output: bool = False,
    finish_reason: Optional[str] = None,
    client_available: bool = True,
    provider: Optional[str] = None,
) -> Optional[str]:
    """Map what is known about a failed answer to one finite failure class.

    Pure and finite (Principle 6).  Precedence: no usable client, then
    malformed/truncated assistant output, then the provider's own error kind.
    Returns None when nothing indicates a failure.
    """
    if not client_available:
        return "provider_test_only" if provider == "mock" else "provider_not_configured"
    if malformed_output:
        return "truncated_output" if finish_reason == "length" else "malformed_output"
    if error_kind is not None:
        return _LLM_KIND_TO_FAILURE_CLASS.get(error_kind, "provider_error")
    return None


class AskTrace:
    """Request-scoped measurement; one per ask."""

    def __init__(self) -> None:
        self.request_id: str = uuid.uuid4().hex
        self.started_at: float = time.time()
        self._t0 = time.perf_counter()
        self._stages: Dict[str, Dict[str, float]] = {}
        self.counters: Dict[str, int] = {name: 0 for name in COUNTER_NAMES}
        self.input_sizes: Dict[str, int] = {}
        self.usage: Dict[str, Any] = {
            "input_tokens": None, "output_tokens": None, "source": "not_reported",
        }
        self.estimated_input_tokens: Optional[int] = None
        self.estimate_method: str = ESTIMATE_METHOD
        self.output_chars: Optional[int] = None
        self.finish_reason: Optional[str] = None
        self.manifest: Dict[str, Any] = {
            "sources": [], "coverage": [], "omitted_sections": [],
            "history_turns": 0, "ui_draft_state": "not_provided", "budget": None,
        }
        self.thread_target: Optional[Dict[str, Any]] = None
        self.outcome: Optional[str] = None
        self.failure_class: Optional[str] = None
        # Issue #473: which LLM config / limits this ask ran with.  Finite
        # codes and numbers only (never a key, URL, or model text).
        self.request_config: Dict[str, Any] = {}
        # Row identity fields the route learns along the way (ids / model
        # labels only -- never text).
        self.meta: Dict[str, Any] = {}

    def elapsed_seconds(self) -> float:
        """Seconds since this ask began (monotonic)."""
        return time.perf_counter() - self._t0

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in ASK_STAGES:
            raise ValueError(f"unknown ask stage: {name!r}")
        started = time.perf_counter()
        try:
            yield
        finally:
            entry = self._stages.setdefault(name, {"ms": 0.0, "count": 0})
            entry["ms"] += (time.perf_counter() - started) * 1000.0
            entry["count"] += 1

    @property
    def stage_timings(self) -> Dict[str, Dict[str, Any]]:
        return {k: {"ms": round(v["ms"], 3), "count": int(v["count"])} for k, v in self._stages.items()}

    def merge_scope_counters(self, scope: Any) -> None:
        self.counters["diagnostics_runs"] = int(getattr(scope, "diagnostics_runs", 0))
        self.counters["system_state_runs"] = int(getattr(scope, "system_state_runs", 0))

    def set_thread_target(self, *, revision_id: Optional[int], digest: str, target_state: Optional[str]) -> None:
        self.thread_target = {
            "revision_id": revision_id, "digest": digest or None, "target_state": target_state,
        }

    def record_pack(self, pack: Any) -> None:
        """Fill the manifest's `sources` from the ContextPack (ids only)."""
        sources: List[Dict[str, Any]] = []

        def add(stype: str, sid: str, *, revision: Any = None, digest: Any = None, freshness: str = "unknown") -> None:
            sources.append({
                "type": stype, "id": str(sid),
                "revision": None if revision is None else str(revision),
                "digest": digest, "freshness": freshness,
            })

        for s in getattr(pack, "settings", []) or []:
            add("setting", s.key)
        for c in getattr(pack, "checks", []) or []:
            add("diagnostic_check", c.check_id)
        for step in getattr(pack, "pipeline_steps", []) or []:
            add("pipeline_step", step)
        for item in getattr(pack, "state_items", []) or []:
            add("state_item", item.state_id)
        for src in getattr(pack, "screen_data_sources", []) or []:
            sid = src.get("id", "")
            if sid.startswith("discussion_target:") and self.thread_target:
                add(
                    "screen_data", sid,
                    revision=self.thread_target.get("revision_id"),
                    digest=self.thread_target.get("digest"),
                    freshness=_TARGET_STATE_TO_FRESHNESS.get(
                        self.thread_target.get("target_state") or "", "unknown"
                    ),
                )
            else:
                add("screen_data", sid)
        for src in getattr(pack, "ui_draft_sources", []) or []:
            add("ui_draft", src.get("id", ""))
        for entry in getattr(pack, "related_context", []) or []:
            add(
                "related_context", entry.get("source_id", ""),
                revision=entry.get("revision_id"), digest=entry.get("digest") or None,
                freshness=entry.get("freshness", "unknown"),
            )
        self.manifest["sources"] = sources
        # Issue #470: what the model was and was not given (codes/counts only).
        self.manifest["coverage"] = [dict(c) for c in getattr(pack, "coverage", []) or []]
        self.manifest["omitted_sections"] = [
            dict(o) for o in getattr(pack, "omitted_sections", []) or []
        ]
        budget = getattr(pack, "budget_info", None)
        if budget is not None:
            self.manifest["budget"] = {
                "limit_chars": budget.get("limit_chars"),
                "used_chars": budget.get("used_chars"),
                "over_budget": bool(budget.get("over_budget")),
            }
        self.manifest["history_turns"] = len(getattr(pack, "conversation", []) or [])


# --- persistence -------------------------------------------------------------


def _retention_days() -> int:
    raw = os.environ.get("ASSISTANT_METRIC_RETENTION_DAYS", "")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def persist(
    trace: AskTrace,
    *,
    system_id: int,
    screen_id: str,
    thread_id: Optional[int],
    assistant_turn_id: Optional[int],
    input_mode: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> bool:
    """Write one `assistant_ask_metric` row.  Never raises."""
    try:
        from .db import get_conn

        outcome = trace.outcome if trace.outcome in ASK_OUTCOMES else "error"
        failure_class = trace.failure_class if trace.failure_class in ASSISTANT_FAILURE_CLASSES else None
        now = time.time()
        usage = dict(trace.usage)
        usage["estimated_input_tokens"] = trace.estimated_input_tokens
        usage["estimate_method"] = trace.estimate_method
        usage["output_chars"] = trace.output_chars
        usage["finish_reason"] = trace.finish_reason
        usage.update(trace.request_config)
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM assistant_ask_metric WHERE system_id = ? AND created_at < ?",
                (system_id, now - _retention_days() * 86400.0),
            )
            conn.execute(
                """
                INSERT INTO assistant_ask_metric (
                    system_id, request_id, screen_id, thread_id, assistant_turn_id,
                    input_mode, started_at, finished_at, outcome, failure_class,
                    provider, model, prompt_version, schema_version,
                    stage_timings_json, counters_json, input_sizes_json, usage_json,
                    manifest_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    system_id, trace.request_id, screen_id, thread_id, assistant_turn_id,
                    input_mode if input_mode in ("text", "voice") else "text",
                    trace.started_at, now, outcome, failure_class,
                    provider or "", model or "", prompt_version or "", schema_version or "",
                    json.dumps(trace.stage_timings), json.dumps(trace.counters),
                    json.dumps(trace.input_sizes), json.dumps(usage),
                    json.dumps(trace.manifest, ensure_ascii=False), now,
                ),
            )
        return True
    except Exception:  # noqa: BLE001 - a metrics failure must never break the answer
        logger.exception("assistant_ask_metric persist failed")
        return False


def row_to_out(row: Any) -> Dict[str, Any]:
    def _load(text: Any, default: Any) -> Any:
        try:
            return json.loads(text) if text else default
        except (TypeError, ValueError):
            return default

    return {
        "request_id": row["request_id"],
        "screen_id": row["screen_id"],
        "thread_id": row["thread_id"],
        "assistant_turn_id": row["assistant_turn_id"],
        "input_mode": row["input_mode"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "outcome": row["outcome"],
        "failure_class": row["failure_class"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "schema_version": row["schema_version"],
        "stage_timings": _load(row["stage_timings_json"], {}),
        "counters": _load(row["counters_json"], {}),
        "input_sizes": _load(row["input_sizes_json"], {}),
        "usage": _load(row["usage_json"], {}),
        "manifest": _load(row["manifest_json"], {}),
        "client_timing": _load(row["client_timing_json"], None),
        "created_at": row["created_at"],
    }


# --- summary -----------------------------------------------------------------


def nearest_rank(samples: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile: always an observed value; None if no samples."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def percentile_summary(samples: Sequence[float], *, not_run_count: int = 0) -> Dict[str, Any]:
    if not samples:
        return {"p50": None, "p95": None, "sample_count": 0,
                "not_run_count": not_run_count, "state": "unmeasured"}
    return {
        "p50": nearest_rank(samples, 0.50), "p95": nearest_rank(samples, 0.95),
        "sample_count": len(samples), "not_run_count": not_run_count, "state": "measured",
    }


def summarize(rows: Sequence[Dict[str, Any]], window_hours: int) -> Dict[str, Any]:
    """Aggregate `row_to_out` dicts into the §1.5 summary."""
    total = len(rows)
    stages: Dict[str, Dict[str, Any]] = {}
    for stage in ASK_STAGES:
        samples = [
            float(r["stage_timings"][stage]["ms"]) for r in rows if stage in r["stage_timings"]
        ]
        stages[stage] = percentile_summary(samples, not_run_count=total - len(samples))

    def client_samples(key: str) -> List[float]:
        out = []
        for r in rows:
            ct = r.get("client_timing") or {}
            if isinstance(ct.get(key), (int, float)):
                out.append(float(ct[key]))
        return out

    first = client_samples("first_visible_ms")
    complete = client_samples("complete_ms")

    outcome_counts = {o: 0 for o in ASK_OUTCOMES}
    failure_counts: Dict[str, int] = {}
    for r in rows:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1
        if r["failure_class"]:
            failure_counts[r["failure_class"]] = failure_counts.get(r["failure_class"], 0) + 1

    # `rejected` is a caller's 4xx, not a system failure, so it is not in the
    # denominator; failure = `failed` + `error`.
    denominator = total - outcome_counts.get("rejected", 0)
    numerator = outcome_counts.get("failed", 0) + outcome_counts.get("error", 0)
    failure_rate = {
        "numerator": numerator, "denominator": denominator,
        "value": (numerator / denominator) if denominator else None,
        "state": "measured" if denominator else "unmeasured",
    }

    reported = [r for r in rows if (r["usage"] or {}).get("source") == "provider_reported"]
    in_vals = [r["usage"]["input_tokens"] for r in reported if isinstance(r["usage"].get("input_tokens"), int)]
    out_vals = [r["usage"]["output_tokens"] for r in reported if isinstance(r["usage"].get("output_tokens"), int)]
    provider_tokens = {
        "input_tokens": sum(in_vals) if in_vals else None,
        "output_tokens": sum(out_vals) if out_vals else None,
        "sample_count": len(reported),
        "state": "measured" if reported else "unmeasured",
    }
    est_vals = [
        r["usage"]["estimated_input_tokens"] for r in rows
        if isinstance((r["usage"] or {}).get("estimated_input_tokens"), int)
    ]
    estimated = {
        "estimated_input_tokens": sum(est_vals) if est_vals else None,
        "estimate_method": ESTIMATE_METHOD,
        "sample_count": len(est_vals),
        "state": "measured" if est_vals else "unmeasured",
    }
    return {
        "window_hours": window_hours,
        "total_requests": total,
        "stages": stages,
        "client_first_visible_ms": percentile_summary(first, not_run_count=total - len(first)),
        "client_complete_ms": percentile_summary(complete, not_run_count=total - len(complete)),
        "outcome_counts": outcome_counts,
        "failure_class_counts": failure_counts,
        "failure_rate": failure_rate,
        "llm_calls_total": sum(int((r["counters"] or {}).get("llm_calls", 0)) for r in rows),
        "provider_reported_tokens": provider_tokens,
        "estimated_tokens": estimated,
    }
