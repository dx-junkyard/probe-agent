"""Natural-language bulk correction -> structured change set API (Issue #289).

Lets a developer write one free-text correction covering multiple
Understanding items ("実は目標はAではなくBです。あと認証まわりの説明も違い
ます"). The text itself is NEVER applied to state (Principle 2/6): a
reasoning LLM (``app/change_sets.py``, fail-closed) turns it into itemized
edit proposals grounded in a finite candidate list, a deterministic post-
pass resolves each item's target, the developer previews the field-level
diffs + a deterministic impact preview, and only the items they explicitly
select are ever applied.

Kept in its own module (like Issues #284/#285/#287/#288) rather than
growing ``routes/interview.py`` further, per CLAUDE.md's guidance for this
sub-issue.

Scope is enforced by construction: the whitelist in
``app/change_sets.py.ALLOWED_TARGET_FIELDS`` only ever addresses
``interview_intent_item.value_text`` (via a fresh supersede row, exactly
like ``interview_intent.correct_intent_item``) and one item's ``summary``
inside the latest ``understanding_revision.current_understanding`` JSON (via
a NEW revision row, deep-copied and edited -- never mutated in place). No
other table or field is ever reachable from this flow.

probe-agent:
  role: API boundary for the natural-language bulk correction -> structured
    change-set preview/selective-apply flow
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify NL text is never written to state directly (only resolved, selected items are), that an item outside the (target_kind, field) whitelist always ends up 'forbidden', and that apply revalidates each selected item against current state before applying it (never applies a stale/conflicting item).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..change_sets import (
    UNDERSTANDING_SECTIONS,
    ChangeSetProposalResult,
    effective_resolution_state,
    generate_change_set_proposal,
    resolve_change_set_items,
)
from ..db import get_conn
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    ChangeSetApplyRequest,
    ChangeSetApplyResultOut,
    ChangeSetCreateRequest,
    ChangeSetDetailOut,
    ChangeSetItemOut,
    ChangeSetOut,
    ChangeSetSkippedItemOut,
)
from ..state_messages import change_set_message

router = APIRouter()


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_change_set_or_404(conn, change_set_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM understanding_change_set WHERE id = ? AND system_id = ?",
        (change_set_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Change set not found")
    return row


def _latest_revision_row(conn, session_id: int, system_id: int):
    return conn.execute(
        """SELECT * FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()


def _change_set_out(row) -> ChangeSetOut:
    return ChangeSetOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        base_revision_id=row["base_revision_id"],
        source_text=row["source_text"],
        status=row["status"],
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# --- Candidate list (deterministic, DB-backed) -------------------------------


def _build_candidates(conn, session_id: int, system_id: int) -> List[Dict[str, Any]]:
    """Finite list of edit targets the LLM may address, each with a unique
    ``(kind, hint)`` the model must echo back verbatim (see
    ``app/change_sets.py``'s system prompt)."""
    candidates: List[Dict[str, Any]] = []

    intent_rows = conn.execute(
        """SELECT * FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
           ORDER BY field, id""",
        (session_id, system_id),
    ).fetchall()
    for r in intent_rows:
        candidates.append({
            "kind": "intent_item",
            "hint": r["field"],
            "current_value": r["value_text"],
            "id": r["id"],
        })

    revision = _latest_revision_row(conn, session_id, system_id)
    current_understanding = (
        json.loads(revision["current_understanding"])
        if revision is not None and revision["current_understanding"] else {}
    )
    for section in UNDERSTANDING_SECTIONS:
        for item in current_understanding.get(section, []) or []:
            name = item.get("name")
            if not name:
                continue
            candidates.append({
                "kind": "understanding_claim",
                "hint": f"{section}::{name}",
                "current_value": item.get("summary", ""),
                "section": section,
                "name": name,
            })

    return candidates


# --- Impact preview (deterministic structural match, Principle 6) -----------


