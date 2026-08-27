"""Purpose Verification: Experience Hypothesis / Outcome Criterion / Reuse
Hypothesis (Issue #391, the 4th and final sub-issue of Epic #387).

`docs/purpose-chain.md` §4 is the design contract this module implements.
The Purpose Chain (#388) and Purpose Needs (#389) modules describe and
diagnose the causal chain from 対象者と現在の課題 down to Capabilities; this
module is the layer §0's diagram marks "必要時のみ": three OPTIONAL concepts
a developer may attach to one element or relation, by the same stable string
identity #388 already uses, to make the causal claim empirically checkable
instead of merely asserted.

```
purpose_experience_hypothesis  -- 最小の成功体験.        proposed|confirmed|retired
purpose_outcome_criterion      -- 成果証拠.               proposed|confirmed|observed
                                                          |contradicted|not_observed
                                                          |not_computed
purpose_reuse_hypothesis       -- 再利用契機.              proposed|confirmed|retired
```

Four rules this module must never violate (§4.1/§4.2/§4.3):

* **Creation is never offered because a resolution level is low.** "Purpose
  Frame が L1 だから" is explicitly NOT a reason (§4.1) -- every create
  endpoint requires a `need_id` naming a currently-`available`
  `app/purpose_needs.py` need whose code is in `CREATABLE_NEED_CODES`, and
  refuses (`NotCreatable`) otherwise. There is no code path that creates a
  row without a real need to point at.
* **An outcome is never inferred.** `record_outcome_result` always writes a
  verdict the DEVELOPER supplies, never one this module computes from the
  evidence text -- "the runtime trace alone proves a user succeeded" is
  exactly the mistake §4.2 exists to forbid, and the same discipline applies
  to any evidence, human-reported or runtime-observed.
* **Human-reported evidence and runtime observation are separate columns,
  never merged into one "result".** `PurposeOutcomeEvidenceSource` picks
  which of the two `purpose_outcome_criterion` column pairs a given
  `record_outcome_result` call writes to; nothing here ever collapses them
  into a shared field.
* **Lineage is an explicit id, resolved at read time.** `resolve_lineage`
  checks `experiment_id` / `candidate_version_id` against THIS System's own
  tables by their own primary key -- never a broader existence or timestamp
  check -- and reports the genuine third value `unresolved` (id set, row
  gone) rather than silently downgrading to `none`.

Two design decisions the spec text leaves open, made explicitly here
(flagged for lead review, see the implementation report):

1. **Which need code makes which concept creatable.** The task instructions
   for this issue are explicit about one case: "the `decision_criterion_missing`
   need is what makes an outcome criterion creatable" -- that need fires for
   the `problem_to_change` / `change_to_intervention` relations (§0's own
   diagram: the top of the chain, where "does this causal step actually
   hold" is exactly what an outcome criterion checks). `experience_hypothesis`
   and `reuse_hypothesis` are not named explicitly anywhere in the spec text,
   but §0's diagram hangs both off Capabilities specifically ("Capabilities
   → 必要時のみ → UX 成功体験 → 成果証拠 → 再利用契機"), and the one
   Capability-scoped need code in #389's fixed table is
   `capability_justification_missing` (fires for a `hypothesis`-status
   `intervention_to_capability` relation) -- so both are gated on that need
   code instead. `CREATABLE_NEED_CODES` states this table explicitly rather
   than leaving it implicit in scattered `if` branches.
2. **`select_verification_prompt`'s at-most-one restraint (§4.5) picks
   `outcome_criterion` over `experience_hypothesis`/`reuse_hypothesis` when
   both happen to be creatable** (they never are for the SAME target, since
   their gating need codes target different relation kinds, but a session
   can have both kinds of opportunity open on DIFFERENT targets
   simultaneously) **and `experience_hypothesis` over `reuse_hypothesis`**
   when only the Capability-scoped need fires (both are creatable off the
   same need, and offering "what would a real user reuse for" before "what
   would a real user first experience" would be asking about a step in the
   chain (再利用契機) before the step it depends on (UX 成功体験) has even
   been proposed).

probe-agent:
  role: Optional Experience/Outcome/Reuse verification concepts attached to Purpose Chain elements/relations, need-gated creation, and the deterministic §4.4 L3 resolution check
  capability: interactive-system-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify a concept can never be created without a currently-available justifying need, that an outcome's observed/contradicted verdict is always a developer-supplied value rather than a computed inference, that human-reported and runtime-observed evidence stay in separate columns, and that lineage renders unresolved rather than none when a linked id no longer exists.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, get_args

from .models import (
    PurposeHypothesisState,
    PurposeOutcomeCriterionState,
    PurposeOutcomeEvidenceSource,
    PurposeOutcomeLineageState,
    PurposeOutcomeVerdict,
    PurposeVerificationConceptKind,
)
from .purpose_needs import PurposeNeed

__all__ = [
    "HYPOTHESIS_STATES",
    "OUTCOME_STATES",
    "EVIDENCE_SOURCES",
    "OUTCOME_VERDICTS",
    "LINEAGE_STATES",
    "CONCEPT_KINDS",
    "CREATABLE_NEED_CODES",
    "PROMPT_PRIORITY",
    "OBSERVATION_HINT",
    "PROMPT_TEXT",
    "VerificationError",
    "NotCreatable",
    "NotFound",
    "VerificationPromptCandidate",
    "creatable_need",
    "select_verification_prompt",
    "resolve_lineage",
    "full_outcome_criterion_target_ids",
    "create_experience_hypothesis",
    "create_reuse_hypothesis",
    "create_outcome_criterion",
    "confirm_hypothesis",
    "retire_hypothesis",
    "confirm_outcome_criterion",
    "link_outcome_criterion",
    "record_outcome_result",
    "record_outcome_unavailable",
    "list_experience_hypotheses",
    "list_reuse_hypotheses",
    "list_outcome_criteria",
]


# --- §0. Finite vocabularies -------------------------------------------------

HYPOTHESIS_STATES: Tuple[str, ...] = get_args(PurposeHypothesisState)
OUTCOME_STATES: Tuple[str, ...] = get_args(PurposeOutcomeCriterionState)
EVIDENCE_SOURCES: Tuple[str, ...] = get_args(PurposeOutcomeEvidenceSource)
OUTCOME_VERDICTS: Tuple[str, ...] = get_args(PurposeOutcomeVerdict)
LINEAGE_STATES: Tuple[str, ...] = get_args(PurposeOutcomeLineageState)
CONCEPT_KINDS: Tuple[str, ...] = get_args(PurposeVerificationConceptKind)

#: concept_kind -> the `purpose_needs` need codes that make it creatable
#: (design decision 1 in the module docstring). A need of any OTHER code can
#: exist on the same target without ever making a row here creatable.
CREATABLE_NEED_CODES: Dict[str, Tuple[str, ...]] = {
    "outcome_criterion": ("decision_criterion_missing",),
    "experience_hypothesis": ("capability_justification_missing",),
    "reuse_hypothesis": ("capability_justification_missing",),
}

#: §4.5's at-most-one selection order (design decision 2): `(row, concept_kind)`.
PROMPT_PRIORITY: Tuple[Tuple[int, str], ...] = (
    (1, "outcome_criterion"),
    (2, "experience_hypothesis"),
    (3, "reuse_hypothesis"),
)

#: 最小の観測方法 -- fixed copy (Principle 6/7), never model output. Named per
#: concept kind because "what is the smallest thing worth writing down" is a
#: genuinely different answer for each of the three.
OBSERVATION_HINT: Dict[str, str] = {
    "experience_hypothesis": (
        "実際のユーザーが最初に何を体験すれば成功と言えるか、一文で書き留めてください。"
        "数値の指標である必要はありません。"
    ),
    "outcome_criterion": (
        "何を測るか（measure）・今の値（baseline）・目指す値（target）・"
        "いつまでに見るか（observation window）を、それぞれ一言で書いてください。"
    ),
    "reuse_hypothesis": (
        "ユーザーがもう一度使いたくなるとしたら、それはどんな場面かを一文で書き留めてください。"
    ),
}

#: 何を検証するか -- fixed prompt copy per concept kind, `{target}` substituted
#: with the justifying need's own `target_label` (Principle 6/7).
PROMPT_TEXT: Dict[str, str] = {
    "experience_hypothesis": "「{target}」について、最小の成功体験を検証条件として書き留めますか。",
    "outcome_criterion": "「{target}」について、成果を示す検証条件を定義しますか。",
    "reuse_hypothesis": "「{target}」について、再利用の契機を検証条件として書き留めますか。",
}


# --- §1. Errors ----------------------------------------------------------------


class VerificationError(Exception):
    """Base for this module's finite error set."""


