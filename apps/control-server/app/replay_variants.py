"""Baseline-replay vs candidate-replay comparison (Issue #242 Phase C / #245).

probe-agent:
  role: Deterministic finite classification of a patched variant's replay
    output against the SAME run's baseline replay output
  capability: replay-simulation
  element_type: core
  consumers: [control-server]
  operation_kind: pure
  state_effects: []
  probe_value: Verify variant diff classification stays a small explicit
    finite set and stays deterministic across repeated/shuffled runs.

Everything here is deterministic (Principle 6): a small explicit finite
``case_status`` set plus field-level equality reused from
``app/comparison.py``. No reasoning model is involved.

IMPORTANT — what "baseline" means here: the BASELINE REPLAY, i.e. the SAME
unpatched snapshot re-executed in this run's own isolated worktree, NOT the
originally recorded production trace. Phase B's ``replay_case_results``
(baseline-vs-recorded, keyed by the same ``replay_run_id``) stays the
authoritative record of the recorded comparison; this module only ever
compares the two harness executions performed in the SAME
``POST /replay-variant-runs`` call. Recorded-error traces are executed
against the baseline exactly like every other case (Phase B already does
this — ``build_case_plans`` does not special-case recorded status), so an
error trace's "did the candidate fix it" question is answerable even though
the live SDK shadow path (``decorator.py``'s ``if run_shadow and raised is
None``) never runs a candidate for a call that raised. That live-SDK
asymmetry is intentionally NOT changed (out of scope); this module is the
offline fix.

Case status (finite set, exactly 7 members — the full matrix):
  * ``match``                   -- both succeeded, outputs equal
  * ``diff``                    -- both succeeded, outputs differ
  * ``candidate_error``         -- candidate raised, baseline succeeded
  * ``error_to_success``        -- baseline raised, candidate succeeded
                                    (the "rescue" case)
  * ``error_to_same_error``     -- both raised, same error first line
  * ``error_to_different_error``-- both raised, different error first line
  * ``skipped``                 -- the case was never executed against
                                    either side (a server-side skip from
                                    ``build_case_plans``, e.g.
                                    ``unreplayable_capture`` /
                                    ``repr_parse_failed`` /
                                    ``undecodable_input`` / ``trace_missing``,
                                    or a harness-level skip such as
                                    ``undecodable_input`` /
                                    ``repr_parse_failed`` surfacing at
                                    execution time). Because baseline and
                                    every variant execute the exact same
                                    ``harness_cases`` list built once by
                                    ``replay_runner.build_case_plans``, a
                                    server-side skip is skipped identically
                                    for the baseline and for every variant —
                                    there is no way for one side to run a
                                    case the other side skipped.

``comparison_mode`` (nullable finite set, only meaningful for ``match`` /
``diff`` -- the two "both succeeded" outcomes):
  * ``structured`` -- both sides produced a ``structured_output`` (harness
    v2). Dict values are compared field-by-field over the union of top-level
    keys via ``comparison.diff_fields`` (missing-key vs null / NaN rules from
    Issue #150, reused verbatim); non-dict structured values are compared
    whole via ``comparison.value_equal``.
  * ``repr`` -- either side lacks a ``structured_output`` (harness omitted it
    because the value was not JSON-native), so comparison falls back to the
    Phase B repr-string equality.
  * ``None`` -- comparison_mode does not apply (candidate_error /
    error_to_success / error_to_same_error / error_to_different_error /
    skipped): those outcomes are decided by success/failure shape or by
    error-first-line text, not by an output-equality mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .comparison import diff_fields, value_equal

TRUNCATION_MARKER = "...<truncated>"

CASE_MATCH = "match"
CASE_DIFF = "diff"
CASE_CANDIDATE_ERROR = "candidate_error"
CASE_ERROR_TO_SUCCESS = "error_to_success"
CASE_ERROR_TO_SAME_ERROR = "error_to_same_error"
CASE_ERROR_TO_DIFFERENT_ERROR = "error_to_different_error"
CASE_SKIPPED = "skipped"

VARIANT_CASE_STATUSES = (
    CASE_MATCH,
    CASE_DIFF,
    CASE_CANDIDATE_ERROR,
    CASE_ERROR_TO_SUCCESS,
    CASE_ERROR_TO_SAME_ERROR,
    CASE_ERROR_TO_DIFFERENT_ERROR,
    CASE_SKIPPED,
)

COMPARISON_STRUCTURED = "structured"
COMPARISON_REPR = "repr"

# Apply-status finite set (replay_variants.apply_status).
APPLY_APPLIED = "applied"
APPLY_INVALID_PATCH = "invalid_patch"
APPLY_NOT_APPLICABLE = "not_applicable"  # baseline: no patch to apply

_NO_STRUCTURED = object()


def _error_first_line(error: Optional[str]) -> Optional[str]:
    if not error:
        return None
    lines = error.splitlines()
    return lines[0] if lines else ""


def _is_truncated(text: Optional[str]) -> bool:
    return bool(text and text.endswith(TRUNCATION_MARKER))


def classify_variant_case(
    trace_id: str,
    position: int,
    harness_case_planned: Optional[Dict[str, Any]],
    baseline_case: Optional[Dict[str, Any]],
    candidate_case: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Deterministic finite classification of one case's baseline vs
    candidate replay execution. See module docstring for the full matrix.

    ``harness_case_planned`` is the server-built ``CasePlan.harness_case``
    (``None`` means this case was skipped server-side before either harness
    ran, e.g. ``unreplayable_capture`` — never call the function on either
    side in that case). ``baseline_case`` / ``candidate_case`` are the raw
    per-case dicts the harness itself returned for that position.
    """
    result: Dict[str, Any] = {
        "trace_id": trace_id,
        "position": position,
        "case_status": CASE_SKIPPED,
        "comparison_mode": None,
        "baseline_output": None,
        "candidate_output": None,
        "candidate_error": None,
        "recorded_error": None,  # baseline REPLAY's own error first line
        "duration_ms": None,
        "duration_delta_ms": None,
        "field_diffs": [],
        "output_truncated": False,
    }
    if harness_case_planned is None or baseline_case is None or candidate_case is None:
        return result

    baseline_status = baseline_case.get("status")
    candidate_status = candidate_case.get("status")

    if baseline_status == "skipped" or candidate_status == "skipped":
        return result

    baseline_duration = baseline_case.get("duration_ms")
    candidate_duration = candidate_case.get("duration_ms")
    result["duration_ms"] = candidate_duration
    if baseline_duration is not None and candidate_duration is not None:
        result["duration_delta_ms"] = candidate_duration - baseline_duration

    if baseline_status == "ok" and candidate_status == "ok":
        b_out = baseline_case.get("output")
        c_out = candidate_case.get("output")
        result["baseline_output"] = b_out
        result["candidate_output"] = c_out
        result["output_truncated"] = _is_truncated(b_out) or _is_truncated(c_out)
        b_struct = baseline_case.get("structured_output", _NO_STRUCTURED)
        c_struct = candidate_case.get("structured_output", _NO_STRUCTURED)
        if b_struct is not _NO_STRUCTURED and c_struct is not _NO_STRUCTURED:
            result["comparison_mode"] = COMPARISON_STRUCTURED
            if isinstance(b_struct, dict) and isinstance(c_struct, dict):
                diffs = diff_fields(b_struct, c_struct)
                result["field_diffs"] = diffs
                result["case_status"] = CASE_MATCH if not diffs else CASE_DIFF
            else:
                equal = value_equal(b_struct, c_struct)
                result["case_status"] = CASE_MATCH if equal else CASE_DIFF
        else:
            result["comparison_mode"] = COMPARISON_REPR
            result["case_status"] = CASE_MATCH if b_out == c_out else CASE_DIFF
        return result

    if baseline_status == "ok" and candidate_status == "error":
        result["baseline_output"] = baseline_case.get("output")
        result["candidate_error"] = _error_first_line(candidate_case.get("error"))
        result["case_status"] = CASE_CANDIDATE_ERROR
        return result

    if baseline_status == "error" and candidate_status == "ok":
        result["recorded_error"] = _error_first_line(baseline_case.get("error"))
        result["candidate_output"] = candidate_case.get("output")
        result["case_status"] = CASE_ERROR_TO_SUCCESS
        return result

    if baseline_status == "error" and candidate_status == "error":
        b_err = _error_first_line(baseline_case.get("error")) or ""
        c_err = _error_first_line(candidate_case.get("error")) or ""
        result["recorded_error"] = b_err
        result["candidate_error"] = c_err
        result["case_status"] = (
            CASE_ERROR_TO_SAME_ERROR if b_err == c_err else CASE_ERROR_TO_DIFFERENT_ERROR
        )
        return result

    # Neither status is a recognized value (should not happen -- the harness
    # only ever emits ok/error/skipped) -- fail closed as skipped rather than
    # guessing a classification.
    return result


