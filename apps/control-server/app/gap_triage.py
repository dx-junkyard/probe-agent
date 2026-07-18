"""Deterministic identity and manual lifecycle for docs-code gaps (Issue #276).

``gap_key`` identifies *where* a gap is (kind + normalized target) and is
deliberately independent of snapshot-local database ids and source line
numbers. ``content_fingerprint`` identifies the current semantic rendering of
that gap. Keeping the two separate lets a human decision survive harmless line
movement while still reopening a dismissed gap when its contents change.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple


TRIAGE_STATUSES = ("open", "acknowledged", "dismissed", "resolved")

# Explicit finite lifecycle. A newly discovered gap is implicitly ``open``.
# Dismissed/resolved decisions may only be revisited by reopening first, which
# keeps the audit history unambiguous.
ALLOWED_TRANSITIONS = {
    "open": {"acknowledged"},
    "acknowledged": {"open", "dismissed", "resolved"},
    "dismissed": {"open"},
    "resolved": {"open"},
}


class GapTriageError(ValueError):
    """Base error returned by the triage API."""


class GapNotFoundError(GapTriageError):
    pass


class GapFingerprintConflict(GapTriageError):
    pass


class GapTransitionError(GapTriageError):
    pass


def _text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def _path(value: Any) -> str:
    raw = _text(value).replace("\\", "/")
    normalized = posixpath.normpath(raw) if raw else ""
    return normalized.removeprefix("./")


def _entrypoint_ref(entrypoint_type: Any, entrypoint_ref: Any) -> str:
    ep_type = _text(entrypoint_type).lower() or "unknown"
    ref = _text(entrypoint_ref)
    # HTTP-like entrypoints are rendered consistently across discovery
    # implementations (``GET /x``, ``GET:/x`` and case differences).
    match = re.match(r"^([A-Za-z]+)\s*:?\s*(/.*)$", ref)
    if match:
        method, route = match.groups()
        route = route.rstrip("/") or "/"
        ref = f"{method.upper()} {route}"
    return f"{ep_type}:{ref or 'unknown'}"


def _target_parts(gap: Dict[str, Any]) -> Tuple[str, List[str]]:
    entrypoints = sorted({
        _entrypoint_ref(ref.get("entrypoint_type"), ref.get("entrypoint_ref"))
        for ref in gap.get("entrypoint_refs") or []
        if ref.get("entrypoint_type") or ref.get("entrypoint_ref")
    })
    if entrypoints:
        return "entrypoint", entrypoints

    symbols = sorted({
        f"{_path(ref.get('path')) or 'unknown'}::{_text(ref.get('qualified_name')) or 'unknown'}"
        for ref in gap.get("symbol_refs") or []
        if ref.get("path") or ref.get("qualified_name")
    })
    if symbols:
        return "symbol", symbols

    docs = sorted({
        _path(ref.get("path"))
        for ref in gap.get("doc_refs") or []
        if ref.get("path")
    })
    if docs:
        # The normalized graph source id disambiguates multiple claims in the
        # same document. It is derived from claim kind/name, not line offsets.
        source_id = _text(gap.get("source_id"))
        name = _text(gap.get("node_name")) or "claim"
        suffix = f"{name} [{source_id}]" if source_id else name
        return "document", [f"{path}::{suffix}" for path in docs]

    source_id = _text(gap.get("source_id"))
    if source_id:
        capability = _text(gap.get("capability_key"))
        name = _text(gap.get("node_name")) or "unknown"
        subject = f"{capability}::{name}" if capability else name
        # Keep the deterministic id for collision resistance, while exposing
        # the human subject next to it rather than returning an opaque hash.
        return "source", [f"{subject} [{source_id}]"]

    capability = _text(gap.get("capability_key"))
    name = _text(gap.get("node_name")) or "unknown"
    return "subject", [f"{capability}::{name}" if capability else name]


def gap_key(gap: Dict[str, Any]) -> str:
    """Return a human-readable, snapshot-stable locator for a gap.

    Line ranges, snapshot ids and database ids are intentionally excluded.
    Multiple targets are sorted so result ordering cannot change identity.
    """
    kind = _text(gap.get("gap_type")).lower() or "unknown"
    target_kind, targets = _target_parts(gap)
    return f"{kind}|{target_kind}|{','.join(targets)}"


def gap_content_fingerprint(gap: Dict[str, Any]) -> str:
    """Hash semantic gap content separately from ``gap_key``.

    Source line positions are omitted: moving unchanged code/documentation
    must not invalidate a human triage decision. Evidence membership, title,
    notes, capability and target names remain part of the content identity.
    """
    canonical = {
        "gap_type": _text(gap.get("gap_type")).lower(),
        "severity": _text(gap.get("severity")).lower(),
        "title": _text(gap.get("title")),
        "node_name": _text(gap.get("node_name")),
        "notes": _text(gap.get("notes")),
        "capability_key": _text(gap.get("capability_key")),
        "doc_paths": sorted({_path(r.get("path")) for r in gap.get("doc_refs") or []}),
        "symbols": sorted({
            (_path(r.get("path")), _text(r.get("qualified_name")))
            for r in gap.get("symbol_refs") or []
        }),
        "entrypoints": sorted({
            _entrypoint_ref(r.get("entrypoint_type"), r.get("entrypoint_ref"))
            for r in gap.get("entrypoint_refs") or []
        }),
        # Persisted ids and source positions are snapshot-local. Keep only
        # semantic locators from generic code_refs.
        "code_refs": sorted(
            json.dumps(
                {
                    "source": _text(r.get("source")),
                    "path": _path(r.get("path")),
                    "qualified_name": _text(r.get("qualified_name")),
                    "route_method": _text(r.get("route_method")).upper(),
                    "route_path": _text(r.get("route_path")).rstrip("/") or None,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for r in gap.get("code_refs") or []
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _latest_rows(conn, system_id: int, keys: Iterable[str]) -> Dict[str, Any]:
    wanted = set(keys)
    if not wanted:
        return {}
    rows = conn.execute(
        """SELECT d.* FROM gap_triage_decisions d
            JOIN (
                SELECT gap_key, MAX(id) AS max_id
                FROM gap_triage_decisions
                WHERE system_id = ?
                GROUP BY gap_key
            ) latest ON latest.max_id = d.id""",
        (system_id,),
    ).fetchall()
    return {row["gap_key"]: row for row in rows if row["gap_key"] in wanted}


def annotate_gaps(
    conn, system_id: int, snapshot_id: Optional[int], gaps: List[Dict[str, Any]]
) -> None:
    """Attach stable identity and effective triage state to live gaps.

    This function is read-only. Automatic reopen is materialized as an audit
    row by ``transition_gap`` when the next human action is submitted.
    """
    identities = [(gap_key(gap), gap_content_fingerprint(gap)) for gap in gaps]
    latest = _latest_rows(conn, system_id, (key for key, _ in identities))
    for gap, (key, fingerprint) in zip(gaps, identities):
        row = latest.get(key)
        status = row["status"] if row is not None else "open"
        reopen_reason = None
        if row is not None and status == "dismissed" and row["content_fingerprint"] != fingerprint:
            status = "open"
            reopen_reason = "content_changed"
        elif (
            row is not None
            and status == "resolved"
            and snapshot_id is not None
            and row["snapshot_id"] is not None
            and row["snapshot_id"] != snapshot_id
        ):
            status = "open"
            reopen_reason = "resolved_gap_reappeared"

        gap["gap_key"] = key
        gap["content_fingerprint"] = fingerprint
        gap["triage_status"] = status
        gap["triage_decision"] = dict(row) if row is not None else None
        gap["triage_reopen_reason"] = reopen_reason


def materialize_automatic_reopens(
    conn, system_id: int, snapshot_id: int, gaps: List[Dict[str, Any]]
) -> int:
    """Persist effective reopen events at build finalization.

    GET remains read-only. A settled System Understanding build is the
    explicit synchronization point that makes content-change/reappearance
    reopen durable and append-only, so returning to an older rendering cannot
    resurrect a previous dismissed/resolved decision.
    """
    annotate_gaps(conn, system_id, snapshot_id, gaps)
    reopened = 0
    for gap in gaps:
        reason = gap.get("triage_reopen_reason")
        if not reason:
            continue
        _insert_decision(
            conn,
            system_id=system_id,
            snapshot_id=snapshot_id,
            key=gap["gap_key"],
            fingerprint=gap["content_fingerprint"],
            status="open",
            user_id=None,
            decision_method="deterministic",
            note=reason,
        )
        reopened += 1
    if reopened:
        # Keep the caller's in-memory projection consistent with persisted
        # latest rows; in particular clear triage_reopen_reason.
        annotate_gaps(conn, system_id, snapshot_id, gaps)
    return reopened


def _insert_decision(
    conn,
    *,
    system_id: int,
    snapshot_id: int,
    key: str,
    fingerprint: str,
    status: str,
    user_id: Optional[int],
    decision_method: str,
    note: Optional[str],
) -> Any:
    cur = conn.execute(
        """INSERT INTO gap_triage_decisions
            (system_id, snapshot_id, gap_key, content_fingerprint, status,
             decided_by_user_id, decision_method, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id,
            snapshot_id,
            key,
            fingerprint,
            status,
            user_id,
            decision_method,
            note,
            time.time(),
        ),
    )
    return conn.execute("SELECT * FROM gap_triage_decisions WHERE id = ?", (cur.lastrowid,)).fetchone()


