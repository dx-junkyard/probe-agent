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
connection lock is process-wide and non-reentrant). `_target_digest_with_conn`
below therefore reimplements each covered target_kind's digest formula
against the CALLER'S OWN `conn` -- reading the exact same domain functions
(`product_objective.get_gap_detail`, `ux_design.get_journey_detail`, ...)
`discussion_adapters._resolve_<kind>` reads, just without that function's own
`with get_conn():` wrapper, so the two never compute a different digest for
the same content. A target_kind reachable via `discussion_context_bundle`'s
open `_extract_generic_refs` enumeration but NOT one of the ones covered here
(`ux_journey_step`, `blueprint_lane_cell`, `interview_session`,
`overview_finding`, ...) is `_UNSUPPORTED`: `_dependency_digest_resolver`
reports it as `None` (the existing "target removed" reading, never a false
"current") and the discussion-origin content hash folds it in as a fixed
sentinel (never silently treated as unchanged root content, and never a
crash) -- a documented, fail-closed simplification; extending coverage to a
new kind is additive.

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

from . import discussion_context_bundle, joint_premise
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
    from an already-open connection."""
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
    return _UNSUPPORTED


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
    would always fire first)."""
    hyp = _hypothesis_row(conn, system_id, origin_id)
    if hyp is None:
        return None
    root = _root_target_for_hypothesis(conn, system_id, hyp)
    if root is None:
        root_kind, root_ref, root_digest = "", "", "__no_root__"
    else:
        root_kind, root_ref = root
        resolved = _target_digest_with_conn(conn, system_id, root_kind, root_ref)
        if resolved is _UNSUPPORTED:
            root_digest = "__unsupported__"
        elif resolved is None:
            root_digest = "__removed__"
        else:
            root_digest = resolved
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
    except joint_premise.JointPremiseError:
        # Defensive (found while building this Issue's representative-chain
        # test): `build_context_bundle` can discover a related entity that
        # RESOLVES but has no revision yet (a just-created Objective/
        # Milestone/Journey with an empty digest), and
        # `normalize_premise_manifest` rejects an empty digest outright.
        # That is a manifest-BUILDING defect one layer down (#458), not
        # something this bridge can repair -- but a promotion must never
        # 500 over it. Falling back to an empty manifest costs only the
        # dependency-staleness axis for THIS promotion; the discussion-
        # origin content hash (root digest) still detects a root change.
        dependencies = []

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