def _affected_alignment_items(conn, session_id: int, system_id: int, item_row) -> List[dict]:
    target_ref = json.loads(item_row["target_ref"]) if item_row["target_ref"] else {}
    if item_row["target_kind"] == "intent_item" and "intent_item_id" in target_ref:
        rows = conn.execute(
            """SELECT id, current_claim, review_category FROM alignment_item
               WHERE session_id = ? AND system_id = ? AND intent_item_id = ?""",
            (session_id, system_id, target_ref["intent_item_id"]),
        ).fetchall()
    elif item_row["target_kind"] == "understanding_claim" and "name" in target_ref:
        name = target_ref["name"]
        like = f"%{name}%"
        rows = conn.execute(
            """SELECT id, current_claim, review_category FROM alignment_item
               WHERE session_id = ? AND system_id = ?
                 AND (current_claim LIKE ? OR intent_summary LIKE ?)""",
            (session_id, system_id, like, like),
        ).fetchall()
    else:
        rows = []
    return [
        {"alignment_item_id": r["id"], "current_claim": r["current_claim"], "review_category": r["review_category"]}
        for r in rows
    ]


# --- Re-validation (shared by preview + apply) -------------------------------


def _current_value_for(conn, session_id: int, system_id: int, item_row, latest_revision) -> Optional[str]:
    """Live current value of an item's target, or None if the target no
    longer exists / was superseded. Feeds ``effective_resolution_state``'s
    ``current_value``/``current_value is None`` (-> 'conflict') check."""
    target_ref = json.loads(item_row["target_ref"]) if item_row["target_ref"] else {}
    if item_row["target_kind"] == "intent_item":
        intent_id = target_ref.get("intent_item_id")
        if intent_id is None:
            return None
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ? AND system_id = ?",
            (intent_id, system_id),
        ).fetchone()
        if row is None or row["superseded_by_id"] is not None:
            return None
        return row["value_text"]
    if item_row["target_kind"] == "understanding_claim":
        section = target_ref.get("section")
        name = target_ref.get("name")
        if section is None or name is None or latest_revision is None:
            return None
        current_understanding = (
            json.loads(latest_revision["current_understanding"])
            if latest_revision["current_understanding"] else {}
        )
        for entry in current_understanding.get(section, []) or []:
            if entry.get("name") == name:
                return entry.get("summary", "")
        return None
    return None


def _effective_state_for_item(conn, session_id: int, system_id: int, item_row, latest_revision) -> str:
    latest_revision_id = latest_revision["id"] if latest_revision is not None else None
    current_value = _current_value_for(conn, session_id, system_id, item_row, latest_revision)
    change_set = conn.execute(
        "SELECT base_revision_id FROM understanding_change_set WHERE id = ?",
        (item_row["change_set_id"],),
    ).fetchone()
    base_revision_id = change_set["base_revision_id"] if change_set is not None else None
    return effective_resolution_state(
        stored_state=item_row["resolution_state"],
        target_kind=item_row["target_kind"],
        before_value=item_row["before_value"],
        base_revision_id=base_revision_id,
        latest_revision_id=latest_revision_id,
        current_value=current_value,
    )


def _item_out(conn, session_id: int, system_id: int, item_row, latest_revision) -> ChangeSetItemOut:
    effective_state = _effective_state_for_item(conn, session_id, system_id, item_row, latest_revision)
    affected = (
        _affected_alignment_items(conn, session_id, system_id, item_row)
        if effective_state == "resolved" else []
    )
    return ChangeSetItemOut(
        id=item_row["id"],
        change_set_id=item_row["change_set_id"],
        system_id=item_row["system_id"],
        target_kind=item_row["target_kind"],
        target_ref=json.loads(item_row["target_ref"]) if item_row["target_ref"] else {},
        field=item_row["field"],
        before_value=item_row["before_value"],
        after_value=item_row["after_value"],
        reason=item_row["reason"],
        resolution_state=effective_state,
        applied=bool(item_row["applied"]),
        applied_at=item_row["applied_at"],
        created_at=item_row["created_at"],
        affected_items=affected,
    )


# --- Create -------------------------------------------------------------------


