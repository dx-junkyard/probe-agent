"""Execution target -> execution-mode scope mapping (Epic #412 / Issue #413, §4.3).

WHY THIS MODULE EXISTS
----------------------
`app/execution_mode.py` owns the fail-closed capability gate, but the gate's
subject is an Evolution Node (`node_key`) or a runtime Flow. The pre-existing
execution entry points -- Experiment run, Replay run, Replay variant run,
Candidate Studio replay/promote -- know nothing about Nodes: they carry a
`component_id` or a `feature_id`. Until this module existed the gate was wired
only into mode queries, the Flow experiment LLM draft, and the recording of an
execution that had ALREADY happened.

    Being unable to record a reference to an execution is not the same as
    being unable to execute.

So a Node whose effective mode was `fixed` could still have a candidate
executed through those endpoints, and the execution-mode axis did not actually
control candidate execution -- #413's core acceptance criterion. This module is
the one canonical deterministic mapping that closes that hole.

THE CONTRACT
------------
`resolve_execution_target` answers ONE question -- "which Evolution Node's
permission governs this execution target?" -- and answers it from persisted
rows only:

    evolution_node_link(link_kind='component'|'feature',
                        target_ref = <the target>,
                        superseded_by_id IS NULL)
      JOIN evolution_node ON the SAME system_id

That is an exact indexed lookup, which is what EM-ADR-1 requires of anything a
fail-closed gate depends on: the gate must be able to say "unreadable => the
safe answer", and a derivation that can itself fail (a call-graph rebuild, a
snapshot parse) turns a gate failure into a service outage instead of a safe
refusal. The `component`/`feature` links are single indexed reads on an
existing canonical table (#396's `evolution_node_link`); this Epic adds no
mapping table of its own.

Matching is EXACT STRING EQUALITY. No prefix, no normalization, no similarity,
no keyword scoring (Core Design Principle 6). A near-miss is not a match: a
gate that guesses which Node "probably" owns a target is the caller's-claim
defect EM-ADR-4 just removed from the resolver, re-introduced one layer up.

THE THREE CLASSIFICATIONS
-------------------------
`governance` is a finite three-value set, and the three are deliberately not
two:

``governed``
    Exactly one Node links this target. The execution-mode gate applies:
    `require_capability(..., node_key=<that Node>)` decides.

``unmapped``
    No Node links this target. The gate does NOT apply and the endpoint
    proceeds exactly as it did before this module existed. This is not a
    concession -- bulk-migrating every existing Component onto an Evolution
    Node is an explicit Epic non-goal (§0.2), and most executions in a real
    System have no Node at all. A gate that refused everything it could not
    classify would break every existing Replay / Experiment / Candidate Studio
    user, which is a worse failure than the hole being closed.

    `unmapped` is NEVER silent. Every gated endpoint reports the
    classification back (see `governance_headers`), and
    `GET /execution-modes/target-governance` answers it for any target without
    running anything. "Not governed" and "permitted" must not look identical
    on the wire -- that is #366's one-displayed-word-two-facts rule applied to
    an authorization answer.

``ambiguous``
    More than one Node links this target. This FAILS CLOSED with the finite
    code `ambiguous_target_mapping`. Picking one Node -- the first, the newest,
    the most permissive -- would be the system deciding whose permission
    applies, which is exactly the class of guess EM-ADR-4 eliminated. Multiple
    concurrent links of one kind are legitimate for a Node (`add_link` never
    auto-supersedes), so two Nodes claiming one Component is a real modelling
    state, not corruption; the developer resolves it by superseding a link.

WHAT THIS MODULE DOES NOT DO
----------------------------
It writes nothing, it calls no LLM, it starts no subprocess, and it never
re-derives an execution mode -- `resolve_execution_mode` stays the single
canonical resolver (§3.5). It only decides WHICH subject the existing gate is
asked about.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from . import execution_mode
from .execution_mode import ExecutionModeDecision, ExecutionModeError

__all__ = [
    "EXECUTION_TARGET_KINDS",
    "EXECUTION_TARGET_GOVERNANCE_VALUES",
    "EXECUTION_TARGET_DENIAL_CODES",
    "TARGET_KIND_LINK_KIND",
    "GOVERNANCE_HEADER",
    "MODE_HEADER",
    "NODE_HEADER",
    "ExecutionTargetError",
    "ExecutionTargetAmbiguous",
    "ExecutionTargetMapping",
    "ExecutionTargetGate",
    "ExecutionTargetDenialOut",
    "ExecutionTargetGovernanceOut",
    "resolve_execution_target",
    "require_target_capability",
    "governance_headers",
    "apply_governance_headers",
    "governance_out",
]


#: The execution-target vocabularies this module can map. Both name an
#: `evolution_node_link.link_kind` that is already in that table's CHECK
#: constraint, so a target kind can never reference a link kind the schema
#: does not have.
#:
#: `component` is the Replay / Candidate Studio axis (`replay_sets`,
#: `candidate_sessions`, `replay_runs` all carry `component_id`).
#: `feature` is the Experiment axis (`experiments.feature_id`, the Feature
#: Map's stable `feature_drafts.feature_id`).
TARGET_KIND_LINK_KIND: Dict[str, str] = {
    "component": "component",
    "feature": "feature",
}
EXECUTION_TARGET_KINDS: Tuple[str, ...] = tuple(TARGET_KIND_LINK_KIND)

#: `governed` / `unmapped` / `ambiguous` -- see the module docstring. Three
#: values, never two: "no Node governs this" and "several do" have opposite
#: safe answers (proceed vs refuse), so collapsing them would either break the
#: legacy path or open the hole again.
EXECUTION_TARGET_GOVERNANCE_VALUES: Tuple[str, ...] = (
    "governed",
    "unmapped",
    "ambiguous",
)

#: This module's OWN finite denial vocabulary. A refusal that originates in the
#: mode resolver keeps `execution_mode`'s codes verbatim (§4.1) -- this one is
#: added because "we could not tell whose permission applies" is a different
#: fact from any mode reading, and reporting it as `capability_not_permitted`
#: would tell the developer to change a mode when the fix is to supersede a
#: link.
EXECUTION_TARGET_DENIAL_CODES: Tuple[str, ...] = ("ambiguous_target_mapping",)

#: Response headers every gated endpoint sets. Headers rather than body fields
#: because the bodies are pre-existing response models shared with untouched
#: endpoints; the classification is additive metadata about the request, not a
#: change to what the endpoint returns.
GOVERNANCE_HEADER = "X-Execution-Governance"
MODE_HEADER = "X-Execution-Mode"
NODE_HEADER = "X-Execution-Node-Key"


class ExecutionTargetError(ExecutionModeError):
    """Base error for the target-mapping layer.

    Derives from `ExecutionModeError` so a caller that already handles the
    execution-mode layer's failures cannot accidentally let one of these
    escape as a 500.
    """


class ExecutionTargetAmbiguous(ExecutionTargetError):
    """More than one Evolution Node links this execution target.

    Carries the finite `denial_code` and the competing `node_keys` so the
    developer is told WHICH links to resolve rather than only that something
    was ambiguous. Routes map this to 409, the same status the capability gate
    uses -- both mean "the persisted configuration refuses this execution".
    """

    def __init__(self, mapping: "ExecutionTargetMapping") -> None:
        super().__init__(
            f"execution target {mapping.target_kind}:{mapping.target_ref!r} is "
            f"linked by more than one Evolution Node "
            f"({', '.join(mapping.node_keys)}); which Node's execution mode "
            "applies cannot be decided by the system. Supersede the "
            "evolution_node_link rows until exactly one Node links it."
        )
        self.denial_code = "ambiguous_target_mapping"
        self.mapping = mapping
        self.node_keys = mapping.node_keys


@dataclass(frozen=True)
class ExecutionTargetMapping:
    """The deterministic reading of one execution target's governance.

    `node_key` / `node_id` are populated ONLY for `governed`. `node_keys`
    always carries every Node that linked the target (empty for `unmapped`,
    one for `governed`, two or more for `ambiguous`), so the ambiguous case can
    name the competing Nodes without a second query.
    """

    system_id: int
    target_kind: str
    target_ref: str
    link_kind: str
    governance: str
    node_key: Optional[str] = None
    node_id: Optional[int] = None
    node_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionTargetGate:
    """What a gated entry point learned before it ran anything.

    `enforced` and `decision` are two facts, not one. `enforced=False` with
    `decision=None` is `unmapped` -- the legacy path, in which no mode was
    consulted at all. It must never render as "mode fixed" or as "permitted":
    the first asserts a reading that was not taken, the second hides that the
    contract does not yet cover this target.
    """

    mapping: ExecutionTargetMapping
    capability: str
    enforced: bool
    decision: Optional[ExecutionModeDecision] = None

    @property
    def governance(self) -> str:
        return self.mapping.governance

    @property
    def node_key(self) -> Optional[str]:
        return self.mapping.node_key

    @property
    def mode(self) -> Optional[str]:
        return self.decision.mode if self.decision is not None else None

    @property
    def mode_source(self) -> Optional[str]:
        return self.decision.source_scope if self.decision is not None else None

    @property
    def mode_reason(self) -> Optional[str]:
        return self.decision.reason if self.decision is not None else None


class ExecutionTargetDenialOut(BaseModel):
    """The 409 body for `ambiguous_target_mapping`.

    Deliberately its OWN model rather than `models.ExecutionModeDenialOut`:
    that model's `denial_code` is the finite §4.1 vocabulary of MODE refusals,
    and widening it would say that "we could not tell whose permission applies"
    is a mode reading. The field SHAPE is identical so a client parses one
    body, and `mode` / `source_scope` / `reason` / `decision` stay `None`
    because no mode was resolved (#380 -- an unreadable fact is never a fact's
    default value).
    """

    denial_code: Literal["ambiguous_target_mapping"]
    message: str
    node_keys: list[str] = Field(default_factory=list)
    mode: None = None
    source_scope: None = None
    reason: None = None
    decision: None = None


class ExecutionTargetGovernanceOut(BaseModel):
    """Read model for `GET /execution-modes/target-governance`.

    Defined here rather than in `app/models.py` so this contract's finite
    vocabularies live next to the code that produces them.

    `mode` is `None` for `unmapped` and `ambiguous` on purpose: no mode was
    resolved, and substituting the fail-closed default would report a reading
    that was never taken (#380 -- an unreadable fact is never a fact's default
    value).
    """

    target_kind: str = Field(description="component | feature")
    target_ref: str
    governance: str = Field(description="governed | unmapped | ambiguous")
    gated: bool = Field(
        description=(
            "True when the execution-mode gate applies to this target. False "
            "means the legacy path: no Evolution Node links it, so existing "
            "behaviour is unchanged."
        )
    )
    node_key: Optional[str] = None
    node_keys: list[str] = Field(default_factory=list)
    mode: Optional[str] = None
    mode_source: Optional[str] = None
    mode_reason: Optional[str] = None
    capability: Optional[str] = None
    capability_permitted: Optional[bool] = None
    denial_code: Optional[str] = None


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def resolve_execution_target(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    target_kind: str,
    target_ref: Optional[str],
) -> ExecutionTargetMapping:
    """Classify one execution target. Reads only -- no judgement about modes.

    Both the link row and the Node row are constrained to `system_id`, so a
    Node in another System can never govern (nor reveal itself through) this
    System's execution. `superseded_by_id IS NULL` is the same currency rule
    `execution_mode._node_flow_scope_refs` uses for Flow membership.
    """
    if target_kind not in TARGET_KIND_LINK_KIND:
        raise execution_mode.ExecutionModeValidationError(
            f"target_kind must be one of {list(EXECUTION_TARGET_KINDS)}, "
            f"got {target_kind!r}"
        )
    link_kind = TARGET_KIND_LINK_KIND[target_kind]
    # NOT stripped. Matching is exact string equality, and trimming the queried
    # ref would be a normalization: `" norm"` would then match a link stored as
    # `"norm"`, which is precisely the near-miss this contract refuses to make.
    # Only the emptiness TEST tolerates whitespace, because a whitespace-only
    # ref cannot be linked at all.
    ref = target_ref or ""
    if not ref.strip():
        # `add_link` refuses an empty `target_ref`, so nothing can be linked
        # to the empty string. Returning `unmapped` rather than raising keeps
        # a legacy row with a blank identifier on the legacy path instead of
        # turning it into a hard failure at an execution entry point.
        return ExecutionTargetMapping(
            system_id=system_id,
            target_kind=target_kind,
            target_ref=ref,
            link_kind=link_kind,
            governance="unmapped",
        )

    rows = conn.execute(
        """SELECT DISTINCT n.id AS node_id, n.node_key AS node_key
             FROM evolution_node_link AS l
             JOIN evolution_node AS n
               ON n.id = l.node_id AND n.system_id = l.system_id
            WHERE l.system_id = ?
              AND l.link_kind = ?
              AND l.target_ref = ?
              AND l.superseded_by_id IS NULL
            ORDER BY n.node_key ASC, n.id ASC""",
        (system_id, link_kind, ref),
    ).fetchall()

    node_keys = tuple(str(row["node_key"]) for row in rows)
    if not rows:
        governance = "unmapped"
        node_key: Optional[str] = None
        node_id: Optional[int] = None
    elif len(rows) == 1:
        governance = "governed"
        node_key = str(rows[0]["node_key"])
        node_id = int(rows[0]["node_id"])
    else:
        governance = "ambiguous"
        node_key = None
        node_id = None

    return ExecutionTargetMapping(
        system_id=system_id,
        target_kind=target_kind,
        target_ref=ref,
        link_kind=link_kind,
        governance=governance,
        node_key=node_key,
        node_id=node_id,
        node_keys=node_keys,
    )


def require_target_capability(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    capability: str,
    target_kind: str,
    target_ref: Optional[str],
    now: Optional[float] = None,
) -> ExecutionTargetGate:
    """Permit `capability` for this execution target, or refuse.

    Order:

    1. classify the target from persisted rows (`resolve_execution_target`);
    2. `ambiguous` -> `ExecutionTargetAmbiguous` (fail closed);
    3. `unmapped` -> return an un-enforced gate; the caller proceeds exactly as
       it did before, and reports the classification;
    4. `governed` -> the EXISTING `execution_mode.require_capability` decides,
       with the mapped Node as its subject. This module never re-implements the
       resolver and never passes a `flow_ref`: with a Node named, the Flow set
       is that Node's persisted membership (EM-ADR-4), and an execution entry
       point has no Flow claim to make anyway.

    Raises `ExecutionTargetAmbiguous` or `execution_mode.ExecutionModeDenied`.
    """
    mapping = resolve_execution_target(
        conn, system_id=system_id, target_kind=target_kind, target_ref=target_ref
    )
    if mapping.governance == "ambiguous":
        raise ExecutionTargetAmbiguous(mapping)
    if mapping.governance == "unmapped":
        return ExecutionTargetGate(
            mapping=mapping, capability=capability, enforced=False, decision=None
        )
    decision = execution_mode.require_capability(
        conn,
        system_id=system_id,
        capability=capability,
        node_key=mapping.node_key,
        now=now,
    )
    return ExecutionTargetGate(
        mapping=mapping, capability=capability, enforced=True, decision=decision
    )


# ---------------------------------------------------------------------------
# Making the classification visible
# ---------------------------------------------------------------------------


def governance_headers(gate: ExecutionTargetGate) -> Dict[str, str]:
    """Headers that say whether this execution was under the mode contract.

    Only facts that were actually read are emitted: an `unmapped` response
    carries the governance header alone, with no mode and no Node, because
    neither was resolved.
    """
    headers: Dict[str, str] = {GOVERNANCE_HEADER: gate.governance}
    if gate.node_key:
        headers[NODE_HEADER] = gate.node_key
    if gate.mode:
        headers[MODE_HEADER] = gate.mode
    return headers


def apply_governance_headers(response: Any, gate: ExecutionTargetGate) -> None:
    """Attach `governance_headers` to a FastAPI `Response`, if one was given.

    Tolerates `None` so a route helper can be called from a context that has no
    response object (for example one endpoint calling another in-process).
    """
    if response is None:
        return
    for name, value in governance_headers(gate).items():
        response.headers[name] = value


def governance_out(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    target_kind: str,
    target_ref: Optional[str],
    capability: Optional[str] = None,
    now: Optional[float] = None,
) -> ExecutionTargetGovernanceOut:
    """The read-only answer for one target, optionally for one capability.

    This performs the same classification the entry points perform and, when a
    capability is named, reports whether it WOULD be permitted -- without
    executing anything. A developer can therefore find out that a target is on
    the legacy path (or that it is ambiguous, or refused) before spending a
    Replay run to discover it.
    """
    mapping = resolve_execution_target(
        conn, system_id=system_id, target_kind=target_kind, target_ref=target_ref
    )
    out = ExecutionTargetGovernanceOut(
        target_kind=mapping.target_kind,
        target_ref=mapping.target_ref,
        governance=mapping.governance,
        gated=mapping.governance == "governed",
        node_key=mapping.node_key,
        node_keys=list(mapping.node_keys),
        capability=capability,
    )
    if mapping.governance == "ambiguous":
        out.denial_code = "ambiguous_target_mapping"
        out.capability_permitted = False if capability else None
        return out
    if mapping.governance == "unmapped":
        # No mode was resolved, so no mode is reported, and `capability_
        # permitted` stays None: the gate does not apply, which is a different
        # answer from "permitted".
        return out

    facts = execution_mode.load_mode_facts(
        conn, system_id=system_id, node_key=mapping.node_key, now=now
    )
    decision = execution_mode.resolve_execution_mode(facts)
    out.mode = decision.mode
    out.mode_source = decision.source_scope
    out.mode_reason = decision.reason
    if capability is not None:
        try:
            execution_mode.require_capability(
                conn,
                system_id=system_id,
                capability=capability,
                node_key=mapping.node_key,
                now=now,
            )
        except execution_mode.ExecutionModeDenied as exc:
            out.capability_permitted = False
            out.denial_code = exc.denial_code
        else:
            out.capability_permitted = True
    return out