class NotCreatable(VerificationError):
    """`need_id` does not currently justify creating this concept kind --
    either it does not resolve, is not `available`, or its code is not in
    `CREATABLE_NEED_CODES[concept_kind]` (§4.1)."""


class NotFound(VerificationError):
    """The concept row does not exist in this System/session."""


# --- §2. Need-gated creatability ----------------------------------------------


def creatable_need(
    needs: Sequence[PurposeNeed], concept_kind: str, need_id: str
) -> PurposeNeed:
    """The `PurposeNeed` that justifies creating `concept_kind`, or raises
    `NotCreatable` (§4.1). Never returns a need whose code is outside
    `CREATABLE_NEED_CODES[concept_kind]`, even if the id resolves to a real,
    currently-available need of a DIFFERENT code -- the code is what
    "justifies" means here, not mere presence."""
    allowed = CREATABLE_NEED_CODES.get(concept_kind, ())
    need = next((n for n in needs if n.id == need_id), None)
    if need is None or need.state != "available" or need.code not in allowed:
        raise NotCreatable(need_id)
    return need


@dataclass(frozen=True)
class VerificationPromptCandidate:
    concept_kind: str
    need: PurposeNeed


def select_verification_prompt(
    needs: Sequence[PurposeNeed], existing_targets: Mapping[str, Set[str]]
) -> Optional[VerificationPromptCandidate]:
    """§4.5: 0 or 1 verification prompt, first-match over `PROMPT_PRIORITY`.

    `existing_targets[concept_kind]` is the set of `f"{target_kind}:{target_id}"`
    strings that ALREADY have at least one row of that concept kind -- a
    target that already has one is not re-offered, so confirming or
    retiring an existing hypothesis is how the developer moves past it,
    never a second nag for the same target.
    """
    for _row, concept_kind in PROMPT_PRIORITY:
        allowed = CREATABLE_NEED_CODES[concept_kind]
        already = existing_targets.get(concept_kind, set())
        candidates = sorted(
            (
                n
                for n in needs
                if n.state == "available"
                and n.code in allowed
                and f"{n.target_kind}:{n.target_id}" not in already
            ),
            key=lambda n: n.id,
        )
        if candidates:
            return VerificationPromptCandidate(concept_kind=concept_kind, need=candidates[0])
    return None


