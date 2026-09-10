"""Hypothesis -> Joint Understanding bridge (Issue #455, Epic #443 §6).

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §6 is the canonical contract;
`docs/01-specifications/ux/decision-discussion-workflow.md` DD-UX-04/DD-UX-05 own the
developer-facing workflow this feeds. This module owns three things:

1. Hypothesis persistence helpers (`hypothesis_out`, `_validate_completeness`)
   -- the actual INSERT happens in `assistant_discussion_proposal.
   create_proposal` (§6.1: a hypothesis is an independent proposal item
   type, never a field_change), but promotion re-validates completeness
   here as defense in depth (Issue #455 Decisions: "生成時にも昇格時にも
   拒否する").
2. `promote_hypothesis` (§6.2): the manual bridge from one selected
   hypothesis into a `owner_scope='discussion'` Joint Understanding session
   (Issue #461), idempotent on a caller-supplied `request_id`.
3. The two `app.joint_premise` provider registrations §337/#461 left
   unregistered for exactly this Issue to fill in:
   `register_discussion_origin_provider` (resolves `origin_kind='discussion'`
   -- a hypothesis id -- into its current content) and
   `register_dependency_digest_resolver` (the live digest of one dependency
   reference in a session's manifest, Issue #461 decision 5/6).

Both registered providers are called from INSIDE an already-open `db.
get_conn()` connection (every existing premise-evaluation call site --
`resolve_premise_facts`, `capture_premise_bundle`, the whole of
`routes/joint_understanding.py` -- holds one open around the call). Calling
`discussion_adapters.get_adapter(kind).resolver(...)` from there would
re-enter `get_conn()` and raise `db.DatabaseReentrancyError` (CLAUDE.md: the
connection lock is process-wide and non-reentrant). `_raw_target_digest_
with_conn` below therefore reimplements each covered target_kind's digest
formula against the CALLER'S OWN `conn` -- reading the exact same domain
functions (`product_objective.get_gap_detail`, `ux_design.get_journey_detail`,
...) `discussion_adapters._resolve_<kind>` reads, just without that
function's own `with get_conn():` wrapper, so the two never compute a
different digest for the same content. `_target_digest_with_conn` wraps it
to substitute an empty ("no revision recorded yet") digest with
`discussion_context_bundle.NO_REVISION_DIGEST` -- the SAME sentinel
`build_context_bundle` now substitutes when building the dependency
manifest, so a captured reference and its later re-resolution always agree.

16 of the 17 registered `target_kind`s are covered (`screen`,
`purpose_element`, `purpose_relation`, `stakeholder`, `stakeholder_need`,
`product_objective`, `product_milestone`, `product_gap`, `product_feature`,
`ux_journey`, `ux_requirement`, `solution_design`, `interview_session`,
`understanding_claim`, `ux_journey_step`, `blueprint_lane_cell`).
`overview_finding` is not: `discussion_adapters._resolve_overview_finding`
calls `overview_projection.build_overview(system_id)`, which is not
conn-parametrized and re-derives a whole System-wide projection --
reimplementing it against a caller-supplied `conn` is well beyond this
bridge's scope. `_raw_target_digest_with_conn` reports it (and any future
uncovered kind) as `_UNSUPPORTED`.

**`_UNSUPPORTED` must never become a digest.** A constant sentinel string
fed into the discussion-origin content hash would make premise verdict
PERMANENTLY `current` for that root regardless of how much its real content
actually drifts -- fail-OPEN, the exact defect Issue #337's premise contract
exists to prevent (this module's own earlier draft made exactly this
mistake with a literal `"__unsupported__"` string; caught on review). The
fix is structural on two levels: `_discussion_origin_provider` returns
`content_hash=None` for an unsupported root (never a string), which makes
`PremiseBundle.is_complete` `False` FOREVER for that session -- the finite
`invalid`/`premise_incomplete` verdict, honest about "cannot verify" rather
than lying "unchanged" -- and `DISCUSSION_ADAPTERS["overview_finding"]
.joint_understanding_bridge` is `False`, enforced by `promote_hypothesis`
itself (`BridgeNotSupported`, 422), so a promotion whose premise this
bridge cannot back is refused up front rather than silently created in a
permanently-`invalid` state.

probe-agent:
  role: hypothesis persistence, manual promotion into Joint Understanding,
    and the discussion-origin premise providers
  capability: ai-discussion-adapter
  element_type: core
  consumers: [routes.assistant]
  operation_kind: orchestration
  state_effects: [database-read, database-write]
  probe_value: Verify a hypothesis missing competing_explanations/refutation_conditions/next_investigation is refused at promotion with no row changed, a retried promotion with the same request_id returns the same Joint Understanding session id, and a request_id reused for a different hypothesis is refused 409.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

from . import discussion_adapters, discussion_context_bundle, joint_premise
from .db import get_conn
from .discussion_save_receipts import compute_request_digest
from .joint_premise import DiscussionOriginFacts
from .joint_understanding import SCHEMA_VERSION as JU_SCHEMA_VERSION

SCHEMA_VERSION = "discussion-hypothesis-v1"

#: Sentinel distinguishing "this target_kind is not one this bridge can
#: verify from an already-open connection" from a real digest or a genuine
#: `None` ("this reference no longer resolves at all"). Never returned to a
#: caller outside this module.
_UNSUPPORTED = object()


class DiscussionHypothesisError(ValueError):
    """Base class for this module's errors; routes translate each subclass."""


