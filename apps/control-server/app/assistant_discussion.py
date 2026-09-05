"""Assistant discussion threads: target-scoped conversation persistence
(Issue #438, Epic #436; registry-backed since Issue #444, Epic #443 Phase 1).

`docs/assistant-discussion.md` §1 is the canonical contract for thread/turn
persistence and target-state derivation; `docs/ai-discussion-adapter.md` §1
is the canonical contract for the `DiscussionAdapter` registry this module
now sits on top of. This module owns:

- `thread_key` construction (screen_id|scope|target_kind|target_ref) --
  thread IDENTITY, never `(system_id, screen_id)` alone, so a Requirement A
  conversation and a Requirement B conversation on the same screen can never
  share a row,
- `evaluate_target_state`, the §1.3 first-match table that derives
  `current` / `stale` / `unresolvable` / `not_tracked` at READ time -- this
  is intentionally never a stored column, so an edited target cannot keep
  reporting a stale answer as current,
- thread/turn persistence (`resolve_or_create_thread` / `get_thread` /
  `list_threads` / `append_turn` / `recent_turns`).

The finite vocabularies (`DISCUSSION_SCOPES` / `DISCUSSION_TARGET_KINDS` /
`DISCUSSION_TARGET_STATES` / `DISCUSSION_SCREEN_IDS`), the `scope ->
target_kind` table (`SCOPE_TARGET_KINDS`), per-kind target resolution
(`resolve_target`), and per-kind route params (`route_params_for_target`)
are now DERIVED from `discussion_adapters.DISCUSSION_ADAPTERS` -- the single
registry `docs/ai-discussion-adapter.md` §1 introduces -- rather than
declared by hand here. `SCOPE_TARGET_KINDS` keeps its exact name and shape
(`Dict[str, Tuple[str, ...]]`) so existing importers/tests are unaffected by
the move; `tests/test_discussion_adapter_registry.py` is what proves the
derived values equal what they were before this move (Issue #444's
acceptance bar: the move must not change behaviour by one bit).

No LLM call anywhere in this module (Principle 6); it is a deterministic
persistence + resolution layer. Resolvers that need the same canonical
projection a screen reads (`overview_finding`) call it exactly as
`assistant_discussion_context.py` does, from OUTSIDE any held connection --
`build_overview` opens its own `get_conn()` internally, and `get_conn()` is
not reentrant (CLAUDE.md Implementation Constraints).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import discussion_adapters
from .db import get_conn

# Re-exported for backward compatibility -- callers that did
# `from .assistant_discussion import ResolvedTarget` (there are none left in
# this codebase, but the name stays public) keep working, and this module's
# own resolver plumbing below uses this alias throughout.
ResolvedTarget = discussion_adapters.ResolvedTarget

# --- §1.1 finite vocabularies (derived from the registry) ---------------------

DISCUSSION_SCOPES: Tuple[str, ...] = ("screen", "entity", "element")

DISCUSSION_TARGET_KINDS: Tuple[str, ...] = discussion_adapters.DISCUSSION_TARGET_KINDS

DISCUSSION_TARGET_STATES: Tuple[str, ...] = (
    "current", "stale", "unresolvable", "not_tracked",
)

# The 4 discussion-enabled screens (§1.1). Any other screen id keeps the
# pre-#438 client-only conversation -- the safe migration path.
DISCUSSION_SCREEN_IDS: Tuple[str, ...] = discussion_adapters.DISCUSSION_SCREEN_IDS

# scope -> allowed target_kind, first-match (§1.1's table), derived as the
# INVERSE of the registry's per-adapter `scope` (Issue #444 §1.1: "delete the
# hand-written dict; keep the exported name and its shape"). A combination
# outside this table is a 422 `discussion_target_scope_mismatch` (fail-closed).
def _build_scope_target_kinds() -> Dict[str, Tuple[str, ...]]:
    by_scope: Dict[str, List[str]] = {scope: [] for scope in DISCUSSION_SCOPES}
    for adapter in discussion_adapters.DISCUSSION_ADAPTERS.values():
        by_scope.setdefault(adapter.scope, []).append(adapter.target_kind)
    return {scope: tuple(kinds) for scope, kinds in by_scope.items()}


SCOPE_TARGET_KINDS: Dict[str, Tuple[str, ...]] = _build_scope_target_kinds()

THREAD_SCHEMA_VERSION = "assistant-discussion-thread-v1"
TURN_SCHEMA_VERSION = "assistant-discussion-turn-v1"

# §1.5: the server bounds the LLM context to the most recent N turns.
MAX_CONTEXT_TURNS = 12

# §1.5: at most this many discussion threads are listed per request.
MAX_LISTED_THREADS = 50


class DiscussionError(ValueError):
    """Base error for this module; routes translate these to HTTP errors."""


class ScopeMismatch(DiscussionError):
    """422 discussion_target_scope_mismatch (§1.1)."""


class UnregisteredTargetKind(DiscussionError):
    """422 discussion_target_kind_unregistered (Issue #444 §1.7).

    Reachable only when a `target_kind` string has no
    `discussion_adapters.DiscussionAdapter` at all -- a value the
    `DiscussionTargetKind` Literal at the API boundary already excludes for
    every ordinary HTTP caller, so this mainly guards a future phase that
    adds a Literal member before registering its adapter (the exact "forgot
    one of the six tables" failure mode §1.1 of
    `docs/ai-discussion-adapter.md` describes), and any direct Python caller
    of `resolve_or_create_thread`.
    """


class ScreenMismatch(DiscussionError):
    """422 discussion_target_screen_mismatch (Issue #444 §1.7): the
    `target_kind`'s adapter does not list this `screen_id` among the screens
    it may be opened from -- e.g. a `ux_journey_step` thread requested with
    `screen_id="overview"`. Checked AFTER `UnregisteredTargetKind` (a kind
    with no adapter has no `screen_ids` to check) and after `ScopeMismatch`
    (§1.7's own bullet order), and only when a thread is being CREATED -- see
    `resolve_or_create_thread`."""


def validate_scope(scope: str, target_kind: str) -> None:
    allowed = SCOPE_TARGET_KINDS.get(scope)
    if allowed is None or target_kind not in allowed:
        raise ScopeMismatch(
            f"discussion_target_scope_mismatch: scope={scope!r} does not allow "
            f"target_kind={target_kind!r}"
        )


def thread_key(screen_id: str, scope: str, target_kind: str, target_ref: str) -> str:
    """§1.2: the thread's canonical identity, never a row id."""
    return f"{screen_id}|{scope}|{target_kind}|{target_ref}"


# --- §1.2/§1.3 target resolution (delegates to the DiscussionAdapter registry) --


def resolve_target(system_id: int, target_kind: str, target_ref: str) -> ResolvedTarget:
    """Resolve one target against its canonical source. Never raises -- a
    stale/deleted/unknown target degrades to `resolution="unresolved"`
    (#437's rule: a stale deep link must not fail the whole assistant).

    Delegates to `discussion_adapters.DISCUSSION_ADAPTERS[target_kind].
    resolver` (Issue #444) -- an unregistered `target_kind` degrades exactly
    like an unresolvable one here (the registration itself is enforced,
    fail-closed, by `resolve_or_create_thread` below; this function's own
    contract has always been "never raises").
    """
    adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(target_kind)
    if adapter is None:
        return ResolvedTarget("", None, "", "unresolved")
    try:
        return adapter.resolver(system_id, target_ref)
    except Exception:  # pragma: no cover - defensive
        return ResolvedTarget("", None, "", "unresolved")


def evaluate_target_state(captured_digest: str, resolved: ResolvedTarget) -> str:
    """§1.3's first-match table. Derived at read time, never stored."""
    if resolved.resolution == "unresolved":
        return "unresolvable"
    if resolved.resolution == "not_tracked":
        return "not_tracked"
    if (captured_digest or "") != (resolved.digest or ""):
        return "stale"
    return "current"


# --- route params for the target's own canonical facts (§1.5) ----------------


def route_params_for_target(target_kind: str, target_ref: str) -> Dict[str, str]:
    """Route params `assistant_discussion_context.build_screen_discussion_context`
    already understands, so a thread's own target lands in the context pack.

    Delegates to `discussion_adapters.DISCUSSION_ADAPTERS[target_kind].
    route_params` (Issue #444). An unregistered kind degrades to `{}`, same
    as every per-kind branch this replaced defaulted to when no case matched.
    """
    adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(target_kind)
    if adapter is None:
        return {}
    return adapter.route_params(target_ref)


# --- persistence ---------------------------------------------------------------


def _turn_out(row: Any) -> Dict[str, Any]:
    data = dict(row)
    try:
        data["citations"] = json.loads(data.pop("citations_json") or "[]")
    except (TypeError, ValueError):
        data["citations"] = []
    data["used_fallback"] = bool(data.get("used_fallback"))
    return data


def _recent_turns_rows(conn, thread_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM assistant_discussion_turn WHERE thread_id = ? ORDER BY turn_number ASC",
        (thread_id,),
    ).fetchall()
    return [_turn_out(r) for r in rows]


def recent_turns(conn, thread_id: int, limit: int = MAX_CONTEXT_TURNS) -> List[Dict[str, Any]]:
    """The most recent `limit` turns, chronological order -- the bounded LLM
    context (§1.5). Callers gate this on `target_state` being `current` or
    `not_tracked` (§1.3): a stale/unresolvable thread's history is readable
    but never auto-inherited as current fact."""
    rows = conn.execute(
        "SELECT * FROM assistant_discussion_turn WHERE thread_id = ? "
        "ORDER BY turn_number DESC LIMIT ?",
        (thread_id, limit),
    ).fetchall()
    return [_turn_out(r) for r in reversed(rows)]


def resolve_or_create_thread(
    system_id: int,
    *,
    scope: str,
    screen_id: str,
    target_kind: str,
    target_ref: str,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """§1.5 `POST /assistant/discussion-threads`: idempotent resolve-or-create.

    The resolver runs BEFORE any connection here is opened -- some resolvers
    (`overview_finding`) open their own connection internally, and
    `get_conn()` is not reentrant. An existing thread's `captured_target_*`
    is left untouched by this call (only creation sets the baseline); it
    advances only when a turn is actually answered (`append_turn` callers
    pass the fresh resolution), so re-opening an unchanged thread never
    silently launders a `stale` state back to `current`.
    """
    if screen_id not in DISCUSSION_SCREEN_IDS:
        raise DiscussionError(f"unknown_screen_id: {screen_id!r}")
    if scope not in DISCUSSION_SCOPES:
        raise DiscussionError(f"unknown_scope: {scope!r}")
    # §1.7's bullet order: an entirely unregistered kind is a different
    # failure than a registered kind used with the wrong scope or screen --
    # checked first because a kind with no adapter has no `scope`/
    # `screen_ids` to compare against.
    adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(target_kind)
    if adapter is None:
        raise UnregisteredTargetKind(
            f"discussion_target_kind_unregistered: target_kind={target_kind!r}"
        )
    validate_scope(scope, target_kind)

    key = thread_key(screen_id, scope, target_kind, target_ref)
    resolved = resolve_target(system_id, target_kind, target_ref)
    now = time.time()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM assistant_discussion_thread WHERE system_id = ? AND thread_key = ?",
            (system_id, key),
        ).fetchone()
        if row is None:
            # §1.7's screen gate applies to CREATING a thread, not to
            # reopening one that already exists. The gate is new in Issue
            # #444 -- before it, any discussion-enabled screen could open any
            # kind -- so a row stored under a combination the registry now
            # narrows must stay reachable: refusing to reopen it protects
            # nothing and loses a real conversation. This is #337's
            # compatibility rule applied here (a legacy row stays READABLE;
            # it is never promoted to something it did not satisfy), and it
            # is why the check sits inside this branch rather than above the
            # lookup.
            if screen_id not in adapter.screen_ids:
                raise ScreenMismatch(
                    f"discussion_target_screen_mismatch: screen_id={screen_id!r} is not "
                    f"valid for target_kind={target_kind!r} (allowed: {adapter.screen_ids!r})"
                )
            conn.execute(
                """INSERT INTO assistant_discussion_thread
                   (system_id, thread_key, scope, screen_id, target_kind, target_ref,
                    target_title, captured_target_revision_id, captured_target_digest,
                    status, created_by, created_at, updated_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                (
                    system_id, key, scope, screen_id, target_kind, target_ref,
                    resolved.title, resolved.revision_id, resolved.digest,
                    created_by, now, now, THREAD_SCHEMA_VERSION,
                ),
            )
            thread_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            row = conn.execute(
                "SELECT * FROM assistant_discussion_thread WHERE id = ?", (thread_id,)
            ).fetchone()
        thread = dict(row)
        turns = _recent_turns_rows(conn, thread["id"])

    target_state = evaluate_target_state(thread["captured_target_digest"], resolved)
    return {"thread": thread, "target_state": target_state, "turns": turns}


def get_thread(system_id: int, thread_id: int) -> Optional[Dict[str, Any]]:
    """§1.5 `GET /assistant/discussion-threads/{id}`. A foreign/unknown
    thread returns `None` (the route turns that into 404)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM assistant_discussion_thread WHERE id = ? AND system_id = ?",
            (thread_id, system_id),
        ).fetchone()
        if row is None:
            return None
        thread = dict(row)
        turns = _recent_turns_rows(conn, thread_id)

    resolved = resolve_target(system_id, thread["target_kind"], thread["target_ref"])
    target_state = evaluate_target_state(thread["captured_target_digest"], resolved)
    return {"thread": thread, "target_state": target_state, "turns": turns}


def list_threads(
    system_id: int,
    *,
    screen_id: Optional[str] = None,
    scope: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_ref: Optional[str] = None,
    limit: int = MAX_LISTED_THREADS,
) -> List[Dict[str, Any]]:
    """§1.5 `GET /assistant/discussion-threads`. System-scoped, newest first,
    capped at `MAX_LISTED_THREADS`. Does not resolve targets (a listing of up
    to 50 threads must not fan out into 50 canonical-projection rebuilds)."""
    clauses = ["system_id = ?"]
    params: List[Any] = [system_id]
    if screen_id:
        clauses.append("screen_id = ?")
        params.append(screen_id)
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if target_kind:
        clauses.append("target_kind = ?")
        params.append(target_kind)
    if target_ref:
        clauses.append("target_ref = ?")
        params.append(target_ref)
    bounded_limit = max(1, min(int(limit or MAX_LISTED_THREADS), MAX_LISTED_THREADS))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM assistant_discussion_thread WHERE {' AND '.join(clauses)} "
            "ORDER BY id DESC LIMIT ?",
            (*params, bounded_limit),
        ).fetchall()
        return [dict(r) for r in rows]


def append_turn(
    conn,
    *,
    system_id: int,
    thread_id: int,
    role: str,
    content: str,
    citations: Optional[Sequence[Dict[str, Any]]] = None,
    target_revision_id: Optional[int] = None,
    target_digest: str = "",
    used_fallback: bool = False,
    decision_method: str,
    input_mode: str = "text",
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert one turn on an ALREADY-OPEN connection -- callers that append a
    user turn and its assistant answer wrap both calls (and any thread
    `updated_at` bump) in one BEGIN/COMMIT so the pair is atomic (§1.5)."""
    if role not in ("user", "assistant"):
        raise DiscussionError(f"invalid_role: {role!r}")
    if decision_method not in ("manual", "reasoning_llm", "deterministic"):
        raise DiscussionError(f"invalid_decision_method: {decision_method!r}")
    if input_mode not in ("text", "voice"):
        raise DiscussionError(f"invalid_input_mode: {input_mode!r}")

    next_row = conn.execute(
        "SELECT COALESCE(MAX(turn_number), 0) AS n FROM assistant_discussion_turn WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    turn_number = int(next_row["n"]) + 1
    now = time.time()
    conn.execute(
        """INSERT INTO assistant_discussion_turn
           (system_id, thread_id, turn_number, role, content, citations_json,
            target_revision_id, target_digest, used_fallback, decision_method,
            input_mode, provider, model, prompt_version, created_by, created_at,
            schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, thread_id, turn_number, role, content,
            json.dumps(list(citations or []), ensure_ascii=False),
            target_revision_id, target_digest, 1 if used_fallback else 0,
            decision_method, input_mode, provider, model, prompt_version,
            created_by, now, TURN_SCHEMA_VERSION,
        ),
    )
    turn_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    row = conn.execute(
        "SELECT * FROM assistant_discussion_turn WHERE id = ?", (turn_id,)
    ).fetchone()
    return _turn_out(row)


def touch_thread_captured_target(conn, thread_id: int, resolved: ResolvedTarget) -> None:
    """Refresh the thread's captured baseline to the resolution a turn was
    just answered against -- called on the SAME connection/transaction as the
    turn insert. This is the only place `captured_target_*` changes after
    creation, so a plain reload (`resolve_or_create_thread` / `get_thread`)
    never silently marks a `stale` thread `current` again -- only actually
    answering a new turn against the current target does."""
    conn.execute(
        "UPDATE assistant_discussion_thread SET captured_target_revision_id = ?, "
        "captured_target_digest = ?, target_title = ?, updated_at = ? WHERE id = ?",
        (resolved.revision_id, resolved.digest, resolved.title or "", time.time(), thread_id),
    )