# --- §3. Lineage ---------------------------------------------------------------


def resolve_lineage(
    conn: sqlite3.Connection,
    system_id: int,
    experiment_id: Optional[int],
    candidate_version_id: Optional[int],
) -> str:
    """§4.3: explicit id, resolved fresh every read -- never a System-wide
    existence check. `unresolved` (id set, row gone) is a real third value,
    never silently reported as `none`."""
    if experiment_id is None and candidate_version_id is None:
        return "none"
    if experiment_id is not None:
        row = conn.execute(
            "SELECT 1 FROM experiments WHERE id = ? AND system_id = ?",
            (experiment_id, system_id),
        ).fetchone()
        if row is None:
            return "unresolved"
    if candidate_version_id is not None:
        row = conn.execute(
            "SELECT 1 FROM candidate_versions WHERE id = ? AND system_id = ?",
            (candidate_version_id, system_id),
        ).fetchone()
        if row is None:
            return "unresolved"
    return "linked"


# --- §4. L3 reachability (§4.4) -------------------------------------------------


def full_outcome_criterion_target_ids(
    conn: sqlite3.Connection, system_id: int, session_id: Optional[int]
) -> Set[Tuple[str, str]]:
    """`(target_kind, target_id)` pairs with an outcome criterion carrying
    ALL FOUR of measure/baseline_value/target_value/observation_window
    (§4.4). State-independent on purpose: §4.4's text checks field presence
    only, and every field is something the developer typed at creation,
    never model output, so a `proposed` row with all four filled in already
    reflects a real manual commitment.

    Returns BOTH `element` and `relation` targets -- `purpose_chain.py`'s
    caller decides how each maps to an element's `L3` eligibility. This
    matters because `CREATABLE_NEED_CODES["outcome_criterion"]` is
    `decision_criterion_missing`, whose needs target the `problem_to_change`
    / `change_to_intervention` RELATIONS (§0's diagram: the top of the
    chain), never an element directly -- so a criterion verifying "does this
    causal step really hold" is evidence FOR the element the relation
    explains, and `purpose_chain._resolution_level`'s caller checks the
    element's own id AND its primary relation's id against this set."""
    if session_id is None:
        return set()
    rows = conn.execute(
        """SELECT target_kind, target_id FROM purpose_outcome_criterion
           WHERE system_id = ? AND session_id = ?
             AND measure != '' AND baseline_value != ''
             AND target_value != '' AND observation_window != ''""",
        (system_id, session_id),
    ).fetchall()
    return {(row["target_kind"], row["target_id"]) for row in rows}


