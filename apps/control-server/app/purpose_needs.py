"""Purpose Needs: adaptive next-question over the Purpose Chain (Issue #389).

`docs/purpose-chain.md` §2 is the design contract this module implements.
Epic #387's UX principle is: ask the developer only what only the developer
can decide, and only when something currently blocks a real judgement.

**A need is never "this optional field is empty".** Every need this module
derives is a deterministic classification of a signal `app/purpose_chain.py`
(#388) ALREADY computed: an element whose `state == "unknown"`, or a relation
whose `status` is `unknown`/`conflicting` or whose `recheck_state` is
`stale`. There is no code path here that inspects an Intent Brief field
(`success_criteria`, `priority`, ...) that Purpose Chain does not itself
read, and `tests/test_purpose_needs.py` proves it: leaving an unrelated
optional Intent field empty produces zero needs.

**The fixed need-code -> answerability table (§2.3, `ANSWERABILITY_TABLE`
below) does NOT replace `app/question_router.py`'s (#286) reasoning-model
router, and must never grow a keyword/heuristic classifier that tries to.**
The two solve different problems:

* A need this module derives is already a SYSTEM classification into one of
  7 codes, each with a *structurally* known answerability -- "an element with
  no source row is missing" and "an `intervention_to_capability` relation
  that is merely `hypothesis` can be verified by reading the code" are direct
  structural facts about the Purpose Chain projection, which is exactly the
  kind of "classification into a small explicit finite set" Principle 6
  permits as a deterministic rule.
* A developer's own FREE-TEXT follow-up question (typed while confirming a
  Q&A item or an Inquiry) has no such structure -- telling "this is a business
  tradeoff" apart from "this is answerable by reading the code" from
  unconstrained natural language is exactly the open-ended judgement Principle
  6 reserves for a reasoning model. That is `question_router.route_question`,
  untouched by this module.

**`system_researchable` needs never reach the developer as a question.**
`select_question` (§2.4) only ever returns a `human_judgement` need; a
`system_researchable` one is surfaced solely as an entry in `routed_needs`
(informational -- the Dashboard is expected to point at the Joint
Understanding investigation, not present it as something to answer).

**No LLM call anywhere in this module** (Principle 6) -- every function here
is a pure function of an already-derived `purpose_chain.PurposeChainResult`
plus, for `apply_response_state`, the caller's own already-read
`purpose_need_response` rows. `routes/purpose_chain.py` is the only place
that touches a database connection or opens a Joint Understanding session.

Two design decisions the spec text leaves open, made explicitly here
(flagged for lead review, see the implementation report):

1. **Which relation-derived need code a `hypothesis`-status relation
   produces** depends on its `kind`: `intervention_to_capability` produces
   `capability_justification_missing` (§2.3's table marks it
   `system_researchable` -- whether a Capability really follows from the
   intervention is verifiable by reading the code); `problem_to_change` /
   `change_to_intervention` produce `decision_criterion_missing`
   (`human_judgement` -- whether an intervention truly serves the desired
   change is not something code alone settles). `relation_unknown` /
   `relation_conflict` are independent of `kind` (the table gives them one
   answerability regardless), and are derived unconditionally from `status`.
   `premise_recheck_required` is derived independently again, from
   `recheck_state == "stale"` -- and ONLY when `decision_id is not None`
   (there is a decision to "re"-check; a relation that was never decided
   cannot have a stale confirmation). These are independent `if`s, not an
   `elif` chain: a relation CAN legitimately produce more than one need at
   once (e.g. `conflicting` via a rejected decision, and separately
   `premise_recheck_required` if the endpoints moved again since the
   rejection) -- `select_question`'s priority table decides which one wins as
   THE question; having both exist as derived facts is correct, not a bug.
2. **`human_value_judgement_required`** fires for any element (of ANY kind,
   including a `core_capability` or a non-frame-slot `intervention` claim)
   whose `confirmation == "conflicting"`. This deliberately overlaps with
   `relation_conflict` for a frame-slot element (whose relation would already
   read `conflicting` too, per `purpose_chain._relation_status` rule 3); the
   overlap costs nothing because `PRIORITY_TABLE` is first-match and the
   relation conflict sits above it, so the developer is asked once. It is NOT
   redundant for an ADDITIONAL `intervention` element (the 2nd+
   `system_purpose` claim, `purpose_chain._intervention_elements`) -- those
   never get a relation built at all, so an element-level conflict there has
   no other way to surface as a need, which is exactly why this code carries a
   priority row of its own rather than waiting for a deep link that nothing
   would ever generate.

probe-agent:
  role: Deterministic derivation of adaptive Purpose Chain follow-up
    questions, and the finite need-code/answerability/state vocabularies that
    back them
  capability: interactive-system-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: pure
  state_effects: []
  probe_value: Verify every need traces to an unknown element or an unknown/conflicting/stale relation (never an empty optional field), that select_question never returns a system_researchable need, that the priority-table tie-break is deterministic, and that a deferred need does not reappear until its target digest moves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, get_args

from . import purpose_chain
from .models import (
    PurposeAnswerability,
    PurposeNeedCode,
    PurposeNeedState,
    PurposeNeedTargetKind,
    PurposeQuestionFallbackReason,
    PurposeResponseKind,
)

__all__ = [
    "NEED_CODES",
    "ANSWERABILITY_VALUES",
    "NEED_STATES",
    "RESPONSE_KINDS",
    "FALLBACK_REASONS",
    "TARGET_KINDS",
    "PRIORITY_TABLE",
    "PurposeNeed",
    "PurposeSuggestedAnswer",
    "PurposeQuestion",
    "target_digest_for",
    "derive_needs",
    "apply_response_state",
    "select_question",
    "build_suggested_answer",
    "build_question",
    "resolve_need",
]


# --- §0. Finite vocabularies, mirrored from app/models.py --------------------

NEED_CODES: Tuple[str, ...] = get_args(PurposeNeedCode)
ANSWERABILITY_VALUES: Tuple[str, ...] = get_args(PurposeAnswerability)
NEED_STATES: Tuple[str, ...] = get_args(PurposeNeedState)
RESPONSE_KINDS: Tuple[str, ...] = get_args(PurposeResponseKind)
FALLBACK_REASONS: Tuple[str, ...] = get_args(PurposeQuestionFallbackReason)
TARGET_KINDS: Tuple[str, ...] = get_args(PurposeNeedTargetKind)

#: §2.4's priority table, first-match. Each row names exactly one need code:
#: since a rule row and a need code are 1:1 here, the tie-break's second key
#: ("need_code の定義順") never actually discriminates between two DIFFERENT
#: codes at the same row -- it only ever needs to break ties between two
#: instances of the SAME code, which the third key (`need_id`, lexicographic)
#: always does deterministically.
#:
#: `human_value_judgement_required` sits at row 2, immediately below the
#: relation conflict it usually duplicates. Leaving it out of the table (so
#: that it could only ever be reached through an explicit `need_id` deep link)
#: looked harmless because a frame-slot or Capability element's conflict also
#: makes its relation `conflicting`, which row 1 already asks about. But an
#: ADDITIONAL `intervention` element -- the 2nd+ `system_purpose` claim --
#: never gets a relation built at all, so for that element the conflict had NO
#: path to the developer whatsoever: a need that exists, blocks a human-only
#: judgement, and is never asked is indistinguishable from one that was never
#: derived. First-match keeps the duplicate case honest: when the same subject
#: also produced a relation conflict, row 1 wins and the developer is asked
#: once.
PRIORITY_TABLE: Tuple[Tuple[int, str], ...] = (
    (1, "relation_conflict"),
    (2, "human_value_judgement_required"),
    (3, "frame_missing"),
    (4, "decision_criterion_missing"),
    (5, "premise_recheck_required"),
    (6, "relation_unknown"),
    (7, "capability_justification_missing"),
)

#: Fixed Japanese labels for the 3 frame slots (§0 invariant: UI copy is
#: server-owned fixed text, never model output).
_FRAME_LABELS: Dict[str, str] = {
    "beneficiary_problem": "対象者と現在の課題",
    "desired_change": "望ましい変化",
    "intervention": "システムの介入",
}

#: need_code (+ frame element kind, where it discriminates) -> answerability,
#: §2.3's fixed table. `_base_answerability` is the only place this is read;
#: `unavailable` (degraded section) and `already_answered` (response history)
#: are never in this table -- they are read at derive/apply time instead.
def _base_answerability(need_code: str, *, frame_kind: Optional[str] = None) -> str:
    if need_code == "frame_missing":
        return "system_researchable" if frame_kind == "intervention" else "human_judgement"
    if need_code == "capability_justification_missing":
        return "system_researchable"
    return "human_judgement"


# --- §1. Data shapes -----------------------------------------------------------


@dataclass(frozen=True)
class PurposeNeed:
    id: str
    code: str
    target_kind: str
    target_id: str
    target_label: str
    answerability: str
    state: str
    #: `target_digest_for(target_kind, target)` at DERIVE time -- what a
    #: recorded response is compared against to decide already_answered /
    #: deferred / waiting (§2.6).
    target_digest: str
    source_revision_ids: Tuple[int, ...] = ()
    #: Internal only (never serialized): the Purpose Chain section this
    #: need's target belongs to, used solely to read `degraded_sections`.
    section: str = "frame"


@dataclass(frozen=True)
class PurposeSuggestedAnswer:
    text: str
    provenance: str
    source_kind: str
    source_ids: Tuple[str, ...]
    is_mock: bool


@dataclass(frozen=True)
class PurposeQuestion:
    need_id: str
    need_code: str
    rule_row: Optional[int]
    prompt: str
    why_now: str
    blocked_decision: str
    unlocks: str
    defer_impact: str
    target_kind: str
    target_id: str
    target_label: str
    answerability: str
    suggested_answer: Optional[PurposeSuggestedAnswer]
    state: str
    source_revision_ids: Tuple[int, ...]
    fallback_reason: Optional[str] = None
    routed_needs: Tuple[PurposeNeed, ...] = ()


# --- §2. Digests -----------------------------------------------------------------


def _relation_digest(relation: "purpose_chain.PurposeRelation") -> str:
    """A relation-target's own digest for §2.6's already_answered/defer logic.

    Deliberately excludes `decision_id`/`decided_at`/`decided_by`/`rationale`:
    those change on EVERY response (that is the point of responding), so
    including them would make a `defer` or a `confirm` immediately look
    "changed" against itself and never reach `already_answered` at all. What
    this digests is what the developer actually SAW when they answered:
    `status` / `recheck_state` / `stale_reason` plus the relation's own fixed
    identity (`kind`/`source_id`/`target_id`).
    """
    canonical = json.dumps(
        {
            "kind": relation.kind,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "status": relation.status,
            "recheck_state": relation.recheck_state,
            "stale_reason": relation.stale_reason,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _missing_element_digest(slot_kind: str) -> str:
    """Placeholder digest for the rare case a frame slot is truly `None`
    (only when the whole `frame` section itself failed to derive -- see
    `purpose_chain.derive_purpose_chain`'s degraded-loader path). The need's
    answerability is already forced to `unavailable` whenever this fires
    (the section is degraded), so this digest is never load-bearing for a
    real already_answered/defer comparison -- it exists only so
    `target_digest_for` stays total.
    """
    return hashlib.sha256(f"missing:{slot_kind}".encode("utf-8")).hexdigest()


def target_digest_for(target_kind: str, target: Any) -> str:
    """The single digest function both derivation and response-comparison
    use, so a captured `purpose_need_response.target_digest` and a freshly
    re-derived need's `target_digest` are always computed the same way."""
    if target_kind == "element":
        if target is None:
            return _missing_element_digest("unknown")
        return purpose_chain.element_digest(target)
    return _relation_digest(target)


# --- §3. Need derivation (pure, over an already-derived PurposeChainResult) ----


def _revision_ids(*elements: Optional["purpose_chain.PurposeElement"]) -> Tuple[int, ...]:
    ids = set()
    for element in elements:
        if element is None:
            continue
        if element.intent_revision_id is not None:
            ids.add(element.intent_revision_id)
        if element.understanding_revision_id is not None:
            ids.add(element.understanding_revision_id)
    return tuple(sorted(ids))


def _relation_label(
    relation: "purpose_chain.PurposeRelation",
    elements_by_id: Mapping[str, "purpose_chain.PurposeElement"],
) -> str:
    if relation.kind == "problem_to_change":
        return f"{_FRAME_LABELS['beneficiary_problem']} → {_FRAME_LABELS['desired_change']}"
    if relation.kind == "change_to_intervention":
        return f"{_FRAME_LABELS['desired_change']} → {_FRAME_LABELS['intervention']}"
    target = elements_by_id.get(relation.target_id)
    capability_label = target.display_statement if target is not None and target.display_statement else "Capability"
    return f"{_FRAME_LABELS['intervention']} → {capability_label}"


def derive_needs(chain: "purpose_chain.PurposeChainResult") -> List[PurposeNeed]:
    """§2.2/§2.3: every need, derived deterministically from `chain` alone.

    Every branch below reads a field `purpose_chain.derive_purpose_chain`
    already computed (`element.state`, `relation.status`,
    `relation.recheck_state`, `relation.decision_id`,
    `element.confirmation`) -- never an Intent Brief field or an
    Understanding claim this module reads for itself. That is what makes
    "an empty optional field is not a need" a structural guarantee rather
    than a convention: there is no code path here that could derive one.
    """
    degraded = set(chain.degraded_sections)
    elements_by_id: Dict[str, "purpose_chain.PurposeElement"] = {e.id: e for e in chain.elements}
    needs: List[PurposeNeed] = []

    # --- frame_missing: one of the 3 frame slots has no content yet -------
    for slot_kind in ("beneficiary_problem", "desired_change", "intervention"):
        element = chain.frame.get(slot_kind)
        if element is not None and element.state != "unknown":
            continue
        target_id = element.id if element is not None else slot_kind
        answerability = "unavailable" if "frame" in degraded else _base_answerability(
            "frame_missing", frame_kind=slot_kind
        )
        needs.append(
            PurposeNeed(
                id=f"frame_missing:{target_id}",
                code="frame_missing",
                target_kind="element",
                target_id=target_id,
                target_label=_FRAME_LABELS[slot_kind],
                answerability=answerability,
                state="available",
                target_digest=target_digest_for("element", element),
                source_revision_ids=_revision_ids(element),
                section="frame",
            )
        )

    # --- relation-derived needs ---------------------------------------------
    for relation in chain.relations:
        unavailable = "relations" in degraded
        label = _relation_label(relation, elements_by_id)
        digest = target_digest_for("relation", relation)
        revisions = _revision_ids(
            elements_by_id.get(relation.source_id), elements_by_id.get(relation.target_id)
        )

        def _mk(code: str, answerability: str) -> PurposeNeed:
            return PurposeNeed(
                id=f"{code}:{relation.id}",
                code=code,
                target_kind="relation",
                target_id=relation.id,
                target_label=label,
                answerability="unavailable" if unavailable else answerability,
                state="available",
                target_digest=digest,
                source_revision_ids=revisions,
                section="relations",
            )

        if relation.status == "conflicting":
            needs.append(_mk("relation_conflict", _base_answerability("relation_conflict")))
        if relation.status == "unknown":
            needs.append(_mk("relation_unknown", _base_answerability("relation_unknown")))
        if relation.recheck_state == "stale" and relation.decision_id is not None:
            needs.append(
                _mk("premise_recheck_required", _base_answerability("premise_recheck_required"))
            )
        if relation.status == "hypothesis":
            if relation.kind == "intervention_to_capability":
                needs.append(
                    _mk(
                        "capability_justification_missing",
                        _base_answerability("capability_justification_missing"),
                    )
                )
            else:
                needs.append(
                    _mk("decision_criterion_missing", _base_answerability("decision_criterion_missing"))
                )

    # --- human_value_judgement_required: an element itself is conflicting --
    for element in chain.elements:
        if element.confirmation != "conflicting":
            continue
        section = "capabilities" if element.kind == "core_capability" else "frame"
        label = element.display_statement or _FRAME_LABELS.get(element.kind, element.kind)
        needs.append(
            PurposeNeed(
                id=f"human_value_judgement_required:{element.id}",
                code="human_value_judgement_required",
                target_kind="element",
                target_id=element.id,
                target_label=label,
                answerability="unavailable" if section in degraded else "human_judgement",
                state="available",
                target_digest=target_digest_for("element", element),
                source_revision_ids=_revision_ids(element),
                section=section,
            )
        )

    return needs


# --- §4. Response history -> state/answerability -------------------------------


def apply_response_state(
    needs: Sequence[PurposeNeed], responses_by_need_id: Mapping[str, Mapping[str, Any]]
) -> List[PurposeNeed]:
    """§2.6/§2.7: fold the current (non-superseded) response row for each
    need id into that need's `state`/`answerability`.

    First-match:

    1. The need's section is degraded -> `unavailable` (a response history we
       cannot trust to compare digests against is not read at all).
    2. A current response exists AND its `target_digest` still matches the
       FRESHLY DERIVED target's digest -> the response is still "about" the
       exact thing being asked, and its `response_kind` decides the state:
       `confirm`/`correct` -> `answered` (and `answerability` becomes
       `already_answered` -- see the module docstring's `relation_conflict`
       example: confirming a relation whose conflict comes from an ENDPOINT
       does not clear `status == "conflicting"`, so the raw need would
       otherwise persist looking untouched); `defer` -> `deferred`;
       `unknown`/`investigate` -> `waiting`.
    3. Otherwise (no response, or the target moved since the last one) ->
       `available` -- §2.6's rule that a deferred need reappears once its
       target digest moves is exactly this branch: a stale `defer` row simply
       fails the digest match in step 2 and falls through here.
    """
    updated: List[PurposeNeed] = []
    for need in needs:
        if need.answerability == "unavailable":
            updated.append(replace(need, state="unavailable"))
            continue
        response = responses_by_need_id.get(need.id)
        if response is not None and response["target_digest"] == need.target_digest:
            kind = response["response_kind"]
            if kind in ("confirm", "correct"):
                updated.append(replace(need, answerability="already_answered", state="answered"))
                continue
            if kind == "defer":
                updated.append(replace(need, state="deferred"))
                continue
            if kind in ("unknown", "investigate"):
                updated.append(replace(need, state="waiting"))
                continue
        updated.append(replace(need, state="available"))
    return updated


# --- §5. Question selection --------------------------------------------------


def select_question(needs: Sequence[PurposeNeed]) -> Optional[PurposeNeed]:
    """§2.4: 0 or 1 question, first-match over `PRIORITY_TABLE`.

    Only `state == "available"` AND `answerability == "human_judgement"`
    needs are eligible -- `system_researchable` never reaches the developer
    as a question (routed instead), `already_answered`/`deferred`/`waiting`/
    `unavailable` needs are not re-asked. Ties within one rule row (multiple
    instances of the same need code) are broken by `need_id`, lexicographic
    -- deterministic, no LLM score, no client heuristic.
    """
    eligible = [n for n in needs if n.state == "available" and n.answerability == "human_judgement"]
    by_code: Dict[str, List[PurposeNeed]] = {}
    for need in eligible:
        by_code.setdefault(need.code, []).append(need)
    for _, code in PRIORITY_TABLE:
        candidates = sorted(by_code.get(code, ()), key=lambda n: n.id)
        if candidates:
            return candidates[0]
    return None


def routed_needs_for(needs: Sequence[PurposeNeed]) -> List[PurposeNeed]:
    """`system_researchable` needs currently available -- informational only
    (§2.4: they never reach the developer as a question)."""
    return sorted(
        (n for n in needs if n.answerability == "system_researchable" and n.state == "available"),
        key=lambda n: n.id,
    )


# --- §6. Suggested answers -----------------------------------------------------


def build_suggested_answer(
    need: PurposeNeed, chain: "purpose_chain.PurposeChainResult"
) -> Optional[PurposeSuggestedAnswer]:
    """§2.5: an AI candidate built ONLY from an existing row's own text.

    Relation targets never get one (a relation is confirmed/rejected, not
    corrected with free text). An element target gets one only when it
    already has content (`display_statement`) -- which structurally excludes
    every `frame_missing` need: if a source row existed, the element would
    already be `present`, not `unknown` (see the module docstring). This is
    intentional, not a gap: there is nothing grounded to suggest for
    something that does not exist yet.
    """
    if need.target_kind != "element":
        return None
    elements_by_id = {e.id: e for e in chain.elements}
    element = elements_by_id.get(need.target_id)
    if element is None or not element.display_statement:
        return None
    return PurposeSuggestedAnswer(
        text=element.display_statement,
        provenance=element.provenance,
        source_kind=element.source_kind,
        source_ids=tuple(element.source_ids),
        is_mock=element.is_mock,
    )


# --- §7. Fixed Japanese question copy, per need code (§2.9 / §0) --------------
#
# Server-owned fixed text (Principle 6/7). `{target}` is substituted with the
# need's own `target_label`. Never model output.

_NEED_COPY: Dict[str, Dict[str, str]] = {
    "frame_missing": {
        "prompt": "「{target}」がまだ分かっていません。教えてください。",
        "why_now": "Purpose Frame の最小3要素のうち「{target}」の内容がありません。",
        "blocked_decision": "Purpose Frame 全体の因果関係の確認。",
        "unlocks": "「{target}」を起点に、他の要素とのつながりを確認できるようになります。",
        "defer_impact": "保留すると、Purpose Frame が未完成のままになります。",
    },
    "relation_conflict": {
        "prompt": "「{target}」に矛盾があります。どちらが正しいか教えてください。",
        "why_now": "現在の内容のままでは、この関係を判断の根拠にできません。",
        "blocked_decision": "この関係に基づく次の判断。",
        "unlocks": "矛盾を解消すると、この関係を再び判断の根拠として使えます。",
        "defer_impact": "保留すると、矛盾を抱えたまま判断が進むことになります。",
    },
    "relation_unknown": {
        "prompt": "「{target}」がまだ説明できません。関係を教えてください。",
        "why_now": "接続の有無自体が分かっていないため、下流の判断に使えません。",
        "blocked_decision": "この関係より下流の Capability の位置づけ。",
        "unlocks": "関係が分かると、下流の Capability の解像度を上げられます。",
        "defer_impact": "保留すると、この関係は不明のままになります。",
    },
    "decision_criterion_missing": {
        "prompt": "「{target}」はまだ確認されていません。この因果関係を確認しますか。",
        "why_now": "現在の next action や改善評価は、この関係が仮説のままでは判断できません。",
        "blocked_decision": "次に何をすべきかの判断、および改善の評価。",
        "unlocks": "確認すると、この関係を根拠に次の判断を進められます。",
        "defer_impact": "保留すると、次の判断がこの仮説のまま止まります。",
    },
    "premise_recheck_required": {
        "prompt": "「{target}」の前提が変わった可能性があります。再確認しますか。",
        "why_now": "確認済みだった内容が、その後の変更で古くなっている可能性があります。",
        "blocked_decision": "この関係を根拠にした判断の継続。",
        "unlocks": "再確認すると、この関係を再び安心して根拠にできます。",
        "defer_impact": "保留すると、古くなった前提のまま扱われ続けます。",
    },
    "capability_justification_missing": {
        "prompt": "「{target}」の根拠はまだ確認されていません。",
        "why_now": "この Capability が実際にシステムの介入から実現されているか、確認できていません。",
        "blocked_decision": "この Capability の位置づけの確認。",
        "unlocks": "確認すると、この Capability を Purpose Chain 上で自信を持って扱えます。",
        "defer_impact": "保留すると、この Capability の根拠が未確認のままになります。",
    },
    "human_value_judgement_required": {
        "prompt": "「{target}」について、矛盾する情報があります。どちらを採用するか教えてください。",
        "why_now": "システム側では判断できない、開発者自身の価値判断が必要です。",
        "blocked_decision": "「{target}」に関する今後の判断。",
        "unlocks": "判断すると、「{target}」を再び安心して参照できます。",
        "defer_impact": "保留すると、矛盾したまま扱われ続けます。",
    },
}

_RULE_ROW_BY_CODE: Dict[str, int] = {code: row for row, code in PRIORITY_TABLE}


def build_question(
    need: PurposeNeed,
    chain: "purpose_chain.PurposeChainResult",
    *,
    fallback_reason: Optional[str] = None,
    routed_needs: Sequence[PurposeNeed] = (),
) -> PurposeQuestion:
    """Build the full §2.5 question payload for one selected/deep-linked need."""
    copy = _NEED_COPY[need.code]
    return PurposeQuestion(
        need_id=need.id,
        need_code=need.code,
        rule_row=_RULE_ROW_BY_CODE.get(need.code),
        prompt=copy["prompt"].format(target=need.target_label),
        why_now=copy["why_now"].format(target=need.target_label),
        blocked_decision=copy["blocked_decision"].format(target=need.target_label),
        unlocks=copy["unlocks"].format(target=need.target_label),
        defer_impact=copy["defer_impact"].format(target=need.target_label),
        target_kind=need.target_kind,
        target_id=need.target_id,
        target_label=need.target_label,
        answerability=need.answerability,
        suggested_answer=build_suggested_answer(need, chain),
        state=need.state,
        source_revision_ids=need.source_revision_ids,
        fallback_reason=fallback_reason,
        routed_needs=tuple(routed_needs),
    )


# --- §8. need_id resolution for the deep-link GET -----------------------------


def resolve_need(needs: Sequence[PurposeNeed], need_id: str) -> Optional[PurposeNeed]:
    """The need matching `need_id` in the CURRENT derivation, if any."""
    for need in needs:
        if need.id == need_id:
            return need
    return None


def parse_need_id(need_id: str) -> Tuple[str, str]:
    """`f"{need_code}:{target_id}"` -> `(need_code, target_id)`.

    Splits on the FIRST colon only: no `need_code` value contains a colon,
    while a relation `target_id` always does (`purpose_chain.relation_id`'s
    own `f"{kind}:{source}->{target}"` shape), so splitting from the right
    would cut a relation id in half.
    """
    code, _, target_id = need_id.partition(":")
    return code, target_id
