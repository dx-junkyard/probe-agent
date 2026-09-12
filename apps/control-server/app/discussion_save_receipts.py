"""Idempotent save-request receipts for prefill-to-save (Issue #452, Epic
#443 §3.4/§3.6/§3.7).

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §3 is the canonical contract. An EXISTING domain
"save" endpoint (today: `POST /ux-design/requirements/{key}/revisions`) may
accept an OPTIONAL `save_request_id` in its request body. This module is the
ONLY place that decides what a repeated id means -- it owns exactly one table
(`discussion_save_receipt`, `app/db.py`) and never writes any domain row
itself.

Contract (Issue #452 Decisions, verbatim):

- 保存は一つのフォームごとに明示操作。冪等 `save_request_id` を既存 domain 保存
  API の任意拡張として導入し、System / actor / target / body digest に bind
  する。**同一ID・同一内容は同じ結果、内容違いは409。**
- 保存 revision と request 結果の対応は同一 transaction で確定する。結果照会
  API を用意し、応答不明時は同じ ID で照会 / 再試行する。類似本文検索で成功と
  推測しない。完了 receipt は対応 revision の保持期間維持し、削除後も同じ ID
  を新規要求へ再利用しない。
- 保存失敗時 draft 保持。retry ボタンが新しい request ID を作らない。ユーザー
  が編集を変えたら新しい保存要求として扱う。

Calling convention every extended save route follows (see
`routes/ux_design.py`'s `add_requirement_revision_endpoint` for the reference
implementation `#454` extends to other targets):

    if payload.save_request_id:
        digest = compute_request_digest({...domain-meaningful fields...})
        existing = check_reusable(conn, system_id=..., save_request_id=...,
                                   request_digest=digest)
        if existing is not None and existing["status"] == "succeeded":
            return <the existing result, no domain write>
        # existing is None, or existing["status"] == "failed": fall through
        # and (re-)attempt the domain write below.
    try:
        detail = <call the domain write>
    except Exception:
        if payload.save_request_id:
            record_outcome(conn, ..., status="failed", error_code=...)
        raise
    if payload.save_request_id:
        record_outcome(conn, ..., status="succeeded", revision_id=..., result_ref=...)
    return detail

`record_outcome` is called on the SAME already-open connection, immediately
after the domain write's own commit returns control to the route -- with no
external call (LLM, subprocess, network) in between. The domain write
functions this wraps (`ux_design.add_requirement_revision`, ...) manage their
own internal `BEGIN`/`COMMIT` and are out of this Issue's edit scope, so the
receipt row is not literally inside the SAME SQL transaction as the revision
insert; it is the narrowest achievable window given that boundary, and it is
what makes a genuinely lost HTTP response (the domain write already
committed, but the client never saw the 2xx) recoverable via `find_receipt`
without ever risking a second write for the crash-mid-route case (astronomically
narrow, and unrecoverable by ANY receipt scheme once the domain commit itself
has already happened but this module's own row has not yet been written --
the same caveat every idempotency-key implementation on top of a
non-2PC store carries).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

SAVE_RECEIPT_STATUSES = ("succeeded", "failed")

#: Bound on the client-supplied id itself (defense in depth; the id is an
#: opaque token, never interpreted).
MAX_SAVE_REQUEST_ID_LENGTH = 200


class SaveRequestConflict(ValueError):
    """`save_request_id` already bound to a DIFFERENT request digest (409,
    per the Issue #452 Decisions: "内容違いは409"). Carries the existing
    receipt row so the caller can report a stable, honest error rather than
    silently overwriting or guessing which attempt "wins"."""

    def __init__(self, existing: Dict[str, Any]):
        super().__init__(
            f"save_request_id {existing['save_request_id']!r} is already bound to a different request"
        )
        self.existing = existing


def compute_request_digest(payload: Dict[str, Any]) -> str:
    """A canonical digest over the domain-meaningful request body. Callers
    MUST exclude `save_request_id` itself (identity, not content) and any
    field that is not part of what gets WRITTEN (e.g. a `change_note` is
    content and belongs; nothing about routing does)."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_receipt(conn: Any, system_id: int, save_request_id: str) -> Optional[Dict[str, Any]]:
    """Read-time lookup for the §3.7 result-query API and for
    `check_reusable` below. Never raises -- an absent receipt is a normal,
    expected outcome (the id has never been used, or nothing has committed
    for it yet)."""
    row = conn.execute(
        "SELECT * FROM discussion_save_receipt WHERE system_id = ? AND save_request_id = ?",
        (system_id, save_request_id),
    ).fetchone()
    return dict(row) if row is not None else None


def check_reusable(
    conn: Any, *, system_id: int, save_request_id: str, request_digest: str,
) -> Optional[Dict[str, Any]]:
    """First step of every extended save endpoint. Returns:

    - `None` -- no prior receipt for this id. The caller performs its normal
      domain write, then calls `record_outcome`.
    - a receipt dict with `status == "succeeded"` -- the caller MUST NOT
      repeat the domain write; it returns the receipt's own `result_ref` /
      `revision_id` (a lost-response retry converges on ONE revision, never
      two).
    - a receipt dict with `status == "failed"` -- nothing was persisted for
      this id yet (the prior attempt did not write a domain row), so the
      caller retries the domain write normally and calls `record_outcome`
      again with whatever the new attempt produces.

    Raises `SaveRequestConflict` when a receipt already exists for this id
    with a DIFFERENT `request_digest` -- reusing an id for different content
    is refused rather than silently treated as a new attempt (the Issue #452
    Decisions require the CLIENT to mint a new id once the developer edits
    the form after a failure).
    """
    existing = find_receipt(conn, system_id, save_request_id)
    if existing is None:
        return None
    if existing["request_digest"] != request_digest:
        raise SaveRequestConflict(existing)
    return existing


def record_outcome(
    conn: Any,
    *,
    system_id: int,
    save_request_id: str,
    actor: Optional[str],
    target_kind: str,
    target_ref: str,
    endpoint_kind: str,
    request_digest: str,
    status: str,
    result_ref: str = "",
    revision_id: Optional[int] = None,
    error_code: str = "",
    now: Optional[float] = None,
) -> None:
    """Insert or update the ONE receipt row for `(system_id,
    save_request_id)`. See the module docstring for the calling convention
    and the transaction-boundary caveat."""
    assert status in SAVE_RECEIPT_STATUSES, f"invalid discussion_save_receipt status: {status!r}"
    now = time.time() if now is None else now
    existing = find_receipt(conn, system_id, save_request_id)
    if existing is None:
        conn.execute(
            """INSERT INTO discussion_save_receipt
                   (system_id, save_request_id, actor, target_kind, target_ref, endpoint_kind,
                    request_digest, status, result_ref, revision_id, error_code, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, save_request_id, actor, target_kind, target_ref, endpoint_kind,
                request_digest, status, result_ref, revision_id, error_code, now, now,
            ),
        )
    else:
        # A retry of a previously-`failed` attempt (§ contract: "保存失敗時
        # draft 保持. retry ボタンが新しい request ID を作らない") lands here --
        # UPDATE, never a second INSERT, so `(system_id, save_request_id)`
        # always names exactly one row.
        conn.execute(
            """UPDATE discussion_save_receipt
                   SET status = ?, result_ref = ?, revision_id = ?, error_code = ?, updated_at = ?
               WHERE system_id = ? AND save_request_id = ?""",
            (status, result_ref, revision_id, error_code, now, system_id, save_request_id),
        )