# --- §5. Creation ----------------------------------------------------------------


def _create_hypothesis(
    conn: sqlite3.Connection,
    table: str,
    *,
    system_id: int,
    session_id: int,
    need: PurposeNeed,
    statement: str,
    created_by: Optional[str],
    now: float,
) -> int:
    cur = conn.execute(
        f"""INSERT INTO {table}
                (system_id, session_id, target_kind, target_id, target_label,
                 target_digest, source_need_id, source_need_code, statement,
                 state, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
        (
            system_id, session_id, need.target_kind, need.target_id, need.target_label,
            need.target_digest, need.id, need.code, statement, created_by, now,
        ),
    )
    return cur.lastrowid


def create_experience_hypothesis(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    need: PurposeNeed,
    statement: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> int:
    return _create_hypothesis(
        conn, "purpose_experience_hypothesis",
        system_id=system_id, session_id=session_id, need=need, statement=statement,
        created_by=created_by, now=time.time() if now is None else now,
    )


def create_reuse_hypothesis(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    need: PurposeNeed,
    statement: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> int:
    return _create_hypothesis(
        conn, "purpose_reuse_hypothesis",
        system_id=system_id, session_id=session_id, need=need, statement=statement,
        created_by=created_by, now=time.time() if now is None else now,
    )


def create_outcome_criterion(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    need: PurposeNeed,
    measure: str,
    baseline_value: str,
    target_value: str,
    observation_window: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> int:
    now = time.time() if now is None else now
    cur = conn.execute(
        """INSERT INTO purpose_outcome_criterion
               (system_id, session_id, target_kind, target_id, target_label,
                target_digest, source_need_id, source_need_code, measure,
                baseline_value, target_value, observation_window, state,
                created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
        (
            system_id, session_id, need.target_kind, need.target_id, need.target_label,
            need.target_digest, need.id, need.code, measure, baseline_value,
            target_value, observation_window, created_by, now,
        ),
    )
    return cur.lastrowid


# --- §6. Transitions -------------------------------------------------------------


def _require_row(conn: sqlite3.Connection, table: str, system_id: int, session_id: int, concept_id: int):
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND system_id = ? AND session_id = ?",
        (concept_id, system_id, session_id),
    ).fetchone()
    if row is None:
        raise NotFound(concept_id)
    return row