class NotFound(DiscussionHypothesisError):
    pass


class HypothesisIncomplete(DiscussionHypothesisError):
    """§6.1: competing_explanations / refutation_conditions /
    next_investigation missing at promotion time. Carries a finite `code` so
    the route can report WHICH of the three is missing without the client
    parsing prose."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class BridgeNotSupported(DiscussionHypothesisError):
    """The discussion's root `target_kind` declares
    `joint_understanding_bridge=False` (Issue #455: today only
    `overview_finding`, whose premise cannot be verified from an
    already-open connection -- see `DISCUSSION_ADAPTERS["overview_finding"]`'s
    own comment). The capability flag is read here, not merely displayed:
    a `True` declaration with no enforcing check behind it would be the
    exact "declares supported, cannot back it" defect #456 exists to
    prevent, one layer further out."""

    def __init__(self, target_kind: str):
        super().__init__(
            f"target_kind={target_kind!r} does not support Joint Understanding "
            "promotion (joint_understanding_bridge=False)"
        )
        self.target_kind = target_kind


class PromotionConflict(DiscussionHypothesisError):
    """§6.2: `request_id` already bound to a DIFFERENT hypothesis/content
    (409) -- reusing a promotion token across content is refused rather than
    silently treated as a new attempt (mirrors `discussion_save_receipts.
    SaveRequestConflict`, Issue #452's identical idempotency discipline)."""

    def __init__(self, existing: Dict[str, Any]):
        super().__init__(
            f"request_id {existing['request_id']!r} is already bound to a "
            "different hypothesis promotion"
        )
        self.existing = existing


# --- Hypothesis reads ---------------------------------------------------------


def _hypothesis_row(conn, system_id: int, hypothesis_id: int):
    return conn.execute(
        "SELECT * FROM assistant_discussion_proposal_hypothesis "
        "WHERE id = ? AND system_id = ?",
        (hypothesis_id, system_id),
    ).fetchone()


def hypothesis_out(row: Any) -> Dict[str, Any]:
    d = dict(row)
    d["competing_explanations"] = json.loads(d.pop("competing_explanations_json") or "[]")
    d["refutation_conditions"] = json.loads(d.pop("refutation_conditions_json") or "[]")
    d["evidence_refs"] = json.loads(d.pop("evidence_refs_json") or "[]")
    return d


def _validate_completeness(row: Any) -> None:
    """§6.1's promotion-time re-check (defense in depth: `generate_proposal`
    already refuses an incomplete hypothesis at generation time, but a row
    could in principle reach this table some other way)."""
    competing = json.loads(row["competing_explanations_json"] or "[]")
    refutation = json.loads(row["refutation_conditions_json"] or "[]")
    next_investigation = row["next_investigation"] or ""
    if not (row["statement"] or "").strip():
        raise HypothesisIncomplete(
            "hypothesis has no statement", "hypothesis_incomplete_statement",
        )
    if not competing:
        raise HypothesisIncomplete(
            "hypothesis has no competing_explanations -- an incomplete "
            "hypothesis is a claim, not a hypothesis",
            "hypothesis_incomplete_competing_explanations",
        )
    if not refutation:
        raise HypothesisIncomplete(
            "hypothesis has no refutation_conditions -- an incomplete "
            "hypothesis is a claim, not a hypothesis",
            "hypothesis_incomplete_refutation_conditions",
        )
    if not next_investigation.strip():
        raise HypothesisIncomplete(
            "hypothesis has no next_investigation -- an incomplete "
            "hypothesis is a claim, not a hypothesis",
            "hypothesis_incomplete_next_investigation",
        )


def _hypothesis_content_payload(row: Any) -> Dict[str, Any]:
    """The hypothesis's own normalized, order-independent content -- never
    including `status` (promoting/rejecting must not, by itself, change what
    this origin's content hash reads as, the same `intent` origin rule
    #337 documents for `status`)."""
    return {
        "statement": row["statement"] or "",
        "competing_explanations": sorted(
            json.loads(row["competing_explanations_json"] or "[]")
        ),
        "refutation_conditions": sorted(
            json.loads(row["refutation_conditions_json"] or "[]")
        ),
        "next_investigation": row["next_investigation"] or "",
        "evidence_refs": sorted(json.loads(row["evidence_refs_json"] or "[]")),
        "uncertainty": row["uncertainty"] or "",
    }


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _root_target_for_hypothesis(
    conn, system_id: int, hyp_row: Any,
) -> Optional[Tuple[str, str]]:
    """The `(target_kind, target_ref)` of the Discussion the hypothesis's
    proposal was generated for, or `None` when that proposal no longer
    resolves in this System."""
    row = conn.execute(
        "SELECT target_kind, target_ref FROM assistant_discussion_proposal "
        "WHERE id = ? AND system_id = ?",
        (hyp_row["proposal_id"], system_id),
    ).fetchone()
    if row is None:
        return None
    return row["target_kind"], row["target_ref"]


# --- conn-safe target digests (see module docstring) --------------------------


def _target_digest_with_conn(conn, system_id: int, target_kind: str, target_ref: str):
    """Mirrors `discussion_adapters._resolve_<kind>`'s own digest formula,
    computed directly against `conn` (never opening a nested `get_conn()`).
    Returns the digest string, `None` when the target genuinely no longer
    resolves, or `_UNSUPPORTED` for a target_kind this bridge cannot verify
    from an already-open connection.

    A resolved-but-revision-less entity's raw digest is `""` (several
    `discussion_adapters._resolve_<kind>` formulas return `revision[...] if
    revision else ""`) -- substituted here to
    `discussion_context_bundle.NO_REVISION_DIGEST`, the SAME sentinel
    `build_context_bundle` now substitutes when building the dependency
    manifest (see that constant's own docstring for why `""` cannot be
    allowed to survive: `normalize_premise_manifest` refuses it, and a
    caller that instead silently dropped the manifest would be a second,
    quieter fail-open). Applying it here too -- once, at this function's
    single choke point, rather than in each branch below -- is what keeps a
    dependency captured via `build_context_bundle` and the SAME reference
    re-resolved later via `_dependency_digest_resolver` comparing equal when
    nothing has changed."""
    raw = _raw_target_digest_with_conn(conn, system_id, target_kind, target_ref)
    if raw is _UNSUPPORTED or raw is None:
        return raw
    return raw or discussion_context_bundle.NO_REVISION_DIGEST


def _raw_target_digest_with_conn(conn, system_id: int, target_kind: str, target_ref: str):
    if target_kind == "screen":
        return ""
    if target_kind == "purpose_element":
        from . import purpose_chain

        try:
            chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
        except Exception:  # pragma: no cover - defensive, mirrors the adapter's own guard
            return None
        element = next((e for e in chain.elements if e.id == target_ref), None)
        return purpose_chain.element_digest(element) if element is not None else None
    if target_kind == "purpose_relation":
        from . import purpose_chain
        from .discussion_adapters import _purpose_relation_digest  # noqa: SLF001

        try:
            chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
        except Exception:  # pragma: no cover - defensive
            return None
        relation = next((r for r in chain.relations if r.id == target_ref), None)
        return _purpose_relation_digest(relation) if relation is not None else None
    if target_kind in ("stakeholder", "stakeholder_need"):
        from . import stakeholder_network as sn

        try:
            resolved = sn._resolve_target(conn, system_id, target_kind, target_ref)  # noqa: SLF001
        except Exception:  # pragma: no cover - defensive
            return None
        if resolved.get("resolution") != "resolved":
            return None
        return resolved.get("digest") or ""
    if target_kind == "product_objective":
        from . import product_objective

        try:
            detail = product_objective.get_objective_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return None
        except Exception:  # pragma: no cover - defensive
            return None
        revision = detail.get("current_revision")
        return revision["content_digest"] if revision else ""
    if target_kind == "product_milestone":
        from . import product_objective

        try:
            detail = product_objective.get_milestone_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return None
        except Exception:  # pragma: no cover - defensive
            return None
        revision = detail.get("current_revision")
        return revision["content_digest"] if revision else ""
    if target_kind == "product_gap":
        from . import product_objective

        try:
            product_objective.get_gap_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return None
        except Exception:  # pragma: no cover - defensive
            return None
        gap_row = product_objective._get_gap_row(conn, system_id, target_ref)  # noqa: SLF001
        if gap_row is None:
            return None
        return product_objective._gap_current_digest(conn, gap_row)  # noqa: SLF001
    if target_kind == "product_feature":
        from . import product_feature

        try:
            detail = product_feature.get_feature_detail(conn, system_id, target_ref)
        except product_feature.NotFound:
            return None
        except Exception:  # pragma: no cover - defensive
            return None
        revision = detail.get("current_revision")
        return revision["content_digest"] if revision else ""
    if target_kind == "ux_journey":
        from . import ux_design

        try:
            detail = ux_design.get_journey_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return None
        revision = detail.get("current_revision")
        return revision["content_digest"] if revision else ""
    if target_kind == "ux_requirement":
        from . import ux_design

        try:
            detail = ux_design.get_requirement_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return None
        revision = detail.get("current_revision")
        return revision["content_digest"] if revision else ""
    if target_kind == "solution_design":
        from . import solution_design

        try:
            detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=target_ref)
        except solution_design.SolutionDesignNotFoundError:
            return None
        return solution_design.content_digest(
            {"title": detail.get("title") or "", "summary": detail.get("summary") or ""}
        )
    if target_kind == "interview_session":
        from .discussion_adapters import _normalized_json_digest  # noqa: SLF001

        try:
            session_id = int(target_ref)
        except (TypeError, ValueError):
            return None
        row = conn.execute(
            "SELECT id, current_understanding FROM interview_session WHERE id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchone()
        if row is None:
            return None
        return _normalized_json_digest(row["current_understanding"])
    if target_kind == "understanding_claim":
        return _resolve_understanding_claim_digest_with_conn(conn, system_id, target_ref)
    if target_kind == "ux_journey_step":
        from . import ux_design

        journey_key, sep, step_key = target_ref.partition("#")
        if not sep or not journey_key or not step_key:
            return None
        journey = ux_design._get_journey_row(conn, system_id, journey_key)  # noqa: SLF001
        if journey is None:
            return None
        resolved = ux_design._resolve_step_target(conn, system_id, journey["id"], step_key)  # noqa: SLF001
        if resolved["resolution"] != "resolved":
            return None
        return resolved.get("digest") or ""
    if target_kind == "blueprint_lane_cell":
        from . import journey_blueprint
        from .discussion_adapters import _canonical_digest as _adapters_canonical_digest  # noqa: SLF001

        parts = target_ref.split("#")
        if len(parts) != 3 or not all(parts):
            return None
        journey_key, step_key, lane_kind = parts
        if lane_kind not in journey_blueprint.LANE_KINDS:
            return None
        try:
            blueprint = journey_blueprint.build_blueprint(conn, system_id, journey_key)
        except journey_blueprint.NotFound:
            return None
        step = next((s for s in blueprint["steps"] if s["step_key"] == step_key), None)
        if step is None:
            return None
        cell = step.get("lanes", {}).get(lane_kind)
        if cell is None:
            return None
        return _adapters_canonical_digest(cell)
    # `overview_finding` is the one remaining registered kind with no
    # conn-safe digest here: `discussion_adapters._resolve_overview_finding`
    # calls `overview_projection.build_overview(system_id)`, which is not
    # conn-parametrized and re-derives a System-wide projection (Purpose
    # Chain, runtime health, findings, ...) -- reimplementing it against a
    # caller-supplied `conn` is a much larger undertaking than this bridge's
    # scope. `DISCUSSION_ADAPTERS["overview_finding"].joint_understanding_
    # bridge` is `False` for exactly this reason (see its own comment).
    return _UNSUPPORTED


def _resolve_understanding_claim_digest_with_conn(
    conn, system_id: int, target_ref: str,
) -> Optional[str]:
    """Mirrors `discussion_adapters._resolve_understanding_claim`'s digest
    formula exactly (same `understanding_brief.build_understanding_brief` +
    `claim_digest`), against the caller's own `conn` -- see this module's
    docstring for why the original cannot be called directly here."""
    from . import understanding_brief

    section, sep, name = target_ref.partition(":")
    if not sep or section not in ("vision", "system_purpose", "core_capabilities") or not name:
        return None
    session_row = conn.execute(
        "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    session_id = session_row["id"] if session_row is not None else None
    try:
        brief = understanding_brief.build_understanding_brief(conn, system_id, session_id)
    except Exception:  # pragma: no cover - defensive, mirrors the adapter's own guard
        return None
    claims = {
        "vision": [brief.vision] if brief.vision is not None else [],
        "system_purpose": brief.system_purpose,
        "core_capabilities": brief.core_capabilities,
    }[section]
    claim = next((c for c in claims if c.name == name), None)
    if claim is None:
        return None
    raw_item = None
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
    return understanding_brief.claim_digest(raw_item) if raw_item is not None else ""


# --- app.joint_premise provider registrations ---------------------------------


def _discussion_origin_provider(
    conn, *, origin_id: int, system_id: int,
) -> Optional[DiscussionOriginFacts]:
    """§6.2's content hash: the hypothesis's own normalized content PLUS the
    Discussion root's current digest -- closing the gap a hash over the
    hypothesis's (otherwise immutable) fields alone would leave: a hypothesis
    about a Requirement that is later edited must not keep reading as
    `current` just because the hypothesis text itself never changes.
    Dependency-level staleness is the SEPARATE `premise_dependency_manifest`
    axis (Issue #461), populated at promotion time from the same
    `discussion_context_bundle` call -- folding it into this hash too would
    make `dependency_content_changed` unreachable (`origin_content_changed`
    would always fire first).

    ``content_hash=None`` (never a constant sentinel string) is returned
    whenever the root's current content cannot actually be determined --
    the root no longer has a resolvable proposal (`_root_target_for_
    hypothesis` returns `None`) or its `target_kind` is outside
    `_target_digest_with_conn`'s covered set (`_UNSUPPORTED`). A constant
    string there would make `PremiseBundle.is_complete` (and therefore the
    stored, CAPTURED `premise_content_hash`) look the same on every future
    read regardless of how much the real root actually changed -- fail-OPEN,
    the exact defect #337 exists to prevent (an unreadable/incomputable fact
    must never read as "unchanged", #380). `content_hash=None` instead makes
    `PremiseBundle.is_complete` `False` FOREVER for this session (captured
    once, at promotion time) -- the finite `invalid`/`premise_incomplete`
    verdict, which blocks `hypothesis_adopted`/`decided` and always asks for
    reconfirmation in reflux, exactly like a session that never captured a
    comparable premise at all. A genuinely REMOVED (but supported-kind)
    target still gets a real, comparable digest (the `"__removed__"`
    sentinel) -- returning to existence moves it away from that sentinel and
    is correctly detected as a change; that case is not fail-open."""
    hyp = _hypothesis_row(conn, system_id, origin_id)
    if hyp is None:
        return None
    incomplete = DiscussionOriginFacts(
        current_origin_id=hyp["id"],
        superseded=(hyp["status"] == "rejected"),
        revision_id=None,
        content_hash=None,
    )
    root = _root_target_for_hypothesis(conn, system_id, hyp)
    if root is None:
        return incomplete
    root_kind, root_ref = root
    resolved = _target_digest_with_conn(conn, system_id, root_kind, root_ref)
    if resolved is _UNSUPPORTED:
        return incomplete
    root_digest = "__removed__" if resolved is None else resolved
    payload = _hypothesis_content_payload(hyp)
    payload["root_kind"] = root_kind
    payload["root_ref"] = root_ref
    payload["root_digest"] = root_digest
    return DiscussionOriginFacts(
        current_origin_id=hyp["id"],
        # A rejected hypothesis is a withdrawn discussion point -- the same
        # meaning Issue #323's `alignment_item.superseded` gives a
        # `review_item` origin whose exact discussion point was replaced.
        superseded=(hyp["status"] == "rejected"),
        revision_id=None,
        content_hash=_canonical_digest(payload),
    )


def _dependency_digest_resolver(
    conn, *, target_kind: str, target_ref: str, system_id: int,
) -> Optional[str]:
    resolved = _target_digest_with_conn(conn, system_id, target_kind, target_ref)
    if resolved is _UNSUPPORTED:
        # Fail-closed (never a false "current"): the existing "removed"
        # reading, documented above as this bridge's one accepted
        # imprecision for a target_kind outside its covered set.
        return None
    return resolved


def install() -> None:
    """Register both providers for the life of the process. Called once, at
    import time (below): every EXISTING consumer of `app.joint_premise.
    resolve_premise_facts` / `evaluate_session_premise` (routes this Issue
    does not own) must see a real provider from the moment any
    `owner_scope='discussion'` session can exist, not from whenever some
    lazy setup path happens to run first."""
    joint_premise.register_discussion_origin_provider(_discussion_origin_provider)
    joint_premise.register_dependency_digest_resolver(_dependency_digest_resolver)


install()


# --- Promotion (§6.2) ----------------------------------------------------------


def promote_hypothesis(
    system_id: int, proposal_id: int, hypothesis_id: int, *,
    request_id: str, actor: Optional[str],
) -> Dict[str, Any]:
    """Bridge one selected hypothesis into a `owner_scope='discussion'`
    Joint Understanding session (Issue #461), using `#461`'s owner scope --
    no owning `interview_session` is required or created.

    Idempotent on `request_id` (Issue #455 Decisions): a retry with the SAME
    `request_id` for the SAME hypothesis returns the SAME
    `joint_understanding_session_id` (`reused=True`) without creating a
    second session; the SAME `request_id` reused for a DIFFERENT hypothesis
    (or, in principle, any change in the digested content) raises
    `PromotionConflict` (409). An incomplete hypothesis raises
    `HypothesisIncomplete` (422) with NOTHING persisted -- validation runs
    strictly before the idempotency table is even read.

    Returns ``{"hypothesis": <dict>, "promotion": <dict>, "reused": bool}``.
    """
    now = time.time()
    request_digest = compute_request_digest({"hypothesis_id": hypothesis_id})

    with get_conn() as conn:
        hyp = _hypothesis_row(conn, system_id, hypothesis_id)
        if hyp is None or hyp["proposal_id"] != proposal_id:
            raise NotFound(
                f"hypothesis {hypothesis_id} not found on proposal {proposal_id}"
            )
        _validate_completeness(hyp)
        root = _root_target_for_hypothesis(conn, system_id, hyp)
        if root is None:
            raise NotFound(f"proposal {proposal_id} not found")
        root_kind, root_ref = root
        adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(root_kind)
        if adapter is None or not adapter.joint_understanding_bridge:
            raise BridgeNotSupported(root_kind)
        thread_row = conn.execute(
            "SELECT thread_id FROM assistant_discussion_proposal WHERE id = ? AND system_id = ?",
            (proposal_id, system_id),
        ).fetchone()
        thread_id = thread_row["thread_id"]

        existing = conn.execute(
            "SELECT * FROM assistant_discussion_hypothesis_promotion "
            "WHERE system_id = ? AND request_id = ?",
            (system_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise PromotionConflict(dict(existing))
            return {
                "hypothesis": hypothesis_out(hyp), "promotion": dict(existing), "reused": True,
            }

        first_turn = hyp["first_turn_number"] if "first_turn_number" in hyp.keys() else None
        last_turn = hyp["last_turn_number"] if "last_turn_number" in hyp.keys() else None
        if first_turn is None or last_turn is None:
            span = conn.execute(
                "SELECT MIN(turn_number) AS lo, MAX(turn_number) AS hi "
                "FROM assistant_discussion_turn WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            first_turn = first_turn if first_turn is not None else span["lo"]
            last_turn = last_turn if last_turn is not None else span["hi"]
        statement = hyp["statement"]

    # --- Build the dependency manifest OUTSIDE any open connection --------
    # `discussion_context_bundle.build_context_bundle` manages its own
    # short-lived connections and calls the full adapter resolver (which
    # opens ITS own) -- calling it while `conn` above is still open would
    # raise `db.DatabaseReentrancyError` (CLAUDE.md's get_conn() rule).
    try:
        bundle = discussion_context_bundle.build_context_bundle(
            system_id, root_kind, root_ref, thread_id=thread_id,
            budget=discussion_context_bundle.DEFAULT_BUDGET,
        )
        dependencies = [ref.as_dict() for ref in bundle.dependencies]
    except discussion_context_bundle.ContextBundleError:
        # The root no longer resolves at all -- an empty manifest still lets
        # the premise capture below proceed; the discussion-origin content
        # hash's own `root_digest="__removed__"` branch is what actually
        # reports this as `missing` on the very first read.
        dependencies = []
    # No `except JointPremiseError` here (deliberately removed on review): a
    # resolved-but-revision-less related entity used to make
    # `normalize_premise_manifest` reject the whole manifest, and silently
    # falling back to an empty one here would have dropped the entire
    # dependency-staleness axis for this promotion WITHOUT recording that it
    # happened -- a second, quieter fail-open stacked on top of the first.
    # The root cause is fixed instead, at the one place a resolved entity's
    # digest becomes a manifest reference
    # (`discussion_context_bundle.NO_REVISION_DIGEST`), so
    # `normalize_premise_manifest` never sees an empty digest from a
    # legitimately resolved entity any more. A `JointPremiseError` reaching
    # here now means the manifest is malformed for a reason that fix does
    # NOT cover -- that must fail this call loudly (500, surfaced to the
    # caller), never be swallowed into a degraded, unrecorded promotion.

    with get_conn() as conn:
        # Defense in depth: another request for the SAME request_id may have
        # committed between the two connections above.
        existing = conn.execute(
            "SELECT * FROM assistant_discussion_hypothesis_promotion "
            "WHERE system_id = ? AND request_id = ?",
            (system_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise PromotionConflict(dict(existing))
            return {
                "hypothesis": hypothesis_out(_hypothesis_row(conn, system_id, hypothesis_id)),
                "promotion": dict(existing), "reused": True,
            }

        premise = joint_premise.capture_premise_bundle(
            conn, origin_kind="discussion", origin_id=hypothesis_id,
            session_row=None, system_id=system_id, now=now, dependencies=dependencies,
        )
        captured_digest = _target_digest_with_conn(conn, system_id, root_kind, root_ref)
        if captured_digest is _UNSUPPORTED or captured_digest is None:
            captured_digest = ""

        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO joint_understanding_session
                    (owner_scope, discussion_thread_id, system_id, origin_kind,
                     origin_id, trigger, question_text, status,
                     premise_snapshot_id, premise_commit_sha, premise_revision_id,
                     premise_content_hash, premise_capability_digest,
                     premise_intent_digest, premise_review_subject_id,
                     premise_tracking_version, premise_captured_at,
                     premise_dependency_manifest_json,
                     premise_dependency_manifest_digest, schema_version,
                     created_at, updated_at)
                VALUES ('discussion', ?, ?, 'discussion', ?, 'discussion_promotion', ?, 'open',
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_id, system_id, hypothesis_id, statement,
                    premise["premise_snapshot_id"], premise["premise_commit_sha"],
                    premise["premise_revision_id"], premise["premise_content_hash"],
                    premise["premise_capability_digest"], premise["premise_intent_digest"],
                    premise["premise_review_subject_id"], premise["premise_tracking_version"],
                    premise["premise_captured_at"],
                    premise["premise_dependency_manifest_json"],
                    premise["premise_dependency_manifest_digest"],
                    JU_SCHEMA_VERSION, now, now,
                ),
            )
            ju_id = cur.lastrowid
            promo_cur = conn.execute(
                """INSERT INTO assistant_discussion_hypothesis_promotion
                       (system_id, hypothesis_id, thread_id, first_turn_number, last_turn_number,
                        captured_target_kind, captured_target_ref, captured_target_digest,
                        joint_understanding_session_id, request_id, request_digest,
                        decision_method, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
                (
                    system_id, hypothesis_id, thread_id, first_turn, last_turn,
                    root_kind, root_ref, captured_digest, ju_id, request_id, request_digest,
                    actor, now,
                ),
            )
            promotion_id = promo_cur.lastrowid
            # Issue #329's boundary applies one layer up too: promoting never
            # touches the origin's answer/decision -- only THIS hypothesis's
            # own `status`.
            conn.execute(
                "UPDATE assistant_discussion_proposal_hypothesis SET status = 'promoted' "
                "WHERE id = ?",
                (hypothesis_id,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        promo_row = conn.execute(
            "SELECT * FROM assistant_discussion_hypothesis_promotion WHERE id = ?",
            (promotion_id,),
        ).fetchone()
        hyp_row = _hypothesis_row(conn, system_id, hypothesis_id)
        return {
            "hypothesis": hypothesis_out(hyp_row), "promotion": dict(promo_row), "reused": False,
        }