def summarize_variant_cases(
    case_rows: List[Dict[str, Any]], max_examples: int
) -> Tuple[Dict[str, int], Dict[str, List[str]], Optional[float]]:
    """Deterministic per-variant aggregate: counts, capped examples per
    ``case_status``, and the average ``duration_delta_ms`` across cases
    where it was computable.

    ``max_examples`` reuses the same "cap example lists" convention as
    ``trace_analyzer.max_examples()`` (``ANALYZER_MAX_EXAMPLES``) and
    ``experiment`` variants — callers pass that same value so the whole
    codebase shares one example-count knob.
    """
    summary = {status: 0 for status in VARIANT_CASE_STATUSES}
    examples: Dict[str, List[str]] = {}
    deltas: List[float] = []
    for case in case_rows:
        status = case.get("case_status")
        if status in summary:
            summary[status] += 1
        bucket = examples.setdefault(status, [])
        trace_id = case.get("trace_id")
        if len(bucket) < max_examples and trace_id not in bucket:
            bucket.append(trace_id)
        delta = case.get("duration_delta_ms")
        if delta is not None:
            deltas.append(delta)
    summary["total"] = len(case_rows)
    avg_delta = (sum(deltas) / len(deltas)) if deltas else None
    return summary, {k: v for k, v in sorted(examples.items())}, avg_delta