def confirm_hypothesis(
    conn: sqlite3.Connection,
    table: str,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    confirmed_by: Optional[str],
    now: Optional[float] = None,
) -> None:
    """`proposed -> confirmed` for either hypothesis table. A no-op state
    transition (confirming an already-confirmed or retired row) is refused
    by the caller reading `state` first -- this function always writes."""
    _require_row(conn, table, system_id, session_id, concept_id)
    now = time.time() if now is None else now
    conn.execute(
        f"UPDATE {table} SET state = 'confirmed', confirmed_by = ?, confirmed_at = ? WHERE id = ?",
        (confirmed_by, now, concept_id),
    )


def retire_hypothesis(
    conn: sqlite3.Connection,
    table: str,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    reason: str,
    retired_by: Optional[str],
    now: Optional[float] = None,
) -> None:
    _require_row(conn, table, system_id, session_id, concept_id)
    now = time.time() if now is None else now
    conn.execute(
        f"""UPDATE {table}
               SET state = 'retired', retired_by = ?, retired_at = ?, retirement_reason = ?
             WHERE id = ?""",
        (retired_by, now, reason, concept_id),
    )


def confirm_outcome_criterion(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    confirmed_by: Optional[str],
    now: Optional[float] = None,
) -> None:
    _require_row(conn, "purpose_outcome_criterion", system_id, session_id, concept_id)
    now = time.time() if now is None else now
    conn.execute(
        """UPDATE purpose_outcome_criterion
               SET state = 'confirmed', confirmed_by = ?, confirmed_at = ?
             WHERE id = ?""",
        (confirmed_by, now, concept_id),
    )


def link_outcome_criterion(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    experiment_id: Optional[int],
    candidate_version_id: Optional[int],
) -> None:
    """§4.3: record the explicit lineage id(s). Does not itself change
    `state` -- linking is a separate fact from confirming or observing."""
    _require_row(conn, "purpose_outcome_criterion", system_id, session_id, concept_id)
    conn.execute(
        """UPDATE purpose_outcome_criterion
               SET experiment_id = ?, candidate_version_id = ?
             WHERE id = ?""",
        (experiment_id, candidate_version_id, concept_id),
    )


def record_outcome_result(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    source: str,
    verdict: str,
    evidence_text: str,
    is_synthetic: bool,
    recorded_by: Optional[str],
    now: Optional[float] = None,
) -> None:
    """Writes exactly ONE evidence column pair (`source` picks which) plus
    the resulting `observed`/`contradicted` state -- always the developer's
    OWN verdict, never computed from `evidence_text` (§4.2, module docstring
    rule 2)."""
    _require_row(conn, "purpose_outcome_criterion", system_id, session_id, concept_id)
    now = time.time() if now is None else now
    state = "observed" if verdict == "supports" else "contradicted"
    if source == "human_reported":
        conn.execute(
            """UPDATE purpose_outcome_criterion
                   SET state = ?, human_reported_evidence = ?, human_reported_verdict = ?,
                       human_reported_at = ?, human_reported_by = ?,
                       human_reported_state = ?, human_reported_is_synthetic = ?
                 WHERE id = ?""",
            (state, evidence_text, verdict, now, recorded_by, state,
             int(is_synthetic), concept_id),
        )
    else:
        conn.execute(
            """UPDATE purpose_outcome_criterion
                   SET state = ?, runtime_observation_text = ?, runtime_observation_verdict = ?,
                       runtime_observed_at = ?, runtime_observed_by = ?,
                       runtime_observation_state = ?, runtime_observation_is_synthetic = ?
                 WHERE id = ?""",
            (state, evidence_text, verdict, now, recorded_by, state,
             int(is_synthetic), concept_id),
        )
    _refresh_outcome_state(conn, concept_id)