def transition_gap(
    conn,
    *,
    system_id: int,
    snapshot_id: int,
    gap: Dict[str, Any],
    expected_fingerprint: str,
    target_status: str,
    user_id: int,
    note: Optional[str] = None,
) -> Any:
    """Apply one validated manual transition to a currently visible gap."""
    if target_status not in TRIAGE_STATUSES:
        raise GapTransitionError(f"Unknown triage status: {target_status}")

    annotate_gaps(conn, system_id, snapshot_id, [gap])
    key = gap["gap_key"]
    fingerprint = gap["content_fingerprint"]
    if expected_fingerprint != fingerprint:
        raise GapFingerprintConflict(
            "Gap content changed; refresh System Understanding before triaging it."
        )

    current = gap["triage_status"]
    if target_status not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none"
        raise GapTransitionError(
            f"Invalid gap triage transition {current} -> {target_status}; allowed: {allowed}"
        )

    reopen_reason = gap.get("triage_reopen_reason")
    if reopen_reason:
        _insert_decision(
            conn,
            system_id=system_id,
            snapshot_id=snapshot_id,
            key=key,
            fingerprint=fingerprint,
            status="open",
            user_id=None,
            decision_method="deterministic",
            note=reopen_reason,
        )

    return _insert_decision(
        conn,
        system_id=system_id,
        snapshot_id=snapshot_id,
        key=key,
        fingerprint=fingerprint,
        status=target_status,
        user_id=user_id,
        decision_method="manual",
        note=note,
    )