@router.post(
    "/interview/sessions/{session_id}/change-sets",
    response_model=ChangeSetDetailOut,
    status_code=201,
)
def create_change_set(
    session_id: int,
    payload: ChangeSetCreateRequest,
    system_id: int = Depends(get_system_id),
) -> ChangeSetDetailOut:
    """Turn a free-text bulk correction into a previewable structured change
    set. Fail-closed (Principle 6): an LLM/config/validation failure still
    records the change set (``status='failed'``, audit trail) but creates no
    change items, and the request itself fails with 502 -- nothing is ever
    applied from a failed proposal."""
    now = time.time()
    config = LLMConfig.intelligence_from_env()
    client_error: Optional[str] = None
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        client = None
        client_error = str(exc)

    # Phase 1 (DB lock held): deterministic reads only.
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        revision = _latest_revision_row(conn, session_id, system_id)
        candidates = _build_candidates(conn, session_id, system_id)

    # Phase 2 (DB lock released): the reasoning call. The LLM client opens its
    # own connection for System quota, so it must not run while this thread
    # holds one (see app/db.py).
    if client_error is not None:
        proposal = ChangeSetProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            error=client_error,
        )
    else:
        proposal = generate_change_set_proposal(
            client, config, source_text=payload.text, candidates=candidates,
        )
    completed_at = time.time()

    # Phase 3 (DB lock re-acquired): persist the audit run, the change set and
    # its items.
    with get_conn() as conn:
        run_status = "failed" if proposal.error else "completed"
        run_cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'nl_change_set', ?, ?, ?, ?, 'reasoning_llm', ?,
                    ?, ?, ?, ?)
            """,
            (
                system_id,
                session["snapshot_id"],
                proposal.provider,
                proposal.model,
                proposal.prompt_version,
                proposal.schema_version,
                run_status,
                proposal.error,
                1 if proposal.is_mock else 0,
                now,
                completed_at,
            ),
        )
        run_id = run_cur.lastrowid

        change_set_cur = conn.execute(
            """INSERT INTO understanding_change_set
                (session_id, system_id, base_revision_id, source_text, status,
                 intelligence_run_id, is_mock, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, system_id,
                revision["id"] if revision is not None else None,
                payload.text,
                "failed" if proposal.error else "proposed",
                run_id,
                1 if proposal.is_mock else 0,
                completed_at, completed_at,
            ),
        )
        change_set_id = change_set_cur.lastrowid

        if proposal.error:
            raise HTTPException(status_code=502, detail=proposal.error)

        resolved_items = resolve_change_set_items(proposal.items, candidates)
        for it in resolved_items:
            conn.execute(
                """INSERT INTO understanding_change_item
                    (change_set_id, system_id, target_kind, target_ref, field,
                     before_value, after_value, reason, resolution_state,
                     applied, applied_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
                (
                    change_set_id, system_id, it.target_kind,
                    json.dumps(it.target_ref, ensure_ascii=False), it.field,
                    it.before_value, it.after_value, it.reason,
                    it.resolution_state, completed_at,
                ),
            )

        change_set_row = conn.execute(
            "SELECT * FROM understanding_change_set WHERE id = ?", (change_set_id,)
        ).fetchone()
        item_rows = conn.execute(
            "SELECT * FROM understanding_change_item WHERE change_set_id = ? ORDER BY id",
            (change_set_id,),
        ).fetchall()
        latest_revision = _latest_revision_row(conn, session_id, system_id)
        return ChangeSetDetailOut(
            change_set=_change_set_out(change_set_row),
            items=[_item_out(conn, session_id, system_id, r, latest_revision) for r in item_rows],
            rebuild_note=change_set_message("rebuild_note"),
        )


# --- Preview ------------------------------------------------------------------


@router.get(
    "/interview/change-sets/{change_set_id}",
    response_model=ChangeSetDetailOut,
)
def get_change_set(
    change_set_id: int,
    system_id: int = Depends(get_system_id),
) -> ChangeSetDetailOut:
    """Field-level diff preview + deterministic impact preview.

    resolution_state here is the item's EFFECTIVE state, re-validated
    against current data on every call (see ``effective_resolution_state``)
    -- it may differ from what was stored at creation time if the
    understanding moved on or the target changed since. This call never
    persists that recomputed state (read-only); only ``apply`` writes a
    transition, and only for an item it actually skips because of it. The
    change set's own ``status`` DOES advance from 'proposed' to 'previewed'
    on first view (a plain state transition, not a reinterpretation).
    """
    with get_conn() as conn:
        change_set_row = _get_change_set_or_404(conn, change_set_id, system_id)
        if change_set_row["status"] == "proposed":
            conn.execute(
                "UPDATE understanding_change_set SET status = 'previewed', updated_at = ? WHERE id = ?",
                (time.time(), change_set_id),
            )
            change_set_row = conn.execute(
                "SELECT * FROM understanding_change_set WHERE id = ?", (change_set_id,)
            ).fetchone()

        item_rows = conn.execute(
            "SELECT * FROM understanding_change_item WHERE change_set_id = ? ORDER BY id",
            (change_set_id,),
        ).fetchall()
        latest_revision = _latest_revision_row(
            conn, change_set_row["session_id"], system_id,
        )
        return ChangeSetDetailOut(
            change_set=_change_set_out(change_set_row),
            items=[
                _item_out(conn, change_set_row["session_id"], system_id, r, latest_revision)
                for r in item_rows
            ],
            rebuild_note=change_set_message("rebuild_note"),
        )


# --- Apply ----------------------------------------------------------------


def _apply_intent_item(conn, item_row, session_id: int, system_id: int, source_text: str, now: float) -> None:
    target_ref = json.loads(item_row["target_ref"])
    intent_id = target_ref["intent_item_id"]
    old_row = conn.execute(
        "SELECT * FROM interview_intent_item WHERE id = ? AND system_id = ?",
        (intent_id, system_id),
    ).fetchone()
    conn.execute("BEGIN")
    try:
        new_cur = conn.execute(
            """INSERT INTO interview_intent_item
                (session_id, system_id, field, value_text, status, origin,
                 source_statement, decision_method, intelligence_run_id, is_mock,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 'confirmed', 'user', ?, 'manual', NULL, 0, ?, ?)""",
            (
                session_id, system_id, old_row["field"], item_row["after_value"],
                source_text, now, now,
            ),
        )
        new_id = new_cur.lastrowid
        conn.execute(
            "UPDATE interview_intent_item SET superseded_by_id = ?, updated_at = ? WHERE id = ?",
            (new_id, now, intent_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _apply_claim_edits(
    conn, session_id: int, system_id: int, claim_items: list, latest_revision, change_set_row, now: float,
) -> int:
    """Apply every selected understanding_claim edit as ONE new
    understanding_revision (deep-copy + edit + append -- never mutated in
    place). Applying them together, against the single ``latest_revision``
    snapshot taken once at the start of this apply call, avoids one edit's
    new revision retroactively making a sibling edit in the same batch look
    'stale'."""
    current_understanding = (
        json.loads(latest_revision["current_understanding"])
        if latest_revision["current_understanding"] else {}
    )
    for item_row in claim_items:
        target_ref = json.loads(item_row["target_ref"])
        section, name = target_ref["section"], target_ref["name"]
        for entry in current_understanding.get(section, []) or []:
            if entry.get("name") == name:
                entry["summary"] = item_row["after_value"]
                break

    revision_cur = conn.execute(
        """INSERT INTO understanding_revision
            (session_id, system_id, snapshot_id, intelligence_run_id,
             current_understanding, gap_analysis, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, system_id, latest_revision["snapshot_id"],
            change_set_row["intelligence_run_id"],
            json.dumps(current_understanding, ensure_ascii=False),
            latest_revision["gap_analysis"], now,
        ),
    )
    return revision_cur.lastrowid


