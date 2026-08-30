"""Gap source federation (Issue #430, Epic #427).

`docs/product-objective-lineage.md` §5.4/§5.10 is the canonical contract.
This module is the SINGLE read-time resolution layer a `product_gap_source_ref`
dispatches through. It calls the 14 EXISTING gap/divergence producers listed in
§5.4's table and reads their EXISTING canonical rows -- it never re-implements
a detection, never copies a detector's text into a new table, and never
normalizes one detector's severity into another's vocabulary (§0 invariant 1
and invariant 9, #380's superset rule).

`resolve_source` is pure with respect to storage: it never INSERTs/UPDATEs
anything, and it never raises for a DATA reason -- an unreadable canon is the
RESULT `source_state='unavailable'`, never the caller's exception to handle.
It raises `ValueError` only when `source_kind` is outside the finite
`ProductGapSourceKind` vocabulary, which is a programming error, not data.

The first-match `source_state` rule (§5.10) is evaluated by each per-kind
resolver in this exact order:

1. reading the canonical source raised -> `unavailable`
2. `source_ref` is absent from the current canon -> `disappeared`
3. the kind's own §5.4 `contradicted` condition holds -> `contradicted`
4. a non-empty `captured_digest` differs from `current_digest` -> `changed`
5. otherwise -> `current`

`deep_link` / `deep_link_state` are attached UNIFORMLY by `resolve_source`
itself from a static per-kind table (§5.8) -- never inside a per-kind
resolver -- because "does a screen exist for this kind" is a property of the
KIND, not of one resolution's outcome. `node_anomaly` has no Dashboard screen
at all (Epic #394 Phase 5's cockpit is #401's unimplemented remainder), so it
is always `deep_link=None` / `deep_link_state='unavailable'` -- never a
fabricated URL.

The same table-driven rule covers a Gap's OTHER two reference kinds -- its
evidence refs and its downstream artifact links (`evidence_deep_link` /
`artifact_deep_link`). They are here, next to the source table, because
"which Dashboard screen owns this kind" is one question and must have one
answer: a Gap's screen would otherwise be free to build its own URLs, which
is exactly the second opinion this Epic exists to prevent (§0-1). Both
tables are per-KIND and carry no resolution: unlike a source ref, an
evidence or artifact ref is not resolved against a canonical detector here,
so `deep_link_state` says only whether a screen exists -- never whether the
referenced row still does.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Optional, Tuple, get_args

from . import drift, functional_lineage, gap_triage, stakeholder_value_network, ux_design
from .models import (
    ProductGapArtifactLinkKind,
    ProductGapEvidenceKind,
    ProductGapSourceKind,
    ProductGapSourceState,
)

#: `get_args(ProductGapSourceKind)` (§5.10's exact contract).
SOURCE_KINDS: Tuple[str, ...] = get_args(ProductGapSourceKind)

#: `get_args(ProductGapSourceState)` (§5.10's exact contract).
SOURCE_STATES: Tuple[str, ...] = get_args(ProductGapSourceState)


@dataclass(frozen=True)
class ResolvedSource:
    """One read-time resolution of a `product_gap_source_ref` (§5.10).

    `severity` carries the detector's OWN vocabulary verbatim, with
    `severity_vocabulary` naming which one -- never normalized across
    sources into a single scale (§5.1 / #380 superset rule). `extra` is
    display-only; nothing in it may influence `source_state` (§5.10's last
    bullet).
    """

    source_state: str
    title: str
    detail: str
    severity: Optional[str]
    severity_vocabulary: Optional[str]
    current_digest: str
    deep_link: Optional[str]
    deep_link_state: str
    #: §5.8.1's SECOND axis -- `selected` when `deep_link` opens the
    #: destination WITH this subject selected, `screen_only` when it opens
    #: the owning screen and the subject must be found there, `unavailable`
    #: when no screen exists. Attached uniformly by `resolve_source`, never
    #: by a per-kind resolver.
    deep_link_target_state: str = "unavailable"
    extra: Dict[str, Any] = field(default_factory=dict)
    #: §5.4/§5.10's per-kind pins, decided by THIS resolver from the
    #: canonical rows it already read -- never accepted from a caller
    #: (`add_gap_source_ref` never takes a pin in its request body). A kind
    #: with no required/optional pin (§5.10's table) leaves these `None`.
    #: `capability_drift` needs `resolved_snapshot_id` AND
    #: `resolved_run_id`; `system_understanding_gap` needs only
    #: `resolved_snapshot_id`; `understanding_review_gap` /
    #: `understanding_claim_change` / `requirement_diff` need only
    #: `resolved_revision_id`.
    resolved_snapshot_id: Optional[int] = None
    resolved_run_id: Optional[int] = None
    resolved_revision_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _digest_state(captured_digest: str, current_digest: str) -> str:
    """§5.10 step 4/5: a non-empty `captured_digest` that differs from
    `current_digest` is `changed`; otherwise `current`."""
    if captured_digest and captured_digest != current_digest:
        return "changed"
    return "current"


def _unavailable(reason: str, *, extra: Optional[Dict[str, Any]] = None) -> ResolvedSource:
    payload = dict(extra or {})
    payload.setdefault("reason", reason)
    return ResolvedSource(
        source_state="unavailable", title="", detail=reason, severity=None,
        severity_vocabulary=None, current_digest="", deep_link=None,
        deep_link_state="unavailable", extra=payload,
    )


def _disappeared(*, title: str = "", detail: str = "", extra: Optional[Dict[str, Any]] = None) -> ResolvedSource:
    return ResolvedSource(
        source_state="disappeared", title=title, detail=detail, severity=None,
        severity_vocabulary=None, current_digest="", deep_link=None,
        deep_link_state="unavailable", extra=extra or {},
    )


def _split2(source_ref: str) -> Optional[Tuple[str, str]]:
    if "|" not in source_ref:
        return None
    a, b = source_ref.split("|", 1)
    return a, b


def _split3(source_ref: str) -> Optional[Tuple[str, str, str]]:
    parts = source_ref.split("|", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _latest_interview_session_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    """The System's newest Interview session (`ORDER BY id DESC`) -- the same
    rule the Interview screen auto-selects with and #380's Overview reads the
    Brief with, so this resolver never describes a different session than
    what the developer sees."""
    row = conn.execute(
        "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# manual (§5.4: no external canon)
# ---------------------------------------------------------------------------


def _resolve_manual(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    return ResolvedSource(
        source_state="current", title="", detail="", severity=None,
        severity_vocabulary=None, current_digest="", deep_link=None,
        deep_link_state="unavailable", extra={},
    )


# ---------------------------------------------------------------------------
# system_understanding_gap
# ---------------------------------------------------------------------------


def _resolve_system_understanding_gap(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    from . import state_facts
    from . import system_understanding_service as sus

    snapshot_row = state_facts.get_latest_ready_snapshot(conn, system_id)
    if snapshot_row is None:
        return _unavailable("no ready snapshot for this System")
    snapshot_id = snapshot_row["id"]

    gaps = sus._load_gaps_from_reconciler(conn, system_id, snapshot_id)
    gap_triage.annotate_gaps(conn, system_id, snapshot_id, gaps)

    match = next((g for g in gaps if gap_triage.gap_key(g) == source_ref), None)
    if match is None:
        return _disappeared()

    current_digest = gap_triage.gap_content_fingerprint(match)
    triage_status = match.get("triage_status")
    if triage_status == "resolved":
        return ResolvedSource(
            source_state="contradicted", title=match.get("title") or "", detail=match.get("notes") or "",
            severity=match.get("severity"), severity_vocabulary="gap_triage",
            current_digest=current_digest, deep_link=None, deep_link_state="unavailable",
            extra={"triage_status": triage_status}, resolved_snapshot_id=snapshot_id,
        )

    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=match.get("title") or "", detail=match.get("notes") or "",
        severity=match.get("severity"), severity_vocabulary="gap_triage",
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable",
        extra={"triage_status": triage_status}, resolved_snapshot_id=snapshot_id,
    )


# ---------------------------------------------------------------------------
# understanding_review_gap
# ---------------------------------------------------------------------------


def _resolve_understanding_review_gap(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split2(source_ref)
    if parts is None:
        return _disappeared()
    gap_type, node_name = parts

    session_id = _latest_interview_session_id(conn, system_id)
    if session_id is None:
        return _unavailable("no Interview session for this System")

    row = conn.execute(
        """SELECT id, gap_analysis FROM understanding_revision
           WHERE session_id = ? AND system_id = ? ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()
    if row is None or not row["gap_analysis"]:
        return _disappeared()
    revision_id = row["id"]

    try:
        items = json.loads(row["gap_analysis"])
    except (TypeError, ValueError) as exc:
        return _unavailable(f"gap_analysis is not valid JSON: {exc}")
    if not isinstance(items, list):
        return _unavailable("gap_analysis is not a JSON array")

    # §5.4's honest weakness: reviewer gap_analysis items carry no row
    # identity, only (gap_type, name). Renaming the claim makes this
    # `disappeared` -- never compensated by storing anything (§0-9).
    match = next(
        (it for it in items if isinstance(it, dict) and it.get("gap_type") == gap_type and it.get("name") == node_name),
        None,
    )
    if match is None:
        return _disappeared()

    current_digest = _digest({
        "gap_type": match.get("gap_type"), "name": match.get("name"),
        "summary": match.get("summary"), "severity": match.get("severity"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=f"{gap_type}: {node_name}", detail=match.get("summary") or "",
        severity=match.get("severity"), severity_vocabulary="understanding_review",
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable", extra={},
        resolved_revision_id=revision_id,
    )


# ---------------------------------------------------------------------------
# understanding_claim_change
# ---------------------------------------------------------------------------


def _resolve_understanding_claim_change(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    from . import understanding_diff

    parts = _split2(source_ref)
    if parts is None:
        return _disappeared()
    section, name = parts

    session_id = _latest_interview_session_id(conn, system_id)
    if session_id is None:
        return _unavailable("no Interview session for this System")

    to_row = conn.execute(
        """SELECT id, current_understanding FROM understanding_revision
           WHERE session_id = ? AND system_id = ? ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()
    if to_row is None:
        return _disappeared()

    try:
        current = json.loads(to_row["current_understanding"]) if to_row["current_understanding"] else None
    except (TypeError, ValueError) as exc:
        return _unavailable(f"current_understanding is not valid JSON: {exc}")

    section_items = (current or {}).get(section) or []
    item = next((it for it in section_items if isinstance(it, dict) and it.get("name") == name), None)
    if item is None:
        return _disappeared()

    from_row = conn.execute(
        """SELECT current_understanding FROM understanding_revision
           WHERE session_id = ? AND system_id = ? AND id < ? ORDER BY id DESC LIMIT 1""",
        (session_id, system_id, to_row["id"]),
    ).fetchone()
    try:
        previous = json.loads(from_row["current_understanding"]) if from_row and from_row["current_understanding"] else None
    except (TypeError, ValueError) as exc:
        return _unavailable(f"previous current_understanding is not valid JSON: {exc}")

    diff_sections = understanding_diff.diff_understanding(previous, current)
    section_diff = next((s for s in diff_sections if s["section"] == section), None)
    buckets = []
    if section_diff is not None:
        if name in section_diff.get("added", []):
            buckets.append("added")
        if name in section_diff.get("removed", []):
            buckets.append("removed")
        if name in section_diff.get("summary_changed", []):
            buckets.append("summary_changed")
        if any(c.get("name") == name for c in section_diff.get("confidence_changed", [])):
            buckets.append("confidence_changed")

    current_digest = _digest({
        "summary": item.get("summary"),
        "confidence_level": (item.get("confidence") or {}).get("level"),
    })

    if not buckets:
        # §5.4: the claim exists but is no longer part of the latest
        # revision-to-revision diff -- the change condition no longer holds.
        return ResolvedSource(
            source_state="contradicted", title=f"{section}: {name}", detail="",
            severity=None, severity_vocabulary=None, current_digest=current_digest,
            deep_link=None, deep_link_state="unavailable", extra={"buckets": buckets},
            resolved_revision_id=to_row["id"],
        )

    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=f"{section}: {name}", detail=f"change_kind={'+'.join(buckets)}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={"buckets": buckets},
        resolved_revision_id=to_row["id"],
    )


# ---------------------------------------------------------------------------
# functional_lineage_gap
# ---------------------------------------------------------------------------


def _resolve_functional_lineage_gap(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split3(source_ref)
    if parts is None:
        return _disappeared()
    code, subject_kind, subject_ref = parts

    # Because sections 6-8 are excluded below, the codes they alone emit can
    # never match here. Reporting that as `disappeared` would be a claim
    # about the world ("the detector no longer emits this gap") when the fact
    # is only that this projection does not answer that question from inside
    # a Gap's own source resolution -- and `disappeared` is a `close_
    # candidate` input (§6), so it would nudge a human toward closing a Gap on
    # evidence that was never gathered. `unavailable` is the honest answer
    # (§0-8), and it is permanent rather than transient, which the reason says
    # outright. `add_gap_source_ref` refuses such a ref at creation, so this
    # branch only ever sees rows stored before that gate existed.
    if code in functional_lineage.PRODUCT_OBJECTIVE_LAYER_GAP_CODES:
        return _unavailable(
            "this gap code is emitted only by the Functional Lineage view's "
            "Product Objective sections, which a Gap's own source resolution "
            "cannot read (it would re-enter the projection); resolve it on "
            "/functional-lineage instead",
            extra={"reason": "product_objective_layer_code", "code": code},
        )

    # The Product Objective sections are DOWNSTREAM of this Gap and would
    # re-enter this very resolver through `get_gap_detail`, so the
    # projection would never return (see `build_functional_lineage`'s
    # docstring). Sections 1-5 -- the detector that emitted this gap code --
    # answer in full, which is the whole of what a detection source needs.
    result = functional_lineage.build_functional_lineage(
        conn, system_id, include_product_objective_layer=False
    )
    match = next(
        (g for g in result["gaps"] if g["code"] == code and g["subject_kind"] == subject_kind and g["subject_ref"] == subject_ref),
        None,
    )
    if match is None:
        if result.get("degraded_sections"):
            # A section failed to load -- we cannot honestly say the gap
            # disappeared vs. simply could not be seen this request (§0-8).
            return _unavailable(
                "functional lineage degraded sections prevented resolution",
                extra={"degraded_sections": result["degraded_sections"]},
            )
        return _disappeared()

    current_digest = _digest(match)
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=code, detail=f"{subject_kind}: {subject_ref}",
        severity=match.get("severity"), severity_vocabulary="functional_lineage",
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable", extra={},
    )


# ---------------------------------------------------------------------------
# value_network_notice
# ---------------------------------------------------------------------------


def _resolve_value_network_notice(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split3(source_ref)
    if parts is None:
        return _disappeared()
    code, subject_kind, subject_key = parts

    result = stakeholder_value_network.build_value_network(conn, system_id)
    match = next(
        (n for n in result["notices"] if n["code"] == code and n["subject_kind"] == subject_kind and n["subject_key"] == subject_key),
        None,
    )
    if match is None:
        if result.get("degraded_sections"):
            return _unavailable(
                "value network degraded sections prevented resolution",
                extra={"degraded_sections": result["degraded_sections"]},
            )
        return _disappeared()

    current_digest = _digest(match)
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=code, detail=f"{subject_kind}: {subject_key}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={},
    )


# ---------------------------------------------------------------------------
# journey_baseline_diff
# ---------------------------------------------------------------------------


def _resolve_journey_baseline_diff(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split2(source_ref)
    if parts is None:
        return _disappeared()
    journey_key, step_key = parts

    try:
        diff = ux_design.baseline_diff_journey(conn, system_id, journey_key)
    except ux_design.NotFound:
        return _disappeared()

    if diff.get("diff_state") != "available":
        # No baseline to diff against right now -- the referenced step has
        # nothing to be found in (§0-8: not_applicable is not "unavailable").
        return _disappeared(extra={"diff_state": diff.get("diff_state")})

    entry = next((e for e in diff.get("steps", []) if e.get("step_key") == step_key), None)
    if entry is None:
        return _disappeared()

    if entry.get("change_kind") == "unchanged":
        return ResolvedSource(
            source_state="contradicted", title=f"{journey_key} / {step_key}", detail="change_kind=unchanged",
            severity=None, severity_vocabulary=None, current_digest="",
            deep_link=None, deep_link_state="unavailable", extra={},
        )

    current_digest = _digest({
        "change_kind": entry.get("change_kind"),
        "from_step": (entry.get("from_step") or {}).get("content_digest"),
        "to_step": (entry.get("to_step") or {}).get("content_digest"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=f"{journey_key} / {step_key}", detail=f"change_kind={entry.get('change_kind')}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={},
    )


# ---------------------------------------------------------------------------
# requirement_diff
# ---------------------------------------------------------------------------


def _resolve_requirement_diff(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split2(source_ref)
    if parts is None:
        return _disappeared()
    requirement_key, criterion_key = parts

    try:
        # Always resolved against the LATEST revision transition (mirrors
        # `journey_baseline_diff`'s no-parameter "current" reading);
        # `captured_revision_id` is accepted for audit/display but does not
        # change which transition is diffed (documented interpretation --
        # see the task report). `to_revision_id` is the revision this
        # resolution actually read, so it is what gets stored as
        # `resolved_revision_id` at add time (§5.10).
        diff = ux_design.diff_requirement_revisions(conn, system_id, requirement_key)
    except ux_design.NotFound:
        return _disappeared()

    if diff.get("diff_state") != "available":
        return _disappeared(extra={"diff_state": diff.get("diff_state")})

    resolved_revision_id = diff.get("to_revision_id")

    entry = next((e for e in diff.get("criteria", []) if e.get("criterion_key") == criterion_key), None)
    if entry is None:
        return _disappeared()

    if entry.get("change_kind") == "unchanged":
        return ResolvedSource(
            source_state="contradicted", title=f"{requirement_key} / {criterion_key}", detail="change_kind=unchanged",
            severity=None, severity_vocabulary=None, current_digest="",
            deep_link=None, deep_link_state="unavailable", extra={},
            resolved_revision_id=resolved_revision_id,
        )

    current_digest = _digest({
        "change_kind": entry.get("change_kind"),
        "from_criterion": (entry.get("from_criterion") or {}).get("content_digest"),
        "to_criterion": (entry.get("to_criterion") or {}).get("content_digest"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=f"{requirement_key} / {criterion_key}", detail=f"change_kind={entry.get('change_kind')}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={},
        resolved_revision_id=resolved_revision_id,
    )


# ---------------------------------------------------------------------------
# capability_drift
# ---------------------------------------------------------------------------


def _latest_indexed_ready_snapshot_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    row = conn.execute(
        """SELECT rs.id FROM repository_snapshots rs
           WHERE rs.system_id = ? AND rs.status = 'ready'
             AND EXISTS (
                 SELECT 1 FROM intelligence_runs ir
                 WHERE ir.system_id = rs.system_id AND ir.snapshot_id = rs.id
                   AND ir.run_type = 'symbol_index' AND ir.status = 'completed'
             )
           ORDER BY rs.id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def _latest_completed_capability_hierarchy_run_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    """The base run `capability_drift` compares against when no pin has been
    captured yet -- the latest completed Capability Hierarchy build for this
    System (the same "latest completed run of the relevant run_type" pattern
    `_latest_indexed_ready_snapshot_id` below uses for `symbol_index`)."""
    row = conn.execute(
        """SELECT id FROM intelligence_runs
           WHERE system_id = ? AND run_type = 'capability_hierarchy' AND status = 'completed'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def _snapshot_facts_for_drift(conn: sqlite3.Connection, snapshot_id: int, system_id: int) -> "drift.SnapshotFacts":
    file_rows = conn.execute(
        "SELECT path, content_hash FROM snapshot_files WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    sym_rows = conn.execute(
        """SELECT cs.path, cs.qualified_name, cs.symbol_source_hash, ssm.explanation_hash
           FROM code_symbols cs
           LEFT JOIN symbol_source_metadata ssm ON ssm.symbol_id = cs.id
           WHERE cs.snapshot_id = ? AND cs.system_id = ?""",
        (snapshot_id, system_id),
    ).fetchall()
    return drift.SnapshotFacts(
        file_hash_by_path={r["path"]: r["content_hash"] for r in file_rows},
        symbol_by_key={(r["path"], r["qualified_name"]): (r["symbol_source_hash"], r["explanation_hash"]) for r in sym_rows},
    )


def _resolve_capability_drift(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    # §5.10: `capability_drift` REQUIRES a pinned base run. `add_gap_source_ref`
    # never accepts a pin from the request body, so the FIRST resolution (at
    # add time) always arrives with `captured_run_id=None` -- the resolver
    # decides the pin itself here, from the canon it already reads: the
    # latest completed Capability Hierarchy build for this System. A pin
    # that WAS already captured is re-read as-is; if it no longer resolves
    # this stays `unavailable` rather than silently substituting a
    # different run (that would make the stored pin meaningless).
    run_id = captured_run_id
    if run_id is None:
        run_id = _latest_completed_capability_hierarchy_run_id(conn, system_id)
        if run_id is None:
            return _unavailable(
                "no capability_hierarchy run has completed for this System",
                extra={"reason": "no_capability_hierarchy_run"},
            )

    run_row = conn.execute(
        "SELECT id, snapshot_id FROM intelligence_runs WHERE id = ? AND system_id = ?",
        (run_id, system_id),
    ).fetchone()
    if run_row is None:
        return _unavailable("captured intelligence run not found", extra={"reason": "captured_run_not_found"})

    if source_ref.startswith("entrypoint:"):
        raw_entrypoint_id = source_ref[len("entrypoint:"):]
        try:
            entrypoint_id = int(raw_entrypoint_id)
        except ValueError:
            return _disappeared()
        node_row = conn.execute(
            """SELECT * FROM capability_hierarchy_nodes
               WHERE intelligence_run_id = ? AND system_id = ? AND entrypoint_id = ?""",
            (run_row["id"], system_id, entrypoint_id),
        ).fetchone()
    else:
        parts = _split2(source_ref)
        if parts is None:
            return _disappeared()
        path, qualified_name = parts
        node_row = conn.execute(
            """SELECT * FROM capability_hierarchy_nodes
               WHERE intelligence_run_id = ? AND system_id = ? AND path = ? AND qualified_name = ?""",
            (run_row["id"], system_id, path, qualified_name),
        ).fetchone()

    if node_row is None:
        # The captured hierarchy run never produced an anchor for this ref --
        # a read-scope problem for THIS request, not evidence the underlying
        # code went away (§0-8).
        return _unavailable("no anchor found for this ref in the captured run", extra={"reason": "anchor_not_captured"})

    target_snapshot_id = _latest_indexed_ready_snapshot_id(conn, system_id) or run_row["snapshot_id"]
    facts = _snapshot_facts_for_drift(conn, target_snapshot_id, system_id)
    anchor = drift.NodeAnchor(
        node_id=node_row["id"], node_type=node_row["node_type"], name=node_row["name"],
        path=node_row["path"], qualified_name=node_row["qualified_name"], entrypoint_id=node_row["entrypoint_id"],
        file_content_hash=node_row["file_content_hash"], symbol_source_hash=node_row["symbol_source_hash"],
        explanation_hash=node_row["explanation_hash"],
    )
    result = drift.compute_anchor_drift(anchor, facts)

    if result.status == drift.MISSING_SOURCE:
        return _disappeared(
            title=node_row["name"] or "", extra={"drift_status": result.status},
        )
    if result.status == drift.FRESH:
        return ResolvedSource(
            source_state="contradicted", title=node_row["name"] or "", detail="drift status: fresh",
            severity=None, severity_vocabulary=None, current_digest="",
            deep_link=None, deep_link_state="unavailable", extra={"drift_status": result.status},
            resolved_snapshot_id=target_snapshot_id, resolved_run_id=run_row["id"],
        )

    current_digest = _digest({
        "file_content_hash": result.current_file_content_hash,
        "symbol_source_hash": result.current_symbol_source_hash,
        "explanation_hash": result.current_explanation_hash,
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=node_row["name"] or "", detail=f"drift status: {result.status}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable",
        extra={"drift_status": result.status, "changed_hashes": result.changed_hashes},
        resolved_snapshot_id=target_snapshot_id, resolved_run_id=run_row["id"],
    )


# ---------------------------------------------------------------------------
# runtime_alignment_mismatch
# ---------------------------------------------------------------------------


def _resolve_runtime_alignment_mismatch(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    row = conn.execute(
        """SELECT * FROM alignment_item
           WHERE system_id = ? AND review_subject_id = ? AND superseded = 0
           ORDER BY id DESC LIMIT 1""",
        (system_id, source_ref),
    ).fetchone()
    if row is None:
        return _disappeared()

    row = dict(row)
    if row.get("runtime_check") == "match":
        return ResolvedSource(
            source_state="contradicted", title=row.get("intent_summary") or row.get("current_claim") or "",
            detail=row.get("gap_summary") or "", severity=None, severity_vocabulary=None,
            current_digest="", deep_link=None, deep_link_state="unavailable",
            extra={"runtime_check": row.get("runtime_check")},
        )

    current_digest = _digest({
        "current_claim": row.get("current_claim"),
        "runtime_check": row.get("runtime_check"),
        "review_category": row.get("review_category"),
        "gap_summary": row.get("gap_summary"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=row.get("intent_summary") or row.get("current_claim") or "",
        detail=row.get("gap_summary") or "", severity=None, severity_vocabulary=None,
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable",
        extra={"runtime_check": row.get("runtime_check")},
    )


# ---------------------------------------------------------------------------
# node_anomaly (§5.8: no Dashboard screen)
# ---------------------------------------------------------------------------


def _resolve_node_anomaly(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    parts = _split2(source_ref)
    if parts is None:
        return _disappeared()
    node_key, dedupe_key = parts

    node_row = conn.execute(
        "SELECT id FROM evolution_node WHERE system_id = ? AND node_key = ?",
        (system_id, node_key),
    ).fetchone()
    if node_row is None:
        return _disappeared()

    row = conn.execute(
        """SELECT * FROM node_anomaly
           WHERE system_id = ? AND node_id = ? AND dedupe_key = ?
           ORDER BY id DESC LIMIT 1""",
        (system_id, node_row["id"], dedupe_key),
    ).fetchone()
    if row is None:
        return _disappeared()

    row = dict(row)
    if row.get("status") == "resolved":
        return ResolvedSource(
            source_state="contradicted", title=row.get("classification") or "", detail=row.get("summary") or "",
            severity=row.get("severity"), severity_vocabulary="node_anomaly",
            current_digest="", deep_link=None, deep_link_state="unavailable",
            extra={"status": row.get("status")},
        )

    current_digest = _digest({
        "classification": row.get("classification"), "severity": row.get("severity"),
        "summary": row.get("summary"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=row.get("classification") or "", detail=row.get("summary") or "",
        severity=row.get("severity"), severity_vocabulary="node_anomaly",
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable",
        extra={"status": row.get("status")},
    )


# ---------------------------------------------------------------------------
# joint_understanding_open
# ---------------------------------------------------------------------------


def _resolve_joint_understanding_open(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    try:
        row_id = int(source_ref)
    except ValueError:
        return _disappeared()

    row = conn.execute(
        "SELECT * FROM joint_understanding_session WHERE id = ? AND system_id = ?",
        (row_id, system_id),
    ).fetchone()
    if row is None:
        return _disappeared()

    row = dict(row)
    if row.get("status") == "closed":
        return ResolvedSource(
            source_state="contradicted", title=row.get("question_text") or "", detail=f"status={row.get('status')}",
            severity=None, severity_vocabulary=None, current_digest="",
            deep_link=None, deep_link_state="unavailable", extra={"status": row.get("status")},
        )

    current_digest = _digest({
        "status": row.get("status"), "question_text": row.get("question_text"), "outcome": row.get("outcome"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=row.get("question_text") or "", detail=f"status={row.get('status')}",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={"status": row.get("status")},
    )


# ---------------------------------------------------------------------------
# inquiry_unresolved
# ---------------------------------------------------------------------------


def _resolve_inquiry_unresolved(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    try:
        row_id = int(source_ref)
    except ValueError:
        return _disappeared()

    row = conn.execute(
        "SELECT * FROM interview_inquiry WHERE id = ? AND system_id = ?",
        (row_id, system_id),
    ).fetchone()
    if row is None:
        return _disappeared()

    row = dict(row)
    message = conn.execute(
        "SELECT content FROM interview_inquiry_message WHERE inquiry_id = ? AND system_id = ? ORDER BY id LIMIT 1",
        (row_id, system_id),
    ).fetchone()
    title = (message["content"] if message else "") or f"Inquiry #{row_id}"

    if row.get("status") in ("answered", "superseded"):
        return ResolvedSource(
            source_state="contradicted", title=title, detail=row.get("status_reason") or "",
            severity=None, severity_vocabulary=None, current_digest="",
            deep_link=None, deep_link_state="unavailable", extra={"status": row.get("status")},
        )

    current_digest = _digest({"status": row.get("status"), "status_reason": row.get("status_reason"), "title": title})
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=title, detail=row.get("status_reason") or "",
        severity=None, severity_vocabulary=None, current_digest=current_digest,
        deep_link=None, deep_link_state="unavailable", extra={"status": row.get("status")},
    )


# ---------------------------------------------------------------------------
# issue_draft
# ---------------------------------------------------------------------------


def _resolve_issue_draft(
    conn: sqlite3.Connection, *, system_id: int, source_ref: str, captured_digest: str,
    captured_snapshot_id: Optional[int], captured_run_id: Optional[int],
    captured_revision_id: Optional[int],
) -> ResolvedSource:
    try:
        row_id = int(source_ref)
    except ValueError:
        return _disappeared()

    row = conn.execute(
        "SELECT * FROM issue_drafts WHERE id = ? AND system_id = ?",
        (row_id, system_id),
    ).fetchone()
    if row is None:
        return _disappeared()

    row = dict(row)
    if row.get("status") in ("closed", "rejected"):
        return ResolvedSource(
            source_state="contradicted", title=row.get("title") or "", detail=(row.get("body_markdown") or "")[:280],
            severity=row.get("severity"), severity_vocabulary="issue_draft",
            current_digest="", deep_link=None, deep_link_state="unavailable", extra={"status": row.get("status")},
        )

    current_digest = _digest({
        "title": row.get("title"), "body_markdown": row.get("body_markdown"),
        "status": row.get("status"), "severity": row.get("severity"),
    })
    state = _digest_state(captured_digest, current_digest)
    return ResolvedSource(
        source_state=state, title=row.get("title") or "", detail=(row.get("body_markdown") or "")[:280],
        severity=row.get("severity"), severity_vocabulary="issue_draft",
        current_digest=current_digest, deep_link=None, deep_link_state="unavailable",
        extra={"status": row.get("status")},
    )


# ---------------------------------------------------------------------------
# Dispatch table + public entry point
# ---------------------------------------------------------------------------

_ResolverFn = Callable[..., ResolvedSource]

_RESOLVERS: Dict[str, _ResolverFn] = {
    "manual": _resolve_manual,
    "system_understanding_gap": _resolve_system_understanding_gap,
    "understanding_review_gap": _resolve_understanding_review_gap,
    "understanding_claim_change": _resolve_understanding_claim_change,
    "functional_lineage_gap": _resolve_functional_lineage_gap,
    "value_network_notice": _resolve_value_network_notice,
    "journey_baseline_diff": _resolve_journey_baseline_diff,
    "requirement_diff": _resolve_requirement_diff,
    "capability_drift": _resolve_capability_drift,
    "runtime_alignment_mismatch": _resolve_runtime_alignment_mismatch,
    "node_anomaly": _resolve_node_anomaly,
    "joint_understanding_open": _resolve_joint_understanding_open,
    "inquiry_unresolved": _resolve_inquiry_unresolved,
    "issue_draft": _resolve_issue_draft,
}

assert set(_RESOLVERS) == set(SOURCE_KINDS), "every ProductGapSourceKind must have exactly one resolver"

#: §5.8: which Dashboard screen owns each kind, or `None` when no screen
#: exists yet. This is a property of the KIND, never of one resolution's
#: outcome -- `resolve_source` attaches it uniformly to every result,
#: overriding whatever a per-kind resolver returned (§5.10).
_DEEP_LINKS: Dict[str, Optional[str]] = {
    "manual": None,
    "system_understanding_gap": "/system-understanding",
    "understanding_review_gap": "/interview",
    "understanding_claim_change": "/interview",
    "functional_lineage_gap": "/functional-lineage",
    "value_network_notice": "/stakeholder-value-network",
    "journey_baseline_diff": "/ux-design-studio",
    "requirement_diff": "/ux-design-studio",
    "capability_drift": "/capability-map",
    "runtime_alignment_mismatch": "/interview",
    "node_anomaly": None,
    "joint_understanding_open": "/interview",
    "inquiry_unresolved": "/interview",
    "issue_draft": "/system-understanding",
}

assert set(_DEEP_LINKS) == set(SOURCE_KINDS)


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _functional_lineage_target(ref: str) -> Optional[str]:
    """`code|subject_kind|subject_ref` -> Functional Lineage's OWN shared
    selection pair (`ref_kind`/`ref`, `readSharedSelection`)."""
    parts = _split3(ref)
    if parts is None:
        return None
    _code, subject_kind, subject_ref = parts
    if not subject_kind or not subject_ref:
        return None
    return f"/functional-lineage?ref_kind={_q(subject_kind)}&ref={_q(subject_ref)}"


def _value_network_target(ref: str) -> Optional[str]:
    """`code|subject_kind|subject_key` -> the Value Network's own `node` /
    `edge` params."""
    parts = _split3(ref)
    if parts is None:
        return None
    _code, subject_kind, subject_key = parts
    if not subject_key:
        return None
    if subject_kind == "stakeholder":
        return f"/stakeholder-value-network?node={_q(subject_key)}"
    if subject_kind == "value_exchange":
        return f"/stakeholder-value-network?edge={_q(subject_key)}"
    return None


def _journey_target(ref: str) -> Optional[str]:
    """`journey_key|step_key` -> the Studio's Journey tab with that Journey
    selected. The Studio has no Step param, so the Step is not claimed."""
    parts = _split2(ref)
    if parts is None:
        return None
    journey_key, _step_key = parts
    return f"/ux-design-studio?tab=journeys&journey={_q(journey_key)}" if journey_key else None


def _requirement_diff_target(ref: str) -> Optional[str]:
    """`requirement_key|criterion_key` -> the Studio's Requirement tab (there
    is no criterion param)."""
    parts = _split2(ref)
    if parts is None:
        return None
    requirement_key, _criterion_key = parts
    return (
        f"/ux-design-studio?tab=requirements&requirement={_q(requirement_key)}"
        if requirement_key else None
    )


#: §5.8.1's SECOND axis: whether the destination can be opened WITH the
#: subject selected, or only as a screen the developer must then search.
#:
#: `deep_link_state` answers "does a screen for this kind exist at all" and is
#: a property of the KIND. Whether the SUBJECT can be selected is a different
#: question with a per-kind answer -- it depends on the params that screen
#: actually reads and on the shape of this kind's ref -- and collapsing the
#: two made 「検出元の画面を開く」 promise more than it delivered wherever the
#: destination has no matching param (#366's one-word-two-facts rule).
#:
#: A builder returns `None` when THIS ref cannot be turned into a selection
#: (a malformed ref, a subject kind the screen has no param for); the result
#: then degrades to the kind's plain screen route -- never to a fabricated
#: param the destination would ignore (#366's `loopSearchParams` rule).
_DEEP_LINK_TARGETS: Dict[str, Callable[[str], Optional[str]]] = {
    "functional_lineage_gap": _functional_lineage_target,
    "value_network_notice": _value_network_target,
    "journey_baseline_diff": _journey_target,
    "requirement_diff": _requirement_diff_target,
}

#: Every other source kind is screen-only TODAY, each for a stated reason
#: rather than by omission:
#:
#: * `system_understanding_gap` / `issue_draft` -- `/system-understanding`
#:   reads no selection param.
#: * `understanding_review_gap` / `understanding_claim_change` /
#:   `runtime_alignment_mismatch` / `joint_understanding_open` /
#:   `inquiry_unresolved` -- `/interview` selects a SESSION (plus fixed
#:   anchors), not one review item / claim / Inquiry, and this layer has no
#:   session id to name.
#: * `capability_drift` -- `/capability-map`'s `?capability=` matches a
#:   Capability NAME, while this kind's subject is a code anchor
#:   (`path|qualified_name` or `entrypoint:<id>`). Those are different
#:   identities, and matching them would be the similarity guess Principle 6
#:   forbids.
#: * `manual` / `node_anomaly` -- no screen at all.


def _targeted(table: Dict[str, Callable[[str], Optional[str]]], kind: str, ref: str, screen: Optional[str]):
    """`(deep_link, deep_link_target_state)` for one kind+ref (§5.8.1)."""
    if not screen:
        return None, "unavailable"
    builder = table.get(kind)
    targeted = builder(ref) if builder else None
    if targeted:
        return targeted, "selected"
    return screen, "screen_only"


def resolve_source(
    conn: sqlite3.Connection, *, system_id: int, source_kind: str, source_ref: str,
    captured_digest: str = "",
    captured_snapshot_id: Optional[int] = None,
    captured_run_id: Optional[int] = None,
    captured_revision_id: Optional[int] = None,
) -> ResolvedSource:
    """Resolve one `product_gap_source_ref` against its kind's single
    canonical detector (§5.10). Never raises for a data reason -- every
    failure to read the canon becomes `source_state='unavailable'`. Raises
    `ValueError` only for a `source_kind` outside the finite vocabulary."""
    if source_kind not in _RESOLVERS:
        raise ValueError(f"Unknown ProductGapSourceKind: {source_kind!r}")

    screen = _DEEP_LINKS[source_kind]
    deep_link_state: str = "available" if screen else "unavailable"
    deep_link, deep_link_target_state = _targeted(_DEEP_LINK_TARGETS, source_kind, source_ref, screen)

    try:
        result = _RESOLVERS[source_kind](
            conn, system_id=system_id, source_ref=source_ref, captured_digest=captured_digest,
            captured_snapshot_id=captured_snapshot_id, captured_run_id=captured_run_id,
            captured_revision_id=captured_revision_id,
        )
    except Exception as exc:  # pragma: no cover - defensive, exercised by tests
        return ResolvedSource(
            source_state="unavailable", title="", detail=str(exc), severity=None,
            severity_vocabulary=None, current_digest="", deep_link=deep_link,
            deep_link_state=deep_link_state, deep_link_target_state=deep_link_target_state,
            extra={"error": type(exc).__name__},
        )

    return replace(
        result, deep_link=deep_link, deep_link_state=deep_link_state,
        deep_link_target_state=deep_link_target_state,
    )


# ---------------------------------------------------------------------------
# Evidence / artifact deep links (§5.8)
# ---------------------------------------------------------------------------

#: `get_args(ProductGapEvidenceKind)`.
EVIDENCE_KINDS: Tuple[str, ...] = get_args(ProductGapEvidenceKind)

#: `get_args(ProductGapArtifactLinkKind)`.
ARTIFACT_KINDS: Tuple[str, ...] = get_args(ProductGapArtifactLinkKind)

#: §5.8 for a Gap's evidence refs. `human_report` / `external_report` /
#: `other` are `None` because probe-agent owns no screen for them at all --
#: the reference is free text or an outside URI, and inventing a route for
#: it would be the fabricated URL §5.8 forbids. `repository_path` names the
#: Repository screen; the route is a bare SCREEN path, never a deep-link
#: target with the path pre-selected, because nothing here resolves the
#: reference against a snapshot.
_EVIDENCE_DEEP_LINKS: Dict[str, Optional[str]] = {
    "trace": "/components",
    "experiment": "/experiments",
    "replay_run": "/simulation-workbench",
    "human_report": None,
    "external_report": None,
    "repository_path": "/repository",
    "other": None,
}

assert set(_EVIDENCE_DEEP_LINKS) == set(EVIDENCE_KINDS)

#: §5.8 for a Gap's downstream artifact links. `product_feature` is `None`
#: on purpose: `GET /product-features` exists but no Dashboard screen owns a
#: Product Feature yet -- it appears only as a node inside the Functional
#: Lineage graph, which is a different question ("what is connected to what")
#: from "open this Feature". This is the same honest `unavailable` that
#: `node_anomaly` carries in `_DEEP_LINKS`, and it is fixed by editing one
#: row once a screen exists.
_ARTIFACT_DEEP_LINKS: Dict[str, Optional[str]] = {
    "issue_draft": "/system-understanding",
    "ux_requirement": "/ux-design-studio",
    "product_feature": None,
    "solution_design": "/ux-design-studio",
}

assert set(_ARTIFACT_DEEP_LINKS) == set(ARTIFACT_KINDS)


#: §5.8.1 for evidence. Each entry emits the DESTINATION's own param names.
#: `human_report` / `external_report` / `other` have no screen at all;
#: `repository_path` has one (`/repository`) that reads no path param, so it
#: stays `screen_only` rather than gaining a param the page would ignore.
_EVIDENCE_DEEP_LINK_TARGETS: Dict[str, Callable[[str], Optional[str]]] = {
    "trace": lambda ref: f"/components?trace={_q(ref)}" if ref else None,
    "experiment": lambda ref: f"/experiments?experiment={_q(ref)}" if ref else None,
    "replay_run": lambda ref: f"/simulation-workbench?replay_run_id={_q(ref)}" if ref else None,
}

#: §5.8.1 for downstream artifact links. `issue_draft`'s screen
#: (`/system-understanding`) reads no selection param, so it stays
#: `screen_only`.
_ARTIFACT_DEEP_LINK_TARGETS: Dict[str, Callable[[str], Optional[str]]] = {
    "ux_requirement": lambda ref: (
        f"/ux-design-studio?tab=requirements&requirement={_q(ref)}" if ref else None
    ),
    "solution_design": lambda ref: (
        f"/ux-design-studio?tab=solutions&design={_q(ref)}" if ref else None
    ),
}


def _deep_link(
    table: Dict[str, Optional[str]],
    targets: Dict[str, Callable[[str], Optional[str]]],
    kind: str,
    ref: str,
    label: str,
) -> Tuple[Optional[str], str, str]:
    if kind not in table:
        raise ValueError(f"Unknown {label}: {kind!r}")
    screen = table[kind]
    route, target_state = _targeted(targets, kind, ref, screen)
    return route, ("available" if screen else "unavailable"), target_state


def evidence_deep_link(evidence_kind: str, evidence_ref: str = "") -> Tuple[Optional[str], str, str]:
    """`(route, deep_link_state, deep_link_target_state)` for one
    `ProductGapEvidenceKind` (§5.8/§5.8.1).

    `evidence_ref` is what makes the difference between opening the owning
    screen and opening it ON this evidence; omitting it yields the plain
    screen route with `screen_only`, never a fabricated param.

    Raises `ValueError` outside the finite vocabulary -- a programming
    error, not data, exactly as `resolve_source` does."""
    return _deep_link(
        _EVIDENCE_DEEP_LINKS, _EVIDENCE_DEEP_LINK_TARGETS,
        evidence_kind, evidence_ref, "ProductGapEvidenceKind",
    )


def artifact_deep_link(link_kind: str, target_ref: str = "") -> Tuple[Optional[str], str, str]:
    """`(route, deep_link_state, deep_link_target_state)` for one
    `ProductGapArtifactLinkKind` (§5.8/§5.8.1)."""
    return _deep_link(
        _ARTIFACT_DEEP_LINKS, _ARTIFACT_DEEP_LINK_TARGETS,
        link_kind, target_ref, "ProductGapArtifactLinkKind",
    )