def _refresh_outcome_state(conn: sqlite3.Connection, concept_id: int) -> None:
    """Aggregate source states without allowing the latest write to hide a conflict."""
    row = conn.execute(
        """SELECT human_reported_state, runtime_observation_state,
                  human_reported_is_synthetic, runtime_observation_is_synthetic
             FROM purpose_outcome_criterion WHERE id = ?""",
        (concept_id,),
    ).fetchone()
    states = {row[0], row[1]} - {None}
    aggregate_synthetic = int(bool(row[2]) or bool(row[3]))
    for candidate in ("contradicted", "observed", "not_observed", "not_computed"):
        if candidate in states:
            conn.execute(
                "UPDATE purpose_outcome_criterion SET state = ?, is_synthetic = ? WHERE id = ?",
                (candidate, aggregate_synthetic, concept_id),
            )
            return
def record_outcome_unavailable(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    session_id: int,
    concept_id: int,
    source: str,
    state: str,
    reason: str,
    recorded_by: Optional[str],
    now: Optional[float] = None,
) -> None:
    """Records `not_observed`/`not_computed` as an explicit sourced fact."""
    _require_row(conn, "purpose_outcome_criterion", system_id, session_id, concept_id)
    if state not in ("not_observed", "not_computed"):
        raise ValueError(state)
    now = time.time() if now is None else now
    if source == "human_reported":
        conn.execute(
            """UPDATE purpose_outcome_criterion
                  SET state = ?, human_reported_state = ?, human_reported_evidence = ?,
                      human_reported_verdict = NULL, human_reported_at = ?,
                      human_reported_by = ?, human_reported_is_synthetic = 0
                WHERE id = ?""",
            (state, state, reason, now, recorded_by, concept_id),
        )
    else:
        conn.execute(
            """UPDATE purpose_outcome_criterion
                  SET state = ?, runtime_observation_state = ?, runtime_observation_text = ?,
                      runtime_observation_verdict = NULL, runtime_observed_at = ?,
                      runtime_observed_by = ?, runtime_observation_is_synthetic = 0
                WHERE id = ?""",
            (state, state, reason, now, recorded_by, concept_id),
        )
    _refresh_outcome_state(conn, concept_id)


# --- §7. Listing -----------------------------------------------------------------


def list_experience_hypotheses(
    conn: sqlite3.Connection, system_id: int, session_id: Optional[int]
) -> List[Dict[str, Any]]:
    if session_id is None:
        return []
    rows = conn.execute(
        """SELECT * FROM purpose_experience_hypothesis
           WHERE system_id = ? AND session_id = ? ORDER BY id""",
        (system_id, session_id),
    ).fetchall()
    return [dict(row) for row in rows]


def list_reuse_hypotheses(
    conn: sqlite3.Connection, system_id: int, session_id: Optional[int]
) -> List[Dict[str, Any]]:
    if session_id is None:
        return []
    rows = conn.execute(
        """SELECT * FROM purpose_reuse_hypothesis
           WHERE system_id = ? AND session_id = ? ORDER BY id""",
        (system_id, session_id),
    ).fetchall()
    return [dict(row) for row in rows]


def list_outcome_criteria(
    conn: sqlite3.Connection, system_id: int, session_id: Optional[int]
) -> List[Dict[str, Any]]:
    if session_id is None:
        return []
    rows = conn.execute(
        """SELECT * FROM purpose_outcome_criterion
           WHERE system_id = ? AND session_id = ? ORDER BY id""",
        (system_id, session_id),
    ).fetchall()
    return [dict(row) for row in rows]