@router.post(
    "/interview/change-sets/{change_set_id}/apply",
    response_model=ChangeSetApplyResultOut,
)
def apply_change_set(
    change_set_id: int,
    payload: ChangeSetApplyRequest,
    system_id: int = Depends(get_system_id),
) -> ChangeSetApplyResultOut:
    """Apply only the listed, currently-resolved items.

    Every selected item is revalidated against current state at this exact
    moment (not the state it was resolved against at creation/preview time)
    -- fail-closed per item, never per whole batch: an item that is no
    longer resolvable is skipped and reported with a reason, while every
    other still-valid selected item is still applied. Calling apply twice
    with the same item_ids is a safe no-op for the already-applied ones
    (the ``applied`` flag guards re-application).
    """
    now = time.time()
    with get_conn() as conn:
        change_set_row = _get_change_set_or_404(conn, change_set_id, system_id)
        if change_set_row["status"] == "discarded":
            raise HTTPException(status_code=409, detail="This change set has been discarded")
        if change_set_row["status"] == "failed":
            raise HTTPException(status_code=409, detail="This change set failed to generate; nothing to apply")

        session_id = change_set_row["session_id"]
        latest_revision = _latest_revision_row(conn, session_id, system_id)

        placeholders = ",".join("?" for _ in payload.item_ids)
        item_rows = conn.execute(
            f"""SELECT * FROM understanding_change_item
                WHERE change_set_id = ? AND system_id = ? AND id IN ({placeholders})
                ORDER BY id""",
            (change_set_id, system_id, *payload.item_ids),
        ).fetchall()
        found_ids = {r["id"] for r in item_rows}
        missing = [i for i in payload.item_ids if i not in found_ids]
        if missing:
            raise HTTPException(status_code=404, detail=f"Change item(s) not found: {missing}")

        applied_ids: List[int] = []
        skipped: List[ChangeSetSkippedItemOut] = []
        claim_items_to_apply = []

        for item_row in item_rows:
            if item_row["applied"]:
                skipped.append(ChangeSetSkippedItemOut(
                    item_id=item_row["id"], resolution_state=item_row["resolution_state"],
                    message=change_set_message("skip_already_applied"),
                ))
                continue

            effective_state = _effective_state_for_item(
                conn, session_id, system_id, item_row, latest_revision,
            )
            if effective_state != "resolved":
                if effective_state != item_row["resolution_state"]:
                    conn.execute(
                        "UPDATE understanding_change_item SET resolution_state = ? WHERE id = ?",
                        (effective_state, item_row["id"]),
                    )
                skipped.append(ChangeSetSkippedItemOut(
                    item_id=item_row["id"], resolution_state=effective_state,
                    message=change_set_message(f"skip_{effective_state}"),
                ))
                continue

            if item_row["target_kind"] == "intent_item":
                _apply_intent_item(
                    conn, item_row, session_id, system_id, change_set_row["source_text"], now,
                )
                conn.execute(
                    "UPDATE understanding_change_item SET applied = 1, applied_at = ? WHERE id = ?",
                    (now, item_row["id"]),
                )
                applied_ids.append(item_row["id"])
            else:
                claim_items_to_apply.append(item_row)

        result_revision_id: Optional[int] = None
        if claim_items_to_apply:
            result_revision_id = _apply_claim_edits(
                conn, session_id, system_id, claim_items_to_apply, latest_revision, change_set_row, now,
            )
            for item_row in claim_items_to_apply:
                conn.execute(
                    "UPDATE understanding_change_item SET applied = 1, applied_at = ? WHERE id = ?",
                    (now, item_row["id"]),
                )
                applied_ids.append(item_row["id"])

        if applied_ids and not skipped:
            new_status = "applied"
        elif applied_ids:
            new_status = "partially_applied"
        else:
            new_status = change_set_row["status"]
        if new_status != change_set_row["status"]:
            conn.execute(
                "UPDATE understanding_change_set SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, change_set_id),
            )

        change_set_row = conn.execute(
            "SELECT * FROM understanding_change_set WHERE id = ?", (change_set_id,)
        ).fetchone()
        result = ChangeSetApplyResultOut(
            change_set=_change_set_out(change_set_row),
            applied_item_ids=applied_ids,
            skipped=skipped,
            result_revision_id=result_revision_id,
        )

    if applied_ids:
        # Issue #288: refresh Understanding/Alignment/Review Queue now that
        # the change is durably committed (see
        # interview_intent.confirm_intent_item's comment for why this call
        # sits outside the `with get_conn()` block).
        from ..interview_refresh import request_refresh

        request_refresh(session_id, system_id, "nl_change_set")

    return result


# --- Discard --------------------------------------------------------------


@router.post(
    "/interview/change-sets/{change_set_id}/discard",
    response_model=ChangeSetOut,
)
def discard_change_set(
    change_set_id: int,
    system_id: int = Depends(get_system_id),
) -> ChangeSetOut:
    now = time.time()
    with get_conn() as conn:
        row = _get_change_set_or_404(conn, change_set_id, system_id)
        if row["status"] == "discarded":
            raise HTTPException(status_code=409, detail="This change set is already discarded")
        conn.execute(
            "UPDATE understanding_change_set SET status = 'discarded', updated_at = ? WHERE id = ?",
            (now, change_set_id),
        )
        row = conn.execute(
            "SELECT * FROM understanding_change_set WHERE id = ?", (change_set_id,)
        ).fetchone()
        return _change_set_out(row)
