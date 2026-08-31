"""Assistant discussion threads: target-scoped conversation persistence
(Issue #438, Epic #436).

`docs/assistant-discussion.md` §1 is the canonical contract. This module
owns:

- the finite vocabularies §1.1 pins (`DISCUSSION_SCOPES` /
  `DISCUSSION_TARGET_KINDS` / `DISCUSSION_TARGET_STATES` /
  `DISCUSSION_SCREEN_IDS`) and the `scope -> target_kind` first-match table,
- `thread_key` construction (screen_id|scope|target_kind|target_ref) --
  thread IDENTITY, never `(system_id, screen_id)` alone, so a Requirement A
  conversation and a Requirement B conversation on the same screen can never
  share a row,
- a per-kind resolver registry that reads the SAME canonical source the
  corresponding Dashboard screen reads, to compute a target's current
  title/revision/digest without ever raising on a missing target (a stale or
  deleted deep link degrades to `resolution="unresolved"`, never a 500 --
  the rule Issue #437 already established for screen discussion context),
- `evaluate_target_state`, the §1.3 first-match table that derives
  `current` / `stale` / `unresolvable` / `not_tracked` at READ time -- this
  is intentionally never a stored column, so an edited target cannot keep
  reporting a stale answer as current,
- thread/turn persistence (`resolve_or_create_thread` / `get_thread` /
  `list_threads` / `append_turn` / `recent_turns`).

No LLM call anywhere in this module (Principle 6); it is a deterministic
persistence + resolution layer. Resolvers that need the same canonical
projection a screen reads (`overview_finding`) call it exactly as
`assistant_discussion_context.py` does, from OUTSIDE any held connection --
`build_overview` opens its own `get_conn()` internally, and `get_conn()` is
not reentrant (CLAUDE.md Implementation Constraints).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .db import get_conn

# --- §1.1 finite vocabularies -------------------------------------------------

DISCUSSION_SCOPES: Tuple[str, ...] = ("screen", "entity", "element")

DISCUSSION_TARGET_KINDS: Tuple[str, ...] = (
    "screen",
    "interview_session",
    "understanding_claim",
    "overview_finding",
    "ux_journey",
    "ux_journey_step",
    "ux_requirement",
    "solution_design",
    "blueprint_lane_cell",
)

DISCUSSION_TARGET_STATES: Tuple[str, ...] = (
    "current", "stale", "unresolvable", "not_tracked",
)

# The 4 discussion-enabled screens (§1.1). Any other screen id keeps the
# pre-#438 client-only conversation -- the safe migration path.
DISCUSSION_SCREEN_IDS: Tuple[str, ...] = (
    "overview", "interview", "ux-design-studio", "journey-blueprint",
)

# scope -> allowed target_kind, first-match (§1.1's table). A combination
# outside this table is a 422 `discussion_target_scope_mismatch` (fail-closed).
SCOPE_TARGET_KINDS: Dict[str, Tuple[str, ...]] = {
    "screen": ("screen",),
    "entity": ("interview_session", "ux_journey", "ux_requirement", "solution_design"),
    "element": (
        "understanding_claim", "overview_finding", "ux_journey_step", "blueprint_lane_cell",
    ),
}

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


# --- §1.2/§1.3 target resolution ----------------------------------------------


@dataclass(frozen=True)
class ResolvedTarget:
    title: str
    revision_id: Optional[int]
    digest: str
    resolution: str  # "resolved" | "unresolved" | "not_tracked"


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_json_digest(text: Optional[str]) -> str:
    if not text:
        canonical: Any = None
    else:
        try:
            canonical = json.loads(text)
        except (TypeError, ValueError):
            canonical = text
    return _canonical_digest(canonical)


def _resolve_screen(system_id: int, target_ref: str) -> ResolvedTarget:
    # `screen` has no digest source (§1.2): never `stale`, always `not_tracked`.
    return ResolvedTarget(title=target_ref, revision_id=None, digest="", resolution="not_tracked")


def _resolve_interview_session(system_id: int, target_ref: str) -> ResolvedTarget:
    try:
        session_id = int(target_ref)
    except (TypeError, ValueError):
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, current_understanding FROM interview_session "
            "WHERE id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchone()
    if row is None:
        return ResolvedTarget("", None, "", "unresolved")
    digest = _normalized_json_digest(row["current_understanding"])
    title = row["title"] or f"Interview session #{session_id}"
    return ResolvedTarget(title=title, revision_id=session_id, digest=digest, resolution="resolved")


_CLAIM_SECTIONS: Tuple[str, ...] = ("vision", "system_purpose", "core_capabilities")


def _resolve_understanding_claim(system_id: int, target_ref: str) -> ResolvedTarget:
    section, sep, name = target_ref.partition(":")
    if not sep or section not in _CLAIM_SECTIONS or not name:
        return ResolvedTarget("", None, "", "unresolved")

    from . import understanding_brief

    with get_conn() as conn:
        session_row = conn.execute(
            "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        session_id = session_row["id"] if session_row is not None else None
        try:
            brief = understanding_brief.build_understanding_brief(conn, system_id, session_id)
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")

        claims = {
            "vision": [brief.vision] if brief.vision is not None else [],
            "system_purpose": brief.system_purpose,
            "core_capabilities": brief.core_capabilities,
        }[section]
        claim = next((c for c in claims if c.name == name), None)
        if claim is None:
            return ResolvedTarget("", None, "", "unresolved")

        raw_item: Optional[Dict[str, Any]] = None
        if session_id is not None:
            understanding_row = conn.execute(
                "SELECT current_understanding FROM interview_session WHERE id = ?",
                (session_id,),
            ).fetchone()
            if understanding_row is not None and understanding_row["current_understanding"]:
                try:
                    parsed = json.loads(understanding_row["current_understanding"])
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    for item in parsed.get(section) or []:
                        if isinstance(item, dict) and str(item.get("name")) == name:
                            raw_item = item
                            break

    # A resolved claim with no findable raw item (defensive: the claim
    # resolved successfully because the NAME matched) degrades the digest to
    # "" rather than fabricating one that was never truly captured -- the
    # same rule `product_objective._resolve_vision_target` applies.
    digest = understanding_brief.claim_digest(raw_item) if raw_item is not None else ""
    return ResolvedTarget(title=claim.name, revision_id=session_id, digest=digest, resolution="resolved")


def _resolve_overview_finding(system_id: int, target_ref: str) -> ResolvedTarget:
    from .overview_projection import build_overview

    try:
        overview = build_overview(system_id)
    except Exception:  # pragma: no cover - defensive
        return ResolvedTarget("", None, "", "unresolved")
    finding = next((f for f in overview.findings if f.dedupe_key == target_ref), None)
    if finding is None:
        return ResolvedTarget("", None, "", "unresolved")
    payload = {
        "kind": finding.kind,
        "severity": finding.severity,
        "summary": finding.summary,
        "decision_impact": finding.decision_impact,
        "provenance": finding.provenance,
        "dedupe_key": finding.dedupe_key,
    }
    return ResolvedTarget(
        title=finding.summary,
        revision_id=finding.revision_id,
        digest=_canonical_digest(payload),
        resolution="resolved",
    )


def _resolve_ux_journey(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    with get_conn() as conn:
        try:
            detail = ux_design.get_journey_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_ux_journey_step(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    journey_key, sep, step_key = target_ref.partition("#")
    if not sep or not journey_key or not step_key:
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        journey = ux_design._get_journey_row(conn, system_id, journey_key)  # noqa: SLF001 - established cross-module reuse (see product_objective.py)
        if journey is None:
            return ResolvedTarget("", None, "", "unresolved")
        resolved = ux_design._resolve_step_target(conn, system_id, journey["id"], step_key)  # noqa: SLF001
    if resolved["resolution"] != "resolved":
        return ResolvedTarget("", None, "", "unresolved")
    title = resolved.get("label") or step_key
    return ResolvedTarget(
        title=title,
        revision_id=journey.get("current_revision_id"),
        digest=resolved.get("digest") or "",
        resolution="resolved",
    )


def _resolve_ux_requirement(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    with get_conn() as conn:
        try:
            detail = ux_design.get_requirement_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("statement") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_solution_design(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import solution_design

    with get_conn() as conn:
        try:
            detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=target_ref)
        except solution_design.SolutionDesignNotFoundError:
            return ResolvedTarget("", None, "", "unresolved")
    # solution_design carries no revision table (flat identity row); its
    # content digest is over the row's own meaning-bearing fields, using its
    # own canonicalization function (never a separate one here).
    digest = solution_design.content_digest(
        {"title": detail.get("title") or "", "summary": detail.get("summary") or ""}
    )
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_blueprint_lane_cell(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import journey_blueprint

    parts = target_ref.split("#")
    if len(parts) != 3 or not all(parts):
        return ResolvedTarget("", None, "", "unresolved")
    journey_key, step_key, lane_kind = parts
    if lane_kind not in journey_blueprint.LANE_KINDS:
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        try:
            blueprint = journey_blueprint.build_blueprint(conn, system_id, journey_key)
        except journey_blueprint.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    step = next((s for s in blueprint["steps"] if s["step_key"] == step_key), None)
    if step is None:
        return ResolvedTarget("", None, "", "unresolved")
    cell = step.get("lanes", {}).get(lane_kind)
    if cell is None:
        return ResolvedTarget("", None, "", "unresolved")
    title = f"{lane_kind} / {step.get('user_intent') or step_key}"
    return ResolvedTarget(
        title=title, revision_id=None, digest=_canonical_digest(cell), resolution="resolved",
    )


_TARGET_RESOLVERS: Dict[str, Callable[[int, str], ResolvedTarget]] = {
    "screen": _resolve_screen,
    "interview_session": _resolve_interview_session,
    "understanding_claim": _resolve_understanding_claim,
    "overview_finding": _resolve_overview_finding,
    "ux_journey": _resolve_ux_journey,
    "ux_journey_step": _resolve_ux_journey_step,
    "ux_requirement": _resolve_ux_requirement,
    "solution_design": _resolve_solution_design,
    "blueprint_lane_cell": _resolve_blueprint_lane_cell,
}


def resolve_target(system_id: int, target_kind: str, target_ref: str) -> ResolvedTarget:
    """Resolve one target against its canonical source. Never raises -- a
    stale/deleted/unknown target degrades to `resolution="unresolved"`
    (#437's rule: a stale deep link must not fail the whole assistant)."""
    resolver = _TARGET_RESOLVERS.get(target_kind)
    if resolver is None:
        return ResolvedTarget("", None, "", "unresolved")
    try:
        return resolver(system_id, target_ref)
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
    already understands, so a thread's own target lands in the context pack."""
    if target_kind == "interview_session":
        return {"session": target_ref}
    if target_kind == "ux_journey":
        return {"journey": target_ref}
    if target_kind == "ux_journey_step":
        journey_key, _, _step_key = target_ref.partition("#")
        return {"journey": journey_key} if journey_key else {}
    if target_kind == "ux_requirement":
        return {"requirement": target_ref}
    if target_kind == "solution_design":
        return {"design": target_ref}
    if target_kind == "blueprint_lane_cell":
        journey_key = target_ref.split("#", 1)[0]
        return {"journey": journey_key} if journey_key else {}
    return {}


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
